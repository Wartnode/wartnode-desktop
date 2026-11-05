# Recovery from brief node sync interruptions

The November 5, 2026 local beta report exposed a desktop lifecycle bug. The
node logged a one-block update from height 5,256,364 to 5,256,365 at
07:48:23.440 and returned to synced at 07:48:23.564 (124 ms). The miner
received the new job, then stopped at 07:48:24. There were no corresponding
node or GPU errors. Individual desktop readiness checks were not logged,
so the evidence cannot distinguish an observed unsynced flag from a tip
change between RPC requests. Either previously caused the desktop to stop
mining permanently.

## Behavior

- Readiness checks retry an unsynced or inconsistent head/template snapshot
  up to three times, 150 ms apart, sharing the RPC client's eight-second
  deadline. An advancing head does not grant readiness for old work. The
  completed result replaces the last published state; starting a check
  does not itself publish an offline state.
- An already running miner has a five-second grace period after the first
  failed sync/connectivity check. A short recovery keeps the same process.
  A longer interruption stops the process and shows **Waiting for node**.
  Polling and RPC duration can extend the time until a stop is observed.
- Two consecutive healthy polls allow a restart. The restart runs the
  normal node, mainnet, payout, GPU and CPU-limit checks using the original
  reviewed configuration. Slow hardware detection requires a fresh node
  check before spawning. Settings review holds automatic restart; saving
  settings while waiting cancels the old request and requires Start mining.
- Stop mining, Stop node, mode changes, resync, shutdown and low disk space
  cancel recovery. A generation token prevents an obsolete queued start or
  restart from running after cancellation, including cancellation during
  RPC or hardware detection.
- Miner crashes, invalid node responses and failed restarts cancel recovery
  and require manual action. This feature does not restart a stopped node.
  Mining intent is held in memory and is not restored after reopening the
  application. Wallet/payment readiness checks retain their strict policy.

## Diagnostics and validation

`logs/lifecycle.log` records UTC timestamps, readiness changes, retry reasons,
grace periods, waiting, starts, resumes and stops. It rotates at 5 MiB and
retains one previous file. Events exclude payout addresses, credentials and
raw RPC error text. Existing node and miner logs remain available.

`tests/test_mining_recovery.py` covers advancing tips, reorgs, brief sync,
long sync, connectivity outages, stable recovery, frozen configuration,
concurrent cancellation, slow hardware detection, hard failures and session
boundaries. Native UI tests cover waiting labels, Stop controls, settings
review, tray handling and the poll callback. These use isolated profiles
and simulated RPC/miner state; they do not manipulate the installed app or
prove a physical GPU soak. This change must be packaged in a new build
before it affects an existing installation.

Validation on November 5, 2026: the standard `scripts/run-tests.py`
application suite passed all 218 tests (101.509 seconds), with no
failures, errors or skips. The focused recovery/UI suite also passed.
