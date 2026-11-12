// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Runs the built gaiacpp program against a mock model server and checks what
// `-p --output-format json` prints and what GAIA_TURN_LOG records.

#include <gtest/gtest.h>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <map>
#include <sstream>
#include <string>

#include "gaia/process.h"
#include "gaia/types.h"
#include "support/mock_llm_server.h"

using gaia::json;

namespace {

namespace fs = std::filesystem;

std::string withUsage(const json& message, int prompt, int cached, int completion) {
    return json{{"choices", json::array({{{"message", message}}})},
                {"usage",
                 {{"prompt_tokens", prompt},
                  {"completion_tokens", completion},
                  {"total_tokens", prompt + completion},
                  {"prompt_tokens_details", {{"cached_tokens", cached}}}}}}
        .dump();
}

json toolCall(const std::string& name, const std::string& argsJson) {
    json args = argsJson;  // arguments travel as a JSON-encoded string
    return {{"role", "assistant"},
            {"content", nullptr},
            {"tool_calls",
             json::array({{{"id", "c1"},
                           {"type", "function"},
                           {"function", {{"name", name}, {"arguments", args}}}}})}};
}

json finalAnswer(const std::string& text) {
    return {{"role", "assistant"}, {"content", text}};
}

class GaiacppCli : public ::testing::Test {
protected:
    void SetUp() override {
        const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
        dir_ = fs::temp_directory_path() / ("gaiacpp-cli-" + std::to_string(stamp));
        fs::create_directories(dir_ / "work");
        std::ofstream(dir_ / "work" / "note.txt") << "hello";
    }
    void TearDown() override {
        std::error_code ec;
        fs::remove_all(dir_, ec);
    }

    gaia::ProcessResult run(const bench::MockLlmServer& mock, const std::string& turnLog,
                            const std::string& extraArgs = "") {
        std::string bin = GAIACPP_BIN;
        if (bin.find(' ') != std::string::npos) bin = "\"" + bin + "\"";
        const std::string cmd = bin + " -p hi -m mock-model --base-url " + mock.baseUrl() +
                                " --native-tools always --output-format json"
                                " --no-memory --no-skills --no-web" + extraArgs;
        // A config path that does not exist keeps the user's own settings out.
        std::map<std::string, std::string> env = {
            {"GAIA_TURN_LOG", turnLog},
            {"GAIACPP_CONFIG", (dir_ / "no-config.json").string()}};
        return gaia::ProcessRunner::run(cmd, 60000, (dir_ / "work").string(), env);
    }

    fs::path dir_;
};

TEST_F(GaiacppCli, JsonOutputAndTurnLogCarryTheSameCounters) {
    bench::MockLlmServer mock;
    mock.pushResponse(withUsage(toolCall("file_read", R"({"path":"note.txt"})"), 1000, 0, 40));
    mock.pushResponse(withUsage(finalAnswer("all done"), 1100, 900, 20));
    const fs::path log = dir_ / "turns.jsonl";

    const gaia::ProcessResult r = run(mock, log.string());
    ASSERT_EQ(r.exitCode, 0) << r.stderr_output;

    const json out = json::parse(r.stdout_output);
    EXPECT_EQ(out["result"], "all done");
    EXPECT_EQ(out["status"], "success");
    EXPECT_EQ(out["num_steps"], 2);
    EXPECT_EQ(out["num_llm_calls"], 2);
    EXPECT_EQ(out["num_tool_calls"], 1);
    EXPECT_EQ(out["error_count"], 0);
    EXPECT_EQ(out["input_tokens"], 2100);
    EXPECT_EQ(out["cached_tokens"], 900);
    EXPECT_EQ(out["output_tokens"], 60);
    EXPECT_EQ(out["peak_context_tokens"], 1120) << "largest single request, not the sum";
    EXPECT_EQ(out["usage"]["cached_tokens"], 900);

    std::ifstream in(log);
    std::string line, extra;
    ASSERT_TRUE(static_cast<bool>(std::getline(in, line))) << "no turn log line written";
    EXPECT_FALSE(static_cast<bool>(std::getline(in, extra))) << "one run writes one line";
    const json turn = json::parse(line);
    EXPECT_EQ(turn["schema"], "gaiacpp.turn/1");
    EXPECT_FALSE(turn.contains("result")) << "the log keeps counters, not the answer";
    for (const char* key : {"num_steps", "num_llm_calls", "num_tool_calls", "error_count",
                            "input_tokens", "cached_tokens", "output_tokens",
                            "peak_context_tokens"}) {
        EXPECT_EQ(turn[key], out[key]) << key;
    }
    ASSERT_EQ(turn["llm_calls"].size(), 2u);
    EXPECT_EQ(turn["llm_calls"][1]["step"], 2);
    EXPECT_EQ(turn["llm_calls"][1]["input_tokens"], 1100);
    EXPECT_EQ(turn["llm_calls"][1]["cached_tokens"], 900);
    ASSERT_EQ(turn["tool_calls"].size(), 1u);
    EXPECT_EQ(turn["tool_calls"][0]["name"], "file_read");
    EXPECT_EQ(turn["tool_calls"][0]["step"], 1);
}

TEST_F(GaiacppCli, TurnLogAppendsAcrossRuns) {
    const fs::path log = dir_ / "turns.jsonl";
    for (int i = 0; i < 2; ++i) {
        bench::MockLlmServer mock;
        mock.pushResponse(withUsage(finalAnswer("ok"), 50, 0, 5));
        ASSERT_EQ(run(mock, log.string()).exitCode, 0);
    }
    std::ifstream in(log);
    std::string line;
    int lines = 0;
    while (std::getline(in, line)) {
        if (!line.empty()) ++lines;
    }
    EXPECT_EQ(lines, 2);
}

TEST_F(GaiacppCli, CountersAreZeroWhenTheServerReportsNoUsage) {
    bench::MockLlmServer mock;
    mock.pushResponse(json{{"choices", json::array({{{"message", finalAnswer("ok")}}})}}.dump());
    const gaia::ProcessResult r = run(mock, "");
    ASSERT_EQ(r.exitCode, 0) << r.stderr_output;
    const json out = json::parse(r.stdout_output);
    EXPECT_EQ(out["num_llm_calls"], 1);
    EXPECT_EQ(out["input_tokens"], 0);
    EXPECT_EQ(out["cached_tokens"], 0);
    EXPECT_EQ(out["peak_context_tokens"], 0);
    EXPECT_FALSE(out.contains("usage"));
}

TEST_F(GaiacppCli, AnUnreachableMcpServerStopsTheRunUnlessMarkedOptional) {
    // Port 9 (discard) refuses connections, so the server is certainly unreachable.
    const auto config = [this](bool optional) {
        const fs::path file = dir_ / (optional ? "optional.json" : "required.json");
        json server = {{"url", "http://127.0.0.1:9/mcp"}};
        if (optional) server["optional"] = true;
        std::ofstream(file) << json{{"mcpServers", {{"tools-that-are-down", server}}}}.dump();
        return " --mcp-config \"" + file.string() + "\"";
    };

    bench::MockLlmServer strict;
    strict.pushResponse(json{{"choices", json::array({{{"message", finalAnswer("ok")}}})}}.dump());
    const gaia::ProcessResult refused = run(strict, "", config(false));
    EXPECT_NE(refused.exitCode, 0);
    EXPECT_NE(refused.stderr_output.find("tools-that-are-down"), std::string::npos) << refused.stderr_output;
    EXPECT_NE(refused.stderr_output.find("\"optional\": true"), std::string::npos) << refused.stderr_output;
    EXPECT_EQ(strict.requestCount(), 0);

    bench::MockLlmServer lenient;
    lenient.pushResponse(json{{"choices", json::array({{{"message", finalAnswer("ok")}}})}}.dump());
    const gaia::ProcessResult started = run(lenient, "", config(true));
    ASSERT_EQ(started.exitCode, 0) << started.stderr_output;
    EXPECT_EQ(json::parse(started.stdout_output)["result"], "ok");
    // Starting without it is said out loud, with the server named.
    EXPECT_NE(started.stderr_output.find("'tools-that-are-down'"), std::string::npos) << started.stderr_output;
    EXPECT_NE(started.stderr_output.find("marked optional"), std::string::npos) << started.stderr_output;
    EXPECT_EQ(lenient.requestCount(), 1);
}

TEST_F(GaiacppCli, UnwritableTurnLogStopsBeforeAnyModelRequest) {
    bench::MockLlmServer mock;
    mock.pushResponse(withUsage(finalAnswer("ok"), 50, 0, 5));
    const fs::path bad = dir_ / "missing-folder" / "turns.jsonl";
    const gaia::ProcessResult r = run(mock, bad.string());
    EXPECT_NE(r.exitCode, 0);
    EXPECT_NE(r.stderr_output.find("GAIA_TURN_LOG"), std::string::npos) << r.stderr_output;
    EXPECT_EQ(mock.requestCount(), 0);
}

}  // namespace
