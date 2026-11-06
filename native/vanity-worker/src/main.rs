//! Public-only Warthog search with restart-safe, acknowledged-work checkpoints.
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::collections::BTreeMap;
use std::io::{self, BufRead, Read, Write};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, AtomicU64, Ordering},
    mpsc,
};
use std::time::{Duration, Instant};
use vanity_pro::{
    chains::Chain,
    coordinator::{self, Config, Mode},
    model::{Candidate, Interval, Job},
    work::WorkIndex,
};

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Checkpoint {
    next: WorkIndex,
    evaluated: WorkIndex,
    elapsed_seconds: f64,
}

#[derive(Debug)]
struct IntegrityFailure;
impl std::fmt::Display for IntegrityFailure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("public candidate verification failed")
    }
}
impl std::error::Error for IntegrityFailure {}

fn integrity(error: &anyhow::Error) -> bool {
    error.downcast_ref::<IntegrityFailure>().is_some()
        || error
            .downcast_ref::<vanity_pro::backend::CorrectnessError>()
            .is_some()
}

fn retain_failure(slot: &mut Option<anyhow::Error>, error: anyhow::Error) {
    if slot
        .as_ref()
        .is_none_or(|previous| integrity(&error) && !integrity(previous))
    {
        *slot = Some(error);
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    protocol: u32,
    purpose: String,
    seconds: Option<f64>,
    job: Job,
    run_id: String,
    checkpoint: Checkpoint,
    backend: String,
    threads: usize,
}

/// Pending intervals are bounded by the worker count. An acknowledged frontier
/// can never pass an unfinished interval, even when later batches finish first.
struct Ledger {
    cursor: WorkIndex,
    pending: BTreeMap<WorkIndex, WorkIndex>,
    evaluated: WorkIndex,
}
impl Ledger {
    fn allocate(&mut self, job: &Job, batch: u32) -> Result<Option<Interval>> {
        let left = self.cursor.distance(job.work_limit()).ok();
        let count = left.map_or(batch, |n| u64::from(batch).min(n) as u32);
        if count == 0 {
            return Ok(None);
        }
        let range = Interval::new(self.cursor, count)?;
        ensure!(range.end <= job.work_limit(), "work range exhausted");
        self.cursor = range.end;
        self.pending.insert(range.start, range.end);
        Ok(Some(range))
    }
    fn finish(
        &mut self,
        range: &Interval,
        completed: u32,
        evaluated: u32,
        found: bool,
    ) -> Result<()> {
        ensure!(
            self.pending.get(&range.start) == Some(&range.end),
            "unknown work interval"
        );
        ensure!(
            completed <= range.count()? && evaluated <= completed,
            "invalid worker completion"
        );
        self.evaluated = self.evaluated.checked_add(u64::from(evaluated))?;
        if !found {
            self.pending.remove(&range.start);
            if completed < range.count()? {
                self.pending
                    .insert(range.start.checked_add(u64::from(completed))?, range.end);
            }
        }
        // A winning batch remains pending until the owner durably records the
        // candidate. A lost terminal message therefore causes safe replay.
        Ok(())
    }
    fn checkpoint(&self, seconds: f64) -> Checkpoint {
        Checkpoint {
            next: self
                .pending
                .first_key_value()
                .map_or(self.cursor, |(start, _)| *start),
            evaluated: self.evaluated,
            elapsed_seconds: seconds,
        }
    }
}

static SEQUENCE: AtomicU64 = AtomicU64::new(0);

fn emit(event: &str, id: &str, run_id: &str, value: serde_json::Value) -> Result<()> {
    let mut out = io::stdout().lock();
    serde_json::to_writer(
        &mut out,
        &json!({"protocol":3,"sequence":SEQUENCE.fetch_add(1, Ordering::Relaxed)+1,
            "event":event,"job_id":id,"run_id":run_id,"data":value}),
    )?;
    writeln!(out)?;
    out.flush()?;
    Ok(())
}

fn run(id_out: &mut String, run_out: &mut String, stage: &mut &'static str) -> Result<()> {
    let initialization = Instant::now();
    let mut line = String::new();
    io::stdin().lock().take(65537).read_line(&mut line)?;
    ensure!(
        line.len() <= 65536 && line.ends_with('\n'),
        "invalid request size"
    );
    let request: Request = serde_json::from_str(&line)?;
    ensure!(request.protocol == 3, "unsupported worker protocol");
    request.job.validate()?;
    ensure!(
        request.job.profile().chain == Chain::Warthog,
        "Warthog mainnet jobs only"
    );
    ensure!((1..=256).contains(&request.threads), "invalid thread count");
    ensure!(
        request.run_id.len() == 36
            && request
                .run_id
                .bytes()
                .enumerate()
                .all(|(i, b)| if [8, 13, 18, 23].contains(&i) {
                    b == b'-'
                } else {
                    b.is_ascii_digit() || (b'a'..=b'f').contains(&b)
                }),
        "invalid run identity"
    );
    ensure!(
        request.checkpoint.next <= request.job.work_limit()
            && request.checkpoint.elapsed_seconds.is_finite()
            && (0.0..=1e12).contains(&request.checkpoint.elapsed_seconds),
        "invalid checkpoint"
    );
    let mode = match request.backend.as_str() {
        "cpu" => Mode::Cpu,
        "opencl" => Mode::Opencl,
        "auto" => Mode::Auto,
        _ => anyhow::bail!("invalid backend"),
    };
    let id = request.job.session_id.to_string();
    let run_id = request.run_id;
    *id_out = id.clone();
    *run_out = run_id.clone();
    let calibration = request.purpose == "calibration";
    ensure!(
        (calibration
            && request
                .seconds
                .is_some_and(|s| s.is_finite() && (5.0..=30.0).contains(&s)))
            || (request.purpose == "search" && request.seconds.is_none()),
        "invalid search purpose or duration"
    );
    let cancel = Arc::new(AtomicBool::new(false));
    let control = cancel.clone();
    std::thread::spawn(move || {
        let mut command = String::new();
        let _ = io::stdin().lock().take(1024).read_line(&mut command);
        control.store(true, Ordering::Relaxed);
    });
    let config = Config {
        mode,
        profile: Some(request.job.profile()),
        threads: request.threads,
        batch_size: if mode == Mode::Cpu { 32768 } else { 4194304 },
        benchmark_strategy: Some(request.job.strategy),
        encoded_suffix: !request.job.pattern.suffix.is_empty(),
        tuning_cache: None,
        cache: None,
        quiet: true,
        ..Config::default()
    };
    emit(
        "initializing",
        &id,
        &run_id,
        json!({"backend":request.backend}),
    )?;
    *stage = "initialization_failed";
    let workers = coordinator::select(&config)?;
    ensure!(!workers.is_empty(), "no search hardware available");
    let names: Vec<String> = workers.iter().map(|worker| worker.name()).collect();
    let mut devices = Vec::new();
    if names.iter().any(|name| name.starts_with("opencl:")) {
        for device in vanity_pro::backend::opencl::discover()? {
            if names
                .iter()
                .any(|name| name.starts_with(&format!("opencl:{}:", device.index)))
            {
                devices.push(
                    json!({"index":device.index,"name":device.name,"vendor":device.vendor,
                    "driver":device.driver,"api":device.api,"memory_bytes":device.memory_bytes}),
                );
            }
        }
    }
    emit(
        "ready",
        &id,
        &run_id,
        json!({"workers":names,
        "hardware":{"cpu":vanity_pro::backend::cpu_info(),"gpus":devices,"threads":request.threads},
        "initialization_seconds":initialization.elapsed().as_secs_f64()}),
    )?;
    *stage = "search_failed";
    let job = Arc::new(request.job);
    let ledger = Arc::new(Mutex::new(Ledger {
        cursor: request.checkpoint.next,
        pending: BTreeMap::new(),
        evaluated: request.checkpoint.evaluated,
    }));
    let (sender, receiver) = mpsc::channel::<Result<Option<Candidate>>>();
    let mut handles = Vec::new();
    let started = Instant::now();
    for mut worker in workers {
        let batch = vanity_pro::backend::aligned_batch(
            &job,
            config.batch_size,
            worker.name().starts_with("opencl:"),
        );
        let (job, ledger, cancel, sender) =
            (job.clone(), ledger.clone(), cancel.clone(), sender.clone());
        handles.push(std::thread::spawn(move || {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(
                || -> Result<Option<Candidate>> {
                    while !cancel.load(Ordering::Relaxed) {
                        let Some(range) = ledger
                            .lock()
                            .map_err(|_| anyhow::anyhow!("work ledger failed"))?
                            .allocate(&job, batch)?
                        else {
                            anyhow::bail!("work range exhausted; start a fresh search");
                        };
                        let result = worker.run(&job, &range, &cancel)?;
                        if let Some(candidate) = &result.candidate {
                            candidate.verify(&job).map_err(|_| IntegrityFailure)?;
                            if !(range.contains(candidate.work_index(&job)?)
                                && candidate.work_index(&job)?
                                    < range.start.checked_add(u64::from(result.completed))?)
                            {
                                return Err(IntegrityFailure.into());
                            }
                        }
                        ledger
                            .lock()
                            .map_err(|_| anyhow::anyhow!("work ledger failed"))?
                            .finish(
                                &range,
                                result.completed,
                                result.evaluated,
                                result.candidate.is_some() && !calibration,
                            )?;
                        if let Some(candidate) = result.candidate
                            && !calibration
                        {
                            return Ok(Some(candidate));
                        }
                        ensure!(
                            result.completed == range.count()? || cancel.load(Ordering::Relaxed),
                            "partial work without cancellation"
                        );
                    }
                    Ok(None)
                },
            ))
            .unwrap_or_else(|_| Err(anyhow::anyhow!("public search worker panicked")));
            if !matches!(&result, Ok(None)) {
                cancel.store(true, Ordering::Relaxed);
            }
            let _ = sender.send(result);
        }));
    }
    drop(sender);
    let progress = || -> Result<serde_json::Value> {
        let elapsed = started.elapsed().as_secs_f64();
        let checkpoint = ledger
            .lock()
            .map_err(|_| anyhow::anyhow!("work ledger failed"))?
            .checkpoint(request.checkpoint.elapsed_seconds + elapsed);
        Ok(json!({"elapsed_seconds":checkpoint.elapsed_seconds,
            "search_seconds":elapsed,
            "candidates_exact":checkpoint.evaluated.decimal(),
            "workers":names,"checkpoint":checkpoint}))
    };
    let (mut found, mut failure) = (None, None);
    let mut duration_reached = false;
    let mut remaining = handles.len();
    while remaining > 0 {
        match receiver.recv_timeout(Duration::from_millis(250)) {
            Ok(result) => {
                remaining -= 1;
                match result {
                    Ok(Some(candidate)) if found.is_none() => found = Some(candidate),
                    Err(error) => retain_failure(&mut failure, error),
                    _ => (),
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                if request
                    .seconds
                    .is_some_and(|limit| started.elapsed().as_secs_f64() >= limit)
                {
                    duration_reached = true;
                    cancel.store(true, Ordering::Relaxed);
                }
                if emit("progress", &id, &run_id, progress()?).is_err() {
                    cancel.store(true, Ordering::Relaxed);
                }
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                retain_failure(
                    &mut failure,
                    anyhow::anyhow!("public worker channel closed unexpectedly"),
                );
                cancel.store(true, Ordering::Relaxed);
                break;
            }
        }
    }
    for handle in handles {
        if handle.join().is_err() {
            retain_failure(
                &mut failure,
                anyhow::anyhow!("public worker stopped unexpectedly"),
            );
        }
    }
    if let Some(error) = failure {
        let mut report = progress()?;
        report = json!({"code":if integrity(&error) {
            "integrity_failed"} else {"search_failed"},"checkpoint":report["checkpoint"]});
        return emit("error", &id, &run_id, report);
    }
    let mut report = progress()?;
    report["candidate"] = serde_json::to_value(&found)?;
    emit(
        if found.is_some() {
            "found"
        } else if calibration && duration_reached {
            "calibrated"
        } else {
            "paused"
        },
        &id,
        &run_id,
        report,
    )
}

fn main() {
    let (mut id, mut run_id) = (String::new(), String::new());
    let mut stage = "invalid_request";
    if let Err(error) = run(&mut id, &mut run_id, &mut stage) {
        if integrity(&error) {
            stage = "integrity_failed";
        }
        let _ = emit("error", &id, &run_id, json!({"code":stage}));
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn ledger() -> Ledger {
        Ledger {
            cursor: WorkIndex::ZERO,
            pending: BTreeMap::new(),
            evaluated: WorkIndex::ZERO,
        }
    }
    fn job() -> Job {
        serde_json::from_value(
            json!({"schema":3,"session_id":"00000000-0000-4000-8000-000000000001",
            "public_key":"0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
            "pattern":{"profile":{"chain":"warthog","network":"mainnet","address_type":"account"},
                "prefix":"a","suffix":""},"strategy":"endomorphism-v1"}),
        )
        .unwrap()
    }
    #[test]
    fn out_of_order_batches_do_not_skip_unconfirmed_work() {
        let mut ledger = ledger();
        let first = ledger.allocate(&job(), 12).unwrap().unwrap();
        let second = ledger.allocate(&job(), 12).unwrap().unwrap();
        ledger.finish(&second, 12, 12, false).unwrap();
        assert_eq!(ledger.checkpoint(1.0).next, WorkIndex::ZERO);
        ledger.finish(&first, 12, 12, false).unwrap();
        assert_eq!(ledger.checkpoint(2.0).next, WorkIndex::from_u64(24));
    }
    #[test]
    fn losing_a_hit_message_replays_the_winning_batch() {
        let mut ledger = ledger();
        let range = ledger.allocate(&job(), 12).unwrap().unwrap();
        ledger.finish(&range, 12, 12, true).unwrap();
        assert_eq!(ledger.checkpoint(1.0).next, range.start);
    }
    #[test]
    fn partial_cancellation_replays_only_the_unconfirmed_tail() {
        let mut ledger = ledger();
        let range = ledger.allocate(&job(), 12).unwrap().unwrap();
        ledger.finish(&range, 5, 5, false).unwrap();
        assert_eq!(ledger.checkpoint(1.0).next, WorkIndex::from_u64(5));
    }
    #[test]
    fn resume_supports_indices_above_u64_without_changing_strategy() {
        let mut ledger = ledger();
        ledger.cursor = WorkIndex::from_u64(u64::MAX).checked_add(1).unwrap();
        let range = ledger.allocate(&job(), 12).unwrap().unwrap();
        assert_eq!(range.count().unwrap(), 12);
        ledger.finish(&range, 12, 12, false).unwrap();
        assert_eq!(ledger.checkpoint(1.0).next, range.end);
    }

    #[test]
    fn correctness_failures_cannot_be_masked_by_later_backend_errors() {
        let mut failure = None;
        retain_failure(&mut failure, anyhow::anyhow!("driver error"));
        retain_failure(&mut failure, IntegrityFailure.into());
        retain_failure(&mut failure, anyhow::anyhow!("another driver error"));
        assert!(integrity(failure.as_ref().unwrap()));
        let error: anyhow::Error = vanity_pro::backend::CorrectnessError("fixture".into()).into();
        assert!(integrity(&error.context("initialization")));
    }
}
