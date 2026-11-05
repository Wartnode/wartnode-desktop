// SPDX-License-Identifier: GPL-3.0-or-later
#ifdef NDEBUG
#undef NDEBUG
#endif
#include "miner_telemetry.hpp"
#include <cassert>
#include <iostream>

int main() {
    using namespace brminer;
    using namespace brminer::warthog;
    GpuTelemetry other,selected;
    other.name="GeForce RTX 3090";other.uuid="GPU-other";other.temperature_c=99;
    selected.name="GeForce RTX 4060";selected.uuid="GPU-selected";selected.temperature_c=61;
    const std::vector<std::string> devices{"NVIDIA GeForce RTX 3090","NVIDIA GeForce RTX 4060"};
    auto matched=selectedTelemetry({selected,other},devices,1);
    assert(matched.uuid=="GPU-selected" && matched.temperature_c==61);
    auto ambiguous=selectedTelemetry({selected,selected},{"GeForce RTX 4060","GeForce RTX 4060"},1);
    assert(!ambiguous.temperature_c && ambiguous.uuid=="opencl:1");
    MiningStats stats;
    stats.uptime=5;stats.janus=374410000;stats.janus_average=370000000;
    stats.gpu_sha=687990000;stats.verus=20970000;stats.accepted=42;stats.gpus={selected,other};
    UniValue parsed;
    const auto wire=dashboardTelemetry(stats,"fixture-session",devices,1);
    assert(wire.size()<4096 && parsed.read(wire));
    assert(parsed["schema"].getInt<std::int64_t>()==1);
    assert(parsed["accepted"].getInt<std::int64_t>()==42);
    assert(parsed["gpus"][0]["id"].get_str()=="GPU-selected");
    assert(parsed["gpus"][0]["temperature_c"].get_real()==61);
    assert(parsed["gpus"][0]["power_w"].isNull());
    assert(wire.find("payout")==std::string::npos && wire.find("endpoint")==std::string::npos);
    std::cout<<wire<<'\n';
}
