// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <chrono>
#include <stdexcept>
#include <string_view>
#include <vector>

namespace brminer::warthog {
inline void validateGpuLimit(unsigned percent) {
    if (percent < 5 || percent > 95)
        throw std::invalid_argument("GPU limit must be between 5% and 95%");
}

// Every completed scan pays its idle time before another dispatch. Queue waits
// and CPU backpressure provide additional idle time; they never earn burst credit.
inline auto gpuBudgetDeadline(std::chrono::steady_clock::time_point started,
    std::chrono::steady_clock::time_point finished, unsigned percent) {
    validateGpuLimit(percent);
    const auto cycle=std::chrono::duration<double>(finished-started)*(100.0/percent);
    return started+std::chrono::duration_cast<std::chrono::steady_clock::duration>(cycle);
}

struct TuningControls;
struct BenchmarkOptions;
struct BenchmarkResult;
int runBudgetedMiner(std::string_view payout, std::string_view threads,
    std::string_view node, const TuningControls& tuning, unsigned gpu_percent);
BenchmarkResult runBudgetedBenchmark(const BenchmarkOptions& options, unsigned gpu_percent,
    std::vector<double>* gpu_busy_samples=nullptr);
}
