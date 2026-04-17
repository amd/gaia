// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Benchmark utilities: timer, memory tracker, result I/O, and comparison.

#pragma once

#include <algorithm>
#include <chrono>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

// Platform-specific memory headers
#if defined(_WIN32)
// WIN32_LEAN_AND_MEAN prevents <windows.h> from pulling in <winsock.h>,
// avoiding redefinition conflicts when <httplib.h> later includes <winsock2.h>.
#    ifndef WIN32_LEAN_AND_MEAN
#        define WIN32_LEAN_AND_MEAN
#    endif
#    ifndef NOMINMAX
#        define NOMINMAX
#    endif
#    include <windows.h>
#    include <psapi.h>
#elif defined(__APPLE__)
#    include <mach/mach.h>
#else
#    include <fstream>
#endif

#include <nlohmann/json.hpp>

using json = nlohmann::json;

namespace bench {

// ---------------------------------------------------------------------------
// Timer
// ---------------------------------------------------------------------------

class Timer {
public:
    void start() { start_ = std::chrono::high_resolution_clock::now(); }

    void stop() { end_ = std::chrono::high_resolution_clock::now(); }

    double elapsedUs() const {
        if (end_ < start_) return 0.0;
        return static_cast<double>(
            std::chrono::duration_cast<std::chrono::microseconds>(end_ - start_).count());
    }

    double elapsedMs() const { return elapsedUs() / 1000.0; }

private:
    std::chrono::high_resolution_clock::time_point start_;
    std::chrono::high_resolution_clock::time_point end_;
};

// ---------------------------------------------------------------------------
// MemoryTracker — returns current process RSS in KB
// ---------------------------------------------------------------------------

class MemoryTracker {
public:
    static long getCurrentRssKb() {
#if defined(_WIN32)
        PROCESS_MEMORY_COUNTERS pmc;
        if (GetProcessMemoryInfo(GetCurrentProcess(), &pmc, sizeof(pmc))) {
            return static_cast<long>(pmc.WorkingSetSize / 1024);
        }
        return 0;
#elif defined(__APPLE__)
        mach_task_basic_info info;
        mach_msg_type_number_t count = MACH_TASK_BASIC_INFO_COUNT;
        if (task_info(mach_task_self(), MACH_TASK_BASIC_INFO,
                      reinterpret_cast<task_info_t>(&info), &count) == KERN_SUCCESS) {
            return static_cast<long>(info.resident_size / 1024);
        }
        return 0;
#else
        // Linux: parse /proc/self/status for VmRSS
        std::ifstream f("/proc/self/status");
        std::string line;
        while (std::getline(f, line)) {
            if (line.rfind("VmRSS:", 0) == 0) {
                std::istringstream iss(line);
                std::string key;
                long val = 0;
                iss >> key >> val;
                return val; // already in KB
            }
        }
        return 0;
#endif
    }
};

// ---------------------------------------------------------------------------
// BenchmarkResult
// ---------------------------------------------------------------------------

struct BenchmarkResult {
    std::string name;
    double value;
    std::string unit;
};

// ---------------------------------------------------------------------------
// JSON I/O
// ---------------------------------------------------------------------------

inline void writeBenchmarkResults(const std::string& path,
                                   const std::vector<BenchmarkResult>& results) {
    // Timestamp (thread-safe via gmtime_r / gmtime_s)
    auto now = std::chrono::system_clock::now();
    std::time_t t = std::chrono::system_clock::to_time_t(now);
    std::tm tm_buf{};
#if defined(_WIN32)
    gmtime_s(&tm_buf, &t);
#else
    gmtime_r(&t, &tm_buf);
#endif
    std::ostringstream ts;
    ts << std::put_time(&tm_buf, "%Y-%m-%dT%H:%M:%SZ");

    // Platform string
    std::string platform;
#if defined(_WIN32)
    platform = "windows";
#elif defined(__APPLE__)
    platform = "macos";
#else
    platform = "linux";
#endif

    json root;
    root["timestamp"] = ts.str();
    root["platform"] = platform;
    json arr = json::array();
    for (const auto& r : results) {
        arr.push_back({{"name", r.name}, {"value", r.value}, {"unit", r.unit}});
    }
    root["results"] = arr;

    std::ofstream f(path);
    if (!f.is_open()) {
        throw std::runtime_error("Cannot write benchmark results to: " + path);
    }
    f << root.dump(2) << "\n";
}

inline std::vector<BenchmarkResult> readBenchmarkResults(const std::string& path) {
    std::ifstream f(path);
    if (!f.is_open()) {
        throw std::runtime_error("Cannot read benchmark results from: " + path);
    }
    json root = json::parse(f);
    std::vector<BenchmarkResult> out;
    for (const auto& r : root.at("results")) {
        out.push_back({r.at("name").get<std::string>(), r.at("value").get<double>(),
                       r.at("unit").get<std::string>()});
    }
    return out;
}

// ---------------------------------------------------------------------------
// Per-metric thresholds
// ---------------------------------------------------------------------------

inline double thresholdForMetric(const std::string& name) {
    // Binary size metrics: 10% threshold (issue: "Fail if size regresses >10%")
    if (name.find("binary_size") != std::string::npos) {
        return 10.0;
    }
    // All other metrics: 15% threshold
    return 15.0;
}

// ---------------------------------------------------------------------------
// compareAndReport: compare current vs baseline, return 0 if OK, 1 if regression
// ---------------------------------------------------------------------------

inline int compareAndReport(const std::string& baselinePath, const std::string& currentPath) {
    std::vector<BenchmarkResult> baseline = readBenchmarkResults(baselinePath);
    std::vector<BenchmarkResult> current = readBenchmarkResults(currentPath);

    // Index baseline by name
    std::map<std::string, double> baseMap;
    for (const auto& r : baseline) {
        baseMap[r.name] = r.value;
    }

    std::cout << "\n=== Benchmark Regression Report ===\n";
    std::cout << std::left << std::setw(45) << "Metric"
              << std::right << std::setw(12) << "Baseline"
              << std::setw(12) << "Current"
              << std::setw(10) << "Change"
              << std::setw(12) << "Threshold"
              << std::setw(10) << "Status" << "\n";
    std::cout << std::string(101, '-') << "\n";

    bool anyRegression = false;
    for (const auto& r : current) {
        auto it = baseMap.find(r.name);
        if (it == baseMap.end()) {
            std::cout << std::left << std::setw(45) << r.name
                      << std::right << std::setw(12) << "N/A"
                      << std::setw(12) << r.value
                      << std::setw(10) << "N/A"
                      << std::setw(12) << "N/A"
                      << std::setw(10) << "NEW" << "\n";
            continue;
        }

        double base = it->second;
        double threshold = thresholdForMetric(r.name);
        double pct = (base == 0.0) ? 0.0 : (r.value - base) / base * 100.0;

        std::string status;
        if (pct > threshold) {
            status = "FAIL";
            anyRegression = true;
        } else if (pct < -1.0) {
            status = "IMPROVED";
        } else {
            status = "OK";
        }

        std::cout << std::left << std::setw(45) << r.name << std::right
                  << std::setw(12) << std::fixed << std::setprecision(1) << base
                  << std::setw(12) << r.value
                  << std::setw(9) << std::showpos << pct << "%" << std::noshowpos
                  << std::setw(12) << (std::to_string(static_cast<int>(threshold)) + "%")
                  << std::setw(10) << status << "\n";
    }
    // Report baseline metrics absent from the current run (benchmark may have crashed)
    for (const auto& b : baseline) {
        bool found = false;
        for (const auto& r : current) {
            if (r.name == b.name) { found = true; break; }
        }
        if (!found) {
            std::cout << std::left << std::setw(45) << b.name
                      << std::right << std::setw(12) << std::fixed << std::setprecision(1)
                      << b.value
                      << std::setw(12) << "N/A"
                      << std::setw(10) << "N/A"
                      << std::setw(12) << "N/A"
                      << std::setw(10) << "MISSING" << "\n";
            anyRegression = true;
        }
    }
    std::cout << std::string(101, '-') << "\n";

    if (anyRegression) {
        std::cout << "\nRESULT: REGRESSION DETECTED — one or more metrics exceed threshold or are missing\n";
        return 1;
    }
    std::cout << "\nRESULT: PASS — no regressions detected\n";
    return 0;
}

// ---------------------------------------------------------------------------
// Median helper
// ---------------------------------------------------------------------------

inline double median(std::vector<double> v) {
    if (v.empty()) return 0.0;
    std::sort(v.begin(), v.end());
    size_t n = v.size();
    return (n % 2 == 0) ? (v[n / 2 - 1] + v[n / 2]) / 2.0 : v[n / 2];
}

} // namespace bench
