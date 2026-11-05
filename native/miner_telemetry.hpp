// SPDX-License-Identifier: GPL-3.0-or-later
// Versioned public telemetry; no keys, endpoints, or payout addresses.
#pragma once
#include "warthog_console.hpp"
#include <univalue.h>
#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <limits>
#include <string>
#include <vector>

namespace brminer::warthog {

inline std::string telemetryName(std::string value) {
    value.erase(std::remove_if(value.begin(),value.end(),[](unsigned char c){return c<32;}),value.end());
    if (value.size()>128) value.resize(128);
    return value;
}

inline std::string normalizedGpuName(const std::string& value) {
    std::string result;
    for (const unsigned char c:value) if (std::isalnum(c)) result+=static_cast<char>(std::tolower(c));
    if (result.starts_with("nvidia")) result.erase(0,6);
    return result;
}

inline GpuTelemetry selectedTelemetry(const std::vector<GpuTelemetry>& sensors,
        const std::vector<std::string>& opencl_names,std::size_t selected) {
    GpuTelemetry unavailable;
    if (selected>=opencl_names.size()) return unavailable;
    unavailable.name=telemetryName(opencl_names[selected]);
    unavailable.uuid="opencl:"+std::to_string(selected);
    const auto name=normalizedGpuName(opencl_names[selected]);
    // A unique model in both inventories identifies the same physical GPU.
    // Identical-model multi-GPU rigs remain unavailable until bus/UUID mapping
    // exists; never assume that NVML and OpenCL enumerate devices in one order.
    if (std::count_if(opencl_names.begin(),opencl_names.end(),[&](const auto& n){return normalizedGpuName(n)==name;})!=1)
        return unavailable;
    const auto matches=std::count_if(sensors.begin(),sensors.end(),[&](const auto& gpu){return normalizedGpuName(gpu.name)==name;});
    if (matches!=1) return unavailable;
    auto result=*std::find_if(sensors.begin(),sensors.end(),[&](const auto& gpu){return normalizedGpuName(gpu.name)==name;});
    result.name=unavailable.name;
    if (result.uuid.empty()) result.uuid=unavailable.uuid;
    return result;
}

inline std::string dashboardTelemetry(const MiningStats& stats,const std::string& session,
        const std::vector<std::string>& devices,std::size_t selected) {
    UniValue result{UniValue::VOBJ};
    result.pushKV("schema",1);
    result.pushKV("session",session);
    const auto number=[&](UniValue& object,const char* key,double value) {
        object.pushKV(key,std::isfinite(value)&&value>=0?UniValue{value}:UniValue{});
    };
    number(result,"elapsed_s",stats.uptime);
    number(result,"janus_hps",stats.janus);
    number(result,"average_janus_hps",stats.janus_average);
    number(result,"gpu_sha_hps",stats.gpu_sha);
    number(result,"verus_hps",stats.verus);
    const auto counter=[&](const char* key,std::uint64_t value) {
        result.pushKV(key,static_cast<std::int64_t>(std::min(value,static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()))));
    };
    counter("accepted",stats.accepted);counter("rejected",stats.rejected);counter("blocks",stats.blocks);
    UniValue gpus{UniValue::VARR};
    if (selected<devices.size()) {
        const auto gpu=selectedTelemetry(stats.gpus,devices,selected);
        UniValue row{UniValue::VOBJ};
        row.pushKV("id",telemetryName(gpu.uuid));
        row.pushKV("name",telemetryName(gpu.name));
        const auto sensor=[&](const char* key,const auto& value) {
            if (value) number(row,key,*value);else row.pushKV(key,UniValue{});
        };
        sensor("temperature_c",gpu.temperature_c);sensor("utilization_pct",gpu.utilization_pct);
        sensor("power_w",gpu.power_w);sensor("core_mhz",gpu.core_mhz);sensor("memory_mhz",gpu.memory_mhz);
        gpus.push_back(row);
    }
    result.pushKV("gpus",gpus);
    return result.write();
}

} // namespace brminer::warthog
