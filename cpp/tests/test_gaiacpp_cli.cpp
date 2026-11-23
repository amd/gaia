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
                            const std::string& extraArgs = "",
                            const std::map<std::string, std::string>& extraEnv = {}) {
        std::string bin = GAIACPP_BIN;
        if (bin.find(' ') != std::string::npos) bin = "\"" + bin + "\"";
        const std::string cmd = bin + " -p hi -m mock-model --base-url " + mock.baseUrl() +
                                " --native-tools always --output-format json"
                                " --no-memory --no-skills --no-web" + extraArgs;
        // A config path that does not exist keeps the user's own settings out.
        std::map<std::string, std::string> env = {
            {"GAIA_TURN_LOG", turnLog},
            {"GAIACPP_CONFIG", (dir_ / "no-config.json").string()}};
        env.insert(extraEnv.begin(), extraEnv.end());
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

TEST_F(GaiacppCli, StepLogHasALinePerCallEvenWhenTheTurnFails) {
    bench::MockLlmServer mock;
    mock.pushResponse(withUsage(toolCall("file_read", R"({"path":"note.txt"})"), 1000, 0, 40));
    mock.pushResponse(R"({"error":{"message":"bad request"}})", 400);
    const fs::path steps = dir_ / "steps.jsonl";

    const gaia::ProcessResult r = run(mock, "", "", {{"GAIA_STEP_LOG", steps.string()}});
    EXPECT_EQ(r.exitCode, 1) << r.stderr_output;

    std::ifstream in(steps);
    std::vector<std::string> kinds;
    for (std::string line; std::getline(in, line);) {
        const json step = json::parse(line);
        EXPECT_EQ(step["schema"], "gaiacpp.step/1");
        kinds.push_back(step.value("kind", ""));
    }
    EXPECT_EQ(kinds, (std::vector<std::string>{"llm", "tool", "llm_error"}));
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

// `steps` reads of distinct files, then an answer (also what a closing call gets).
void pushReads(bench::MockLlmServer& mock, const fs::path& work, int steps) {
    for (int i = 0; i < steps; ++i) {
        const std::string name = "f" + std::to_string(i) + ".txt";
        std::ofstream(work / name) << i;
        mock.pushResponse(withUsage(toolCall("file_read", json{{"path", name}}.dump()), 100, 0, 5));
    }
    mock.pushResponse(withUsage(finalAnswer("all read"), 100, 0, 5));
}

TEST_F(GaiacppCli, TheDefaultStepLimitIsABackstopNotABudget) {
    bench::MockLlmServer mock;
    pushReads(mock, dir_ / "work", 60);
    const gaia::ProcessResult r = run(mock, "");
    ASSERT_EQ(r.exitCode, 0) << r.stderr_output;
    const json out = json::parse(r.stdout_output);
    EXPECT_EQ(out["status"], "success") << "stopped short of the work: " << out.dump();
    EXPECT_EQ(out["num_steps"], 61);
    for (const auto& body : mock.receivedBodies()) {
        EXPECT_EQ(body.find("Step budget:"), std::string::npos) << "61 of 1000 steps is nowhere near the limit";
    }
}

TEST_F(GaiacppCli, NoWebRefusesAShellFetchAndTellsTheModelTheRule) {
    bench::MockLlmServer mock;
    // A reserved name: a regression shows as a failed lookup, not a page fetched.
    mock.pushResponse(
        withUsage(toolCall("bash_execute", R"({"command":"curl -s -L https://example.invalid/page"})"), 100, 0, 5));
    mock.pushResponse(withUsage(finalAnswer("Web access is off, so I could not read the page."), 120, 0, 9));
    const gaia::ProcessResult r = run(mock, "", " --dangerously-skip-permissions");
    ASSERT_EQ(r.exitCode, 0) << r.stderr_output;

    const auto bodies = mock.receivedBodies();
    ASSERT_EQ(bodies.size(), 2u);
    EXPECT_NE(bodies[0].find("Web access is off for this run. Do not search the web or fetch pages"),
              std::string::npos)
        << "the prompt states the rule";
    EXPECT_NE(bodies[1].find("Web access is off for this run, so GAIA didn't run"), std::string::npos)
        << "the command is refused, not run: " << bodies[1];
    EXPECT_EQ(bodies[1].find("Could not resolve host"), std::string::npos) << "curl ran";
}

TEST_F(GaiacppCli, ATurnThatRanOutOfStepsWithAnAnswerIsIncompleteAndExitsOne) {
    bench::MockLlmServer mock;
    pushReads(mock, dir_ / "work", 3);
    const gaia::ProcessResult r = run(mock, "", " --max-steps 3");
    EXPECT_EQ(r.exitCode, 1) << r.stderr_output;
    const json out = json::parse(r.stdout_output);
    EXPECT_EQ(out["status"], "incomplete") << out.dump();
    EXPECT_EQ(out["is_error"], true);
    EXPECT_EQ(out["result"], "all read");
}

TEST_F(GaiacppCli, ATestsPassClaimNoTestRunBacksIsCaveatedAndTheRunIsIncomplete) {
    bench::MockLlmServer mock;
    // The command runs, but it is no test run: what Gemma-4-E4B did with `python test_stats.py`.
    mock.pushResponse(withUsage(toolCall("bash_execute", R"({"command":"echo checked"})"), 100, 0, 5));
    mock.pushResponse(withUsage(finalAnswer("Fixed it. All 4 tests pass."), 120, 0, 9));
    mock.pushResponse(withUsage(finalAnswer("Fixed it. All 4 tests pass."), 140, 0, 9));
    const gaia::ProcessResult r = run(mock, "", " --dangerously-skip-permissions");
    EXPECT_EQ(r.exitCode, 1) << r.stderr_output;
    const json out = json::parse(r.stdout_output);
    EXPECT_EQ(out["status"], "incomplete") << out.dump();
    EXPECT_EQ(out["result"], "Fixed it. All 4 tests pass.\n\nI couldn't confirm all of this:\n\n- No tests were run this turn.");
    ASSERT_EQ(mock.requestCount(), 3) << "sent back once, then caveated";
}

TEST_F(GaiacppCli, ALoopTheModelThenAnswersFromIsASuccessAndExitsZero) {
    bench::MockLlmServer mock;
    for (int i = 0; i < 8; ++i) {
        mock.pushResponse(withUsage(toolCall("file_read", R"({"path":"note.txt"})"), 100, 0, 5));
    }
    mock.pushResponse(withUsage(finalAnswer("The note says hello."), 100, 0, 5));
    const gaia::ProcessResult r = run(mock, "");
    EXPECT_EQ(r.exitCode, 0) << r.stderr_output;
    const json out = json::parse(r.stdout_output);
    EXPECT_EQ(out["status"], "success") << out.dump();
    EXPECT_EQ(out["is_error"], false);
    EXPECT_EQ(out["result"], "The note says hello.");
    EXPECT_EQ(out["num_llm_calls"], 9);
}

TEST_F(GaiacppCli, EitherStepLimitVariableIsHonouredAndTheFlagWins) {
    // The model wants one more read than the limit allows, so only the limit ends the turn there.
    const auto stopsAt = [this](int limit, const std::map<std::string, std::string>& env, const std::string& args) {
        bench::MockLlmServer mock;
        pushReads(mock, dir_ / "work", limit);
        mock.pushResponse(withUsage(finalAnswer("never asked for"), 100, 0, 5));
        const gaia::ProcessResult r = run(mock, "", args, env);
        const json out = json::parse(r.stdout_output);
        return out["num_steps"] == limit && out["status"] == "incomplete" && out["result"] == "all read";
    };
    EXPECT_TRUE(stopsAt(3, {{"GAIA_AGENT_MAX_STEPS", "3"}}, ""));
    EXPECT_TRUE(stopsAt(4, {{"GAIA_MAX_STEPS", "4"}}, ""));
    EXPECT_TRUE(stopsAt(4, {{"GAIA_AGENT_MAX_STEPS", "3"}, {"GAIA_MAX_STEPS", "4"}}, ""))
        << "GAIA_MAX_STEPS wins when both are set";
    EXPECT_TRUE(stopsAt(5, {{"GAIA_AGENT_MAX_STEPS", "3"}, {"GAIA_MAX_STEPS", "4"}}, " --max-steps 5"));
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

TEST_F(GaiacppCli, ARunThatThrowsStillPrintsOneJsonRecord) {
    bench::MockLlmServer mock;
    const fs::path bad = dir_ / "missing-folder" / "turns.jsonl";
    const gaia::ProcessResult r = run(mock, bad.string());
    EXPECT_EQ(r.exitCode, 1) << r.stderr_output;
    const json out = json::parse(r.stdout_output, nullptr, false);
    ASSERT_TRUE(out.is_object()) << "stdout is not one JSON record: " << r.stdout_output;
    EXPECT_EQ(out["status"], "error");
    EXPECT_EQ(out["is_error"], true);
    EXPECT_NE(out.value("result", "").find("GAIA_TURN_LOG"), std::string::npos) << out.dump();
    EXPECT_NE(r.stderr_output.find("GAIA_TURN_LOG"), std::string::npos) << r.stderr_output;
}

TEST_F(GaiacppCli, BadNumericFlagsAndVariablesAreUsageErrors) {
    bench::MockLlmServer mock;
    const auto refused = [&](const std::string& args, const std::map<std::string, std::string>& env,
                             const std::string& name, const std::string& value) {
        const gaia::ProcessResult r = run(mock, "", args, env);
        EXPECT_EQ(r.exitCode, 2) << name << " " << value << ": " << r.stderr_output;
        EXPECT_NE(r.stderr_output.find(name), std::string::npos) << r.stderr_output;
        EXPECT_NE(r.stderr_output.find("'" + value + "'"), std::string::npos) << r.stderr_output;
    };
    refused(" --ctx-size -1", {}, "--ctx-size", "-1");
    refused(" --max-tokens 0", {}, "--max-tokens", "0");
    refused(" --max-tokens 99999999999", {}, "--max-tokens", "99999999999");
    refused(" --max-steps -5", {}, "--max-steps", "-5");
    refused(" --max-steps 5x", {}, "--max-steps", "5x");
    refused(" --temperature nan", {}, "--temperature", "nan");
    refused(" --temperature 2.5", {}, "--temperature", "2.5");
    refused(" --temperature 0.5c", {}, "--temperature", "0.5c");
    refused("", {{"GAIA_MAX_STEPS", "0"}}, "GAIA_MAX_STEPS", "0");
    refused("", {{"GAIA_CONTEXT_SIZE", "-4"}}, "GAIA_CONTEXT_SIZE", "-4");
    refused(" --port 99999", {}, "--port", "99999");
    refused(" --port -1", {}, "--port", "-1");
    const gaia::ProcessResult twoModes = run(mock, "", " --mcp");
    EXPECT_EQ(twoModes.exitCode, 2) << twoModes.stderr_output;
    EXPECT_NE(twoModes.stderr_output.find("pick one"), std::string::npos) << twoModes.stderr_output;
    EXPECT_EQ(mock.requestCount(), 0);
}

TEST_F(GaiacppCli, AWronglyTypedSettingNamesTheFileAndTheKey) {
    bench::MockLlmServer mock;
    const auto refused = [&](const std::string& name, const json& settings, const std::string& key,
                             const std::string& expected) {
        const fs::path file = dir_ / name;
        std::ofstream(file) << settings.dump();
        const gaia::ProcessResult r = run(mock, "", " --config \"" + file.string() + "\"");
        EXPECT_EQ(r.exitCode, 1) << r.stderr_output;
        EXPECT_NE(r.stderr_output.find(name), std::string::npos) << r.stderr_output;
        EXPECT_NE(r.stderr_output.find("\"" + key + "\" must be " + expected), std::string::npos)
            << r.stderr_output;
        EXPECT_EQ(r.stderr_output.find("json.exception"), std::string::npos) << r.stderr_output;
    };
    refused("list-temperature.json", {{"temperature", json::array({1})}}, "temperature", "a number from 0 to 2");
    refused("hot-temperature.json", {{"temperature", 9}}, "temperature", "a number from 0 to 2");
    refused("number-model.json", {{"modelId", 5}}, "modelId", "a string");
    refused("zero-steps.json", {{"maxSteps", 0}}, "maxSteps", "a whole number above 0");
    refused("text-context.json", {{"contextSize", "big"}}, "contextSize", "a whole number above 0");
    refused("text-streaming.json", {{"streaming", "yes"}}, "streaming", "true or false");
    EXPECT_EQ(mock.requestCount(), 0);
}

TEST_F(GaiacppCli, EveryFailureAfterParsingEndsStdoutWithAnErrorRecord) {
    const auto expectRecord = [](const gaia::ProcessResult& r, const std::string& what) {
        EXPECT_EQ(r.exitCode, 1) << what << ": " << r.stderr_output;
        const json out = json::parse(r.stdout_output, nullptr, false);
        ASSERT_TRUE(out.is_object()) << what << ": stdout is not one JSON record: " << r.stdout_output;
        EXPECT_EQ(out.value("status", ""), "error") << what << ": " << out.dump();
        EXPECT_EQ(out.value("is_error", false), true) << what;
        EXPECT_FALSE(out.value("result", "").empty()) << what;
        EXPECT_FALSE(r.stderr_output.empty()) << what;
    };
    bench::MockLlmServer mock;

    const fs::path corrupt = dir_ / "corrupt.json";
    std::ofstream(corrupt) << "{ \"modelId\": ";
    expectRecord(run(mock, "", " --config \"" + corrupt.string() + "\""), "corrupt settings file");

    const fs::path typed = dir_ / "typed.json";
    std::ofstream(typed) << json{{"temperature", json::array({1})}}.dump();
    expectRecord(run(mock, "", " --config \"" + typed.string() + "\""), "wrongly typed settings file");

    const fs::path mcp = dir_ / "mcp.json";
    std::ofstream(mcp) << json{{"mcpServers", {{"down", {{"url", "http://127.0.0.1:9/mcp"}}}}}}.dump();
    expectRecord(run(mock, "", " --mcp-config \"" + mcp.string() + "\""), "MCP server that cannot start");

    std::string bin = GAIACPP_BIN;
    if (bin.find(' ') != std::string::npos) bin = "\"" + bin + "\"";
    const fs::path missing = dir_ / "no-such-prompt.txt";
    expectRecord(gaia::ProcessRunner::run(
                     bin + " --prompt-file \"" + missing.string() + "\" -m mock-model --base-url " +
                         mock.baseUrl() + " --output-format json --no-memory --no-skills --no-web",
                     60000, (dir_ / "work").string(),
                     {{"GAIACPP_CONFIG", (dir_ / "no-config.json").string()}}),
                 "unreadable prompt file");
    EXPECT_EQ(mock.requestCount(), 0);

    for (int i = 0; i < 12; ++i) mock.pushResponse(R"({"error":{"message":"model failed to load"}})", 500);
    expectRecord(run(mock, ""), "model failure");
}

TEST_F(GaiacppCli, ServeWithoutAKeyDoesNotStart) {
    bench::MockLlmServer mock;
    std::string bin = GAIACPP_BIN;
    if (bin.find(' ') != std::string::npos) bin = "\"" + bin + "\"";
    const gaia::ProcessResult r = gaia::ProcessRunner::run(
        bin + " serve --host 127.0.0.1 --port 0 -m mock-model --base-url " + mock.baseUrl() +
            " --no-memory --no-skills --no-web",
        30000, (dir_ / "work").string(), {{"GAIACPP_CONFIG", (dir_ / "no-config.json").string()}});
    EXPECT_EQ(r.exitCode, 2) << r.stderr_output;
    EXPECT_NE(r.stderr_output.find("--server-key-env"), std::string::npos) << r.stderr_output;
    EXPECT_EQ(r.stdout_output.find("\"ready\""), std::string::npos) << r.stdout_output;
}

TEST_F(GaiacppCli, ServeOnAPortInUseFailsWithoutWaiting) {
    bench::MockLlmServer mock;
    // A listener that shares its port with nobody, whatever options the other side sets.
    httplib::Server blocker;
    blocker.set_socket_options([](socket_t sock) {
#ifdef _WIN32
        int yes = 1;
        setsockopt(sock, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, reinterpret_cast<const char*>(&yes), sizeof(yes));
#else
        (void)sock;
#endif
    });
    const int port = blocker.bind_to_any_port("127.0.0.1");
    ASSERT_GT(port, 0);

    std::string bin = GAIACPP_BIN;
    if (bin.find(' ') != std::string::npos) bin = "\"" + bin + "\"";
    const std::string cmd = bin + " serve --host 127.0.0.1 --port " + std::to_string(port) +
                            " -m mock-model --base-url " + mock.baseUrl() +
                            " --no-memory --no-skills --no-web --server-key-env SERVE_KEY";
    const auto t0 = std::chrono::steady_clock::now();
    const gaia::ProcessResult r = gaia::ProcessRunner::run(
        cmd, 30000, (dir_ / "work").string(),
        {{"GAIACPP_CONFIG", (dir_ / "no-config.json").string()}, {"SERVE_KEY", "test-key"}});
    const auto elapsed = std::chrono::steady_clock::now() - t0;
    blocker.stop();

    EXPECT_NE(r.exitCode, 0);
    EXPECT_NE(r.stderr_output.find("failed to bind"), std::string::npos) << r.stderr_output;
    EXPECT_EQ(r.stdout_output.find("\"ready\""), std::string::npos) << r.stdout_output;
    // The wait this guards against is 10 s on top of a cold process start.
    EXPECT_LT(elapsed, std::chrono::seconds(9)) << "the bind error waited for the ready announcement";
}

TEST_F(GaiacppCli, AHelperRunShowsInTheJsonOutputAndTheStepLog) {
    bench::MockLlmServer mock;
    // One helper at a time, so the replies are taken in order: parent, helper, helper, parent.
    mock.pushResponse(withUsage(
        toolCall("delegate_task", R"({"tasks":[{"task":"what does note.txt say","paths":["note.txt"]}]})"), 1000, 0, 40));
    mock.pushResponse(withUsage(toolCall("file_read", R"({"path":"note.txt"})"), 300, 0, 10));
    mock.pushResponse(withUsage(finalAnswer("note.txt says hello"), 350, 200, 15));
    mock.pushResponse(withUsage(finalAnswer("It says hello."), 1200, 900, 20));
    const fs::path steps = dir_ / "steps.jsonl";

    const gaia::ProcessResult r = run(mock, "", " --max-helpers 1", {{"GAIA_STEP_LOG", steps.string()}});
    ASSERT_EQ(r.exitCode, 0) << r.stderr_output;

    const json out = json::parse(r.stdout_output);
    EXPECT_EQ(out["result"], "It says hello.");
    ASSERT_TRUE(out.contains("helpers")) << out.dump(2);
    EXPECT_EQ(out["helpers"]["runs"], 1);
    EXPECT_EQ(out["helpers"]["ok"], 1);
    EXPECT_EQ(out["helpers"]["failed"], 0);
    EXPECT_EQ(out["helpers"]["width"], 1);
    EXPECT_EQ(out["helpers"]["input_tokens"], 650);
    EXPECT_EQ(out["helpers"]["cached_tokens"], 200);
    EXPECT_EQ(out["helpers"]["output_tokens"], 25);
    EXPECT_EQ(out["input_tokens"], 2850) << "the run's total counts the helper's tokens";
    EXPECT_EQ(out["cached_tokens"], 1100);
    EXPECT_EQ(out["output_tokens"], 85);
    EXPECT_EQ(out["num_tool_calls"], 1) << "the parent made one call; the helper's are its own";

    std::ifstream in(steps);
    int helperLines = 0, helperCalls = 0;
    for (std::string line; std::getline(in, line);) {
        const json step = json::parse(line);
        if (step.value("kind", "") == "helper") {
            ++helperLines;
            EXPECT_EQ(step["status"], "ok");
        } else if (step.contains("helper")) {
            ++helperCalls;
        }
    }
    EXPECT_EQ(helperLines, 1);
    EXPECT_EQ(helperCalls, 3) << "two model calls and one tool call, each tagged with the helper";
}

TEST_F(GaiacppCli, ARunWithoutHelpersHasNoHelpersBlock) {
    bench::MockLlmServer mock;
    mock.pushResponse(withUsage(finalAnswer("ok"), 50, 0, 5));
    const gaia::ProcessResult r = run(mock, "");
    ASSERT_EQ(r.exitCode, 0) << r.stderr_output;
    EXPECT_FALSE(json::parse(r.stdout_output).contains("helpers"));
}

TEST_F(GaiacppCli, AHostedModelThroughLemonadeGetsTheProvidersWindowUnlessOneIsGiven) {
    std::ofstream(dir_ / "work" / "second.txt") << "another file, so the prompt states how much there is to read";
    auto windowIn = [](const bench::MockLlmServer& mock) {
        const json body = json::parse(mock.receivedBodies().back());
        const std::string system = body["messages"][0]["content"].get<std::string>();
        const std::string marker = "Your context window holds ";
        const size_t at = system.find(marker);
        return at == std::string::npos ? std::string("(not stated)") : system.substr(at + marker.size(), 7);
    };
    {
        bench::MockLlmServer mock;
        mock.pushResponse(withUsage(finalAnswer("ok"), 50, 0, 5));
        ASSERT_EQ(run(mock, "", " -m fireworks.some-model").exitCode, 0);
        EXPECT_EQ(windowIn(mock), "131072 ");
    }
    {
        bench::MockLlmServer mock;
        mock.pushResponse(withUsage(finalAnswer("ok"), 50, 0, 5));
        ASSERT_EQ(run(mock, "", " -m fireworks.some-model --ctx-size 20000").exitCode, 0);
        EXPECT_EQ(windowIn(mock), "20000 t") << "a window that was asked for is kept";
    }
    {
        bench::MockLlmServer mock;
        mock.pushResponse(withUsage(finalAnswer("ok"), 50, 0, 5));
        ASSERT_EQ(run(mock, "").exitCode, 0);
        EXPECT_EQ(windowIn(mock), "32768 t") << "a local model keeps the default";
    }
}

TEST_F(GaiacppCli, MaxHelpersMustBeAWholeNumber) {
    bench::MockLlmServer mock;
    const gaia::ProcessResult r = run(mock, "", " --max-helpers many");
    EXPECT_EQ(r.exitCode, 2);
    EXPECT_NE(r.stderr_output.find("--max-helpers"), std::string::npos) << r.stderr_output;
}

}  // namespace
