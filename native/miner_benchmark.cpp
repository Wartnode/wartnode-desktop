// SPDX-License-Identifier: GPL-3.0-or-later
// Driver-only benchmark reporting for the BRminer-derived WartNode engine.
#include "warthog_benchmark.hpp"
#include "platform.hpp"
#include <univalue.h>
#include <cmath>
#include <numeric>
#ifndef WARTNODE_MINER_BUILD_ID
#define WARTNODE_MINER_BUILD_ID "development"
#endif
namespace brminer::warthog {
void summarizeBenchmark(BenchmarkResult& result) {
    result.cpu = cpuDescription();
    result.os = "windows-x86_64";
    result.build_id = WARTNODE_MINER_BUILD_ID;
    result.measured_s = 0;
    double work{};
    for (const auto& sample : result.samples) {
        result.measured_s += sample.interval_s;
        work += sample.janus_hps * sample.interval_s;
    }
    if (result.measured_s == 0) return;
    result.mean_janus_hps = work/result.measured_s;
    double variance{}, squared_weights{};
    for (const auto& sample : result.samples) {
        variance += sample.interval_s * std::pow(sample.janus_hps-result.mean_janus_hps,2);
        squared_weights += sample.interval_s * sample.interval_s;
    }
    result.stddev_janus_hps = std::sqrt(variance/result.measured_s);
    const auto effective_samples = result.measured_s*result.measured_s/squared_weights;
    result.standard_error_hps = result.stddev_janus_hps/std::sqrt(effective_samples);
}
std::string benchmarkJson(const BenchmarkResult& result) {
    UniValue json{UniValue::VOBJ};
    json.pushKV("schema","wartnode.benchmark.v1");
    json.pushKV("miner_version","0.2.0");
    json.pushKV("build_id",result.build_id);
    json.pushKV("compiler","clang-cl");
    json.pushKV("backend",result.backend);
    json.pushKV("cpu",result.cpu);
    json.pushKV("threads",static_cast<int>(result.options.threads));
    json.pushKV("requested_seconds",result.options.duration_seconds);
    json.pushKV("measured_seconds",result.measured_s);
    json.pushKV("mean_janus_hps",result.mean_janus_hps);
    json.pushKV("stddev_janus_hps",result.stddev_janus_hps);
    json.pushKV("correctness_passed",result.correctness_passed);
    json.pushKV("cancelled",result.cancelled);
    UniValue devices{UniValue::VARR};
    for (const auto& device : result.gpu_names) devices.push_back(UniValue{device});
    json.pushKV("gpu_names",devices);
    UniValue samples{UniValue::VARR};
    for (const auto& sample : result.samples) {
        UniValue row{UniValue::VOBJ};
        row.pushKV("interval_s",sample.interval_s);
        row.pushKV("janus_hps",sample.janus_hps);
        row.pushKV("gpu_sha_hps",sample.gpu_sha_hps);
        row.pushKV("verus_hps",sample.verus_hps);
        samples.push_back(row);
    }
    json.pushKV("samples",samples);
    return json.write();
}
}
