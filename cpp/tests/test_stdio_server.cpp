// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The TUI <-> flagship stdio protocol, driven the way the Go TUI drives it:
// lines in on a pipe that stays open, canonical events out, one terminal
// event per query. Field names are checked against the shared wire fixture.

#include <gtest/gtest.h>

#include "flagship_agent.h"
#include "stdio_server.h"
#include "support/fake_embedder.h"
#include "support/mock_llm_server.h"

#include <gaia/file_tools.h>

#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <istream>
#include <mutex>
#include <sstream>
#include <streambuf>
#include <thread>

using namespace gaia;
namespace fs = std::filesystem;

namespace {

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
    std::size_t pos_ = 0;
    bool closed_ = false;
    char cur_ = 0;
};

// Collects event lines and lets a test wait for one.
class EventSink : public std::streambuf {
public:
    std::vector<json> events() {
        std::lock_guard<std::mutex> lock(m_);
        return events_;
    }
    json waitFor(const std::string& type, std::size_t nth = 1) {
        std::unique_lock<std::mutex> lock(m_);
        json found;
        cv_.wait_for(lock, std::chrono::seconds(20), [&] {
            std::size_t seen = 0;
            for (const auto& e : events_) {
                if (e.value("type", "") == type && ++seen == nth) {
                    found = e;
                    return true;
                }
            }
            return false;
        });
        return found;
    }

protected:
    int_type overflow(int_type c) override {
        if (c == traits_type::eof()) return c;
        std::lock_guard<std::mutex> lock(m_);
        if (c == '\n') {
            events_.push_back(json::parse(line_));
            line_.clear();
            cv_.notify_all();
        } else {
            line_.push_back(static_cast<char>(c));
        }
        return c;
    }

private:
    std::mutex m_;
    std::condition_variable cv_;
    std::string line_;
    std::vector<json> events_;
};

std::string toolCall(const std::string& id, const std::string& name, const json& args) {
    return json{{"choices", json::array({{{"message",
                                           {{"role", "assistant"},
                                            {"content", nullptr},
                                            {"tool_calls", json::array({{{"id", id},
                                                                         {"type", "function"},
                                                                         {"function", {{"name", name}, {"arguments", args.dump()}}}}})}}}}})}}
        .dump();
}

std::string answer(const std::string& text) {
    return json{{"choices", json::array({{{"message", {{"role", "assistant"}, {"content", text}}}}})}}.dump();
}

json wireFixture() {
    std::ifstream in(std::string(GAIA_REPO_FIXTURES_DIR) + "/stdio/gaia_stdio_wire.json");
    return json::parse(in);
}

class StdioServerTest : public ::testing::Test {
protected:
    fs::path work_;
    bench::MockLlmServer mock_;
    PipeBuf inBuf_;
    EventSink outBuf_;
    std::istream in_{&inBuf_};
    std::ostream out_{&outBuf_};
    std::unique_ptr<FlagshipAgent> agent_;
    std::unique_ptr<StdioServer> server_;
    std::thread thread_;
    int exitCode_ = -1;

    void SetUp() override {
        work_ = fs::weakly_canonical(fs::temp_directory_path()) / "gaia_stdio_server_test";
        std::error_code ec;
        fs::remove_all(work_, ec);
        fs::create_directories(work_);
        FileStateTracker::instance().clear();
    }
    void start(bool fullAccess = false, bool allowParent = false) {
        AgentConfig c = flagshipConfig();
        c.baseUrl = mock_.baseUrl();
        c.modelId = "Gemma-4-E4B-it-GGUF";
        c.silentMode = true;
        c.streaming = false;
        FlagshipOptions o;
        o.workDir = work_.u8string();
        o.allowedPaths = {allowParent ? work_.parent_path().u8string() : o.workDir};
        o.memoryDb = ":memory:";
        o.web = false;
        o.skipDefaultSkillRoots = true;
        o.embedder = gaia_test::fakeEmbed;
        agent_ = std::make_unique<FlagshipAgent>(c, o);
        server_ = std::make_unique<StdioServer>(*agent_, in_, out_, fullAccess, false, "0.1.0");
        thread_ = std::thread([this] { exitCode_ = server_->run(); });
    }
    void TearDown() override {
        inBuf_.close();
        if (thread_.joinable()) thread_.join();
        FileStateTracker::instance().clear();
        std::error_code ec;
        fs::remove_all(work_, ec);
    }
};

}  // namespace

TEST_F(StdioServerTest, StartupStatusCarriesTheStdioExtensionFields) {
    start();
    const json status = outBuf_.waitFor("status");
    ASSERT_FALSE(status.is_null());
    for (const auto& f : wireFixture()["events"]["status"]["stdio_extension"]) {
        EXPECT_TRUE(status.contains(f.get<std::string>())) << f;
    }
    EXPECT_EQ(status["model_id"], "Gemma-4-E4B-it-GGUF");
    EXPECT_TRUE(status["lemonade_reachable"].get<bool>());
}

TEST_F(StdioServerTest, OneQueryOneTerminalEventWithToolEventsBetween) {
    std::ofstream(work_ / "notes.txt") << "remember the milk";
    mock_.pushResponse(toolCall("c1", "read_file", {{"file_path", "notes.txt"}}));
    mock_.pushResponse(answer("It says to remember the milk."));
    start();
    inBuf_.push(json{{"gaia_query", "what do my\nnotes say?"}}.dump());
    const json final = outBuf_.waitFor("final");
    ASSERT_FALSE(final.is_null());
    EXPECT_EQ(final["answer"], "It says to remember the milk.");
    EXPECT_EQ(final["usage"]["steps"], 2);
    const auto events = outBuf_.events();
    const json fixture = wireFixture()["events"];
    bool sawCall = false, sawResult = false;
    for (const auto& e : events) {
        const std::string type = e["type"];
        ASSERT_TRUE(fixture.contains(type)) << type;
        if (type == "tool_call") {
            sawCall = true;
            EXPECT_EQ(e["tool"], "read_file");
            EXPECT_EQ(e["narration"], "Reading notes.txt");
        }
        if (type == "tool_result") {
            sawResult = true;
            EXPECT_NE(e["preview"].get<std::string>().find("remember the milk"), std::string::npos);
        }
    }
    EXPECT_TRUE(sawCall && sawResult);
    // The multi-line query arrived as one question.
    const json body = json::parse(mock_.receivedBodies().front());
    EXPECT_EQ(body["messages"][1]["content"], "what do my\nnotes say?");
}

TEST_F(StdioServerTest, ConfirmationIsAnsweredMidTurnOverStdin) {
    mock_.pushResponse(toolCall("c1", "write_file", {{"file_path", "out.txt"}, {"content", "hi"}}));
    mock_.pushResponse(answer("Wrote it."));
    start();
    inBuf_.push("write hi to out.txt");
    const json prompt = outBuf_.waitFor("needs_confirmation");
    ASSERT_FALSE(prompt.is_null());
    EXPECT_EQ(prompt["action"], "write_file");
    EXPECT_EQ(prompt["always_scope"], "write_file on out.txt");
    ASSERT_TRUE(prompt.contains("confirm_id"));
    EXPECT_FALSE(fs::exists(work_ / "out.txt"));
    inBuf_.push(json{{"gaia_control", "tool_decision"}, {"decision", "allow"}, {"confirm_id", prompt["confirm_id"]}}.dump());
    EXPECT_EQ(outBuf_.waitFor("final")["answer"], "Wrote it.");
    EXPECT_TRUE(fs::exists(work_ / "out.txt"));
}

TEST_F(StdioServerTest, DenyAndStaleDecisionsNeverRunTheTool) {
    mock_.pushResponse(toolCall("c1", "run_shell_command", {{"command", "echo hi > shell.txt"}}));
    mock_.pushResponse(answer("Okay, not running it."));
    start();
    inBuf_.push("make shell.txt");
    const json prompt = outBuf_.waitFor("needs_confirmation");
    ASSERT_FALSE(prompt.is_null());
    EXPECT_FALSE(prompt.contains("always_scope"));  // no narrow "always" for the shell
    inBuf_.push(json{{"gaia_control", "tool_decision"}, {"decision", "allow"}, {"confirm_id", "stale"}}.dump());
    inBuf_.push(json{{"gaia_control", "tool_decision"}, {"decision", "deny"}, {"confirm_id", prompt["confirm_id"]}}.dump());
    EXPECT_EQ(outBuf_.waitFor("final")["answer"], "Okay, not running it.");
    EXPECT_FALSE(fs::exists(work_ / "shell.txt"));
}

TEST_F(StdioServerTest, FullAccessSkipsThePrompt) {
    mock_.pushResponse(toolCall("c1", "write_file", {{"file_path", "fa.txt"}, {"content", "x"}}));
    mock_.pushResponse(answer("Done."));
    start(/*fullAccess=*/true);
    inBuf_.push("write fa.txt");
    EXPECT_EQ(outBuf_.waitFor("final")["answer"], "Done.");
    EXPECT_TRUE(fs::exists(work_ / "fa.txt"));
    for (const auto& e : outBuf_.events()) EXPECT_NE(e["type"], "needs_confirmation");
}

TEST_F(StdioServerTest, SentinelsAndModelCommandEachEndWithAFinal) {
    mock_.setModelsList(R"({"data":[{"id":"Gemma-4-E4B-it-GGUF"},{"id":"Qwen3-8B-GGUF"}]})");
    start();
    inBuf_.push(std::string("\x00gaia:clear_conversation\x00", 25));
    EXPECT_EQ(outBuf_.waitFor("final", 1)["answer"], "conversation_cleared");
    inBuf_.push(std::string("\x00gaia:memory_dump\x00", 18));
    const json dump = json::parse(outBuf_.waitFor("final", 2)["answer"].get<std::string>());
    EXPECT_TRUE(dump["available"].get<bool>());
    EXPECT_TRUE(dump.contains("items"));
    inBuf_.push(std::string("\x00gaia:warm_up\x00", 14));
    EXPECT_EQ(outBuf_.waitFor("final", 3)["answer"], "warmed_up");
    inBuf_.push("/model Qwen3-8B-GGUF");
    EXPECT_NE(outBuf_.waitFor("final", 4)["answer"].get<std::string>().find("Qwen3-8B-GGUF"), std::string::npos);
    EXPECT_EQ(agent_->config().modelId, "Qwen3-8B-GGUF");
}

TEST_F(StdioServerTest, ClosingStdinEndsTheServer) {
    start();
    inBuf_.close();
    thread_.join();
    EXPECT_EQ(exitCode_, 0);
}

TEST_F(StdioServerTest, UnreachableModelEndsTheTurnWithAnErrorEvent) {
    mock_.pushResponse(R"({"error":"model failed to load"})", 500);
    mock_.pushResponse(R"({"error":"model failed to load"})", 500);
    start();
    inBuf_.push("hello?");
    const json err = outBuf_.waitFor("error");
    ASSERT_FALSE(err.is_null());
    EXPECT_NE(err["detail"].get<std::string>().find("500"), std::string::npos) << err.dump();
    for (const auto& e : outBuf_.events()) EXPECT_NE(e["type"], "final");
}

TEST_F(StdioServerTest, AcceptEditsStopsAtTheWorkingFolderBoundary) {
    // A sibling whose name starts with the working folder's name is not inside it.
    const fs::path sibling = fs::path(work_.u8string() + "-secrets");
    fs::create_directories(sibling);
    mock_.pushResponse(toolCall("c1", "write_file", {{"file_path", (sibling / "x.txt").u8string()}, {"content", "x"}}));
    mock_.pushResponse(answer("Asked first."));
    start(false, /*allowParent=*/true);
    inBuf_.push(json{{"gaia_control", "accept_edits"}, {"enabled", true}}.dump());
    inBuf_.push("write x");
    const json prompt = outBuf_.waitFor("needs_confirmation");
    ASSERT_FALSE(prompt.is_null()) << json(outBuf_.events()).dump(1);
    inBuf_.push(json{{"gaia_control", "tool_decision"}, {"decision", "deny"}, {"confirm_id", prompt["confirm_id"]}}.dump());
    EXPECT_EQ(outBuf_.waitFor("final")["answer"], "Asked first.");
    EXPECT_FALSE(fs::exists(sibling / "x.txt"));
    std::error_code ec;
    fs::remove_all(sibling, ec);
}

TEST_F(StdioServerTest, AlwaysIsNotAGrantForTheShell) {
    mock_.pushResponse(toolCall("c1", "run_shell_command", {{"command", "echo one"}}));
    mock_.pushResponse(toolCall("c2", "run_shell_command", {{"command", "echo two"}}));
    mock_.pushResponse(answer("Done."));
    start();
    inBuf_.push("echo twice");
    const json first = outBuf_.waitFor("needs_confirmation");
    ASSERT_FALSE(first.is_null());
    inBuf_.push(json{{"gaia_control", "tool_decision"}, {"decision", "always"}, {"confirm_id", first["confirm_id"]}}.dump());
    // The second call must ask again.
    json second;
    for (int i = 0; i < 200 && second.is_null(); ++i) {
        for (const auto& e : outBuf_.events()) {
            if (e["type"] == "needs_confirmation" && e["confirm_id"] != first["confirm_id"]) second = e;
        }
        if (second.is_null()) std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    ASSERT_FALSE(second.is_null());
    inBuf_.push(json{{"gaia_control", "tool_decision"}, {"decision", "deny"}, {"confirm_id", second["confirm_id"]}}.dump());
    EXPECT_EQ(outBuf_.waitFor("final")["answer"], "Done.");
}

TEST_F(StdioServerTest, ModelCommandRefusesAModelLemonadeDoesNotHave) {
    start();
    inBuf_.push("/model Not-A-Real-Model");
    const json err = outBuf_.waitFor("error");
    ASSERT_FALSE(err.is_null());
    EXPECT_NE(err["detail"].get<std::string>().find("Unknown Lemonade model 'Not-A-Real-Model'"), std::string::npos)
        << err.dump();
    EXPECT_NE(err["detail"].get<std::string>().find("mock-model"), std::string::npos);
    EXPECT_EQ(agent_->config().modelId, "Gemma-4-E4B-it-GGUF");
    for (const auto& e : outBuf_.events()) EXPECT_NE(e["type"], "final");
}

TEST_F(StdioServerTest, ModelSwitchRecomputesTheToolProtocol) {
    mock_.setModelsList(R"({"data":[{"id":"gemma4-it-e2b-FLM"}]})");
    start();
    inBuf_.push("/model gemma4-it-e2b-FLM");
    ASSERT_FALSE(outBuf_.waitFor("final").is_null());
    EXPECT_FALSE(agent_->config().useNativeToolCalls());
    EXPECT_FALSE(agent_->config().streaming);
}

TEST_F(StdioServerTest, MalformedControlIsAnErrorNotACrash) {
    mock_.pushResponse(answer("Still here."));
    start();
    inBuf_.push(json{{"gaia_control", "full_access"}, {"enabled", "yes"}}.dump());
    const json err = outBuf_.waitFor("error");
    ASSERT_FALSE(err.is_null());
    EXPECT_NE(err["detail"].get<std::string>().find("'enabled' must be true or false"), std::string::npos);
    inBuf_.push(json{{"gaia_control", 7}}.dump());
    ASSERT_FALSE(outBuf_.waitFor("error", 2).is_null());
    inBuf_.push("are you alive?");
    EXPECT_EQ(outBuf_.waitFor("final")["answer"], "Still here.");
}

TEST_F(StdioServerTest, MalformedDecisionDeniesTheOpenPrompt) {
    mock_.pushResponse(toolCall("c1", "write_file", {{"file_path", "m.txt"}, {"content", "x"}}));
    mock_.pushResponse(answer("Not written."));
    start();
    inBuf_.push("write m.txt");
    const json prompt = outBuf_.waitFor("needs_confirmation");
    ASSERT_FALSE(prompt.is_null());
    inBuf_.push(json{{"gaia_control", "tool_decision"}, {"decision", true}, {"confirm_id", prompt["confirm_id"]}}.dump());
    EXPECT_EQ(outBuf_.waitFor("final")["answer"], "Not written.");
    EXPECT_FALSE(fs::exists(work_ / "m.txt"));
    // Mid-turn, the complaint is a status line: an error would end the turn early.
    bool sawStatus = false;
    for (const auto& e : outBuf_.events()) {
        EXPECT_NE(e["type"], "error");
        if (e["type"] == "status" && e.value("message", "").find("malformed control") != std::string::npos) sawStatus = true;
    }
    EXPECT_TRUE(sawStatus);
}

TEST_F(StdioServerTest, AlwaysForAnEditCoversThatFileOnly) {
    mock_.pushResponse(toolCall("c1", "write_file", {{"file_path", "a.txt"}, {"content", "1"}}));
    mock_.pushResponse(toolCall("c2", "write_file", {{"file_path", "a.txt"}, {"content", "2"}}));
    mock_.pushResponse(toolCall("c3", "write_file", {{"file_path", "b.txt"}, {"content", "3"}}));
    mock_.pushResponse(answer("Done."));
    start();
    inBuf_.push("write the files");
    const json first = outBuf_.waitFor("needs_confirmation");
    ASSERT_FALSE(first.is_null());
    EXPECT_EQ(first["always_scope"], "write_file on a.txt");
    inBuf_.push(json{{"gaia_control", "tool_decision"}, {"decision", "always"}, {"confirm_id", first["confirm_id"]}}.dump());
    // The second write to a.txt runs without asking; b.txt asks.
    const json second = outBuf_.waitFor("needs_confirmation", 2);
    ASSERT_FALSE(second.is_null());
    EXPECT_EQ(second["always_scope"], "write_file on b.txt");
    std::string content;
    std::getline(std::ifstream(work_ / "a.txt"), content);
    EXPECT_EQ(content, "2");
    inBuf_.push(json{{"gaia_control", "tool_decision"}, {"decision", "deny"}, {"confirm_id", second["confirm_id"]}}.dump());
    EXPECT_EQ(outBuf_.waitFor("final")["answer"], "Done.");
    EXPECT_FALSE(fs::exists(work_ / "b.txt"));
}

TEST_F(StdioServerTest, AcceptEditsStillAsksForHiddenFiles) {
    mock_.pushResponse(toolCall("c1", "write_file", {{"file_path", "plain.txt"}, {"content", "x"}}));
    mock_.pushResponse(toolCall("c2", "write_file", {{"file_path", ".github/workflows/ci.yml"}, {"content", "x"}}));
    mock_.pushResponse(answer("Asked."));
    start();
    inBuf_.push(json{{"gaia_control", "accept_edits"}, {"enabled", true}}.dump());
    inBuf_.push("write both");
    const json prompt = outBuf_.waitFor("needs_confirmation");
    ASSERT_FALSE(prompt.is_null());
    EXPECT_EQ(prompt["args"]["file_path"], ".github/workflows/ci.yml");
    EXPECT_TRUE(fs::exists(work_ / "plain.txt"));  // accepted without a prompt
    inBuf_.push(json{{"gaia_control", "tool_decision"}, {"decision", "deny"}, {"confirm_id", prompt["confirm_id"]}}.dump());
    EXPECT_EQ(outBuf_.waitFor("final")["answer"], "Asked.");
    EXPECT_FALSE(fs::exists(work_ / ".github" / "workflows" / "ci.yml"));
}

TEST_F(StdioServerTest, AFailedModelLoadEndsTheTurnWithAnError) {
    mock_.setLoadStatus(500);
    start();
    inBuf_.push("hello?");
    const json err = outBuf_.waitFor("error");
    ASSERT_FALSE(err.is_null());
    EXPECT_NE(err["detail"].get<std::string>().find("Could not load Gemma-4-E4B-it-GGUF"), std::string::npos) << err.dump();
    EXPECT_EQ(mock_.requestCount(), 0);
}
