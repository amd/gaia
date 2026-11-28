// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// `gaiacpp --stdio`, driven the way the GAIA terminal UI drives it: lines in on
// a pipe that stays open, canonical events out, one terminal event per query.
// Event and control names are checked against tests/fixtures/stdio/gaia_stdio_wire.json.

#include <gtest/gtest.h>

#include <algorithm>
#include <cctype>
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

#include "../agents/gaiacpp/bash_tools.h"
#include "../agents/gaiacpp/gaia_agent.h"
#include "../agents/gaiacpp/stdio_server.h"
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

// What an approval card adds under a command whose program GAIA has no rule for.
const char* const kUncheckedProgram =
    "GAIA can't check where this command reads or writes: GAIA has no rule for what this program reads or "
    "writes. It can reach files outside the folders this chat may use.";

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

// A folder link at `link` to `target`, which may not exist. A junction on Windows: it needs no privilege.
void makeFolderLink(const fs::path& target, const fs::path& link) {
#ifdef _WIN32
    std::error_code ec;
    const bool made = fs::create_directories(target, ec);
    const std::string cmd = "cmd /c mklink /J \"" + link.string() + "\" \"" + target.string() + "\" >nul 2>&1";
    ASSERT_EQ(std::system(cmd.c_str()), 0) << cmd;
    if (made) fs::remove_all(target, ec);
#else
    std::error_code ec;
    fs::create_directory_symlink(target, link, ec);
    ASSERT_FALSE(ec) << ec.message();
#endif
}

const std::string kStopNote =
    "(The user stopped this reply before it finished. Don't continue it unless they ask again.)";

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

// A model server whose reply streams a token every few milliseconds for half a
// minute, so a test can cancel in the middle of it.
class SlowStreamServer {
public:
    SlowStreamServer() {
        server_.Get("/api/v1/health", [](const httplib::Request&, httplib::Response& res) {
            res.set_content(bench::kHealthOk, "application/json");
        });
        server_.Get("/api/v1/models", [](const httplib::Request&, httplib::Response& res) {
            res.set_content(bench::kModelsList, "application/json");
        });
        server_.Post("/api/v1/load", [](const httplib::Request&, httplib::Response& res) {
            res.set_content(bench::kLoadOk, "application/json");
        });
        server_.Post("/api/v1/chat/completions", [this](const httplib::Request&, httplib::Response& res) {
            res.set_chunked_content_provider("text/event-stream", [this](size_t, httplib::DataSink& sink) {
                if (sent_ >= 6000) {
                    sink.done();
                    return true;
                }
                static const std::string event = "data: {\"choices\":[{\"delta\":{\"content\":\"tok \"}}]}\n\n";
                if (!sink.write(event.data(), event.size())) return false;
                ++sent_;
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
                return true;
            });
        });
        port_ = server_.bind_to_any_port("127.0.0.1");
        thread_ = std::thread([this] { server_.listen_after_bind(); });
        server_.wait_until_ready();
    }
    ~SlowStreamServer() {
        server_.stop();
        if (thread_.joinable()) thread_.join();
    }
    std::string baseUrl() const { return "http://127.0.0.1:" + std::to_string(port_); }
    int sent() const { return sent_.load(); }

private:
    httplib::Server server_;
    std::thread thread_;
    int port_ = 0;
    std::atomic<int> sent_{0};
};

size_t countTokens(const std::vector<std::string>& lines) {
    size_t n = 0;
    for (const auto& e : parseEvents(lines)) n += e.is_object() && e.value("type", "") == "token" ? 1 : 0;
    return n;
}

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
    const json wire = wireFixture();  // a range-for over part of a temporary reads freed memory
    for (const auto& f : wire["events"]["status"]["stdio_extension"]) {
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

TEST_F(GaiacppStdio, TheFinalUsageCountsToolCallsAndOmitsAZeroCount) {
    const fs::path a = work_ / "a.txt", b = work_ / "b.txt";
    std::ofstream(a) << "one";
    std::ofstream(b) << "two";
    mock_.pushResponse(toolCall("file_read", {{"path", a.string()}}));
    mock_.pushResponse(toolCall("file_read", {{"path", b.string()}}));
    mock_.pushResponse(answer("one and two"));
    mock_.pushResponse(answer("Nothing to read."));
    start();
    ask("read both");
    const json withTools = waitTerminal(1);
    ASSERT_TRUE(withTools.is_object());
    EXPECT_EQ(withTools["usage"]["steps"], 3);
    EXPECT_EQ(withTools["usage"]["tools_used"], 2) << withTools.dump();

    ask("and now nothing");
    const json plain = waitTerminal(2);
    ASSERT_TRUE(plain.is_object());
    EXPECT_EQ(plain["answer"], "Nothing to read.");
    EXPECT_FALSE(plain["usage"].contains("tools_used")) << plain.dump();
}

TEST_F(GaiacppStdio, ADelegationReportsProgressOnTheHelpersPhaseAndPreviewsItsSummary) {
    const json tasks = json::array({{{"task", "Who owns notes_01.md?"}}, {{"task", "Who owns notes_02.md?"}}});
    mock_.pushResponse(toolCall("delegate_task", {{"tasks", tasks}}));
    mock_.pushResponse(answer("Owner: Ada"));
    mock_.pushResponse(answer("Owner: Li"));
    mock_.pushResponse(answer("Ada and Li."));
    start();
    ask("who owns the notes?");
    const json final = waitTerminal(1);
    ASSERT_TRUE(final.is_object());
    EXPECT_EQ(final["answer"], "Ada and Li.");

    std::vector<std::string> progress;
    json call, result;
    for (const auto& e : events()) {
        const std::string type = e.value("type", "");
        if (type == "status" && e.value("phase", "") == "helpers") progress.push_back(e.value("message", ""));
        if (type == "tool_call") call = e;
        if (type == "tool_result") result = e;
    }
    ASSERT_TRUE(call.is_object());
    EXPECT_EQ(call["narration"], "Handing 2 tasks to helpers");
    ASSERT_EQ(progress.size(), 2u) << "one progress status per helper that ends";
    EXPECT_EQ(progress[0], "1 of 2 helpers done");
    EXPECT_EQ(progress[1], "2 of 2 helpers done");
    ASSERT_TRUE(result.is_object());
    EXPECT_EQ(result["data"]["status"], "success") << result.dump();
    EXPECT_EQ(result["preview"], "2 of 2 helpers finished.");
    EXPECT_EQ(result["preview"], result["data"]["summary"]);
}

TEST_F(GaiacppStdio, ADelegationWhereEveryHelperFailedPreviewsTheSummaryWithoutTheAdvice) {
    const json tasks = json::array({{{"task", "Who owns notes_01.md?"}}, {{"task", "Who owns notes_02.md?"}}});
    mock_.pushResponse(toolCall("delegate_task", {{"tasks", tasks}}));
    mock_.pushResponse(R"({"error":"boom"})", 500);
    mock_.pushResponse(R"({"error":"boom"})", 500);
    start();
    ask("who owns the notes?");
    ASSERT_TRUE(waitTerminal(1).is_object());

    const json result = waitFor("tool_result");
    ASSERT_TRUE(result.is_object());
    EXPECT_EQ(result["data"]["status"], "error") << result.dump();
    ASSERT_TRUE(result["data"].contains("advice"));
    const std::string advice = result["data"]["advice"].get<std::string>();
    EXPECT_NE(result["data"]["error"].get<std::string>().find(advice), std::string::npos);
    EXPECT_EQ(result["preview"], "0 of 2 helpers finished, 2 failed.");
    const json last = waitEvent(
        [](const json& e) { return e.value("type", "") == "status" && e.value("phase", "") == "helpers"; }, 2);
    ASSERT_TRUE(last.is_object());
    EXPECT_EQ(last["message"], "2 of 2 helpers done, 2 failed");
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

// The model server answered; the model ran out of reply limit. Not "did not answer", and no
// flag the terminal UI's user cannot pass.
TEST_F(GaiacppStdio, AReplyThatRunsOutWhileReasoningIsNotBlamedOnTheModelServer) {
    mock_.enableSseStreaming(true);
    mock_.pushSseStream("data: " + json{{"choices", json::array({{{"index", 0},
                                                                  {"delta", {{"reasoning_content", "thinking for a long time"}}},
                                                                  {"finish_reason", "length"}}})}}
                                           .dump() +
                              "\n\ndata: [DONE]\n\n");
    Start s;
    s.streaming = true;
    start(s);
    ask("refactor everything");
    const json terminal = waitTerminal(1);
    ASSERT_TRUE(terminal.is_object());
    EXPECT_EQ(terminal["type"], "final") << terminal.dump();
    const std::string answer = terminal.value("answer", "");
    EXPECT_NE(answer.find("reply limit of"), std::string::npos) << terminal.dump();
    EXPECT_NE(answer.find("Break the request into smaller steps"), std::string::npos) << terminal.dump();
    EXPECT_EQ(terminal.dump().find("--max-tokens"), std::string::npos) << terminal.dump();
    EXPECT_EQ(terminal.dump().find("did not answer"), std::string::npos) << terminal.dump();
    // While it reasoned, the terminal UI was told so: how much, never what.
    bool progress = false;
    for (const auto& e : events()) {
        if (e.value("type", "") != "status" || e.value("phase", "") != "reasoning") continue;
        progress = true;
        EXPECT_EQ(e.value("words", 0), 5) << e.dump();
        EXPECT_EQ(e.dump().find("thinking for"), std::string::npos) << e.dump();
    }
    EXPECT_TRUE(progress) << "no reasoning progress was reported";
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

TEST_F(GaiacppStdio, AnUnparseableJsonLineIsAnErrorNotAPrompt) {
    mock_.pushResponse(answer("asked"));
    start();
    askRaw(R"({"gaia_query": "half a mess)");
    const json refused = waitTerminal(1);
    ASSERT_TRUE(refused.is_object());
    EXPECT_EQ(refused["type"], "error") << refused.dump();
    EXPECT_NE(refused.value("detail", "").find("not valid JSON"), std::string::npos) << refused.dump();
    EXPECT_EQ(mock_.requestCount(), 0) << "the broken line reached the model";

    askRaw("{braces} in plain words? no: but this line is fine");
    askRaw("plain words still work");
    const json asked = waitTerminal(3);
    ASSERT_TRUE(asked.is_object());
    EXPECT_EQ(mock_.requestCount(), 1) << "only the plain-text line is a prompt";
    EXPECT_EQ(asked["answer"], "asked");
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
    EXPECT_EQ(prompt["summary"], out.string() + "\n\nWhat will be saved:\n    hi");
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

TEST_F(GaiacppStdio, AnEditPromptShowsBeforeAndAfterAndItsResultReadsInWords) {
    const fs::path file = work_ / "inventory.py";
    std::ofstream(file) << "count = 1\nname = 'a'\n";
    mock_.pushResponse(toolCall("file_read", {{"path", file.string()}}));
    mock_.pushResponse(
        toolCall("file_edit", {{"path", file.string()}, {"old_string", "count = 1"}, {"new_string", "count = 2"}}));
    mock_.pushResponse(answer("Fixed."));
    start();
    ask("fix the count");
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object());
    EXPECT_EQ(prompt["action"], "file_edit");
    EXPECT_EQ(prompt["summary"],
              file.string() + "\n\nBefore:\n    count = 1\n\nAfter:\n    count = 2");
    decide("allow", prompt["confirm_id"]);
    ASSERT_TRUE(waitTerminal(1).is_object());

    std::vector<std::string> previews;
    for (const auto& e : events()) {
        if (e.value("type", "") == "tool_result") previews.push_back(e.value("preview", ""));
    }
    ASSERT_EQ(previews.size(), 2u);
    EXPECT_EQ(previews[0], "Read 2 lines from inventory.py");
    EXPECT_EQ(previews[1], "Changed 1 place in inventory.py");
    for (const auto& p : previews) EXPECT_EQ(p.find('{'), std::string::npos) << p;
}

TEST_F(GaiacppStdio, ALongWritePromptCountsTheLinesItLeavesOut) {
    std::string content;
    for (int i = 1; i <= 15; ++i) content += "line " + std::to_string(i) + "\n";
    const fs::path out = work_ / "long.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", out.string()}, {"content", content}}));
    mock_.pushResponse(answer("Not written."));
    start();
    ask("write it");
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object());
    const std::string summary = prompt.value("summary", "");
    EXPECT_NE(summary.find("    line 12\n"), std::string::npos) << summary;
    EXPECT_EQ(summary.find("line 13"), std::string::npos) << summary;
    EXPECT_NE(summary.find("[+3 more lines not shown]"), std::string::npos) << summary;
    decide("deny", prompt["confirm_id"]);
    ASSERT_TRUE(waitTerminal(1).is_object());
}

TEST_F(GaiacppStdio, AWritePromptShowsHiddenTextAsCodesAndCountsACutLine) {
    // An ESC sequence could repaint the card and a bidi override reorder it.
    const std::string content = "ok\x1b[2Ksneaky\nabc\xE2\x80\xAE" "cba\n" + std::string(250, 'x') + "\n";
    const fs::path out = work_ / "odd.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", out.string()}, {"content", content}}));
    mock_.pushResponse(answer("Not written."));
    start();
    ask("write it");
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object());
    const std::string summary = prompt.value("summary", "");
    EXPECT_EQ(summary.find('\x1b'), std::string::npos) << summary;
    EXPECT_NE(summary.find("    ok[U+001B][2Ksneaky\n"), std::string::npos) << summary;
    EXPECT_NE(summary.find("abc[U+202E]cba"), std::string::npos) << summary;
    EXPECT_NE(summary.find("    " + std::string(200, 'x') + "… [+50 more characters not shown]"), std::string::npos)
        << summary;
    decide("deny", prompt["confirm_id"]);
    ASSERT_TRUE(waitTerminal(1).is_object());
}

// A zero-width space would make "inv" + U+200B + "oice.txt" read as invoice.txt.
TEST_F(GaiacppStdio, AZeroWidthCharacterInAPathShowsAsACode) {
    const std::string start_of_path = (work_ / "inv").string();
    mock_.pushResponse(
        toolCall("file_write", {{"path", start_of_path + "\xE2\x80\x8B" "oice.txt"}, {"content", "hi"}}));
    mock_.pushResponse(answer("Not written."));
    start();
    ask("write it");
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object());
    EXPECT_EQ(prompt["summary"], start_of_path + "[U+200B]oice.txt\n\nWhat will be saved:\n    hi");
    decide("deny", prompt["confirm_id"]);
    ASSERT_TRUE(waitTerminal(1).is_object());
}

TEST_F(GaiacppStdio, AZeroWidthCharacterInAShellCommandShowsAsACode) {
    // Word joiner in the program, zero-width space in the argument.
    mock_.pushResponse(toolCall("bash_execute", {{"command", "mk\xE2\x81\xA0" "dir inv\xE2\x80\x8B" "oice"}}));
    mock_.pushResponse(answer("Not made."));
    Start s;
    s.shell = true;
    start(s);
    ask("make a folder");
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object());
    // mk + joiner + dir is not mkdir, so the card also says the command could not be checked.
    EXPECT_EQ(prompt["summary"], std::string("mk[U+2060]dir inv[U+200B]oice\n\n") + kUncheckedProgram);
    const std::string scope = prompt.value("always_scope", "");
    for (const char* hidden : {"\xE2\x81\xA0", "\xE2\x80\x8B"}) {
        EXPECT_EQ(scope.find(hidden), std::string::npos) << scope;
        EXPECT_EQ(prompt.value("summary", "").find(hidden), std::string::npos);
    }
    decide("deny", prompt["confirm_id"]);
    ASSERT_TRUE(waitTerminal(1).is_object());
}

// Joiners, direction marks, variation selectors and CR read as nothing inside a
// name, so "inv" + ZWJ + "oice.txt" would pass for invoice.txt. Same lines as
// person_approval.visible_strict() in Python.
TEST_F(GaiacppStdio, LookAlikeCharactersInAPathShowAsCodes) {
    const std::string base = (work_ / "").string();
    const std::vector<std::pair<std::string, std::string>> cases = {
        {"inv\xE2\x80\x8D" "oice.txt", "inv[U+200D]oice.txt"},                // ZWJ
        {"inv\xE2\x80\x8C" "oice.txt", "inv[U+200C]oice.txt"},                // ZWNJ
        {"report\xE2\x80\x8E.txt", "report[U+200E].txt"},                     // LRM
        {"report\xE2\x80\x8F.txt", "report[U+200F].txt"},                     // RLM
        {"\xF0\x9F\x93\x81\xEF\xB8\x8F" "notes.txt", "\xF0\x9F\x93\x81[U+FE0F]notes.txt"},  // emoji + VS16
        {"\xE8\x91\x9B\xF3\xA0\x84\x80.txt", "\xE8\x91\x9B[U+E0100].txt"},    // CJK + VS17
        {"a\r\nb.txt", "a[U+000D][U+000A]b.txt"},                            // CRLF
        {"a\tb.txt", "a[U+0009]b.txt"},                                       // tab
    };
    for (const auto& [name, shown] : cases) {
        mock_.pushResponse(toolCall("file_write", {{"path", base + name}, {"content", "hi"}}));
        mock_.pushResponse(answer("Not written."));
        if (!agent_) start();
        ask("write it");
        const json prompt = waitFor("needs_confirmation", queries_);
        ASSERT_TRUE(prompt.is_object()) << shown;
        EXPECT_EQ(prompt["summary"], base + shown + "\n\nWhat will be saved:\n    hi");
        decide("deny", prompt["confirm_id"]);
        ASSERT_TRUE(waitTerminal(queries_).is_object());
    }
}

TEST_F(GaiacppStdio, AJoinerInAProgramNameShowsAsACodeInTheCardAndItsAlwaysLabel) {
    mock_.pushResponse(toolCall("bash_execute", {{"command", "gi\xE2\x80\x8D" "t status\n\tgi\xE2\x80\x8C" "t log"}}));
    mock_.pushResponse(answer("Not run."));
    Start s;
    s.shell = true;
    start(s);
    ask("check the repo");
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object());
    // A command's words get the name rule; its line breaks and indents stay layout.
    EXPECT_EQ(prompt["summary"], std::string("gi[U+200D]t status\n\tgi[U+200C]t log\n\n") + kUncheckedProgram);
    EXPECT_FALSE(prompt.contains("always_scope")) << "two commands on two lines get no 'always': " << prompt.dump();
    decide("deny", prompt["confirm_id"]);
    ASSERT_TRUE(waitTerminal(1).is_object());

    // One command: its label is one line, and the name rule codes a tab in it too.
    mock_.pushResponse(toolCall("bash_execute", {{"command", "gi\xE2\x80\x8D" "t status\tgi\xE2\x80\x8C" "t"}}));
    mock_.pushResponse(answer("Not run."));
    ask("check the repo again");
    const json single = waitFor("needs_confirmation", 2);
    ASSERT_TRUE(single.is_object());
    EXPECT_EQ(single["always_scope"], "Run `gi[U+200D]t status[U+0009]gi[U+200C]t`");
    decide("deny", single["confirm_id"]);
    ASSERT_TRUE(waitTerminal(2).is_object());
}

// "mkdir a && rm -rf b" must never be offered as "Always allow Run `mkdir b`".
TEST_F(GaiacppStdio, ALineOfSeveralCommandsIsNotOfferedAlways) {
    Start s;
    s.shell = true;
    start(s);
    int query = 0;
    for (const char* chained : {"mkdir made1 && rm -rf made3", "mkdir a; rmdir a", "ls nope || mkdir b", "mkdir c | cat",
                                "(mkdir d)", "mkdir \"$(date +%s)\"", "mkdir e\nrmdir e", "mkdir f &"}) {
        mock_.pushResponse(toolCall("bash_execute", {{"command", chained}}));
        mock_.pushResponse(answer("Not run."));
        ask("run it");
        const json prompt = waitFor("needs_confirmation", ++query);
        ASSERT_TRUE(prompt.is_object()) << chained;
        EXPECT_EQ(prompt.value("summary", "").rfind(chained, 0), 0u) << "the card shows the whole line: " << prompt.dump();
        EXPECT_FALSE(prompt.contains("always_scope")) << chained << ": " << prompt.dump();
        // An "always" sent anyway approves this one run and grants nothing.
        decide("always", prompt["confirm_id"]);
        ASSERT_TRUE(waitTerminal(query).is_object());
    }
    mock_.pushResponse(toolCall("bash_execute", {{"command", "mkdir made1 && rm -rf made3"}}));
    mock_.pushResponse(answer("Not run."));
    ask("run it again");
    const json again = waitFor("needs_confirmation", ++query);
    ASSERT_TRUE(again.is_object()) << "the earlier 'always' must not have been kept";
    decide("deny", again["confirm_id"]);
    ASSERT_TRUE(waitTerminal(query).is_object());

    // One command keeps its offer, and quoted operators are not a chain.
    mock_.pushResponse(toolCall("bash_execute", {{"command", "mkdir 'a && b'"}}));
    mock_.pushResponse(answer("Not run."));
    ask("one more");
    const json single = waitFor("needs_confirmation", ++query);
    ASSERT_TRUE(single.is_object());
    EXPECT_TRUE(single.contains("always_scope")) << single.dump();
    decide("deny", single["confirm_id"]);
    ASSERT_TRUE(waitTerminal(query).is_object());
}

// A command the check can't read says so on its card, and a checked one says nothing more.
TEST_F(GaiacppStdio, AnUncheckedShellCommandSaysSoOnItsCard) {
    Start s;
    s.shell = true;
    start(s);
    int query = 0;
    for (const char* unchecked : {"python3 build.py", "make", "npm test", "bash run.sh", "git commit -m x"}) {
        mock_.pushResponse(toolCall("bash_execute", {{"command", unchecked}}));
        mock_.pushResponse(answer("Not run."));
        ask("run it");
        const json prompt = waitFor("needs_confirmation", ++query);
        ASSERT_TRUE(prompt.is_object()) << unchecked;
        const std::string summary = prompt.value("summary", "");
        EXPECT_EQ(summary.rfind(unchecked, 0), 0u) << summary;
        EXPECT_NE(summary.find("GAIA can't check where this command reads or writes"), std::string::npos) << summary;
        EXPECT_NE(summary.find("It can reach files outside the folders this chat may use"), std::string::npos) << summary;
        decide("deny", prompt["confirm_id"]);
        ASSERT_TRUE(waitTerminal(query).is_object());
    }
    for (const char* checked : {"mkdir reports", "cp a.txt b.txt", "rm -f old.log", "touch x && mv x y"}) {
        mock_.pushResponse(toolCall("bash_execute", {{"command", checked}}));
        mock_.pushResponse(answer("Not run."));
        ask("run it");
        const json prompt = waitFor("needs_confirmation", ++query);
        ASSERT_TRUE(prompt.is_object()) << checked;
        EXPECT_EQ(prompt["summary"], checked) << "a checked command carries no warning";
        decide("deny", prompt["confirm_id"]);
        ASSERT_TRUE(waitTerminal(query).is_object());
    }
}

// Content keeps the prose rule: emoji ZWJ sequences, an emoji's own selector and
// a keycap still read as written.
TEST_F(GaiacppStdio, AWrittenBodyKeepsEmojiJoinersAsWritten) {
    const std::string family = "\xF0\x9F\x91\xA8\xE2\x80\x8D\xF0\x9F\x91\xA9\xE2\x80\x8D\xF0\x9F\x91\xA7";
    const std::string body = "family " + family + "\n\xE2\x9D\xA4\xEF\xB8\x8F and 1\xEF\xB8\x8F\xE2\x83\xA3";
    const fs::path out = work_ / "notes.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", out.string()}, {"content", body}}));
    mock_.pushResponse(answer("Not written."));
    start();
    ask("write it");
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object());
    EXPECT_EQ(prompt["summary"], out.string() + "\n\nWhat will be saved:\n    family " + family +
                                     "\n    \xE2\x9D\xA4\xEF\xB8\x8F and 1\xEF\xB8\x8F\xE2\x83\xA3");
    decide("deny", prompt["confirm_id"]);
    ASSERT_TRUE(waitTerminal(1).is_object());
}

TEST_F(GaiacppStdio, InvisibleCharactersInAnEditShowAsCodes) {
    const fs::path file = work_ / "totals.py";
    std::ofstream(file) << "total = 1\n";
    mock_.pushResponse(toolCall("file_read", {{"path", file.string()}}));
    mock_.pushResponse(toolCall("file_edit", {{"path", file.string()},
                                              {"old_string", "total = 1"},
                                              {"new_string", "total\xE2\x81\xA0 = 2\xEF\xBB\xBF\xC2\xAD"}}));
    mock_.pushResponse(answer("Not changed."));
    start();
    ask("change the total");
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object());
    EXPECT_EQ(prompt["summary"],
              file.string() + "\n\nBefore:\n    total = 1\n\nAfter:\n    total[U+2060] = 2[U+FEFF][U+00AD]");
    decide("deny", prompt["confirm_id"]);
    ASSERT_TRUE(waitTerminal(1).is_object());
}

// Same text and expected line as person_approval.visible() gives in Python.
TEST_F(GaiacppStdio, BidiAndZeroWidthTogetherShowAsThePythonAgentShowsThem) {
    const std::string content =
        "abc\xE2\x80\xAE" "cba\xE2\x80\x8B\xE2\x81\xA6" "x\xE2\x81\xA9 a\xE2\x80\x8D" "b "
        "\xE2\x9D\xA4\xEF\xB8\x8F x\xEF\xB8\x8F \xF3\xA0\x81\x81\xE1\xA0\x8E\xC2\xAD end";
    const fs::path out = work_ / "mixed.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", out.string()}, {"content", content}}));
    mock_.pushResponse(answer("Not written."));
    start();
    ask("write it");
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object());
    // ZWJ and an emoji's own variation selector stay as written; a stray selector is a code.
    EXPECT_EQ(prompt["summary"], out.string() +
                                     "\n\nWhat will be saved:\n"
                                     "    abc[U+202E]cba[U+200B][U+2066]x[U+2069] a\xE2\x80\x8D" "b "
                                     "\xE2\x9D\xA4\xEF\xB8\x8F x[U+FE0F] [U+E0041][U+180E][U+00AD] end");
    decide("deny", prompt["confirm_id"]);
    ASSERT_TRUE(waitTerminal(1).is_object());
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
        // A timeout is nobody's no, and is worded as one.
        EXPECT_NE(result["data"].dump().find(decision == "deny" ? "denied by user" : "nobody could give it"),
                  std::string::npos)
            << result.dump();
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
    EXPECT_EQ(prompt["summary"], "mkdir first") << "the command itself: the prompt's title asks the question";
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

TEST(GaiacppStdioEdits, AnEditIsJudgedWhereItLandsPastEveryLink) {
    std::mt19937_64 rng(std::random_device{}());
    const fs::path base = fs::weakly_canonical(fs::temp_directory_path()) / ("gaiacpp-links-" + std::to_string(rng()));
    const fs::path root = base / "work", outside = base / "outside";
    fs::create_directories(root / "src");
    fs::create_directories(outside / "inner");
    const std::vector<std::string> roots = {root.string()};

    // A link to a folder that isn't there yet.
    makeFolderLink(outside / "not-yet", root / "gone");
    EXPECT_FALSE(fs::exists(root / "gone")) << "the link dangles";
    EXPECT_FALSE(editIsInside((root / "gone" / "new.txt").string(), roots));
    EXPECT_FALSE(editIsInside((root / "gone" / "a" / "b" / "new.txt").string(), roots));
    EXPECT_FALSE(editIsInside((root / "fresh" / ".." / "gone" / "new.txt").string(), roots));

    // A link inside a linked folder, the inner one dangling.
    makeFolderLink(outside, root / "outer");
    makeFolderLink(base / "far", outside / "inner" / "next");
    EXPECT_FALSE(editIsInside((root / "outer" / "inner" / "next" / "new.txt").string(), roots));
    EXPECT_FALSE(editIsInside((root / "outer" / "new.txt").string(), roots));

    // A link that stays inside is still an edit in the folder.
    makeFolderLink(root / "src", root / "alias");
    EXPECT_TRUE(editIsInside((root / "alias" / "new.txt").string(), roots));

#ifndef _WIN32
    // A file link to a file not there yet, directly and through a chain of relative links.
    fs::create_symlink(outside / "new.txt", root / "out.md");
    fs::create_symlink("hop", root / "chain.md");
    fs::create_symlink(fs::path("..") / "outside" / "chained.txt", root / "hop");
    EXPECT_FALSE(editIsInside((root / "out.md").string(), roots));
    EXPECT_FALSE(editIsInside((root / "chain.md").string(), roots));
    // `..` after a link climbs from where the link leads, not from the link's own folder.
    EXPECT_FALSE(editIsInside((root / "outer" / ".." / "escaped.txt").string(), roots));
    // A loop resolves to nowhere, which is never inside.
    fs::create_symlink("loop-b", root / "loop-a");
    fs::create_symlink("loop-a", root / "loop-b");
    EXPECT_FALSE(editIsInside((root / "loop-a").string(), roots));
#endif

    std::error_code ec;
    for (const fs::path& link : {root / "gone", root / "outer", outside / "inner" / "next", root / "alias"}) {
        fs::remove(link, ec);
    }
    fs::remove_all(base, ec);
}

// A folder beside the working one, removed when the test ends.
struct OutsideFolder {
    fs::path dir;
    explicit OutsideFolder(const fs::path& work) : dir(work.parent_path() / (work.filename().u8string() + "-out")) {
        fs::create_directories(dir);
    }
    ~OutsideFolder() {
        std::error_code ec;
        fs::remove_all(dir, ec);
    }
};

bool isFolderPrompt(const json& e) {
    return e.is_object() && e.value("type", "") == "needs_confirmation" &&
           e.value("action", "") == "allow_path_access";
}

TEST_F(GaiacppStdio, AWriteOutsideTheFolderAsksForTheFolderAndAllowOpensItForTheSession) {
    OutsideFolder outside(work_);
    const fs::path target = outside.dir / "new.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", target.string()}, {"content", "one"}}));
    mock_.pushResponse(answer("Written."));
    mock_.pushResponse(toolCall("file_write", {{"path", target.string()}, {"content", "two"}}));
    mock_.pushResponse(answer("Written again."));
    start();
    ask("create it");
    const json folder = waitFor("needs_confirmation", 1);
    ASSERT_TRUE(isFolderPrompt(folder)) << folder.dump();
    EXPECT_NE(folder.value("summary", "").find(target.string()), std::string::npos) << folder.dump();
    EXPECT_FALSE(folder.contains("always_scope")) << "a folder prompt offers no 'always'";
    EXPECT_FALSE(fs::exists(target)) << "nothing is written before the user answers";
    decide("allow", folder["confirm_id"]);
    // The write itself still asks, as any write does.
    const json write = waitFor("needs_confirmation", 2);
    ASSERT_TRUE(write.is_object());
    EXPECT_EQ(write.value("action", ""), "file_write") << write.dump();
    decide("allow", write["confirm_id"]);
    ASSERT_TRUE(waitTerminal(1).is_object());
    EXPECT_TRUE(fs::exists(target));

    // The path stays open for the session: only the write asks next time.
    ask("again");
    const json again = waitFor("needs_confirmation", 3);
    ASSERT_TRUE(again.is_object());
    EXPECT_EQ(again.value("action", ""), "file_write") << again.dump();
    decide("allow", again["confirm_id"]);
    ASSERT_TRUE(waitTerminal(2).is_object());
    EXPECT_EQ(count("needs_confirmation"), 3u);
}

TEST_F(GaiacppStdio, DenyingTheFolderPromptRefusesWithoutNamingAFlagTheTerminalUiLacks) {
    OutsideFolder outside(work_);
    const fs::path target = outside.dir / "new.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", target.string()}, {"content", "one"}}));
    mock_.pushResponse(toolCall("file_write", {{"path", target.string()}, {"content", "one"}}));
    mock_.pushResponse(answer("I couldn't."));
    start();
    ask("create it");
    const json folder = waitFor("needs_confirmation", 1);
    ASSERT_TRUE(isFolderPrompt(folder)) << folder.dump();
    decide("deny", folder["confirm_id"]);
    ASSERT_TRUE(waitTerminal(1).is_object());
    EXPECT_EQ(count("needs_confirmation"), 1u) << "a no is not asked again in the same turn";
    size_t refusals = 0;
    for (const auto& e : events()) {
        if (!e.is_object() || e.value("type", "") != "tool_result") continue;
        const std::string text = e.dump();
        EXPECT_NE(text.find("did not allow"), std::string::npos) << text;
        EXPECT_EQ(text.find("--allow-dir"), std::string::npos) << text;
        ++refusals;
    }
    EXPECT_EQ(refusals, 2u);
    EXPECT_FALSE(fs::exists(target));
}

TEST_F(GaiacppStdio, FullAccessStillAsksBeforeOpeningAnotherFolder) {
    OutsideFolder outside(work_);
    const fs::path target = outside.dir / "new.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", target.string()}, {"content", "one"}}));
    mock_.pushResponse(answer("Written."));
    Start s;
    s.fullAccess = true;
    start(s);
    ask("create it");
    const json folder = waitFor("needs_confirmation", 1);
    ASSERT_TRUE(isFolderPrompt(folder)) << "full access must not open a folder unasked: " << folder.dump();
    EXPECT_FALSE(fs::exists(target));
    decide("allow", folder["confirm_id"]);
    ASSERT_TRUE(waitTerminal(1).is_object());
    EXPECT_EQ(count("needs_confirmation"), 1u) << "the write itself needs no prompt under full access";
    EXPECT_TRUE(fs::exists(target));
}

#ifndef _WIN32
// The checks on a command look in the folders the chat has now, not only the ones it started with.
TEST_F(GaiacppStdio, AFolderTheUserOpensIsSeenByTheCommandChecksAtOnce) {
    // Not under the temp folder: commands may always read system folders.
    struct Scratch {
        fs::path dir = fs::canonical(fs::current_path()) / ("gaiacpp-stdio-out-" + std::to_string(std::random_device{}()));
        ~Scratch() {
            std::error_code ec;
            fs::remove_all(dir, ec);
        }
    } scratch;
    fs::create_directories(scratch.dir);
    std::ofstream(scratch.dir / "sales.csv") << "region,amount\nNorth,5\nSouth,7\n";
    mock_.pushResponse(toolCall("bash_execute", {{"command", "ls " + scratch.dir.string()}}));
    mock_.pushResponse(toolCall(
        "bash_execute", {{"command", "python -c \"import pandas as pd; print(pd.read_csv('sales.csv').sum())\""}}));
    mock_.pushResponse(answer("Done."));
    Start s;
    s.fullAccess = true;
    start(s);
    ask("total the sales");
    const json folder = waitFor("needs_confirmation", 1);
    ASSERT_TRUE(isFolderPrompt(folder)) << folder.dump();
    decide("allow", folder["confirm_id"]);
    ASSERT_TRUE(waitTerminal(1).is_object());

    std::vector<std::string> results;
    for (const auto& e : events()) {
        if (e.is_object() && e.value("type", "") == "tool_result") results.push_back(e.dump());
    }
    ASSERT_EQ(results.size(), 2u);
    EXPECT_NE(results[1].find("sales.csv is a small table: read it with file_read first"), std::string::npos)
        << "the table in the folder just opened was not seen: " << results[1];
}
#endif

TEST_F(GaiacppStdio, AFileInsideTheFolderRaisesNoFolderPrompt) {
    std::ofstream(work_ / "note.txt") << "inside";
    mock_.pushResponse(toolCall("file_read", {{"path", (work_ / "note.txt").string()}}));
    mock_.pushResponse(answer("Read."));
    start();
    ask("read it");
    ASSERT_TRUE(waitTerminal(1).is_object());
    EXPECT_EQ(count("needs_confirmation"), 0u);
}

TEST_F(GaiacppStdio, AcceptEditsDoesNotWriteThroughALinkOutOfTheFolder) {
    const fs::path outside = work_.parent_path() / (work_.filename().u8string() + "-outside");
    makeFolderLink(outside, work_ / "drop");
    const fs::path through = work_ / "drop" / "new.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", through.string()}, {"content", "1"}}));
    mock_.pushResponse(answer("Done."));
    start();
    control({{"gaia_control", "accept_edits"}, {"enabled", true}});
    ask("write it");
    // Accept-edits doesn't cover it: the user is asked about where the write really lands.
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object());
    EXPECT_EQ(prompt.value("action", ""), "allow_path_access") << prompt.dump();
    EXPECT_NE(prompt.value("summary", "").find((outside / "new.txt").string()), std::string::npos) << prompt.dump();
    decide("deny", prompt["confirm_id"]);
    ASSERT_TRUE(waitTerminal(1).is_object());
    EXPECT_EQ(count("needs_confirmation"), 1u);
    bool refused = false;
    for (const auto& e : events()) {
        refused |= e.is_object() && e.value("type", "") == "tool_result" && e.dump().find("Refused") != std::string::npos;
    }
    EXPECT_TRUE(refused) << "the write is refused, not run";
    EXPECT_FALSE(fs::exists(outside / "new.txt"));
    std::error_code ec;
    fs::remove(work_ / "drop", ec);
    fs::remove_all(outside, ec);
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

TEST_F(GaiacppStdio, ARequestCancelledDuringAModelCallIsNotPendingOnTheNextTurn) {
    Release release;
    mock_.holdNextResponse(release.promise.get_future().share());
    start();
    ask("write a long essay about rivers");
    ASSERT_TRUE(waitRequests(mock_, 1));
    control({{"gaia_control", "cancel"}});
    ASSERT_TRUE(logBuf_.waitForText("stopping the running turn"));
    release.now();
    ASSERT_TRUE(waitTerminal(1).is_object());

    mock_.pushResponse(answer("4"));
    ask("what is 2 + 2?");
    const json next = waitTerminal(2);
    ASSERT_TRUE(next.is_object());
    EXPECT_EQ(next["answer"], "4");

    const json messages = json::parse(mock_.receivedBodies().at(1))["messages"];
    ASSERT_EQ(messages.size(), 4u) << messages.dump(2);
    EXPECT_EQ(messages[1]["role"], "user");
    EXPECT_EQ(messages[1]["content"], "write a long essay about rivers");
    EXPECT_EQ(messages[2]["role"], "assistant");
    EXPECT_FALSE(messages[2].contains("tool_calls"));
    const std::string stopped = messages[2]["content"];
    ASSERT_GE(stopped.size(), kStopNote.size());
    EXPECT_EQ(stopped.substr(stopped.size() - kStopNote.size()), kStopNote) << stopped;
    EXPECT_EQ(messages[3]["role"], "user");
    EXPECT_EQ(messages[3]["content"], "what is 2 + 2?");
}

TEST_F(GaiacppStdio, ACancelDuringAToolStepLeavesAValidConversationForTheNextTurn) {
    const fs::path out = work_ / "out.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", out.string()}, {"content", "hi"}}));
    start();
    ask("write it");
    ASSERT_TRUE(waitFor("needs_confirmation").is_object());
    control({{"gaia_control", "cancel"}});
    ASSERT_TRUE(waitTerminal(1).is_object());
    EXPECT_FALSE(fs::exists(out));

    mock_.pushResponse(answer("4"));
    ask("what is 2 + 2?");
    const json next = waitTerminal(2);
    ASSERT_TRUE(next.is_object());
    EXPECT_EQ(next["answer"], "4");

    const json messages = json::parse(mock_.receivedBodies().at(1))["messages"];
    std::vector<std::string> roles;
    for (const auto& m : messages) roles.push_back(m.value("role", ""));
    ASSERT_EQ(roles, (std::vector<std::string>{"system", "user", "assistant", "tool", "assistant", "user"}))
        << messages.dump(2);
    ASSERT_EQ(messages[2]["tool_calls"].size(), 1u);
    EXPECT_EQ(messages[3]["tool_call_id"], messages[2]["tool_calls"][0]["id"]);
    EXPECT_EQ(messages[4]["content"], kStopNote);
    EXPECT_FALSE(messages[4].contains("tool_calls"));
    EXPECT_EQ(messages[5]["content"], "what is 2 + 2?");
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

TEST_F(GaiacppStdio, CancelEndsARunningShellCommand) {
    const bool posix = !BashTools::detectShell().empty();
    const std::string command = posix ? "echo x > started.txt; sleep 60"
                                      : "echo x>started.txt & ping -n 60 127.0.0.1 >nul";
    mock_.pushResponse(toolCall("bash_execute", {{"command", command}, {"timeout_ms", 60000}}));
    Start s;
    s.shell = true;
    s.fullAccess = true;
    start(s);
    ask("wait a minute");
    const auto deadline = std::chrono::steady_clock::now() + kWait;
    while (!fs::exists(work_ / "started.txt") && std::chrono::steady_clock::now() < deadline) {
        std::this_thread::yield();
    }
    ASSERT_TRUE(fs::exists(work_ / "started.txt")) << "the command never started";
    const auto cancelledAt = std::chrono::steady_clock::now();
    control({{"gaia_control", "cancel"}});
    const json terminal = waitTerminal(1);
    ASSERT_TRUE(terminal.is_object()) << "a cancel must end the turn while a command runs";
    EXPECT_LT(std::chrono::steady_clock::now() - cancelledAt, std::chrono::seconds(10));
    EXPECT_EQ(terminal["type"], "final");
    EXPECT_EQ(terminal["answer"], "");
    const json result = waitFor("tool_result");
    ASSERT_TRUE(result.is_object()) << "the cancelled command still reports its result";
    EXPECT_EQ(result["data"].value("cancelled", false), true) << result.dump();
    EXPECT_EQ(mock_.requestCount(), 1);

    mock_.pushResponse(answer("Still here."));
    ask("are you there?");
    const json next = waitTerminal(2);
    ASSERT_TRUE(next.is_object());
    EXPECT_EQ(next["answer"], "Still here.");
}

TEST_F(GaiacppStdio, CancelStopsAStreamedReplyAtOnce) {
    SlowStreamServer slow;
    Start s;
    s.baseUrl = slow.baseUrl();
    s.streaming = true;
    start(s);
    ask("talk for a long time");
    ASSERT_TRUE(outBuf_.waitUntil([](const std::vector<std::string>& lines) { return countTokens(lines) >= 5; }));
    const auto cancelledAt = std::chrono::steady_clock::now();
    control({{"gaia_control", "cancel"}});
    ASSERT_TRUE(logBuf_.waitForText("stopping the running turn"));
    const size_t atCancel = countTokens(outBuf_.lines());
    const json terminal = waitTerminal(1);
    ASSERT_TRUE(terminal.is_object());
    EXPECT_LT(std::chrono::steady_clock::now() - cancelledAt, std::chrono::seconds(3));
    EXPECT_EQ(terminal["type"], "final") << terminal.dump();
    EXPECT_LE(countTokens(outBuf_.lines()), atCancel + 20) << "tokens kept arriving after the cancel";
    EXPECT_LT(slow.sent(), 3000) << "the request was not dropped";
    // Tokens travel in batches, so a slow reader never has hundreds queued.
    EXPECT_LT(countTokens(outBuf_.lines()), static_cast<size_t>(slow.sent()) / 2 + 5);
    std::string text;
    for (const auto& e : events()) {
        if (e.value("type", "") == "token") text += e["delta"].get<std::string>();
    }
    EXPECT_EQ(text.size() % 4, 0u) << "no token was split or lost: " << text.size();
    finish();
}

TEST_F(GaiacppStdio, AToolWhoseChecksThrowStillGivesTheModelAResult) {
    mock_.pushResponse(toolCall("fragile", {{"x", 1}}));
    mock_.pushResponse(answer("It failed, and I heard why."));
    start();
    ASSERT_TRUE(waitFor("status").is_object());
    ToolInfo fragile;
    fragile.name = "fragile";
    fragile.description = "throws before it runs";
    fragile.policy = ToolPolicy::CONFIRM;
    fragile.needsConfirm = [](const json&) -> bool { throw std::runtime_error("locale not supported"); };
    fragile.callback = [](const json&) { return json{{"status", "success"}}; };
    agent_->toolRegistry().registerTool(fragile);
    ask("use the fragile tool");
    const json final = waitTerminal(1);
    ASSERT_TRUE(final.is_object());
    EXPECT_EQ(final["type"], "final") << "one tool's failure must not end the turn: " << final.dump();
    EXPECT_EQ(final["answer"], "It failed, and I heard why.");
    const json result = waitFor("tool_result");
    ASSERT_TRUE(result.is_object()) << "the model was told nothing";
    EXPECT_NE(result["data"].dump().find("locale not supported"), std::string::npos) << result.dump();
    EXPECT_EQ(mock_.requestCount(), 2);
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

TEST_F(GaiacppStdio, ClearingTheConversationDropsItsAlwaysGrants) {
    for (const char* dir : {"one", "two", "three"}) {
        mock_.pushResponse(toolCall("bash_execute", {{"command", std::string("mkdir ") + dir}}));
        mock_.pushResponse(answer("Made."));
    }
    Start s;
    s.shell = true;
    start(s);
    ask("make one");
    const json first = waitFor("needs_confirmation");
    ASSERT_TRUE(first.is_object());
    decide("always", first["confirm_id"]);
    ASSERT_TRUE(waitTerminal(1).is_object());
    ASSERT_FALSE(agent_->allowedToolsStore()->allAllowed().empty()) << "mkdir was granted";

    control({{"gaia_control", "clear_history"}});
    ask("make two");
    const json second = waitFor("needs_confirmation", 2);
    ASSERT_TRUE(second.is_object()) << "the grant went with the cleared conversation";
    decide("always", second["confirm_id"]);
    ASSERT_TRUE(waitTerminal(2).is_object());
    EXPECT_TRUE(fs::is_directory(work_ / "two"));

    ask(std::string("\0gaia:clear_conversation\0", 25));
    ASSERT_TRUE(waitTerminal(3).is_object());
    EXPECT_TRUE(agent_->allowedToolsStore()->allAllowed().empty()) << "the sentinel clears grants too";
    ask("make three");
    const json third = waitFor("needs_confirmation", 3);
    ASSERT_TRUE(third.is_object());
    decide("deny", third["confirm_id"]);
    ASSERT_TRUE(waitTerminal(4).is_object());
    EXPECT_FALSE(fs::exists(work_ / "three"));
}

TEST_F(GaiacppStdio, ClearingOneConversationLeavesAnotherSessionsGrants) {
    start();
    // Once the status is out, the agent holds this session's in-memory grants, not the saved ones.
    ASSERT_TRUE(waitFor("status").is_object());
    // A second session: its own agent, server and pipes.
    AgentConfig c;
    c.baseUrl = mock_.baseUrl();
    c.modelId = "mock-model";
    c.silentMode = true;
    c.llmRetries = 0;
    GaiaAgent::Options o;
    o.skills = false;
    o.webTools = false;
    o.documents = false;
    o.vision = false;
    o.memory = false;
    o.workspaceRoots = {work_.string()};
    GaiaAgent otherAgent(c, o);
    otherAgent.setConfig(c);
    PipeBuf otherIn;
    LineSink otherOut, otherLog;
    std::istream otherInStream(&otherIn);
    std::ostream otherOutStream(&otherOut), otherLogStream(&otherLog);
    StdioOptions so;
    so.editRoots = {work_.string()};
    so.log = &otherLogStream;
    StdioServer other(otherAgent, otherInStream, otherOutStream, so);
    std::thread otherThread([&] { other.run(); });
    struct Joiner {
        PipeBuf& in;
        std::thread& t;
        ~Joiner() {
            in.close();
            if (t.joinable()) t.join();
        }
    } joiner{otherIn, otherThread};
    ASSERT_TRUE(::waitEvent(otherOut, [](const json& e) { return e.is_object() && e.value("type", "") == "status"; })
                    .is_object());
    otherAgent.allowedToolsStore()->addAlwaysAllowed("bash_execute:mkdir");
    agent_->allowedToolsStore()->addAlwaysAllowed("bash_execute:mkdir");
    ASSERT_NE(otherAgent.allowedToolsStore(), agent_->allowedToolsStore()) << "each session keeps its own grants";

    ask(std::string("\0gaia:clear_conversation\0", 25));
    ASSERT_TRUE(waitTerminal(1).is_object());
    EXPECT_TRUE(agent_->allowedToolsStore()->allAllowed().empty());
    EXPECT_EQ(otherAgent.allowedToolsStore()->allAllowed(), std::vector<std::string>{"bash_execute:mkdir"})
        << "the other session's grant is untouched";
}

TEST_F(GaiacppStdio, MemoryDumpSentinelAnswersWithTheStoredMemories) {
    {
        MemoryStore store((work_ / "memory.db").string());
        store.remember("likes green tea", "preference");
        store.remember("the build runs on ninja", "fact", "work", "ninja");
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
    EXPECT_EQ(dump["stats"]["entity_count"], 1) << "counted from the store";
    EXPECT_FALSE(dump["stats"].contains("avg_confidence")) << "the store keeps no confidence to report";
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
        EXPECT_FALSE(item.contains("confidence")) << "not tracked, so not sent as a number";
        EXPECT_FALSE(item.contains("last_used"));
        if (item.contains("entity")) EXPECT_EQ(item["entity"], "ninja");
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
    const json wire = wireFixture();  // a range-for over part of a temporary reads freed memory
    for (const auto& f : wire["events"]["status"]["stdio_extension"]) {
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
    // Cut off mid-object: neither a control nor something to ask the model.
    askRaw(R"({"gaia_control": "full_access", "enabled": tru)");
    const json asked = waitTerminal(1);
    ASSERT_TRUE(asked.is_object());
    EXPECT_EQ(asked["type"], "error") << asked.dump();
    EXPECT_EQ(events().size(), 2u) << "the ping and the error: no control wrote to stdout";
    EXPECT_EQ(mock_.requestCount(), 0);

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

TEST_F(GaiacppStdio, AnApprovalWithoutItsIdApprovesNothing) {
    const fs::path out = work_ / "late.txt";
    mock_.pushResponse(toolCall("file_write", {{"path", out.string()}, {"content", "hi"}}));
    mock_.pushResponse(answer("Not written."));
    start();
    ASSERT_TRUE(waitFor("status").is_object());
    ask("write it");
    const json prompt = waitFor("needs_confirmation");
    ASSERT_TRUE(prompt.is_object());
    // A yes with no id could be a late answer to an approval that already closed.
    control({{"gaia_control", "tool_decision"}, {"decision", "allow"}});
    control({{"gaia_control", "tool_decision"}, {"decision", "always"}});
    ASSERT_TRUE(logBuf_.waitForText("decision with no confirm_id"));
    EXPECT_FALSE(fs::exists(out)) << "an id-less yes approved the open prompt";
    // A no needs no id: it can only make the agent do less.
    control({{"gaia_control", "tool_decision"}, {"decision", "deny"}});
    ASSERT_TRUE(waitTerminal(1).is_object());
    EXPECT_FALSE(fs::exists(out));
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

TEST_F(GaiacppStdioBinary, CancelStopsAStreamedReplyAtOnceOverPipes) {
    SlowStreamServer slow;
    auto child = spawn(slow.baseUrl());
    child->send(json{{"gaia_query", "talk for a long time"}}.dump());
    ASSERT_TRUE(child->out.waitUntil([](const std::vector<std::string>& lines) { return countTokens(lines) >= 5; }))
        << seen(*child);
    const auto cancelledAt = std::chrono::steady_clock::now();
    const size_t atCancel = countTokens(child->out.lines());
    child->send(json{{"gaia_control", "cancel"}}.dump());
    const json terminal = waitEvent(child->out, isTerminal, 1);
    ASSERT_TRUE(terminal.is_object()) << seen(*child);
    EXPECT_LT(std::chrono::steady_clock::now() - cancelledAt, std::chrono::seconds(3));
    EXPECT_EQ(terminal["type"], "final");
    // A second or so of tokens at most, where the whole reply is 6000.
    EXPECT_LE(countTokens(child->out.lines()), atCancel + 300);
    child->closeStdin();
    EXPECT_TRUE(child->waitExit(kWait));
}

TEST_F(GaiacppStdioBinary, CancelEndsARunningShellCommandOverPipes) {
    bench::MockLlmServer mock;
    const bool posix = !BashTools::detectShell().empty();
    const std::string command = posix ? "echo x > started.txt; sleep 90"
                                      : "echo x>started.txt & ping -n 90 127.0.0.1 >nul";
    mock.pushResponse(toolCall("bash_execute", {{"command", command}, {"timeout_ms", 120000}}));
    auto child = spawn(mock.baseUrl(), {"--full-access"});
    child->send(json{{"gaia_query", "wait a while"}}.dump());
    const fs::path started = dir_ / "work" / "started.txt";
    const auto deadline = std::chrono::steady_clock::now() + kWait;
    while (!fs::exists(started) && std::chrono::steady_clock::now() < deadline) std::this_thread::yield();
    ASSERT_TRUE(fs::exists(started)) << seen(*child);
    const auto cancelledAt = std::chrono::steady_clock::now();
    child->send(json{{"gaia_control", "cancel"}}.dump());
    const json terminal = waitEvent(child->out, isTerminal, 1);
    ASSERT_TRUE(terminal.is_object()) << "no terminal event after the cancel: " << seen(*child);
    EXPECT_LT(std::chrono::steady_clock::now() - cancelledAt, std::chrono::seconds(10));
    EXPECT_EQ(terminal["type"], "final");
    child->closeStdin();
    EXPECT_TRUE(child->waitExit(kWait));
}

TEST_F(GaiacppStdioBinary, ATurnInAFolderWithANonAsciiNameRunsShellCommandsAndReads) {
    // Cyrillic is outside the Western code page, where a narrow path fails.
    const std::string cafe = "\xd0\xbf\xd1\x80\xd0\xbe\xd0\xb5\xd0\xba\xd1\x82 caf\xc3\xa9",
                      nihon = "\xe6\x97\xa5\xe6\x9c\xac data";
    const fs::path folder = dir_ / fs::u8path(cafe);
    fs::create_directories(folder);
    fs::create_directories(dir_ / fs::u8path(nihon));
    std::ofstream(folder / "note.txt") << "remember the milk";
    const bool posix = !BashTools::detectShell().empty();
    const std::string pwd = posix ? "pwd" : "cd";
    bench::MockLlmServer mock;
    mock.pushResponse(toolCall("bash_execute", {{"command", pwd}}));
    mock.pushResponse(toolCall("file_read", {{"path", "note.txt"}}));
    mock.pushResponse(toolCall("bash_execute", {{"command", "cd \"../" + nihon + "\" && " + pwd}}));
    mock.pushResponse(toolCall("bash_execute", {{"command", "cd \"../" + cafe + "\" && " + pwd}}));
    mock.pushResponse(answer("All four worked."));
    auto child = spawn(mock.baseUrl(), {"--full-access", "--allow-dir", ".."}, folder);
    child->send(json{{"gaia_query", "look around"}}.dump());
    const json final = waitEvent(child->out, isTerminal, 1);
    ASSERT_TRUE(final.is_object()) << seen(*child);
    EXPECT_EQ(final["type"], "final") << seen(*child);
    EXPECT_EQ(final["answer"], "All four worked.") << seen(*child);

    std::vector<json> results;
    for (const auto& e : parseEvents(child->out.lines())) {
        if (e.value("type", "") == "tool_result") results.push_back(e["data"]);
    }
    ASSERT_EQ(results.size(), 4u) << "every tool call reports a result: " << seen(*child);
    EXPECT_EQ(results[0].value("exit_code", -1), 0) << results[0].dump();
    EXPECT_NE(results[0].value("stdout", "").find(cafe), std::string::npos) << results[0].dump();
    EXPECT_NE(results[0].value("cwd", "").find(cafe), std::string::npos) << results[0].dump();
    EXPECT_NE(results[1].dump().find("remember the milk"), std::string::npos) << results[1].dump();
    EXPECT_EQ(results[2].value("exit_code", -1), 0) << results[2].dump();
    EXPECT_NE(results[2].value("stdout", "").find(nihon), std::string::npos) << results[2].dump();
    EXPECT_NE(results[2].value("cwd", "").find(nihon), std::string::npos) << results[2].dump();
    EXPECT_EQ(results[3].value("exit_code", -1), 0) << results[3].dump();
    EXPECT_NE(results[3].value("cwd", "").find(cafe), std::string::npos) << results[3].dump();
    child->closeStdin();
    EXPECT_TRUE(child->waitExit(kWait));
}

TEST_F(GaiacppStdioBinary, ARefusalNamesTheRealWorkingFolder) {
    bench::MockLlmServer mock;
    mock.pushResponse(toolCall("bash_execute", {{"command", "ls //server/share"}}));
    mock.pushResponse(answer("can't"));
    const ProcessResult r = run(mock.baseUrl(), kQuietFlags, {json{{"gaia_query", "list the share"}}.dump()});
    ASSERT_FALSE(r.timedOut) << r.stderr_output;
    std::string refusal;
    for (const auto& e : parseEvents(splitLines(r.stdout_output))) {
        if (e.is_object() && e.value("type", "") == "tool_result") refusal = e.dump();
    }
    ASSERT_NE(refusal.find("Refused"), std::string::npos) << r.stdout_output;
    EXPECT_EQ(refusal.find("(.)"), std::string::npos) << refusal;
    std::string work = fs::canonical(dir_ / "work").generic_u8string();
#ifdef _WIN32
    // Workspace spells a Windows folder in lower case, as the file system ignores case.
    std::transform(work.begin(), work.end(), work.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
#endif
    EXPECT_NE(refusal.find(work), std::string::npos) << "the folder list names " << work << ": " << refusal;
    EXPECT_EQ(refusal.find("--allow-dir"), std::string::npos) << "gaia-cli has no such flag: " << refusal;
}
