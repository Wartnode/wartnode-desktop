// Internal acceptance executable; never included in the product bundle.
#include "warthog_miner.hpp"
#include "warthog_gpu.hpp"
#include <cstdlib>
#include <iostream>

int main() {
    try {
        const bool gpu = !brminer::warthog::OpenClSha256tMiner::devices().empty();
        if (!gpu) throw std::runtime_error("GPU required for protocol acceptance");
        _putenv_s("BRMINER_BENCHMARK_TELEMETRY","1");
        _putenv_s("WARTNODE_DEVICE_INDEX","0");
        brminer::warthog::TuningControls tuning;
        tuning.backend = "opencl";
        return brminer::warthog::runMiner("0000000000000000000000000000000000000000de47c9b2", "2", "127.0.0.1:19457", tuning);
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
