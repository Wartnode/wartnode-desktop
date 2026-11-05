// SPDX-License-Identifier: GPL-3.0-or-later
// Warthog-only entry point. No pool, proxy, fallback, or other-coin options.
#include "warthog_miner.hpp"
#include "warthog_benchmark.hpp"
#include "warthog_gpu.hpp"
#include "platform.hpp"
#include "mining_budget.hpp"
#include <univalue.h>
#include <algorithm>
#include <charconv>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

int main(int argc, char** argv) {
    try {
        std::string payout, backend{"opencl"}, threads{"1"}, profile;
        unsigned port = 19456, device = 0, duration = 5, gpu_limit = 50;
        bool benchmark = false;
        for (int i = 1; i < argc; ++i) {
            const std::string option = argv[i];
            if (option == "--benchmark") { benchmark = true; continue; }
            if (option == "--devices") {
                UniValue result{UniValue::VOBJ}, opencl{UniValue::VARR};
                result.pushKV("cpu", brminer::cpuDescription());
                try {
                    for (const auto& name : brminer::warthog::OpenClSha256tMiner::devices()) opencl.push_back(UniValue{name});
                } catch (const std::exception&) {}
                result.pushKV("opencl",opencl);
                std::cout << result.write() << '\n';
                return 0;
            }
            if (option == "--version") {
                std::cout << "WartNode in-house miner 0.2.0 / clang-cl / driver OpenCL + CPU / BRminer-derived\n";
                return 0;
            }
            if (i + 1 == argc) throw std::invalid_argument("Missing option value");
            const std::string value = argv[++i];
            if (option == "--payout") payout = value;
            else if (option == "--backend") backend = value;
            else if (option == "--profile-dir") profile = value;
            else if (option == "--threads" || option == "--port" || option == "--device" || option == "--duration" || option == "--gpu-limit") {
                unsigned parsed{};
                const auto conversion = std::from_chars(value.data(), value.data()+value.size(), parsed);
                if (conversion.ec != std::errc{} || conversion.ptr != value.data()+value.size())
                    throw std::invalid_argument("Invalid numeric option");
                if (option == "--gpu-limit") {
                    brminer::warthog::validateGpuLimit(parsed);
                    gpu_limit = parsed;
                } else if (option == "--port") {
                    if (parsed != 19456) throw std::invalid_argument("Only the managed local Stratum port is allowed");
                    port = parsed;
                } else if (option == "--device") {
                    if (parsed > 63) throw std::invalid_argument("Invalid GPU device index");
                    device = parsed;
                } else if (option == "--duration") {
                    if (parsed < 1 || parsed > 300) throw std::invalid_argument("Benchmark duration must be 1..300 seconds");
                    duration = parsed;
                } else {
                    if (!parsed || parsed > std::max(1u, std::thread::hardware_concurrency()))
                        throw std::invalid_argument("Invalid CPU thread count");
                    threads = value;
                }
            } else throw std::invalid_argument("Only managed-node solo mining options are supported");
        }
        if (!benchmark && (payout.size() != 48 || payout.find_first_not_of("0123456789abcdefABCDEF") != std::string::npos))
            throw std::invalid_argument("A valid Warthog payout address is required");
        if (backend != "opencl")
            throw std::invalid_argument("Unsupported mining backend");
        bool has_gpu = false;
        try { has_gpu = device < brminer::warthog::OpenClSha256tMiner::devices().size(); }
        catch (const std::exception&) {}
        if (!has_gpu) throw std::invalid_argument("Warthog mining requires a supported GPU and CPU together");
        brminer::warthog::TuningControls tuning;
        tuning.backend = backend;
        tuning.profile_directory = profile;
        _putenv_s("WARTNODE_DEVICE_INDEX",std::to_string(device).c_str());
        _putenv_s("BRMINER_BENCHMARK_TELEMETRY","1");
        if (benchmark) {
            brminer::warthog::BenchmarkOptions options;
            options.duration_seconds = duration;
            options.warmup_seconds = 1;
            options.threads = static_cast<unsigned>(std::stoul(threads));
            options.backend = backend;
            options.quiet = true;
            std::vector<double> gpu_samples;
            auto result=brminer::warthog::runBudgetedBenchmark(options,gpu_limit,&gpu_samples);
            UniValue report;
            if (!report.read(brminer::warthog::benchmarkJson(result))) throw std::runtime_error("Invalid benchmark report");
            report.pushKV("gpu_limit_pct",static_cast<int>(gpu_limit));
            UniValue samples{UniValue::VARR};
            for (auto sample : gpu_samples) samples.push_back(UniValue{sample});
            report.pushKV("gpu_scan_busy_pct",samples);
            std::cout << report.write() << '\n';
            return 0;
        }
        return brminer::warthog::runBudgetedMiner(payout, threads, "127.0.0.1:" + std::to_string(port), tuning,gpu_limit);
    } catch (const std::exception& error) {
        std::cerr << "WartNode miner: " << error.what() << '\n';
        return 1;
    }
}
