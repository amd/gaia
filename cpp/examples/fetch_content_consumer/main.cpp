// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Minimal third-party consumer example.
// Subclasses gaia::Agent with a single get_current_time tool.
//
// Prerequisites:
//   - LLM server running at http://localhost:8000/api/v1 (e.g. lemonade-server start)
//
// Build (FetchContent — no install needed):
//   cmake -B build -S .
//   cmake --build build --config Release
//
// Run:
//   ./build/my_agent           (Linux)
//   build\Release\my_agent.exe (Windows)

#include <gaia/agent.h>

#include <chrono>
#include <ctime>
#include <iostream>
#include <string>

/// Minimal agent that exposes one tool: get_current_time.
class TimeAgent : public gaia::Agent {
public:
    TimeAgent() : Agent(makeConfig()) {
        init(); // must be called after the derived constructor sets up state
    }

protected:
    std::string getSystemPrompt() const override {
        return "You are a helpful assistant. Use tools to answer questions accurately.";
    }

    void registerTools() override {
        toolRegistry().registerTool(
            "get_current_time",
            "Return the current local date and time as an ISO-8601 string.",
            [](const gaia::json&) -> gaia::json {
                auto now = std::chrono::system_clock::now();
                auto t   = std::chrono::system_clock::to_time_t(now);
                char buf[32];
                std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", std::localtime(&t));
                return gaia::json{{"time", std::string(buf)}};
            },
            {} // no parameters
        );
    }

private:
    static gaia::AgentConfig makeConfig() {
        gaia::AgentConfig cfg;
        cfg.baseUrl  = "http://localhost:8000/api/v1";
        cfg.modelId  = "Qwen3-4B-GGUF";
        cfg.maxSteps = 10;
        return cfg;
    }
};

int main() {
    try {
        TimeAgent agent;

        // processQuery() returns {"result": "...", "steps_taken": N, "steps_limit": M}
        auto result = agent.processQuery("What is the current date and time?");
        std::cout << result["result"].get<std::string>() << "\n";

    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << "\n";
        return 1;
    }
}
