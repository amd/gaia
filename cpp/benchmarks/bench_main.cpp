// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// C++ framework performance benchmarks.
// Measures binary size, startup time, loop latency, and memory footprint.
//
// Usage:
//   # Run all benchmarks and write results
//   gaia_benchmarks --output results.json \
//       --static-lib-size <bytes> --shared-lib-size <bytes> --exe-size <bytes>
//
//   # Compare current vs baseline
//   gaia_benchmarks --compare --baseline baseline.json --current results.json

#include "bench_utils.h"
#include "mock_llm_server.h"

#include <gaia/agent.h>
#include <gaia/console.h>
#include <gaia/tool_registry.h>
#include <gaia/types.h>

#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>

// ---------------------------------------------------------------------------
// BenchAgent — minimal agent subclass for benchmarking
// ---------------------------------------------------------------------------

class BenchAgent : public gaia::Agent {
public:
    explicit BenchAgent(const gaia::AgentConfig& config) : gaia::Agent(config) {
        // Do NOT call init() here — startup benchmark calls benchInit() explicitly
    }

    /// Expose init() for explicit invocation in startup benchmark.
    void benchInit() {
        if (initCalled_) return;
        initCalled_ = true;
        init();
        // Silence final-answer output so benchmark iterations don't flood stdout
        setOutputHandler(std::make_unique<gaia::SilentConsole>(true));
    }

protected:
    void registerTools() override {
        gaia::ToolParameter msgParam;
        msgParam.name = "message";
        msgParam.type = gaia::ToolParamType::STRING;
        msgParam.description = "Message to echo";
        msgParam.required = true;

        toolRegistry().registerTool(
            "echo",
            "Echo a message back",
            [](const nlohmann::json& args) -> nlohmann::json {
                return nlohmann::json{{"echoed", args.value("message", "")}};
            },
            {msgParam});
    }

    std::string getSystemPrompt() const override { return "You are a benchmark agent."; }

private:
    bool initCalled_ = false;
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static gaia::AgentConfig makeConfig(const std::string& url) {
    gaia::AgentConfig cfg;
    cfg.baseUrl = url;
    cfg.modelId = "";      // empty → skip ensureModelLoaded()
    cfg.maxSteps = 5;
    cfg.silentMode = true; // suppress all console output
    cfg.debug = false;
    return cfg;
}

// ---------------------------------------------------------------------------
// Benchmark 1: Binary Size
// Records sizes passed via CLI args — actual measurement happens in CI shell.
// ---------------------------------------------------------------------------

static bench::BenchmarkResult benchStaticLibSize(long bytes) {
    return {"binary_size_static_lib_bytes", static_cast<double>(bytes), "bytes"};
}

static bench::BenchmarkResult benchSharedLibSize(long bytes) {
    return {"binary_size_shared_lib_bytes", static_cast<double>(bytes), "bytes"};
}

static bench::BenchmarkResult benchExeSize(long bytes) {
    return {"binary_size_example_exe_bytes", static_cast<double>(bytes), "bytes"};
}

// ---------------------------------------------------------------------------
// Benchmark 2: Startup Time
// N iterations of: construct BenchAgent → benchInit() → systemPrompt()
// No HTTP calls — modelId is empty, so no ensureModelLoaded().
// ---------------------------------------------------------------------------

static bench::BenchmarkResult benchStartupTime(int iterations = 100) {
    std::cout << "  Running startup benchmark (" << iterations << " iterations)...\n";

    // Use a dummy URL — no HTTP calls will be made (modelId is empty)
    const std::string dummyUrl = "http://127.0.0.1:1"; // won't be contacted

    std::vector<double> times;
    times.reserve(iterations);

    bench::Timer timer;
    for (int i = 0; i < iterations; ++i) {
        timer.start();
        {
            BenchAgent agent(makeConfig(dummyUrl));
            agent.benchInit();
            (void)agent.systemPrompt();
        }
        timer.stop();
        times.push_back(timer.elapsedUs());
    }

    double med = bench::median(times);
    std::cout << "  Startup median: " << std::fixed << std::setprecision(1) << med << " us\n";
    return {"startup_time_median_us", med, "us"};
}

// ---------------------------------------------------------------------------
// Benchmark 3: Loop Latency
// N iterations of processQuery() with a mock server.
// Each call uses a 2-step sequence: tool call → answer.
// History is cleared between iterations so each call is independent.
// ---------------------------------------------------------------------------

static bench::BenchmarkResult benchLoopLatency(int iterations = 50) {
    std::cout << "  Running loop latency benchmark (" << iterations << " iterations)...\n";

    bench::MockLlmServer server;
    BenchAgent agent(makeConfig(server.baseUrl()));
    agent.benchInit();
    agent.setDefaultPolicy(gaia::ToolPolicy::ALLOW);

    std::vector<double> times;
    times.reserve(iterations);

    bench::Timer timer;
    for (int i = 0; i < iterations; ++i) {
        // Queue: tool call first, then answer
        server.pushResponse(bench::kToolCall);
        server.pushResponse(bench::kDefaultAnswer);

        agent.clearHistory();

        timer.start();
        agent.processQuery("benchmark");
        timer.stop();
        times.push_back(timer.elapsedUs());
    }

    double med = bench::median(times);
    std::cout << "  Loop latency median: " << std::fixed << std::setprecision(1) << med
              << " us\n";
    return {"loop_latency_median_us", med, "us"};
}

// ---------------------------------------------------------------------------
// Benchmark 4: Memory Footprint
// 20 processQuery() calls WITHOUT clearing history (conversation accumulates).
// Measures baseline RSS, peak RSS, and per-step growth.
// ---------------------------------------------------------------------------

static std::vector<bench::BenchmarkResult> benchMemoryFootprint(int steps = 20) {
    std::cout << "  Running memory benchmark (" << steps << " steps)...\n";

    bench::MockLlmServer server;
    BenchAgent agent(makeConfig(server.baseUrl()));
    agent.benchInit();
    agent.setDefaultPolicy(gaia::ToolPolicy::ALLOW);

    // Force system prompt computation before measuring baseline
    (void)agent.systemPrompt();

    long baselineKb = bench::MemoryTracker::getCurrentRssKb();
    std::cout << "  Baseline RSS: " << baselineKb << " KB\n";

    long peakKb = baselineKb;
    for (int i = 0; i < steps; ++i) {
        // Each call returns an answer directly (no tool calls) so history grows
        // by one user message + one assistant message per step.
        server.pushResponse(bench::kDefaultAnswer);
        agent.processQuery("benchmark step " + std::to_string(i));

        long rss = bench::MemoryTracker::getCurrentRssKb();
        if (rss > peakKb) peakKb = rss;
    }

    long finalKb = bench::MemoryTracker::getCurrentRssKb();
    double perStepGrowth = (steps > 0) ? static_cast<double>(finalKb - baselineKb) / steps : 0.0;

    std::cout << "  Peak RSS:         " << peakKb << " KB\n";
    std::cout << "  Per-step growth:  " << std::fixed << std::setprecision(1) << perStepGrowth
              << " KB\n";

    return {
        {"memory_baseline_kb", static_cast<double>(baselineKb), "KB"},
        {"memory_peak_kb", static_cast<double>(peakKb), "KB"},
        {"memory_per_step_growth_kb", perStepGrowth, "KB"},
    };
}

// ---------------------------------------------------------------------------
// CLI parsing helpers
// ---------------------------------------------------------------------------

static std::string getArg(const std::vector<std::string>& args, const std::string& flag,
                           const std::string& defaultVal = "") {
    for (size_t i = 0; i + 1 < args.size(); ++i) {
        if (args[i] == flag) return args[i + 1];
    }
    return defaultVal;
}

static bool hasFlag(const std::vector<std::string>& args, const std::string& flag) {
    for (const auto& a : args) {
        if (a == flag) return true;
    }
    return false;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main(int argc, char* argv[]) {
    // Unset GAIA_CPP_BASE_URL so it does not interfere with benchmarks
    // that explicitly set their own baseUrl via AgentConfig.
#if defined(_WIN32)
    _putenv_s("GAIA_CPP_BASE_URL", "");
#else
    unsetenv("GAIA_CPP_BASE_URL");
#endif

    std::vector<std::string> args(argv + 1, argv + argc);

    // ---- Compare mode ----
    if (hasFlag(args, "--compare")) {
        std::string baseline = getArg(args, "--baseline");
        std::string current = getArg(args, "--current");
        if (baseline.empty() || current.empty()) {
            std::cerr << "Usage: gaia_benchmarks --compare --baseline <file> --current <file>\n";
            return 1;
        }
        try {
            return bench::compareAndReport(baseline, current);
        } catch (const std::exception& e) {
            std::cerr << "Comparison failed: " << e.what() << "\n";
            return 1;
        }
    }

    // ---- Benchmark mode ----
    std::string outputPath = getArg(args, "--output", "benchmark-results.json");

    long staticLibBytes = 0;
    long sharedLibBytes = 0;
    long exeBytes = 0;
    try {
        staticLibBytes = std::stol(getArg(args, "--static-lib-size", "0"));
        sharedLibBytes = std::stol(getArg(args, "--shared-lib-size", "0"));
        exeBytes = std::stol(getArg(args, "--exe-size", "0"));
    } catch (const std::exception& e) {
        std::cerr << "Error: invalid size argument: " << e.what() << "\n";
        std::cerr << "Usage: gaia_benchmarks --output <file>"
                     " --static-lib-size <bytes> --shared-lib-size <bytes> --exe-size <bytes>\n";
        return 1;
    }

    std::cout << "=== GAIA C++ Performance Benchmarks ===\n\n";

    std::vector<bench::BenchmarkResult> results;

    // Benchmark 1: Binary sizes (from CLI args)
    std::cout << "Benchmark 1: Binary Sizes\n";
    results.push_back(benchStaticLibSize(staticLibBytes));
    results.push_back(benchSharedLibSize(sharedLibBytes));
    results.push_back(benchExeSize(exeBytes));
    std::cout << "  Static lib:  " << staticLibBytes << " bytes\n";
    std::cout << "  Shared lib:  " << sharedLibBytes << " bytes\n";
    std::cout << "  Example exe: " << exeBytes << " bytes\n\n";

    // Benchmark 2: Startup time
    std::cout << "Benchmark 2: Startup Time\n";
    try {
        results.push_back(benchStartupTime(100));
    } catch (const std::exception& e) {
        std::cerr << "  WARNING: Startup benchmark failed: " << e.what() << "\n";
    }
    std::cout << "\n";

    // Benchmark 3: Loop latency
    std::cout << "Benchmark 3: Loop Latency\n";
    try {
        results.push_back(benchLoopLatency(50));
    } catch (const std::exception& e) {
        std::cerr << "  WARNING: Loop latency benchmark failed: " << e.what() << "\n";
    }
    std::cout << "\n";

    // Benchmark 4: Memory footprint
    std::cout << "Benchmark 4: Memory Footprint\n";
    try {
        auto memResults = benchMemoryFootprint(20);
        results.insert(results.end(), memResults.begin(), memResults.end());
    } catch (const std::exception& e) {
        std::cerr << "  WARNING: Memory benchmark failed: " << e.what() << "\n";
    }
    std::cout << "\n";

    // Write results
    try {
        bench::writeBenchmarkResults(outputPath, results);
        std::cout << "Results written to: " << outputPath << "\n";
    } catch (const std::exception& e) {
        std::cerr << "Failed to write results: " << e.what() << "\n";
        return 1;
    }

    return 0;
}
