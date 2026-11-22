// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// gaiacode the way GAIA's apps run it: the daemon's sidecar for the Agent UI
// (the built program, `--host 127.0.0.1 --port 0` with a sidecar token), and
// the terminal UI's stdio child (the shared StdioServer over a CodeAgent).

#include <gtest/gtest.h>

#include <httplib.h>

#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <random>
#include <regex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "../agents/gaiacode/code_agent.h"
#include "../agents/gaiacpp/bash_tools.h"
#include "../agents/gaiacpp/stdio_server.h"
#include "gaia/types.h"
#include "support/child_process.h"
#include "support/mock_llm_server.h"

using gaia::json;
namespace fs = std::filesystem;
using std::chrono::seconds;

namespace {

constexpr const char* kToken = "gaiacode-sidecar-token-21c9";

std::string reply(const std::string& content) {
    return json{{"choices", json::array({{{"message", {{"role", "assistant"}, {"content", content}}}}})},
                {"usage", {{"prompt_tokens", 120}, {"completion_tokens", 8}, {"total_tokens", 128}}}}
        .dump();
}
// Prompt-JSON replies: the sidecar runs mock-model without native tool calls.
std::string toolReply(const std::string& tool, const json& args) {
    return reply(json{{"tool", tool}, {"tool_args", args}}.dump());
}
std::string answerReply(const std::string& text) { return reply(json{{"thought", "done"}, {"answer", text}}.dump()); }

std::string nativeAnswer(const std::string& text) {
    return json{{"choices", json::array({{{"message", {{"role", "assistant"}, {"content", text}}}}})},
                {"usage", {{"prompt_tokens", 100}, {"completion_tokens", 7}, {"total_tokens", 107}}}}
        .dump();
}
std::string nativeToolCall(const std::string& name, const json& args) {
    return json{{"choices",
                 json::array({{{"message",
                                {{"role", "assistant"},
                                 {"content", nullptr},
                                 {"tool_calls", json::array({{{"id", "c1"},
                                                              {"type", "function"},
                                                              {"function", {{"name", name}, {"arguments", args.dump()}}}}})}}}}})},
                {"usage", {{"prompt_tokens", 100}, {"completion_tokens", 7}, {"total_tokens", 107}}}}
        .dump();
}

std::string readFile(const fs::path& p) {
    std::ifstream in(p, std::ios::binary);
    std::stringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

fs::path freshRoot(const std::string& tag) {
    std::mt19937_64 rng(std::random_device{}());
    const fs::path r = fs::weakly_canonical(fs::temp_directory_path()) / (tag + "-" + std::to_string(rng()));
    fs::create_directories(r);
    return fs::canonical(r);
}

std::vector<json> sseEvents(const std::string& body) {
    std::vector<json> out;
    std::istringstream in(body);
    for (std::string line; std::getline(in, line);) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.rfind("data: ", 0) == 0) out.push_back(json::parse(line.substr(6), nullptr, false));
    }
    return out;
}

int indexOf(const std::vector<json>& events, const std::string& type, int from = 0) {
    for (int i = from; i < static_cast<int>(events.size()); ++i) {
        if (events[i].is_object() && events[i].value("type", "") == type) return i;
    }
    return -1;
}

std::string dumpAll(const std::vector<json>& events) {
    std::string s;
    for (const auto& e : events) s += "\n  " + e.dump();
    return s;
}

// ---------------------------------------------------------------- the Agent UI's sidecar

class GaiacodeSidecar : public ::testing::Test {
protected:
    void SetUp() override {
        root_ = freshRoot("gaiacode-sidecar");
        fs::create_directories(root_ / "home");
        work_ = root_ / "work";
        fs::create_directories(work_);
        std::ofstream(work_ / "calc.py") << "def add(a, b):\n    return a - b\n";
    }
    void TearDown() override {
        if (HasFailure()) std::cerr << "---- gaiacode sidecar stderr ----\n" << readFile(root_ / "err.log") << "\n";
        child_.reset();
        std::error_code ec;
        fs::remove_all(root_, ec);
    }

    void start(bool withDefaultModel = true) {
        const std::string home = (root_ / "home").string();
        testsupport::EnvOverrides env = {{"GAIA_GAIA_SIDECAR_TOKEN", std::string(kToken)},
                                         {"GAIA_GAIA_SIDECAR_TOKEN_FILE", std::nullopt},
                                         {"GAIA_HOST_KEYCHAIN_SECRET_FILE", std::nullopt},
                                         {"LEMONADE_BASE_URL", mock_.baseUrl()},
                                         {"LEMONADE_API_KEY", std::nullopt},
                                         {"GAIA_BASE_URL", std::nullopt},
                                         {"GAIA_CPP_BASE_URL", std::nullopt},
                                         {"GAIA_API_KEY", std::nullopt},
                                         {"GAIA_MODEL_ID", withDefaultModel ? std::optional<std::string>("mock-model")
                                                                            : std::nullopt},
                                         {"GH_TOKEN", std::nullopt},
                                         {"GITHUB_TOKEN", std::nullopt},
                                         {"GAIA_HOME", home},
                                         {"HOME", home},
                                         {"USERPROFILE", home}};
        child_ = std::make_unique<testsupport::ChildProcess>(
            std::vector<std::string>{GAIACODE_BIN, "--host", "127.0.0.1", "--port", "0"}, env, root_.string(),
            (root_ / "out.log").string(), (root_ / "err.log").string());
        static const std::regex ready(R"(gaiacode \S+ sidecar \(api [0-9.]+\) on http://127\.0\.0\.1:([0-9]+)\r?\n)");
        std::smatch m;
        std::string log;
        const auto deadline = std::chrono::steady_clock::now() + seconds(30);
        while (std::chrono::steady_clock::now() < deadline) {
            log = readFile(root_ / "err.log");
            if (std::regex_search(log, m, ready) || !child_->running()) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
        ASSERT_FALSE(m.empty()) << "no ready line (running=" << child_->running() << "):\n" << log;
        port_ = std::stoi(m[1].str());
    }

    httplib::Client client() const {
        httplib::Client c("127.0.0.1", port_);
        c.set_connection_timeout(10);
        c.set_read_timeout(90);
        return c;
    }
    httplib::Headers bearer() const { return {{"Authorization", std::string("Bearer ") + kToken}}; }

    std::vector<json> turn(const json& body) {
        auto res = client().Post("/v1/gaia/query", bearer(), body.dump(), "application/json");
        if (!res) {
            ADD_FAILURE() << "no response: " << httplib::to_string(res.error());
            return {};
        }
        EXPECT_EQ(res->status, 200) << res->body;
        return sseEvents(res->body);
    }

    bench::MockLlmServer mock_;
    std::unique_ptr<testsupport::ChildProcess> child_;
    fs::path root_, work_;
    int port_ = 0;
};

TEST_F(GaiacodeSidecar, ReportsItselfAndRefusesWithoutTheToken) {
    ASSERT_NO_FATAL_FAILURE(start());
    auto v = client().Get("/v1/gaia/version", bearer());
    ASSERT_TRUE(v);
    ASSERT_EQ(v->status, 200) << v->body;
    const json j = json::parse(v->body);
    EXPECT_EQ(j["agent"], "gaia") << "the Agent UI talks to whichever harness as the gaia agent";
    EXPECT_FALSE(j.value("version", "").empty());
    const json body = {{"query", "hi"}, {"run_id", "run-denied"}, {"context", json::array()}};
    auto denied = client().Post("/v1/gaia/query", httplib::Headers{{"Authorization", "Bearer wrong"}}, body.dump(),
                                "application/json");
    ASSERT_TRUE(denied);
    EXPECT_EQ(denied->status, 401);
    EXPECT_EQ(mock_.requestCount(), 0) << "a chat without the token never reaches the model";
}

TEST_F(GaiacodeSidecar, AChatRunsGaiacodesToolsInItsFolder) {
    const fs::path file = work_ / "calc.py";
    mock_.pushResponse(toolReply("file_read", {{"path", file.string()}}));
    mock_.pushResponse(answerReply("add subtracts instead of adding."));
    ASSERT_NO_FATAL_FAILURE(start());

    const auto events = turn({{"query", "Why is add wrong?"},
                              {"run_id", "run-1"},
                              {"context", json::array()},
                              {"session_id", "chat-1"},
                              {"allowed_paths", json::array({work_.string()})}});
    const int call = indexOf(events, "tool_call");
    ASSERT_GE(call, 0) << dumpAll(events);
    EXPECT_EQ(events[call]["tool"], "file_read");
    const int result = indexOf(events, "tool_result", call);
    ASSERT_GT(result, call) << dumpAll(events);
    EXPECT_NE(events[result]["data"].dump().find("return a - b"), std::string::npos) << events[result].dump();
    ASSERT_FALSE(events.empty());
    EXPECT_EQ(events.back().value("type", ""), "final") << dumpAll(events);
    EXPECT_EQ(events.back().value("answer", ""), "add subtracts instead of adding.");

    // The model was offered gaiacode's coding tools, not gaiacpp's general ones.
    const std::string offered = mock_.receivedBodies().front();
    for (const char* tool : {"run_checks", "git_status", "gh_pr_checks"}) {
        EXPECT_NE(offered.find(tool), std::string::npos) << tool;
    }
    for (const char* tool : {"query_documents", "web_search", "delegate_task"}) {
        EXPECT_EQ(offered.find(std::string("\"") + tool + "\""), std::string::npos) << tool;
    }
}

TEST_F(GaiacodeSidecar, InternetOffKeepsGitHubAway) {
    mock_.pushResponse(toolReply("gh_issue_view", {{"number", 7}}));
    mock_.pushResponse(answerReply("GitHub is off."));
    ASSERT_NO_FATAL_FAILURE(start());
    const auto events = turn({{"query", "Read issue 7"},
                              {"run_id", "run-off"},
                              {"context", json::array()},
                              {"session_id", "chat-off"},
                              {"internet_off", true},
                              {"allowed_paths", json::array({work_.string()})}});
    const int result = indexOf(events, "tool_result");
    ASSERT_GE(result, 0) << dumpAll(events);
    EXPECT_NE(events[result]["data"].dump().find("Internet access is off"), std::string::npos)
        << events[result].dump();
}

TEST_F(GaiacodeSidecar, AChatWithNoModelIsRefusedNotRunOnAGuess) {
    ASSERT_NO_FATAL_FAILURE(start(/*withDefaultModel=*/false));
    const json body = {{"query", "hi"}, {"run_id", "run-nomodel"}, {"context", json::array()}};
    auto res = client().Post("/v1/gaia/query", bearer(), body.dump(), "application/json");
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 400) << res->body;
    EXPECT_NE(res->body.find("no default model"), std::string::npos) << res->body;
    EXPECT_EQ(mock_.requestCount(), 0);
}

TEST_F(GaiacodeSidecar, AttachedFilesAreNamedToTheModel) {
    mock_.pushResponse(answerReply("Read it."));
    ASSERT_NO_FATAL_FAILURE(start());
    const fs::path file = work_ / "calc.py";
    const auto events = turn({{"query", "Look at the attached file"},
                              {"run_id", "run-docs"},
                              {"context", json::array()},
                              {"session_id", "chat-docs"},
                              {"documents", json::array({file.string()})}});
    ASSERT_FALSE(events.empty());
    EXPECT_EQ(events.back().value("type", ""), "final") << dumpAll(events);
    EXPECT_EQ(indexOf(events, "tool_call"), -1) << "nothing is indexed: gaiacode has no document index";
    const std::string sent = mock_.receivedBodies().front();
    EXPECT_NE(sent.find("Attached files"), std::string::npos) << sent.substr(0, 4000);
    EXPECT_NE(sent.find("calc.py"), std::string::npos) << "the file is named by its path";
}

// A GitHub write waits for the user's answer, and a denial means nothing is posted.
TEST_F(GaiacodeSidecar, AGitHubWriteAsksAndADenialPostsNothing) {
    mock_.pushResponse(toolReply("gh_issue_comment", {{"number", 3}, {"body", "Fixed in main."}}));
    mock_.pushResponse(answerReply("Not posted."));
    ASSERT_NO_FATAL_FAILURE(start());

    std::mutex mu;
    std::string streamed;
    bool done = false;
    std::thread reader([&] {
        httplib::Client c("127.0.0.1", port_);
        c.set_read_timeout(90);
        httplib::Request req;
        req.method = "POST";
        req.path = "/v1/gaia/query";
        req.headers = bearer();
        req.set_header("Content-Type", "application/json");
        req.body = json{{"query", "Comment on issue 3"},
                        {"run_id", "run-write"},
                        {"context", json::array()},
                        {"session_id", "chat-write"},
                        {"allowed_paths", json::array({work_.string()})}}
                       .dump();
        req.content_receiver = [&](const char* data, size_t n, uint64_t, uint64_t) {
            std::lock_guard<std::mutex> lock(mu);
            streamed.append(data, n);
            return true;
        };
        c.send(req);
        std::lock_guard<std::mutex> lock(mu);
        done = true;
    });
    std::string confirmId;
    const auto deadline = std::chrono::steady_clock::now() + seconds(60);
    while (confirmId.empty() && std::chrono::steady_clock::now() < deadline) {
        {
            std::lock_guard<std::mutex> lock(mu);
            for (const auto& e : sseEvents(streamed)) {
                if (e.is_object() && e.value("type", "") == "needs_confirmation") {
                    EXPECT_EQ(e.value("action", ""), "gh_issue_comment") << e.dump();
                    confirmId = e.value("confirm_id", "");
                }
            }
            if (done) break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    ASSERT_FALSE(confirmId.empty()) << "the write never asked:\n" << streamed;
    auto d = client().Post("/v1/gaia/query/run-write/tool_decision", bearer(),
                           json{{"decision", "deny"}, {"confirm_id", confirmId}}.dump(), "application/json");
    ASSERT_TRUE(d);
    EXPECT_EQ(d->status, 200) << d->body;
    reader.join();
    const auto events = sseEvents(streamed);
    ASSERT_FALSE(events.empty());
    EXPECT_EQ(events.back().value("type", ""), "final") << dumpAll(events);
    const int result = indexOf(events, "tool_result");
    ASSERT_GE(result, 0) << dumpAll(events);
    EXPECT_EQ(events[result]["data"].dump().find("\"html_url\""), std::string::npos) << events[result].dump();
}

TEST_F(GaiacodeSidecar, AttachedFilesAreNamedUnlessTheConversationAlreadyHasThem) {
    mock_.pushResponse(answerReply("Read it."));
    mock_.pushResponse(answerReply("Still read."));
    mock_.pushResponse(answerReply("Again."));
    ASSERT_NO_FATAL_FAILURE(start());
    const fs::path file = work_ / "calc.py";
    const auto ask = [&](const char* run, const json& context) {
        turn({{"query", "Look at the attached file"},
              {"run_id", run},
              {"context", context},
              {"session_id", "chat-names"},
              {"documents", json::array({file.string()})}});
    };
    ask("run-n1", json::array());
    // A retry, a trimmed history, or the UI's own copy of turn 1: the path isn't in it.
    ask("run-n2", json::array({{{"role", "user"}, {"content", "Look at the attached file"}},
                               {{"role", "assistant"}, {"content", "Read it."}}}));
    // Turn 1 as the model saw it: the path is there already.
    ask("run-n3", json::array({{{"role", "user"}, {"content", "Look at the attached file\n\nAttached files: "
                                                              "\n- " + file.string()}},
                               {{"role", "assistant"}, {"content", "Read it."}}}));
    const auto sent = mock_.receivedBodies();
    ASSERT_EQ(sent.size(), 3u);
    const auto lastUser = [](const std::string& body) {
        std::string text;
        const json parsed = json::parse(body);
        for (const auto& m : parsed["messages"]) {
            if (m.value("role", "") == "user" && m["content"].is_string()) text = m["content"].get<std::string>();
        }
        return text;
    };
    EXPECT_NE(lastUser(sent[0]).find("Attached files"), std::string::npos) << lastUser(sent[0]);
    EXPECT_NE(lastUser(sent[1]).find("Attached files"), std::string::npos) << "lost: " << lastUser(sent[1]);
    EXPECT_EQ(lastUser(sent[2]).find("Attached files ("), std::string::npos) << "repeated: " << lastUser(sent[2]);
}

TEST_F(GaiacodeSidecar, InternetOffNeverAsksAboutAGitHubWrite) {
    mock_.pushResponse(toolReply("gh_issue_comment", {{"number", 3}, {"body", "Fixed."}}));
    mock_.pushResponse(answerReply("GitHub is off."));
    ASSERT_NO_FATAL_FAILURE(start());
    const auto events = turn({{"query", "Comment on issue 3"},
                              {"run_id", "run-offwrite"},
                              {"context", json::array()},
                              {"session_id", "chat-offwrite"},
                              {"internet_off", true},
                              {"allowed_paths", json::array({work_.string()})}});
    EXPECT_EQ(indexOf(events, "needs_confirmation"), -1) << dumpAll(events);
    const int result = indexOf(events, "tool_result");
    ASSERT_GE(result, 0) << dumpAll(events);
    EXPECT_NE(events[result]["data"].dump().find("Internet access is off"), std::string::npos)
        << events[result].dump();
}

// ---------------------------------------------------------------- the terminal UI's stdio child

// Lines out, collected; a test waits for the turn's terminal event.
class Sink : public std::streambuf {
public:
    std::vector<std::string> lines() {
        std::lock_guard<std::mutex> lock(m_);
        return lines_;
    }

protected:
    int_type overflow(int_type c) override {
        if (c == traits_type::eof()) return c;
        std::lock_guard<std::mutex> lock(m_);
        if (c == '\n') {
            lines_.push_back(line_);
            line_.clear();
        } else {
            line_.push_back(static_cast<char>(c));
        }
        return c;
    }

private:
    std::mutex m_;
    std::string line_;
    std::vector<std::string> lines_;
};

TEST(GaiacodeStdio, ATurnRunsCodingToolsAndEndsOnce) {
    const fs::path work = freshRoot("gaiacode-stdio");
    std::ofstream(work / "calc.py") << "def add(a, b):\n    return a - b\n";
    bench::MockLlmServer mock;
    mock.pushResponse(nativeToolCall("file_read", {{"path", (work / "calc.py").string()}}));
    mock.pushResponse(nativeAnswer("It subtracts."));

    gaia::AgentConfig cfg;
    cfg.baseUrl = mock.baseUrl();
    cfg.modelId = "mock-model";
    cfg.silentMode = true;
    cfg.maxSteps = 6;
    cfg.llmRetries = 0;
    cfg.nativeToolCalls = gaia::NativeToolCalls::Always;
    gaia::code::CodeAgent::Options o;
    o.workspaceRoots = {work.u8string()};
    o.shell = gaia::BashTools::newSession(work.u8string());
    o.github = false;
    o.skills = false;
    gaia::code::CodeAgent agent(cfg, o);
    agent.setConfig(cfg);

    std::istringstream in(json{{"gaia_query", "What does add do?"}}.dump() + "\n");
    Sink sink;
    std::ostream out(&sink);
    std::ostringstream log;
    gaia::StdioOptions so;
    so.program = "gaiacode";
    so.agentVersion = "9.9.9";
    so.editRoots = {work.u8string()};
    so.memory = false;
    so.nativeTools = gaia::NativeToolCalls::Always;
    so.log = &log;
    int code = -1;
    {
        gaia::StdioServer server(agent, in, out, so);
        code = server.run();
    }
    EXPECT_EQ(code, 0) << log.str();

    std::vector<json> events;
    for (const auto& l : sink.lines()) events.push_back(json::parse(l, nullptr, false));
    const int status = indexOf(events, "status");
    ASSERT_GE(status, 0) << dumpAll(events);
    EXPECT_EQ(events[status]["agent_version"], "9.9.9");
    int terminals = 0;
    for (const auto& e : events) terminals += e.value("type", "") == "final" || e.value("type", "") == "error";
    EXPECT_EQ(terminals, 1) << dumpAll(events);
    const int fin = indexOf(events, "final");
    ASSERT_GE(fin, 0) << dumpAll(events) << "\n" << log.str();
    EXPECT_NE(events[fin].dump().find("It subtracts."), std::string::npos) << events[fin].dump();
    EXPECT_NE(dumpAll(events).find("file_read"), std::string::npos) << "the tool call is shown";
    std::error_code ec;
    fs::remove_all(work, ec);
}

// ---------------------------------------------------------------- launch errors

// Exit code and stderr of gaiacode run with `args`, outside the daemon and with no model named.
std::pair<int, std::string> launch(const std::vector<std::string>& args) {
    const fs::path dir = freshRoot("gaiacode-launch");
    std::vector<std::string> argv = {GAIACODE_BIN};
    argv.insert(argv.end(), args.begin(), args.end());
    int code = -1;
    {
        testsupport::ChildProcess child(argv,
                                        {{"GAIA_GAIA_SIDECAR_TOKEN", std::nullopt},
                                         {"GAIA_GAIA_SIDECAR_TOKEN_FILE", std::nullopt},
                                         {"GAIA_MODEL_ID", std::nullopt}},
                                        dir.string(), (dir / "out.log").string(), (dir / "err.log").string());
        EXPECT_TRUE(child.waitExit(30000));
        code = child.exitCode();
    }
    const std::string err = readFile(dir / "err.log");
    std::error_code ec;
    fs::remove_all(dir, ec);
    return {code, err};
}

TEST(GaiacodeLaunch, NoModeOutsideTheDaemonSaysWhereToUseIt) {
    const auto [code, err] = launch({});
    EXPECT_EQ(code, 2) << err;
    EXPECT_NE(err.find("terminal UI or Agent UI"), std::string::npos) << err;
}

TEST(GaiacodeLaunch, StdioWithoutAModelIsRefused) {
    const auto [code, err] = launch({"--stdio"});
    EXPECT_EQ(code, 2) << err;
    EXPECT_NE(err.find("--model"), std::string::npos) << err;
}

TEST(GaiacodeLaunch, AppFlagsNeedStdio) {
    const auto [code, err] = launch({"-p", "x", "--model", "m", "--accept-edits"});
    EXPECT_EQ(code, 2) << err;
    EXPECT_NE(err.find("go with --stdio"), std::string::npos) << err;
}

}  // namespace
