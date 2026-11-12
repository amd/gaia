// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// `gaiacpp --stdio`, driven the way the GAIA terminal UI drives it: lines in on
// a pipe that stays open, canonical events out, one terminal event per query.
// Event and control names are checked against tests/fixtures/stdio/gaia_stdio_wire.json.

#include <gtest/gtest.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
#include <future>
#include <istream>
#include <map>
#include <mutex>
#include <ostream>
#include <random>
#include <set>
#include <streambuf>
#include <string>
#include <thread>
#include <vector>

#include "../agents/bash/bash_tools.h"
#include "../agents/gaia/gaia_agent.h"
#include "../agents/gaia/stdio_server.h"
#include "gaia/memory_store.h"
#include "gaia/process.h"
#include "support/mock_llm_server.h"

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <csignal>
#include <fcntl.h>
#include <poll.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

using namespace gaia;
namespace fs = std::filesystem;

namespace {

constexpr auto kWait = std::chrono::seconds(30);

// An input stream that blocks for more data until close(), like a pipe.
class PipeBuf : public std::streambuf {
public:
    void push(const std::string& line) {
        std::lock_guard<std::mutex> lock(m_);
        data_ += line + "\n";
        cv_.notify_all();
    }
    void close() {
        std::lock_guard<std::mutex> lock(m_);
        closed_ = true;
        cv_.notify_all();
    }

protected:
    int_type underflow() override {
        std::unique_lock<std::mutex> lock(m_);
        cv_.wait(lock, [this] { return pos_ < data_.size() || closed_; });
        if (pos_ >= data_.size()) return traits_type::eof();
        cur_ = data_[pos_++];
        setg(&cur_, &cur_, &cur_ + 1);
        return traits_type::to_int_type(cur_);
    }

private:
    std::mutex m_;
    std::condition_variable cv_;
    std::string data_;
    size_t pos_ = 0;
    bool closed_ = false;
    char cur_ = 0;
};

// Collects written lines and lets a test wait, bounded, for them.
class LineSink : public std::streambuf {
public:
    std::vector<std::string> lines() {
        std::lock_guard<std::mutex> lock(m_);
        return lines_;
    }
    // True once `done` accepts the lines so far; false after kWait.
    bool waitUntil(const std::function<bool(const std::vector<std::string>&)>& done) {
        std::unique_lock<std::mutex> lock(m_);
        return cv_.wait_for(lock, kWait, [&] { return done(lines_); });
    }
    bool waitForText(const std::string& text) {
        return waitUntil([&](const std::vector<std::string>& lines) {
            for (const auto& l : lines) {
                if (l.find(text) != std::string::npos) return true;
            }
            return false;
        });
    }
    bool contains(const std::string& text) {
        for (const auto& l : lines()) {
            if (l.find(text) != std::string::npos) return true;
        }
        return false;
    }
    // Every later write fails, as on a pipe whose reader has gone.
    void breakPipe() {
        std::lock_guard<std::mutex> lock(m_);
        broken_ = true;
    }
    // Every later write throws, as a broken stream implementation would.
    void throwOnWrite() {
        std::lock_guard<std::mutex> lock(m_);
        throws_ = true;
    }
    // The writer of the next line holding `text` stays inside that write until unblock().
    void blockAfter(const std::string& text) {
        std::lock_guard<std::mutex> lock(m_);
        blockOn_ = text;
    }
    void unblock() {
        std::lock_guard<std::mutex> lock(m_);
        blockOn_.clear();
        blocked_ = false;
        cv_.notify_all();
    }

protected:
    int_type overflow(int_type c) override {
        if (c == traits_type::eof()) return c;
        std::unique_lock<std::mutex> lock(m_);
        if (broken_) return traits_type::eof();
        if (throws_) throw std::runtime_error("the stream is broken");
        if (c == '\n') {
            const bool hold = !blockOn_.empty() && line_.find(blockOn_) != std::string::npos;
            lines_.push_back(line_);
            line_.clear();
            cv_.notify_all();
            if (hold) {
                blocked_ = true;
                cv_.wait(lock, [this] { return !blocked_; });
            }
        } else {
            line_.push_back(static_cast<char>(c));
        }
        return c;
    }

private:
    std::mutex m_;
    std::condition_variable cv_;
    std::string line_;
    std::vector<std::string> lines_;
    bool broken_ = false;
    bool throws_ = false;
    std::string blockOn_;
    bool blocked_ = false;
};

bool isTerminal(const json& e) {
    return e.is_object() && (e.value("type", "") == "final" || e.value("type", "") == "error");
}

std::vector<json> parseEvents(const std::vector<std::string>& lines) {
    std::vector<json> events;
    for (const auto& l : lines) {
        if (!l.empty() && l.back() == '\r') {
            events.push_back(json::parse(l.substr(0, l.size() - 1), nullptr, false));
        } else {
            events.push_back(json::parse(l, nullptr, false));
        }
    }
    return events;
}

// The nth event `match` accepts (1-based) among the lines written so far, or null after kWait.
json waitEvent(LineSink& sink, const std::function<bool(const json&)>& match, size_t nth = 1) {
    json found;
    sink.waitUntil([&](const std::vector<std::string>& lines) {
        size_t seen = 0;
        for (const auto& e : parseEvents(lines)) {
            if (match(e) && ++seen == nth) {
                found = e;
                return true;
            }
        }
        return false;
    });
    return found;
}

bool waitRequests(const bench::MockLlmServer& mock, int n) {
    const auto deadline = std::chrono::steady_clock::now() + kWait;
    while (mock.requestCount() < n && std::chrono::steady_clock::now() < deadline) std::this_thread::yield();
    return mock.requestCount() >= n;
}

std::string reply(const json& message) {
    return json{{"choices", json::array({{{"message", message}}})},
                {"usage", {{"prompt_tokens", 100}, {"completion_tokens", 7}, {"total_tokens", 107}}}}
        .dump();
}

std::string toolCall(const std::string& name, const json& args) {
    return reply({{"role", "assistant"},
                  {"content", nullptr},
                  {"tool_calls", json::array({{{"id", "c1"},
                                               {"type", "function"},
                                               {"function", {{"name", name}, {"arguments", args.dump()}}}}})}});
}

std::string answer(const std::string& text) { return reply({{"role", "assistant"}, {"content", text}}); }

std::string sse(const std::vector<std::string>& deltas) {
    std::string raw;
    for (const auto& d : deltas) {
        raw += "data: " + json{{"choices", json::array({{{"delta", {{"content", d}}}}})}}.dump() + "\n\n";
    }
    return raw + "data: [DONE]\n\n";
}

json wireFixture() {
    std::ifstream in(std::string(GAIA_REPO_FIXTURES_DIR) + "/stdio/gaia_stdio_wire.json");
    return json::parse(in);
}

// Keys an event of this type may carry over stdio, from the fixture.
std::set<std::string> allowedKeys(const json& spec) {
    std::set<std::string> keys = {"type"};
    for (const char* list : {"fields", "stdio_extension", "phase_extension", "history_cut_extension"}) {
        if (!spec.contains(list)) continue;
        for (const auto& k : spec[list]) keys.insert(k.get<std::string>());
    }
    if (spec.contains("not_sent_over_stdio")) {
        for (const auto& k : spec["not_sent_over_stdio"]) keys.erase(k.get<std::string>());
    }
    return keys;
}

void expectCanonical(const std::vector<json>& events) {
    const json spec = wireFixture()["events"];
    for (const auto& e : events) {
        ASSERT_TRUE(e.is_object()) << "stdout carried a line that is not a JSON object";
        const std::string type = e.value("type", "");
        ASSERT_TRUE(spec.contains(type)) << "not a canonical event type: " << e.dump();
        const auto keys = allowedKeys(spec[type]);
        for (const auto& [key, value] : e.items()) {
            EXPECT_TRUE(keys.count(key)) << type << " carries '" << key << "', which the contract doesn't list";
        }
    }
}

size_t countTerminals(const std::vector<json>& events) {
    size_t n = 0;
    for (const auto& e : events) n += isTerminal(e) ? 1 : 0;
    return n;
}

// A URL nothing listens on: a port that was just free.
std::string deadUrl() {
    bench::MockLlmServer gone;
    return gone.baseUrl();
}

// Lets go of a held model reply even when the test leaves early.
struct Release {
    std::promise<void> promise;
    bool done = false;
    void now() {
        if (!done) promise.set_value();
        done = true;
    }
    ~Release() { now(); }
};

struct Start {
    std::string baseUrl;  // empty = the fixture's mock
    std::string model = "mock-model";
    bool streaming = false;
    bool fullAccess = false;
    bool acceptEdits = false;
    bool memory = false;
    bool shell = false;
    std::function<bool()> hostAlive;
};

class GaiacppStdio : public ::testing::Test {
protected:
    fs::path work_;
    bench::MockLlmServer mock_;
    PipeBuf inBuf_;
    LineSink outBuf_;
    LineSink logBuf_;
    std::istream in_{&inBuf_};
    std::ostream out_{&outBuf_};
    std::ostream log_{&logBuf_};
    std::unique_ptr<GaiaAgent> agent_;
    std::unique_ptr<StdioServer> server_;
    std::thread thread_;
    std::promise<void> returned_;
    std::atomic<int> exitCode_{-1};
    size_t queries_ = 0;
    bool stopped_ = false;

    void SetUp() override {
        std::mt19937_64 rng(std::random_device{}());
        work_ = fs::weakly_canonical(fs::temp_directory_path()) / ("gaiacpp-stdio-" + std::to_string(rng()));
        fs::create_directories(work_);
    }

    void start(const Start& s = {}) {
        AgentConfig c;
        c.baseUrl = s.baseUrl.empty() ? mock_.baseUrl() : s.baseUrl;
        c.modelId = s.model;
        c.silentMode = true;
        c.streaming = s.streaming;
        c.maxSteps = 6;
        c.llmRetries = 0;
        c.nativeToolCalls = NativeToolCalls::Always;
        GaiaAgent::Options o;
        o.skills = false;
        o.webTools = false;
        o.documents = false;
        o.vision = false;
        o.workspaceRoots = {work_.string()};
        o.memory = s.memory;
        o.memoryPath = (work_ / "memory.db").string();
        if (s.shell) o.shell = BashTools::newSession(work_.string());
        agent_ = std::make_unique<GaiaAgent>(c, o);
        agent_->setConfig(c);  // the constructor lets GAIA_* variables override
        StdioOptions so;
        so.fullAccess = s.fullAccess;
        so.acceptEdits = s.acceptEdits;
        so.agentVersion = "9.9.9";
        so.editRoots = {work_.string()};
        so.memory = s.memory;
        so.memoryPath = o.memoryPath;
        so.nativeTools = NativeToolCalls::Always;
        so.log = &log_;
        so.hostAlive = s.hostAlive;
        server_ = std::make_unique<StdioServer>(*agent_, in_, out_, so);
        thread_ = std::thread([this] {
            exitCode_ = server_->run();
            returned_.set_value();
        });
    }

    void ask(const std::string& text) { askRaw(json{{"gaia_query", text}}.dump()); }
    void askRaw(const std::string& line) {
        ++queries_;
        inBuf_.push(line);
    }
    void control(const json& message) { inBuf_.push(message.dump()); }
    void decide(const std::string& decision, const std::string& confirmId) {
        control({{"gaia_control", "tool_decision"}, {"decision", decision}, {"confirm_id", confirmId}});
    }

    std::vector<json> events() { return parseEvents(outBuf_.lines()); }

    // The nth event `match` accepts (1-based), or null after kWait.
    json waitEvent(const std::function<bool(const json&)>& match, size_t nth) {
        return ::waitEvent(outBuf_, match, nth);
    }
    json waitFor(const std::string& type, size_t nth = 1) {
        return waitEvent([&](const json& e) { return e.is_object() && e.value("type", "") == type; }, nth);
    }
    // The terminal event of the nth query sent.
    json waitTerminal(size_t nth) { return waitEvent(isTerminal, nth); }

    size_t count(const std::string& type) {
        size_t n = 0;
        for (const auto& e : events()) n += e.is_object() && e.value("type", "") == type ? 1 : 0;
        return n;
    }

    // Close stdin, let the server finish, and check what every test must hold.
    void stop() {
        if (stopped_) return;
        stopped_ = true;
        outBuf_.unblock();
        inBuf_.close();
        if (thread_.joinable()) thread_.join();
        const auto all = events();
        expectCanonical(all);
        EXPECT_EQ(countTerminals(all), queries_) << "each query ends in exactly one final or error";
    }

    // stop(), then let go of the model server: its connection is kept open until then.
    void finish() {
        stop();
        server_.reset();
        agent_.reset();
    }

    void TearDown() override {
        finish();
        std::error_code ec;
        fs::remove_all(work_, ec);
    }
};

}  // namespace

// ---- startup ----

TEST_F(GaiacppStdio, StartupStatusCarriesTheModelPing) {
    start();
    const json status = waitFor("status");
    ASSERT_TRUE(status.is_object());
    for (const auto& f : wireFixture()["events"]["status"]["stdio_extension"]) {
        if (f == "lemonade_version") continue;  // only when the server reports one
        EXPECT_TRUE(status.contains(f.get<std::string>())) << f;
    }
    EXPECT_EQ(status["model_id"], "mock-model");
    EXPECT_EQ(status["model_display"], "mock-model");
    EXPECT_EQ(status["model_backend"], "lemonade");
    EXPECT_EQ(status["model_remote"], false);
    EXPECT_EQ(status["lemonade_reachable"], true);
    EXPECT_EQ(status["lemonade_base_url"], mock_.baseUrl() + "/api/v1");
    EXPECT_EQ(status["agent_version"], "9.9.9");
    EXPECT_EQ(events().front(), status) << "the ping is the first line on the wire";
}

TEST_F(GaiacppStdio, StartupStatusIsSentWhenNoModelServerAnswers) {
    Start s;
    s.baseUrl = deadUrl();
    const auto t0 = std::chrono::steady_clock::now();
    start(s);
    const json status = waitFor("status");
    ASSERT_TRUE(status.is_object());
    EXPECT_LT(std::chrono::steady_clock::now() - t0, std::chrono::seconds(15));
    EXPECT_EQ(status["lemonade_reachable"], false);
    EXPECT_EQ(status["model_id"], "mock-model");
    EXPECT_FALSE(status.contains("lemonade_version"));
    EXPECT_TRUE(logBuf_.waitForText("did not answer"));

    ask("anyone there?");
    const json terminal = waitTerminal(1);
    ASSERT_TRUE(terminal.is_object());
    EXPECT_EQ(terminal["type"], "error") << terminal.dump();
    EXPECT_NE(terminal["detail"].get<std::string>().find("did not answer"), std::string::npos) << terminal.dump();
}

// ---- turns ----

TEST_F(GaiacppStdio, AnAnswerEndsInOneFinalWithUsage) {
    mock_.pushResponse(answer("Hello."));
    start();
    ask("hi");
    const json final = waitTerminal(1);
    ASSERT_TRUE(final.is_object());
    EXPECT_EQ(final["type"], "final");
    EXPECT_EQ(final["answer"], "Hello.");
    EXPECT_EQ(final["usage"]["steps"], 1);
    EXPECT_EQ(final["usage"]["input_tokens"], 100);
    EXPECT_EQ(final["usage"]["tokens"], 7);
    EXPECT_EQ(count("token"), 0u);
}

TEST_F(GaiacppStdio, AToolTurnReportsCallThenResultThenFinal) {
    const fs::path notes = work_ / "notes.txt";
    std::ofstream(notes) << "remember the milk";
    mock_.pushResponse(toolCall("file_read", {{"path", notes.string()}}));
    mock_.pushResponse(answer("It says to remember the milk."));
    start();
    ask("what do my\nnotes say?");
    const json final = waitTerminal(1);
    ASSERT_TRUE(final.is_object());
    EXPECT_EQ(final["answer"], "It says to remember the milk.");
    EXPECT_EQ(final["usage"]["steps"], 2);

    const auto all = events();
    int call = -1, result = -1, end = -1;
    for (size_t i = 0; i < all.size(); ++i) {
        const std::string type = all[i].value("type", "");
        if (type == "tool_call") call = static_cast<int>(i);
        if (type == "tool_result") result = static_cast<int>(i);
        if (type == "final") end = static_cast<int>(i);
    }
    ASSERT_GE(call, 0);
    ASSERT_GE(result, 0);
    EXPECT_LT(call, result);
    EXPECT_LT(result, end);
    EXPECT_EQ(end, static_cast<int>(all.size()) - 1) << "nothing follows the terminal event";
    EXPECT_EQ(all[call]["tool"], "file_read");
    EXPECT_EQ(all[call]["args"]["path"], notes.string());
    EXPECT_EQ(all[call]["narration"], "Reading " + notes.string());
    EXPECT_EQ(all[result]["tool"], "file_read");
    EXPECT_NE(all[result]["data"].dump().find("remember the milk"), std::string::npos);
    EXPECT_FALSE(all[result]["preview"].get<std::string>().empty());
    EXPECT_EQ(count("needs_confirmation"), 0u) << "a read asks nobody";

    // The multi-line query arrived as one question.
    const json first = json::parse(mock_.receivedBodies().at(0));
    EXPECT_EQ(first["messages"].back()["content"], "what do my\nnotes say?");
}

TEST_F(GaiacppStdio, AStreamedAnswerArrivesAsTokensWithReasoningLeftOut) {
    mock_.enableSseStreaming(true);
    mock_.pushSseStream(sse({"<thi", "nk>secret plan</th", "ink>Hi", " there"}));
    Start s;
    s.streaming = true;
    start(s);
    ask("hi");
    const json final = waitTerminal(1);
    ASSERT_TRUE(final.is_object());
    EXPECT_EQ(final["type"], "final") << final.dump();
    std::string streamed;
    for (const auto& e : events()) {
        if (e.value("type", "") == "token") streamed += e["delta"].get<std::string>();
    }
    EXPECT_EQ(streamed, "Hi there");
    EXPECT_EQ(final["answer"].get<std::string>().find("secret"), std::string::npos) << final.dump();
}

TEST_F(GaiacppStdio, APlainLineAndAJsonLookingLineAreBothQueries) {
    mock_.pushResponse(answer("one"));
    mock_.pushResponse(answer("two"));
    mock_.pushResponse(answer("three"));
    start();
    askRaw("plain words, no JSON");
    askRaw(R"({"explain": "this object"})");
    askRaw(R"({"gaia_query": 5})");
    ASSERT_TRUE(waitTerminal(3).is_object());
    const auto bodies = mock_.receivedBodies();
    ASSERT_EQ(bodies.size(), 3u);
    EXPECT_EQ(json::parse(bodies[0])["messages"].back()["content"], "plain words, no JSON");
    EXPECT_EQ(json::parse(bodies[1])["messages"].back()["content"], R"({"explain": "this object"})");
    EXPECT_EQ(json::parse(bodies[2])["messages"].back()["content"], R"({"gaia_query": 5})");
    EXPECT_EQ(waitTerminal(3)["answer"], "three");
}

// ---- approvals ----

TEST_F(GaiacppStdio, AllowRunsTheToolOnce) {
    const fs::path out = work_ / "out.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", out.string()}, {"content", "hi"}}));
    mock_.pushResponse(answer("Written."));
    start();
    ask("write it");
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object());
    EXPECT_FALSE(prompt.contains("always_scope")) << "a file write has no scope narrow enough to grant";
    for (const char* key : {"run_id", "action", "summary", "confirm_id", "risk"}) {
        ASSERT_TRUE(prompt.contains(key) && prompt[key].is_string() && !prompt[key].get<std::string>().empty()) << key;
    }
    EXPECT_EQ(prompt["action"], "file_write");
    EXPECT_EQ(prompt["risk"], "write");
    EXPECT_EQ(prompt["summary"], "Write " + out.string());
    EXPECT_FALSE(fs::exists(out)) << "nothing is written before the answer";

    decide("allow", prompt["confirm_id"]);
    const json final = waitTerminal(1);
    ASSERT_TRUE(final.is_object());
    EXPECT_EQ(final["answer"], "Written.");
    EXPECT_TRUE(fs::exists(out));

    // "allow" covers that one call.
    const fs::path again = work_ / "again.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", again.string()}, {"content", "hi"}}));
    mock_.pushResponse(answer("Not written."));
    ask("write another");
    const json second = waitFor("needs_confirmation", 2);
    ASSERT_TRUE(second.is_object());
    EXPECT_NE(second["confirm_id"], prompt["confirm_id"]);
    decide("deny", second["confirm_id"]);
    ASSERT_TRUE(waitTerminal(2).is_object());
    EXPECT_FALSE(fs::exists(again));
}

TEST_F(GaiacppStdio, DenyAndTimeoutLeaveTheToolUnrun) {
    for (const std::string decision : {"deny", "timeout"}) {
        const fs::path out = work_ / (decision + ".txt");
        mock_.pushResponse(toolCall("file_write", {{"path", out.string()}, {"content", "hi"}}));
        mock_.pushResponse(answer("Not written."));
        if (!agent_) start();
        ask("write it");
        const json prompt = waitFor("needs_confirmation", queries_);
        ASSERT_TRUE(prompt.is_object());
        decide(decision, prompt["confirm_id"]);
        const json final = waitTerminal(queries_);
        ASSERT_TRUE(final.is_object());
        EXPECT_EQ(final["answer"], "Not written.");
        EXPECT_FALSE(fs::exists(out)) << decision;
        const json result = waitFor("tool_result", queries_);
        EXPECT_NE(result["data"].dump().find("denied"), std::string::npos) << result.dump();
    }
}

TEST_F(GaiacppStdio, ADecisionForAnotherApprovalIsDropped) {
    const fs::path out = work_ / "out.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", out.string()}, {"content", "hi"}}));
    mock_.pushResponse(answer("Not written."));
    start();
    decide("allow", "from-an-earlier-turn");  // nothing is waiting yet
    ask("write it");
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object());
    decide("allow", "not-" + prompt["confirm_id"].get<std::string>());
    decide("always", "0000000000000000");
    // Controls are handled in order, so the stale answers are behind this one.
    decide("deny", prompt["confirm_id"]);
    ASSERT_TRUE(waitTerminal(1).is_object());
    EXPECT_FALSE(fs::exists(out));
    EXPECT_TRUE(logBuf_.contains("nothing is waiting for one"));
    EXPECT_TRUE(logBuf_.contains("dropped a 'allow' decision for not-"));
    EXPECT_TRUE(logBuf_.contains("dropped a 'always' decision for 0000000000000000"));
    EXPECT_EQ(count("needs_confirmation"), 1u);
}

TEST_F(GaiacppStdio, AlwaysOnAFileWriteCoversThatOneCallOnly) {
    const fs::path source = work_ / "src" / "a.py", hook = work_ / ".git" / "hooks" / "pre-commit";
    mock_.pushResponse(toolCall("file_write", {{"path", source.string()}, {"content", "1"}}));
    mock_.pushResponse(answer("One."));
    mock_.pushResponse(toolCall("file_write", {{"path", hook.string()}, {"content", "2"}}));
    mock_.pushResponse(answer("Two."));
    start();
    ask("write the source file");
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object());
    EXPECT_FALSE(prompt.contains("always_scope")) << "always is not offered for a file write";
    decide("always", prompt["confirm_id"]);  // a host that sends it anyway
    ASSERT_TRUE(waitTerminal(1).is_object());
    EXPECT_TRUE(fs::exists(source));
    EXPECT_TRUE(logBuf_.contains("'always' was not offered for file_write"));

    ask("write the hook");
    const json again = waitFor("needs_confirmation", 2);
    ASSERT_TRUE(again.is_object()) << "another file still asks";
    EXPECT_FALSE(fs::exists(hook));
    decide("deny", again["confirm_id"]);
    ASSERT_TRUE(waitTerminal(2).is_object());
    EXPECT_FALSE(fs::exists(hook));
}

TEST_F(GaiacppStdio, AlwaysForAShellCommandCoversThatProgramOnly) {
    mock_.pushResponse(toolCall("bash_execute", {{"command", "mkdir first"}}));
    mock_.pushResponse(answer("Made."));
    mock_.pushResponse(toolCall("bash_execute", {{"command", "mkdir second"}}));
    mock_.pushResponse(answer("Made again."));
    mock_.pushResponse(toolCall("bash_execute", {{"command", "rmdir first"}}));
    mock_.pushResponse(answer("Kept."));
    Start s;
    s.shell = true;
    start(s);
    ask("make a folder");
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object());
    EXPECT_EQ(prompt["action"], "bash_execute");
    EXPECT_EQ(prompt["risk"], "execute");
    EXPECT_EQ(prompt["summary"], "Run: mkdir first");
    EXPECT_EQ(prompt["always_scope"], "Run mkdir commands");
    decide("always", prompt["confirm_id"]);
    ASSERT_TRUE(waitTerminal(1).is_object());
    EXPECT_TRUE(fs::is_directory(work_ / "first"));

    ask("make another");
    ASSERT_TRUE(waitTerminal(2).is_object());
    EXPECT_TRUE(fs::is_directory(work_ / "second"));
    EXPECT_EQ(count("needs_confirmation"), 1u) << "mkdir was granted";

    ask("remove the first");
    const json other = waitFor("needs_confirmation", 2);
    ASSERT_TRUE(other.is_object()) << "another program still asks";
    EXPECT_EQ(other["always_scope"], "Run rmdir commands");
    decide("deny", other["confirm_id"]);
    ASSERT_TRUE(waitTerminal(3).is_object());
    EXPECT_TRUE(fs::is_directory(work_ / "first"));
}

TEST_F(GaiacppStdio, FullAccessTurnsPromptsOffAndBackOn) {
    const fs::path one = work_ / "one.txt", two = work_ / "two.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", one.string()}, {"content", "1"}}));
    mock_.pushResponse(answer("One."));
    mock_.pushResponse(toolCall("file_write", {{"path", two.string()}, {"content", "2"}}));
    mock_.pushResponse(answer("Two."));
    start();
    control({{"gaia_control", "full_access"}, {"enabled", true}});
    ask("write one");
    ASSERT_TRUE(waitTerminal(1).is_object());
    EXPECT_TRUE(fs::exists(one));
    EXPECT_EQ(count("needs_confirmation"), 0u);

    control({{"gaia_control", "full_access"}, {"enabled", false}});
    ask("write two");
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object()) << "prompts are back";
    decide("deny", prompt["confirm_id"]);
    ASSERT_TRUE(waitTerminal(2).is_object());
    EXPECT_FALSE(fs::exists(two));
}

TEST_F(GaiacppStdio, FullAccessAtLaunchSkipsPrompts) {
    const fs::path out = work_ / "out.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", out.string()}, {"content", "hi"}}));
    mock_.pushResponse(answer("Written."));
    Start s;
    s.fullAccess = true;
    start(s);
    ask("write it");
    ASSERT_TRUE(waitTerminal(1).is_object());
    EXPECT_TRUE(fs::exists(out));
    EXPECT_EQ(count("needs_confirmation"), 0u);
}

TEST_F(GaiacppStdio, AcceptEditsCoversEditsInTheFolderButNotHiddenOnes) {
    const fs::path plain = work_ / "sub" / "plain.txt", hidden = work_ / ".config" / "run.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", plain.string()}, {"content", "1"}}));
    mock_.pushResponse(answer("Plain."));
    mock_.pushResponse(toolCall("file_write", {{"path", hidden.string()}, {"content", "2"}}));
    mock_.pushResponse(answer("Hidden."));
    mock_.pushResponse(toolCall("bash_execute", {{"command", "mkdir made"}}));
    mock_.pushResponse(answer("Shell."));
    Start s;
    s.shell = true;
    start(s);
    control({{"gaia_control", "accept_edits"}, {"enabled", true}});
    ask("write the plain file");
    ASSERT_TRUE(waitTerminal(1).is_object());
    EXPECT_TRUE(fs::exists(plain));
    EXPECT_EQ(count("needs_confirmation"), 0u);

    ask("write the hidden file");
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object()) << "a dot-folder still asks";
    decide("deny", prompt["confirm_id"]);
    ASSERT_TRUE(waitTerminal(2).is_object());
    EXPECT_FALSE(fs::exists(hidden));

    ask("run a command");
    const json shell = waitFor("needs_confirmation", 2);
    ASSERT_TRUE(shell.is_object()) << "a shell command still asks";
    EXPECT_EQ(shell["action"], "bash_execute");
    decide("deny", shell["confirm_id"]);
    ASSERT_TRUE(waitTerminal(3).is_object());
    EXPECT_FALSE(fs::exists(work_ / "made"));
}

TEST(GaiacppStdioEdits, InsideMeansInTheFolderAndNowhereCodeRuns) {
    std::mt19937_64 rng(std::random_device{}());
    const fs::path root = fs::weakly_canonical(fs::temp_directory_path()) / ("gaiacpp-edits-" + std::to_string(rng()));
    fs::create_directories(root / "src");
    const std::vector<std::string> roots = {root.string()};
    EXPECT_TRUE(editIsInside((root / "notes.txt").string(), roots));
    EXPECT_TRUE(editIsInside((root / "src" / "new" / "a.cpp").string(), roots));
    EXPECT_FALSE(editIsInside((root / ".git" / "hooks" / "pre-commit").string(), roots));
    EXPECT_FALSE(editIsInside((root / "src" / ".env").string(), roots));
    EXPECT_FALSE(editIsInside((root / ".github" / "workflows" / "ci.yml").string(), roots));
    EXPECT_FALSE(editIsInside((root / "src" / ".." / ".." / "escaped.txt").string(), roots));
    EXPECT_FALSE(editIsInside((root.parent_path() / "sibling.txt").string(), roots));
    EXPECT_FALSE(editIsInside(root.string(), roots)) << "the folder itself is not a file in it";
    EXPECT_FALSE(editIsInside("", roots));
    EXPECT_FALSE(editIsInside((root / "notes.txt").string(), {}));
    EXPECT_FALSE(editIsInside((root / "notes.txt").string(), {root.root_path().string()})) << "a drive root is too broad";
    const std::string home = getEnvVar("USERPROFILE", getEnvVar("HOME"));
    if (!home.empty()) {
        EXPECT_FALSE(editIsInside((fs::path(home) / "notes.txt").string(), {home})) << "a home folder is too broad";
    }

    // A folder on PATH, and on Windows a file cmd.exe would run instead of a program there.
    fs::create_directories(root / "tools");
    std::ofstream(root / "tools" / "gaiatool.exe") << "x";
    const std::string path = getEnvVar("PATH");
#ifdef _WIN32
    const std::string withTools = (root / "tools").string() + ";" + path;
    _putenv_s("PATH", withTools.c_str());
#else
    setenv("PATH", ((root / "tools").string() + ":" + path).c_str(), 1);
#endif
    EXPECT_FALSE(editIsInside((root / "tools" / "new-tool").string(), roots));
    EXPECT_TRUE(editIsInside((root / "src" / "gaiatool.txt").string(), roots));
#ifdef _WIN32
    EXPECT_FALSE(editIsInside((root / "src" / "gaiatool.bat").string(), roots));
    _putenv_s("PATH", path.c_str());
#else
    setenv("PATH", path.c_str(), 1);
#endif
    EXPECT_TRUE(editIsInside((root / "tools" / "new-tool").string(), roots)) << "off PATH again";

    std::error_code ec;
    fs::remove_all(root, ec);
}

TEST(GaiacppStdioEdits, TheWorkingFolderMayHaveANonAsciiName) {
    std::mt19937_64 rng(std::random_device{}());
    const fs::path folder = fs::weakly_canonical(fs::temp_directory_path()) /
                            fs::u8path("gaiacpp-w\xc3\xb6rk \xc3\xb1-" + std::to_string(rng()));
    fs::create_directories(folder);
    const fs::path before = fs::current_path();
    fs::current_path(folder);
    const std::vector<std::string> roots = {workingFolderUtf8()};
    const bool relative = editIsInside("note.txt", roots);
    const bool absolute = editIsInside((folder / "sub" / "note.txt").u8string(), roots);
    const bool hidden = editIsInside((folder / ".env").u8string(), roots);
    fs::current_path(before);
    EXPECT_EQ(roots[0], folder.u8string());
    EXPECT_TRUE(relative);
    EXPECT_TRUE(absolute);
    EXPECT_FALSE(hidden);
    std::error_code ec;
    fs::remove_all(folder, ec);
}

// ---- cancel ----

TEST_F(GaiacppStdio, CancelWhileWaitingForApprovalEndsTheTurn) {
    const fs::path out = work_ / "out.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", out.string()}, {"content", "hi"}}));
    start();
    ask("write it");
    ASSERT_TRUE(waitFor("needs_confirmation").is_object());
    control({{"gaia_control", "cancel"}});
    const json terminal = waitTerminal(1);
    ASSERT_TRUE(terminal.is_object()) << "a cancelled turn still ends";
    EXPECT_EQ(terminal["type"], "final");
    EXPECT_EQ(terminal["answer"], "");
    EXPECT_FALSE(fs::exists(out));
    EXPECT_EQ(mock_.requestCount(), 1) << "no further model call after the cancel";

    mock_.pushResponse(answer("Still here."));
    ask("are you there?");
    const json next = waitTerminal(2);
    ASSERT_TRUE(next.is_object());
    EXPECT_EQ(next["answer"], "Still here.");
}

TEST_F(GaiacppStdio, CancelDuringAModelCallEndsTheTurn) {
    Release release;
    mock_.holdNextResponse(release.promise.get_future().share());
    start();
    ask("think about it");
    const auto deadline = std::chrono::steady_clock::now() + kWait;
    while (mock_.requestCount() < 1 && std::chrono::steady_clock::now() < deadline) std::this_thread::yield();
    ASSERT_EQ(mock_.requestCount(), 1);
    control({{"gaia_control", "cancel"}});
    ASSERT_TRUE(logBuf_.waitForText("stopping the running turn"));
    release.now();
    const json terminal = waitTerminal(1);
    ASSERT_TRUE(terminal.is_object());
    EXPECT_EQ(terminal["type"], "final");
    EXPECT_EQ(terminal["answer"], "");

    mock_.pushResponse(answer("Back."));
    ask("again");
    const json next = waitTerminal(2);
    ASSERT_TRUE(next.is_object());
    EXPECT_EQ(next["answer"], "Back.");
}

TEST_F(GaiacppStdio, ACancelForAQueryNotStartedYetEndsItWithoutRunningIt) {
    mock_.pushResponse(answer("one"));
    start();
    // Holds the server inside the write of the first final: that turn is over, the next not taken yet.
    outBuf_.blockAfter(R"("answer":"one")");
    ask("first");
    ASSERT_TRUE(waitTerminal(1).is_object());
    ask("second");
    control({{"gaia_control", "cancel"}});
    ASSERT_TRUE(logBuf_.waitForText("cancelled a queued query"));
    outBuf_.unblock();
    const json second = waitTerminal(2);
    ASSERT_TRUE(second.is_object()) << "a cancelled query still gets its terminal event";
    EXPECT_EQ(second["type"], "final");
    EXPECT_EQ(second["answer"], "");
    EXPECT_EQ(mock_.requestCount(), 1) << "the cancelled query never reached the model";

    mock_.pushResponse(answer("three"));
    ask("third");
    const json third = waitTerminal(3);
    ASSERT_TRUE(third.is_object());
    EXPECT_EQ(third["answer"], "three");
}

TEST_F(GaiacppStdio, CancelWithNoTurnRunningChangesNothing) {
    mock_.pushResponse(answer("Fine."));
    start();
    control({{"gaia_control", "cancel"}});
    ask("hi");
    const json final = waitTerminal(1);
    ASSERT_TRUE(final.is_object());
    EXPECT_EQ(final["answer"], "Fine.");
    EXPECT_TRUE(logBuf_.contains("nothing to stop"));
}

// ---- sentinels ----

TEST_F(GaiacppStdio, ClearConversationSentinelAndControlBothForgetTheHistory) {
    for (int i = 0; i < 3; ++i) mock_.pushResponse(answer("ok " + std::to_string(i)));
    start();
    ask("first");
    ASSERT_TRUE(waitTerminal(1).is_object());
    ask(std::string("\0gaia:clear_conversation\0", 25));
    const json cleared = waitTerminal(2);
    ASSERT_TRUE(cleared.is_object());
    EXPECT_EQ(cleared["type"], "final");
    EXPECT_EQ(cleared["answer"], "conversation_cleared");
    ask("second");
    ASSERT_TRUE(waitTerminal(3).is_object());
    control({{"gaia_control", "clear_history"}});
    ask("third");
    ASSERT_TRUE(waitTerminal(4).is_object());

    const auto bodies = mock_.receivedBodies();
    ASSERT_EQ(bodies.size(), 3u) << "a sentinel never reaches the model";
    for (const auto& body : bodies) {
        size_t users = 0;
        const json request = json::parse(body);
        for (const auto& m : request["messages"]) users += m.value("role", "") == "user" ? 1 : 0;
        EXPECT_EQ(users, 1u) << "earlier turns were forgotten: " << body;
    }
}

TEST_F(GaiacppStdio, MemoryDumpSentinelAnswersWithTheStoredMemories) {
    {
        MemoryStore store((work_ / "memory.db").string());
        store.remember("likes green tea", "preference");
        store.remember("the build runs on ninja", "fact", "work");
    }
    Start s;
    s.memory = true;
    start(s);
    ask(std::string("\0gaia:memory_dump\0", 18));
    const json final = waitTerminal(1);
    ASSERT_TRUE(final.is_object());
    ASSERT_EQ(final["type"], "final") << final.dump();
    const json dump = json::parse(final["answer"].get<std::string>());
    EXPECT_EQ(dump["available"], true);
    EXPECT_EQ(dump["total"], 2);
    EXPECT_EQ(dump["shown"], 2);
    EXPECT_EQ(dump["stats"]["total_knowledge"], 2);
    EXPECT_EQ(dump["stats"]["by_category"]["preference"], 1);
    EXPECT_EQ(dump["stats"]["by_context"]["work"], 1);
    ASSERT_TRUE(dump["contexts"].is_array());
    ASSERT_EQ(dump["items"].size(), 2u);
    for (const auto& item : dump["items"]) {
        // The types tui/internal/client/memory.go decodes into.
        EXPECT_TRUE(item["id"].is_string());
        EXPECT_TRUE(item["category"].is_string());
        EXPECT_TRUE(item["content"].is_string());
        EXPECT_TRUE(item["context"].is_string());
        EXPECT_TRUE(item["sensitive"].is_boolean());
        EXPECT_TRUE(item["created_at"].is_string());
    }
    EXPECT_EQ(mock_.requestCount(), 0);
}

TEST_F(GaiacppStdio, MemoryDumpSaysWhenMemoryIsOff) {
    start();
    ask(std::string("\0gaia:memory_dump\0", 18));
    const json final = waitTerminal(1);
    ASSERT_TRUE(final.is_object());
    const json dump = json::parse(final["answer"].get<std::string>());
    EXPECT_EQ(dump["available"], false);
    EXPECT_FALSE(dump["reason"].get<std::string>().empty());
}

TEST_F(GaiacppStdio, WarmUpLoadsALocalModel) {
    bench::MockLlmServer cold(/*reportModelLoaded=*/false);
    Start s;
    s.baseUrl = cold.baseUrl();
    start(s);
    ask(std::string("\0gaia:warm_up\0", 14));
    const json final = waitTerminal(1);
    ASSERT_TRUE(final.is_object());
    EXPECT_EQ(final["type"], "final") << final.dump();
    EXPECT_EQ(final["answer"], "warmed_up");
    EXPECT_EQ(cold.loadRequestCount(), 1);
    EXPECT_EQ(cold.requestCount(), 0) << "warming up asks the model nothing";
    finish();
}

TEST_F(GaiacppStdio, WarmUpIsSkippedForACloudModel) {
    bench::MockLlmServer cold(/*reportModelLoaded=*/false);
    Start s;
    s.baseUrl = cold.baseUrl();
    s.model = "fireworks.some-model";
    start(s);
    const json status = waitFor("status");
    EXPECT_EQ(status["model_backend"], "fireworks");
    EXPECT_EQ(status["model_remote"], true);
    ask(std::string("\0gaia:warm_up\0", 14));
    const json final = waitTerminal(1);
    ASSERT_TRUE(final.is_object());
    EXPECT_EQ(final["answer"], "warm_up_skipped");
    EXPECT_EQ(cold.loadRequestCount(), 0);
    finish();
}

TEST_F(GaiacppStdio, WarmUpFailureIsAnErrorNotASilentSkip) {
    bench::MockLlmServer cold(/*reportModelLoaded=*/false);
    cold.setLoadStatus(500);
    Start s;
    s.baseUrl = cold.baseUrl();
    start(s);
    ask(std::string("\0gaia:warm_up\0", 14));
    const json terminal = waitTerminal(1);
    ASSERT_TRUE(terminal.is_object());
    EXPECT_EQ(terminal["type"], "error") << terminal.dump();
    EXPECT_NE(terminal["detail"].get<std::string>().find("Could not get the model ready"), std::string::npos);
    finish();
}

// ---- /model ----

TEST_F(GaiacppStdio, ModelCommandSwitchesListsAndRefuses) {
    mock_.setModelsList(R"({"data":[
        {"id":"mock-model","downloaded":true,"labels":["chat"]},
        {"id":"other-model","downloaded":true},
        {"id":"fireworks.fast","recipe":"cloud","downloaded":false},
        {"id":"not-downloaded","downloaded":false},
        {"id":"Whisper-Base","downloaded":true,"labels":["transcription"]}]})");
    mock_.pushResponse(answer("From the other model."));
    start();
    ASSERT_TRUE(waitFor("status").is_object());

    ask("/model");
    const json list = waitTerminal(1);
    ASSERT_TRUE(list.is_object());
    EXPECT_EQ(list["type"], "final");
    const std::string text = list["answer"];
    EXPECT_NE(text.find("`mock-model` ← current"), std::string::npos) << text;
    EXPECT_NE(text.find("`other-model`"), std::string::npos);
    EXPECT_NE(text.find("`fireworks.fast`"), std::string::npos);
    EXPECT_EQ(text.find("not-downloaded"), std::string::npos);
    EXPECT_EQ(text.find("Whisper-Base"), std::string::npos);

    for (const std::string bad : {"nope", "not-downloaded", "Whisper-Base"}) {
        ask("/model " + bad);
        const json refused = waitTerminal(queries_);
        ASSERT_TRUE(refused.is_object());
        EXPECT_EQ(refused["type"], "error") << bad;
        EXPECT_NE(refused["detail"].get<std::string>().find("Unknown model '" + bad + "'"), std::string::npos);
    }
    size_t pings = 0;
    for (const auto& e : events()) pings += e.contains("model_id") ? 1 : 0;
    EXPECT_EQ(pings, 1u) << "a refused switch sends no ping";

    ask("/model other-model");
    const json switched = waitTerminal(queries_);
    ASSERT_TRUE(switched.is_object());
    EXPECT_EQ(switched["type"], "final") << switched.dump();
    EXPECT_NE(switched["answer"].get<std::string>().find("other-model"), std::string::npos);
    const auto after = events();
    const json& ping = after[after.size() - 2];
    EXPECT_EQ(ping["type"], "status") << "the ping comes right before the final";
    EXPECT_EQ(ping["model_id"], "other-model");
    EXPECT_EQ(ping["model_remote"], false);
    for (const auto& f : wireFixture()["events"]["status"]["stdio_extension"]) {
        if (f != "lemonade_version") EXPECT_TRUE(ping.contains(f.get<std::string>())) << f;
    }

    ask("who answers?");
    ASSERT_TRUE(waitTerminal(queries_).is_object());
    EXPECT_EQ(json::parse(mock_.receivedBodies().at(0))["model"], "other-model");

    ask("/model fireworks.fast");
    ASSERT_TRUE(waitTerminal(queries_).is_object());
    const auto last = events();
    EXPECT_EQ(last[last.size() - 2]["model_backend"], "fireworks");
    EXPECT_EQ(last[last.size() - 2]["model_remote"], true);
}

TEST_F(GaiacppStdio, ModelSwitchFailsLoudlyWhenTheServerIsGone) {
    Start s;
    s.baseUrl = deadUrl();
    start(s);
    ask("/model other-model");
    const json terminal = waitTerminal(1);
    ASSERT_TRUE(terminal.is_object());
    EXPECT_EQ(terminal["type"], "error");
    EXPECT_NE(terminal["detail"].get<std::string>().find("was not changed"), std::string::npos) << terminal.dump();
    EXPECT_EQ(agent_->config().modelId, "mock-model");
}

TEST_F(GaiacppStdio, ModelSwitchUsesTheServersSpellingOfAUserModel) {
    mock_.setModelsList(R"({"data":[{"id":"mock-model"},{"id":"user.Foo"},{"id":"Bar"}]})");
    mock_.pushResponse(answer("from Foo"));
    mock_.pushResponse(answer("from Bar"));
    start();
    const std::vector<std::pair<std::string, std::string>> cases = {{"Foo", "user.Foo"}, {"user.Bar", "Bar"}};
    for (size_t i = 0; i < cases.size(); ++i) {
        ask("/model " + cases[i].first);
        const json switched = waitTerminal(queries_);
        ASSERT_TRUE(switched.is_object());
        ASSERT_EQ(switched["type"], "final") << switched.dump();
        const auto all = events();
        EXPECT_EQ(all[all.size() - 2]["model_id"], cases[i].second);
        EXPECT_EQ(agent_->config().modelId, cases[i].second);
        ask("who answers?");
        const json final = waitTerminal(queries_);
        ASSERT_TRUE(final.is_object());
        EXPECT_EQ(final["type"], "final") << final.dump();
        EXPECT_EQ(json::parse(mock_.receivedBodies().at(i))["model"], cases[i].second);
    }
}

// ---- controls ----

TEST_F(GaiacppStdio, AMalformedControlIsRejectedAndChangesNothing) {
    const fs::path out = work_ / "out.txt";
    mock_.pushResponse(answer("That was not JSON."));
    mock_.pushResponse(toolCall("file_write", {{"path", out.string()}, {"content", "hi"}}));
    mock_.pushResponse(answer("Not written."));
    start();
    ASSERT_TRUE(waitFor("status").is_object());
    control({{"gaia_control", "full_access"}, {"enabled", "yes"}});
    control({{"gaia_control", "full_access"}});
    control({{"gaia_control", "accept_edits"}, {"enabled", 1}});
    control({{"gaia_control", 5}});
    control({{"gaia_control", json::array({"cancel"})}});
    control({{"gaia_control", "make_coffee"}});
    // Cut off mid-object, so not a control: it is asked like any other line.
    askRaw(R"({"gaia_control": "full_access", "enabled": tru)");
    const json asked = waitTerminal(1);
    ASSERT_TRUE(asked.is_object());
    EXPECT_EQ(asked["answer"], "That was not JSON.");
    EXPECT_EQ(events().size(), 3u) << "the ping, Thinking and the final: no control wrote to stdout";

    ask("write it");
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object()) << "none of the malformed controls turned prompts off";
    control({{"gaia_control", "tool_decision"}, {"decision", 7}, {"confirm_id", prompt["confirm_id"]}});
    ASSERT_TRUE(waitTerminal(2).is_object()) << "an unreadable decision denies instead of hanging";
    EXPECT_FALSE(fs::exists(out));

    size_t rejected = 0;
    for (const auto& l : logBuf_.lines()) rejected += l.find("rejected a control message") != std::string::npos ? 1 : 0;
    EXPECT_EQ(rejected, 6u);
    EXPECT_TRUE(logBuf_.contains("ignored unknown control verb 'make_coffee'"));
}

TEST_F(GaiacppStdio, AClosedStdoutEndsTheServerWithoutWaitingForStdin) {
    start();
    ASSERT_TRUE(waitFor("status").is_object());
    outBuf_.breakPipe();
    // Not counted as queries: nothing can report their end.
    inBuf_.push("is anyone reading?");
    inBuf_.push("and this one?");
    ASSERT_EQ(returned_.get_future().wait_for(kWait), std::future_status::ready) << "stdin is still open";
    EXPECT_TRUE(server_->hostGone());
    EXPECT_TRUE(logBuf_.contains("the host left"));
    EXPECT_EQ(mock_.requestCount(), 0) << "no model call is made for a host that left";
}

TEST_F(GaiacppStdio, AStreamThatThrowsOnWriteEndsTheServerInsteadOfEscaping) {
    out_.exceptions(std::ios::badbit | std::ios::failbit);
    outBuf_.breakPipe();
    start();
    ASSERT_EQ(returned_.get_future().wait_for(kWait), std::future_status::ready);
    EXPECT_TRUE(server_->hostGone());
    out_.exceptions(std::ios::goodbit);
}

TEST_F(GaiacppStdio, AnInternalErrorEndsRunWithACodeInsteadOfEscaping) {
    out_.exceptions(std::ios::badbit);
    outBuf_.throwOnWrite();
    start();
    ASSERT_EQ(returned_.get_future().wait_for(kWait), std::future_status::ready) << "stdin is still open";
    EXPECT_EQ(exitCode_.load(), 1);
    EXPECT_TRUE(logBuf_.contains("stopped on an internal error: the stream is broken"));
    out_.exceptions(std::ios::goodbit);
}

TEST_F(GaiacppStdio, QueriesAlreadyReadStillRunAfterStdinCloses) {
    mock_.pushResponse(answer("one"));
    mock_.pushResponse(answer("two"));
    Start s;
    s.hostAlive = [] { return true; };
    start(s);
    ask("first");
    ask("second");
    inBuf_.close();
    const json second = waitTerminal(2);
    ASSERT_TRUE(second.is_object());
    EXPECT_EQ(second["answer"], "two");
}

TEST_F(GaiacppStdio, StdinClosingWithTheHostGoneStopsTheRunningTurn) {
    const fs::path out = work_ / "out.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", out.string()}, {"content", "hi"}}));
    std::atomic<bool> alive{true};
    Start s;
    s.hostAlive = [&alive] { return alive.load(); };
    start(s);
    // Not counted as a query: nobody is left to read its end.
    inBuf_.push(json{{"gaia_query", "write it"}}.dump());
    ASSERT_TRUE(waitFor("needs_confirmation").is_object());
    alive = false;
    inBuf_.close();
    ASSERT_EQ(returned_.get_future().wait_for(kWait), std::future_status::ready);
    EXPECT_TRUE(server_->hostGone());
    EXPECT_TRUE(logBuf_.contains("stdin and stdout are both closed"));
    EXPECT_FALSE(fs::exists(out));
    EXPECT_EQ(mock_.requestCount(), 1) << "no further model step for a host that left";
}

// ---- the wire contract ----

TEST_F(GaiacppStdio, EveryNameOnTheWireIsInTheContract) {
    const json wire = wireFixture();
    for (const auto& type : StdioServer::eventTypes()) {
        EXPECT_TRUE(wire["events"].contains(type)) << type << " is not a canonical event type";
    }
    const auto& types = StdioServer::eventTypes();
    for (const char* needed : {"status", "token", "tool_call", "tool_result", "needs_confirmation", "final", "error"}) {
        EXPECT_NE(std::find(types.begin(), types.end(), needed), types.end()) << needed;
    }
    std::set<std::string> verbs;
    for (const auto& [verb, spec] : wire["stdin"]["control_verbs"].items()) verbs.insert(verb);
    EXPECT_EQ(std::set<std::string>(StdioServer::controlVerbs().begin(), StdioServer::controlVerbs().end()), verbs);
    std::set<std::string> decisions;
    for (const auto& d : wire["stdin"]["decisions"]) decisions.insert(d.get<std::string>());
    EXPECT_EQ(std::set<std::string>(StdioServer::decisions().begin(), StdioServer::decisions().end()), decisions);

    // And the server acts on each of them, built from the fixture's own keys.
    const std::string controlKey = wire["stdin"]["control_key"], queryKey = wire["stdin"]["query_key"];
    start();
    for (const auto& [verb, spec] : wire["stdin"]["control_verbs"].items()) {
        json message = {{controlKey, verb}};
        for (const auto& field : spec["fields"]) {
            if (field == "enabled") message["enabled"] = false;
            else if (field == "decision") message["decision"] = "deny";
            else if (field == "confirm_id") message["confirm_id"] = "none-open";
            else FAIL() << "the fixture gained a field this test doesn't know: " << field;
        }
        control(message);
    }
    for (const auto& d : wire["stdin"]["decisions"]) control({{controlKey, "tool_decision"}, {"decision", d}});
    control({{controlKey, "tool_decision"}, {"decision", "maybe"}});

    for (const auto& [name, spec] : wire["stdin"]["query_sentinels"].items()) {
        askRaw(json{{queryKey, spec["query"]}}.dump());
        const json final = waitTerminal(queries_);
        ASSERT_TRUE(final.is_object()) << name;
        EXPECT_EQ(final["type"], "final") << name << ": " << final.dump();
        if (spec.contains("ack_answer")) {
            const std::string got = final["answer"];
            EXPECT_TRUE(got == spec["ack_answer"].get<std::string>() || got == spec.value("skipped_answer", ""))
                << name << ": " << got;
        }
    }
    // The sentinels ran after every control above, so the log is complete.
    EXPECT_FALSE(logBuf_.contains("unknown control verb"));
    EXPECT_FALSE(logBuf_.contains("rejected a control message"));
    EXPECT_TRUE(logBuf_.contains("unknown tool decision 'maybe'")) << "the check above can fail";
    size_t unknown = 0;
    for (const auto& l : logBuf_.lines()) unknown += l.find("unknown tool decision") != std::string::npos ? 1 : 0;
    EXPECT_EQ(unknown, 1u) << "every decision in the fixture is accepted";
    EXPECT_EQ(mock_.requestCount(), 0);
}

// ---- the built program ----

namespace {

// A child process with its stdin and stdout on pipes this test holds, the way
// the terminal UI holds its agent. Killed on destruction.
class PipedChild {
public:
    LineSink out;  // what the child wrote to stdout, line by line

    PipedChild(const std::vector<std::string>& argv, const std::map<std::string, std::string>& env,
               const fs::path& cwd, const fs::path& stderrFile) {
#ifdef _WIN32
        SECURITY_ATTRIBUTES sa{sizeof(sa), nullptr, TRUE};
        HANDLE inRead = nullptr, outWrite = nullptr;
        if (!CreatePipe(&inRead, &stdin_, &sa, 0) || !CreatePipe(&stdout_, &outWrite, &sa, 0)) {
            throw std::runtime_error("PipedChild: CreatePipe failed");
        }
        SetHandleInformation(stdin_, HANDLE_FLAG_INHERIT, 0);
        SetHandleInformation(stdout_, HANDLE_FLAG_INHERIT, 0);
        HANDLE err = CreateFileW(stderrFile.c_str(), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, &sa,
                                 CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        std::wstring cmd;
        for (const auto& a : argv) {
            const std::wstring w = fs::u8path(a).wstring();
            cmd += (cmd.empty() ? L"" : L" ");
            cmd += w.find(L' ') == std::wstring::npos ? w : L"\"" + w + L"\"";
        }
        // The child inherits this process's environment, so the extra variables go in around the launch.
        std::map<std::string, std::string> saved;
        for (const auto& [name, value] : env) {
            saved[name] = getEnvVar(name.c_str());
            _putenv_s(name.c_str(), value.c_str());
        }
        STARTUPINFOW si{};
        si.cb = sizeof(si);
        si.dwFlags = STARTF_USESTDHANDLES;
        si.hStdInput = inRead;
        si.hStdOutput = outWrite;
        si.hStdError = err;
        PROCESS_INFORMATION pi{};
        const BOOL ok = CreateProcessW(nullptr, cmd.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr,
                                       cwd.c_str(), &si, &pi);
        for (const auto& [name, value] : saved) _putenv_s(name.c_str(), value.c_str());
        CloseHandle(inRead);
        CloseHandle(outWrite);
        if (err != INVALID_HANDLE_VALUE) CloseHandle(err);
        if (!ok) throw std::runtime_error("PipedChild: CreateProcess failed for " + argv[0]);
        CloseHandle(pi.hThread);
        process_ = pi.hProcess;
#else
        std::signal(SIGPIPE, SIG_IGN);  // a write to a child that has exited must fail, not kill the tests
        int in[2], outPipe[2];
        if (pipe(in) != 0 || pipe(outPipe) != 0) throw std::runtime_error("PipedChild: pipe failed");
        std::vector<std::string> args(argv);
        std::vector<char*> cargs;
        for (auto& a : args) cargs.push_back(a.data());
        cargs.push_back(nullptr);
        const std::string dir = cwd.string(), errPath = stderrFile.string();
        pid_ = fork();
        if (pid_ < 0) throw std::runtime_error("PipedChild: fork failed");
        if (pid_ == 0) {
            dup2(in[0], STDIN_FILENO);
            dup2(outPipe[1], STDOUT_FILENO);
            const int err = open(errPath.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
            if (err >= 0) dup2(err, STDERR_FILENO);
            close(in[0]);
            close(in[1]);
            close(outPipe[0]);
            close(outPipe[1]);
            if (chdir(dir.c_str()) != 0) _exit(126);
            for (const auto& [name, value] : env) setenv(name.c_str(), value.c_str(), 1);
            execv(cargs[0], cargs.data());
            _exit(127);
        }
        close(in[0]);
        close(outPipe[1]);
        stdin_ = in[1];
        stdout_ = outPipe[0];
#endif
        reader_ = std::thread([this] { readLoop(); });
    }

    ~PipedChild() {
        closeStdout();
        closeStdin();
#ifdef _WIN32
        if (!exited_) TerminateProcess(process_, 1);
        WaitForSingleObject(process_, 10000);
        CloseHandle(process_);
#else
        if (!exited_) {
            kill(pid_, SIGKILL);
            int status = 0;
            waitpid(pid_, &status, 0);
        }
#endif
    }

    PipedChild(const PipedChild&) = delete;
    PipedChild& operator=(const PipedChild&) = delete;

    void send(const std::string& line) {
        const std::string bytes = line + "\n";
#ifdef _WIN32
        DWORD n = 0;
        WriteFile(stdin_, bytes.data(), static_cast<DWORD>(bytes.size()), &n, nullptr);
#else
        const ssize_t n = write(stdin_, bytes.data(), bytes.size());
        (void)n;
#endif
    }

    // End of input for the child.
    void closeStdin() {
        if (stdinClosed_) return;
        stdinClosed_ = true;
#ifdef _WIN32
        CloseHandle(stdin_);
#else
        close(stdin_);
#endif
    }

    // Nobody reads the child's stdout from here on: its next write fails.
    void closeStdout() {
        if (stdoutClosed_) return;
        stdoutClosed_ = true;
        stopReading_ = true;
        if (reader_.joinable()) reader_.join();
#ifdef _WIN32
        CloseHandle(stdout_);
#else
        close(stdout_);
#endif
    }

    // True when the child exited on its own within `limit`.
    bool waitExit(std::chrono::milliseconds limit) {
        if (exited_) return true;
#ifdef _WIN32
        if (WaitForSingleObject(process_, static_cast<DWORD>(limit.count())) != WAIT_OBJECT_0) return false;
        DWORD code = 0;
        GetExitCodeProcess(process_, &code);
        exitCode_ = static_cast<int>(code);
#else
        const auto deadline = std::chrono::steady_clock::now() + limit;
        int status = 0;
        while (waitpid(pid_, &status, WNOHANG) != pid_) {
            if (std::chrono::steady_clock::now() >= deadline) return false;
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        exitCode_ = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + (WIFSIGNALED(status) ? WTERMSIG(status) : 0);
#endif
        exited_ = true;
        return true;
    }
    int exitCode() const { return exitCode_; }

    // True once every writer of the child's stdout has closed it, within `limit`.
    bool waitStdoutEof(std::chrono::milliseconds limit) {
        const auto deadline = std::chrono::steady_clock::now() + limit;
        while (!stdoutEof_ && std::chrono::steady_clock::now() < deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        return stdoutEof_;
    }

private:
    void readLoop() {
        std::ostream sink(&out);
        char buf[4096];
        while (!stopReading_) {
#ifdef _WIN32
            DWORD available = 0;
            if (!PeekNamedPipe(stdout_, nullptr, 0, nullptr, &available, nullptr)) {
                stdoutEof_ = true;  // no writer is left
                return;
            }
            if (available == 0) {
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
                continue;
            }
            DWORD n = 0;
            if (!ReadFile(stdout_, buf, std::min<DWORD>(available, sizeof(buf)), &n, nullptr) || n == 0) return;
            sink.write(buf, n);
#else
            struct pollfd p {};
            p.fd = stdout_;
            p.events = POLLIN;
            if (poll(&p, 1, 20) <= 0) continue;
            const ssize_t n = read(stdout_, buf, sizeof(buf));
            if (n <= 0) {
                stdoutEof_ = n == 0;
                return;
            }
            sink.write(buf, n);
#endif
        }
    }

#ifdef _WIN32
    HANDLE process_ = nullptr;
    HANDLE stdin_ = nullptr;
    HANDLE stdout_ = nullptr;
#else
    pid_t pid_ = -1;
    int stdin_ = -1;
    int stdout_ = -1;
#endif
    std::thread reader_;
    std::atomic<bool> stopReading_{false};
    std::atomic<bool> stdoutEof_{false};
    bool stdinClosed_ = false;
    bool stdoutClosed_ = false;
    bool exited_ = false;
    int exitCode_ = -1;
};

class GaiacppStdioBinary : public ::testing::Test {
protected:
    void SetUp() override {
        std::mt19937_64 rng(std::random_device{}());
        dir_ = fs::weakly_canonical(fs::temp_directory_path()) / ("gaiacpp-stdio-bin-" + std::to_string(rng()));
        fs::create_directories(dir_ / "work");
    }
    void TearDown() override {
        std::error_code ec;
        fs::remove_all(dir_, ec);
    }

    // Runs `gaiacpp --stdio <flags>` with `lines` on stdin, then end of input.
    ProcessResult run(const std::string& baseUrl, const std::string& flags, const std::vector<std::string>& lines) {
        {
            std::ofstream in(dir_ / "work" / "stdin.txt", std::ios::binary);
            for (const auto& l : lines) in << l << "\n";
        }
        std::string bin = GAIACPP_BIN;
        if (bin.find(' ') != std::string::npos) bin = "\"" + bin + "\"";
        const std::string cmd = bin + " --stdio " + flags + " < stdin.txt";
        // The endpoint arrives the way the terminal UI passes it; the missing
        // config path keeps the user's own settings out.
        const std::map<std::string, std::string> env = {
            {"LEMONADE_BASE_URL", baseUrl + "/api/v1"},
            {"GAIACPP_CONFIG", (dir_ / "no-config.json").string()}};
        return ProcessRunner::run(cmd, 120000, (dir_ / "work").string(), env, 1 << 20);
    }

    // Starts `gaiacpp --stdio <flags>` in `cwd` with its stdin and stdout on pipes.
    std::unique_ptr<PipedChild> spawn(const std::string& baseUrl, const std::vector<std::string>& flags = {},
                                      fs::path cwd = {}) {
        std::vector<std::string> argv = {GAIACPP_BIN, "--stdio", "--model", "mock-model", "--native-tools",
                                         "always", "--no-memory", "--no-skills", "--no-web"};
        argv.insert(argv.end(), flags.begin(), flags.end());
        const std::map<std::string, std::string> env = {
            {"LEMONADE_BASE_URL", baseUrl + "/api/v1"},
            {"GAIACPP_CONFIG", (dir_ / "no-config.json").string()}};
        return std::make_unique<PipedChild>(argv, env, cwd.empty() ? dir_ / "work" : cwd, dir_ / "stderr.txt");
    }

    // Everything the child said, for a failure message.
    std::string seen(PipedChild& child) {
        std::string text = "stdout:";
        for (const auto& l : child.out.lines()) text += "  " + l;
        return text + " stderr: " + stderrText();
    }

    std::string stderrText() {
        std::ifstream in(dir_ / "stderr.txt", std::ios::binary);
        return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
    }

    static std::vector<std::string> splitLines(const std::string& text) {
        std::vector<std::string> lines;
        std::string line;
        for (char c : text) {
            if (c == '\n') {
                if (!line.empty()) lines.push_back(line);
                line.clear();
            } else {
                line.push_back(c);
            }
        }
        if (!line.empty()) lines.push_back(line);
        return lines;
    }

    fs::path dir_;
};

const char* const kQuietFlags = "--model mock-model --native-tools always --no-memory --no-skills --no-web";

}  // namespace

TEST_F(GaiacppStdioBinary, SpeaksTheProtocolOverRealPipes) {
    bench::MockLlmServer mock;
    mock.pushResponse(toolCall("file_write", {{"path", "made.txt"}, {"content", "hi"}}));
    mock.pushResponse(answer("all done"));
    mock.pushResponse(answer("second answer"));
    const ProcessResult r = run(mock.baseUrl(), std::string(kQuietFlags) + " --full-access --accept-edits --dev",
                                {json{{"gaia_query", "write a\nfile"}}.dump(),
                                 json{{"gaia_query", std::string("\0gaia:clear_conversation\0", 25)}}.dump(),
                                 "a plain second question"});
    ASSERT_FALSE(r.timedOut) << r.stderr_output;
    ASSERT_EQ(r.exitCode, 0) << r.stderr_output;

    const auto events = parseEvents(splitLines(r.stdout_output));
    expectCanonical(events);
    ASSERT_FALSE(events.empty());
    EXPECT_EQ(events[0]["type"], "status");
    EXPECT_EQ(events[0]["model_id"], "mock-model");
    EXPECT_EQ(events[0]["lemonade_reachable"], true);
    EXPECT_EQ(events[0]["lemonade_base_url"], mock.baseUrl() + "/api/v1");
    EXPECT_FALSE(events[0]["agent_version"].get<std::string>().empty());

    std::vector<json> terminals;
    size_t prompts = 0;
    for (const auto& e : events) {
        if (isTerminal(e)) terminals.push_back(e);
        prompts += e.value("type", "") == "needs_confirmation" ? 1 : 0;
    }
    ASSERT_EQ(terminals.size(), 3u) << r.stdout_output;
    EXPECT_EQ(terminals[0]["answer"], "all done");
    EXPECT_EQ(terminals[1]["answer"], "conversation_cleared");
    EXPECT_EQ(terminals[2]["answer"], "second answer");
    EXPECT_EQ(prompts, 0u) << "--full-access turns approval prompts off";
    EXPECT_TRUE(fs::exists(dir_ / "work" / "made.txt"));
    EXPECT_EQ(json::parse(mock.receivedBodies().at(0))["messages"].back()["content"], "write a\nfile");
}

TEST_F(GaiacppStdioBinary, StartsAndAnswersSentinelsWithNoModelServer) {
    const ProcessResult r = run(deadUrl(), kQuietFlags,
                                {json{{"gaia_query", std::string("\0gaia:clear_conversation\0", 25)}}.dump()});
    ASSERT_FALSE(r.timedOut) << r.stderr_output;
    ASSERT_EQ(r.exitCode, 0) << r.stderr_output;
    const auto events = parseEvents(splitLines(r.stdout_output));
    expectCanonical(events);
    ASSERT_EQ(events.size(), 2u) << r.stdout_output;
    EXPECT_EQ(events[0]["type"], "status");
    EXPECT_EQ(events[0]["lemonade_reachable"], false);
    EXPECT_EQ(events[0]["model_id"], "mock-model");
    EXPECT_EQ(events[1]["answer"], "conversation_cleared");
}

TEST_F(GaiacppStdioBinary, ClaudeFlagsAndUnknownFlagsFailLoudly) {
    bench::MockLlmServer mock;
    for (const std::string flag : {"--use-claude", "--claude-model claude-sonnet-5"}) {
        const ProcessResult r = run(mock.baseUrl(), std::string(kQuietFlags) + " " + flag, {});
        EXPECT_EQ(r.exitCode, 2) << flag;
        EXPECT_NE(r.stderr_output.find("no Claude provider"), std::string::npos) << r.stderr_output;
        EXPECT_NE(r.stderr_output.find("--model"), std::string::npos) << "says what to do instead";
        EXPECT_TRUE(r.stdout_output.empty()) << r.stdout_output;
    }
    const ProcessResult r = run(mock.baseUrl(), std::string(kQuietFlags) + " --bypass-permissions", {});
    EXPECT_EQ(r.exitCode, 2);
    EXPECT_NE(r.stderr_output.find("unknown argument '--bypass-permissions'"), std::string::npos) << r.stderr_output;

    // The terminal UI's own flags mean nothing to the other modes.
    std::string bin = GAIACPP_BIN;
    if (bin.find(' ') != std::string::npos) bin = "\"" + bin + "\"";
    const ProcessResult headless = ProcessRunner::run(bin + " -p hi --dev", 60000, (dir_ / "work").string());
    EXPECT_EQ(headless.exitCode, 2);
    EXPECT_NE(headless.stderr_output.find("go with --stdio"), std::string::npos) << headless.stderr_output;
    EXPECT_EQ(mock.requestCount(), 0);
}

// ---- the built program on live pipes ----

TEST_F(GaiacppStdioBinary, AShellCommandThatReadsStdinCannotEatTheNextQuery) {
    bench::MockLlmServer mock;
    const std::string readsStdin = BashTools::detectShell().empty() ? "more" : "cat";
    mock.pushResponse(toolCall("bash_execute", {{"command", readsStdin}}));
    mock.pushResponse(answer("first"));
    mock.pushResponse(answer("second"));
    auto child = spawn(mock.baseUrl());
    child->send(json{{"gaia_query", "read your input"}}.dump());
    // A command that only reads may run unasked; one that asks is approved.
    const json gate = waitEvent(child->out, [](const json& e) {
        return e.is_object() && (e.value("type", "") == "needs_confirmation" || e.value("type", "") == "tool_result");
    });
    ASSERT_TRUE(gate.is_object()) << stderrText();
    if (gate["type"] == "needs_confirmation") {
        child->send(json{{"gaia_control", "tool_decision"}, {"decision", "allow"}, {"confirm_id", gate["confirm_id"]}}
                        .dump());
    }
    child->send(json{{"gaia_query", "and again"}}.dump());
    const json second = waitEvent(child->out, isTerminal, 2);
    ASSERT_TRUE(second.is_object()) << "the second query was lost: " << stderrText();
    EXPECT_EQ(waitEvent(child->out, isTerminal, 1)["answer"], "first");
    EXPECT_EQ(second["answer"], "second");
    EXPECT_EQ(mock.requestCount(), 3);
    child->closeStdin();
    ASSERT_TRUE(child->waitExit(kWait)) << "stdin closed with nothing running";
    EXPECT_EQ(child->exitCode(), 0);
    expectCanonical(parseEvents(child->out.lines()));
}

TEST_F(GaiacppStdioBinary, AClosedStdoutPipeEndsTheProcessCleanly) {
    bench::MockLlmServer mock;
    auto child = spawn(mock.baseUrl());
    ASSERT_TRUE(waitEvent(child->out, [](const json& e) { return e.is_object() && e.contains("model_id"); })
                    .is_object());
    child->closeStdout();
    child->send(json{{"gaia_query", "is anyone reading?"}}.dump());
    ASSERT_TRUE(child->waitExit(kWait)) << "stdin is still open: " << stderrText();
    EXPECT_EQ(child->exitCode(), 0) << "an exit, not a signal";
    EXPECT_NE(stderrText().find("the host left"), std::string::npos) << stderrText();
    EXPECT_EQ(mock.requestCount(), 0);
}

TEST_F(GaiacppStdioBinary, StdinAndStdoutClosingMidTurnEndsTheProcessAtOnce) {
    bench::MockLlmServer mock;
    Release release;
    mock.holdNextResponse(release.promise.get_future().share());
    auto child = spawn(mock.baseUrl());
    child->send(json{{"gaia_query", "think for a long time"}}.dump());
    ASSERT_TRUE(waitRequests(mock, 1)) << stderrText();
    child->closeStdin();
    child->closeStdout();
    // The model has still not answered: the process must not wait for it.
    ASSERT_TRUE(child->waitExit(std::chrono::seconds(15))) << stderrText();
    EXPECT_FALSE(release.done);
    EXPECT_EQ(child->exitCode(), 0);
    EXPECT_NE(stderrText().find("stdin and stdout are both closed"), std::string::npos) << stderrText();
}

TEST_F(GaiacppStdioBinary, StdinClosingMidTurnStillAnswersWhileStdoutIsRead) {
    bench::MockLlmServer mock;
    Release release;
    mock.holdNextResponse(release.promise.get_future().share());
    auto child = spawn(mock.baseUrl());
    child->send(json{{"gaia_query", "one question, then end of input"}}.dump());
    ASSERT_TRUE(waitRequests(mock, 1)) << stderrText();
    child->closeStdin();
    release.now();
    const json final = waitEvent(child->out, isTerminal, 1);
    ASSERT_TRUE(final.is_object()) << stderrText();
    EXPECT_EQ(final["type"], "final") << final.dump();
    EXPECT_FALSE(final["answer"].get<std::string>().empty());
    ASSERT_TRUE(child->waitExit(kWait));
    EXPECT_EQ(child->exitCode(), 0);
}

// On Windows the agent itself cannot run a turn in such a folder yet: its system
// prompt names the folder in the ANSI code page, which is not valid UTF-8 JSON.
#ifndef _WIN32
TEST_F(GaiacppStdioBinary, AcceptEditsWorksInAFolderWithANonAsciiName) {
    const fs::path folder = dir_ / fs::u8path("w\xc3\xb6rk \xc3\xb1");
    fs::create_directories(folder);
    bench::MockLlmServer mock;
    mock.pushResponse(toolCall("file_write", {{"path", "note.txt"}, {"content", "hi"}}));
    mock.pushResponse(answer("Written."));
    auto child = spawn(mock.baseUrl(), {"--accept-edits"}, folder);
    child->send(json{{"gaia_query", "write a note"}}.dump());
    const json final = waitEvent(child->out, isTerminal, 1);
    ASSERT_TRUE(final.is_object()) << stderrText();
    EXPECT_EQ(final["answer"], "Written.") << seen(*child);
    size_t prompts = 0;
    std::string result;
    for (const auto& e : parseEvents(child->out.lines())) {
        prompts += e.value("type", "") == "needs_confirmation" ? 1 : 0;
        if (e.value("type", "") == "tool_result") result = e.dump();
    }
    EXPECT_EQ(prompts, 0u) << "an edit inside the working folder runs without asking";
    EXPECT_EQ(result.find("denied"), std::string::npos) << result;
    child->closeStdin();
    EXPECT_TRUE(child->waitExit(kWait));
}
#endif

// A process a tool leaves running must not keep the host's pipes open: the host
// waits for end of file on them after the agent exits.
TEST_F(GaiacppStdioBinary, ABackgroundProcessDoesNotHoldTheHostsPipesOpen) {
    bench::MockLlmServer mock;
    // Runs until stop.txt appears in the working folder, two minutes at most.
    const std::string background =
        BashTools::detectShell().empty()
            ? "start \"\" /b cmd /c \"for /l %%i in (1,1,120) do @if not exist stop.txt ping -n 2 127.0.0.1 >nul\""
            : "(i=0; while [ ! -f stop.txt ] && [ $i -lt 120 ]; do sleep 1; i=$((i+1)); done) >/dev/null 2>&1 &";
    mock.pushResponse(toolCall("bash_execute", {{"command", background}}));
    mock.pushResponse(answer("started"));
    auto child = spawn(mock.baseUrl(), {"--full-access"});
    child->send(json{{"gaia_query", "start the watcher"}}.dump());
    const json final = waitEvent(child->out, isTerminal, 1);
    ASSERT_TRUE(final.is_object()) << seen(*child);
    EXPECT_EQ(final["answer"], "started") << seen(*child);
    child->closeStdin();
    ASSERT_TRUE(child->waitExit(kWait)) << seen(*child);
    EXPECT_EQ(child->exitCode(), 0);
    const bool eof = child->waitStdoutEof(std::chrono::seconds(10));
    std::ofstream(dir_ / "work" / "stop.txt") << "stop";
    EXPECT_TRUE(eof) << "the background process kept the agent's stdout open";
    // Gone before the folder is removed.
    child->waitStdoutEof(std::chrono::seconds(10));
}
