// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Tests for the interactive TUI: the event loop's component tree is driven
// with real ftxui::Events and asserted against real rendered frames. No
// terminal is involved — run() only adds ScreenInteractive on top of the
// handleEvent()/renderFrame() pair exercised here.
//
// The agent talks to an in-process mock LLM server, so these are end-to-end
// through Agent::processQuery() on its worker thread.

#ifdef GAIA_HAS_TUI

#include <gtest/gtest.h>

#include <ftxui/component/event.hpp>

#include <gaia/agent.h>
#include <gaia/security.h>
#include <gaia/tui_app.h>

#include <atomic>
#include <chrono>
#include <filesystem>
#include <future>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>

#include "support/mock_llm_server.h"
#include "support/screen_text.h"

using namespace gaia;
using namespace std::chrono_literals;
namespace fs = std::filesystem;

namespace {

// Agent answers immediately with "the sky is blue".
const std::string kAnswer =
    R"({"choices":[{"message":{"content":"{\"thought\":\"t\",\"goal\":\"g\",\"answer\":\"the sky is blue\"}"}}]})";

// Agent calls the confirm_me tool, then answers on the next turn.
const std::string kToolCall =
    R"({"choices":[{"message":{"content":"{\"thought\":\"t\",\"goal\":\"g\",\"tool\":\"confirm_me\",\"tool_args\":{\"message\":\"hi\"}}"}}]})";

/// Test agent with one ALLOW tool and one CONFIRM tool.
class TuiTestAgent : public Agent {
public:
    explicit TuiTestAgent(const AgentConfig& config) : Agent(config) { init(); }

    std::atomic<int> confirmToolRuns{0};

protected:
    void registerTools() override {
        toolRegistry().registerTool(
            "echo", "Echo the input",
            [](const json& args) -> json { return json{{"echoed", args.value("message", "")}}; },
            {});
        toolRegistry().registerTool(
            "confirm_me", "A tool that needs confirmation",
            [this](const json& args) -> json {
                ++confirmToolRuns;
                return json{{"ran", args.value("message", "")}};
            },
            {}, false, ToolPolicy::CONFIRM);
    }

    std::string getSystemPrompt() const override { return "test agent"; }
};

AgentConfig makeConfig(const std::string& baseUrl) {
    AgentConfig config;
    config.baseUrl = baseUrl;
    config.modelId = "mock-model";
    config.maxSteps = 3;
    config.streaming = false;
    config.silentMode = false;  // exercises the stdin confirm callback TuiApp replaces
    return config;
}

void typeText(TuiApp& app, const std::string& text) {
    for (char c : text) {
        ASSERT_TRUE(app.handleEvent(ftxui::Event::Character(c))) << "input dropped '" << c << "'";
    }
}

void submitText(TuiApp& app, const std::string& text) {
    typeText(app, text);
    app.handleEvent(ftxui::Event::Return);
}

bool contains(const std::string& haystack, const std::string& needle) {
    return haystack.find(needle) != std::string::npos;
}

template <typename Predicate>
bool waitFor(Predicate predicate, std::chrono::milliseconds timeout = 5s) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        if (predicate()) return true;
        std::this_thread::sleep_for(5ms);
    }
    return predicate();
}

/// Unique temp directory for an AllowedToolsStore, removed on destruction.
class TempDir {
public:
    TempDir() {
        static std::atomic<int> counter{0};
        const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
        path_ = fs::temp_directory_path() /
                ("gaia_tui_test_" + std::to_string(stamp) + "_" +
                 std::to_string(counter.fetch_add(1)));
        fs::create_directories(path_);
    }
    ~TempDir() {
        std::error_code ec;
        fs::remove_all(path_, ec);
    }
    std::string str() const { return path_.string(); }

private:
    fs::path path_;
};

} // namespace

// ---------------------------------------------------------------------------
// The regression test: agent output must be visible on the rendered frame.
//
// Before this loop existed, the default interactive mode installed a headless
// element builder and every one of these lines went into a vector nobody read.
// ---------------------------------------------------------------------------

TEST(TuiApp, AgentOutputIsVisibleOnTheRenderedFrame) {
    bench::MockLlmServer mock;
    mock.pushResponse(kAnswer);

    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);

    submitText(app, "why is the sky blue?");
    ASSERT_TRUE(app.waitForIdle(10s)) << "agent turn never finished";

    const std::string frame = app.renderToString(80, 24);
    EXPECT_TRUE(contains(frame, "the sky is blue"))
        << "agent output never reached the screen. Frame was:\n"
        << frame;
    EXPECT_TRUE(contains(frame, "why is the sky blue?")) << "user input was not echoed";
}

TEST(TuiApp, FrameShowsTheStatusBarAndKeyHints) {
    bench::MockLlmServer mock;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);

    const std::string frame = app.renderToString(80, 24);
    EXPECT_TRUE(contains(frame, "GAIA Agent"));
    EXPECT_TRUE(contains(frame, "[ok] ready"));
    EXPECT_TRUE(contains(frame, "enter send"));
    EXPECT_TRUE(contains(frame, "ctrl-c quit"));
}

TEST(TuiApp, RendersAt80x24) {
    bench::MockLlmServer mock;
    mock.pushResponse(kAnswer);
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);

    submitText(app, "hello");
    ASSERT_TRUE(app.waitForIdle(10s));

    const std::string frame = app.renderToString(80, 24);
    const auto lines = gaia_test::visibleLines(frame);
    EXPECT_EQ(lines.size(), 24u);
    for (const auto& line : lines) {
        EXPECT_LE(line.size(), 80u) << "line wider than 80 columns: " << line;
    }
    // The chrome survives at the target size.
    EXPECT_TRUE(contains(frame, "[ok] ready"));
    EXPECT_TRUE(contains(frame, "the sky is blue"));
}

TEST(TuiApp, FrameIsAsciiOnlyIncludingTheModal) {
    bench::MockLlmServer mock;
    mock.pushResponse(kToolCall);
    mock.pushResponse(kAnswer);

    TempDir dir;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    agent.toolRegistry().setAllowedToolsStore(std::make_shared<AllowedToolsStore>(dir.str()));
    TuiApp app(agent);

    for (unsigned char c : app.renderToString(80, 24)) {
        ASSERT_LT(c, 0x80u) << "non-ASCII byte in the idle frame";
    }

    submitText(app, "run the tool");
    ASSERT_TRUE(waitFor([&] { return app.modalVisible(); }));
    const std::string modalFrame = app.renderToString(80, 24);
    for (unsigned char c : modalFrame) {
        EXPECT_LT(c, 0x80u) << "non-ASCII byte in the modal frame";
    }
    for (const auto& line : gaia_test::visibleLines(modalFrame)) {
        EXPECT_LE(line.size(), 80u) << "modal line wider than 80 columns: " << line;
    }

    app.handleEvent(ftxui::Event::Escape);
    ASSERT_TRUE(app.waitForIdle(10s));
}

// ---------------------------------------------------------------------------
// Streaming
// ---------------------------------------------------------------------------

TEST(TuiApp, StreamingTokensAppearIncrementallyInTheFrame) {
    bench::MockLlmServer mock;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);

    // printStreamToken is what LemonadeClient's SSE callback drives.
    agent.console().printStreamToken("The ");
    EXPECT_TRUE(contains(app.renderToString(), "The "));

    agent.console().printStreamToken("sky ");
    agent.console().printStreamToken("is ");
    EXPECT_TRUE(contains(app.renderToString(), "The sky is"));

    agent.console().printStreamToken("blue");
    agent.console().printStreamEnd();
    EXPECT_TRUE(contains(app.renderToString(), "The sky is blue"));
}

// ---------------------------------------------------------------------------
// Tool confirmation modal
// ---------------------------------------------------------------------------

TEST(TuiApp, ConfirmToolRaisesAModalAndApproveRunsTheTool) {
    bench::MockLlmServer mock;
    mock.pushResponse(kToolCall);
    mock.pushResponse(kAnswer);

    TempDir dir;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    auto store = std::make_shared<AllowedToolsStore>(dir.str());
    agent.toolRegistry().setAllowedToolsStore(store);

    TuiApp app(agent);
    submitText(app, "run the tool");

    ASSERT_TRUE(waitFor([&] { return app.modalVisible(); })) << "no confirmation modal appeared";

    const std::string frame = app.renderToString(80, 24);
    EXPECT_TRUE(contains(frame, "tool confirmation required"));
    EXPECT_TRUE(contains(frame, "confirm_me"));
    EXPECT_TRUE(contains(frame, "[y] allow once"));
    EXPECT_TRUE(contains(frame, "[n] deny"));

    // The modal is exclusive: typing does not leak into the input line.
    app.handleEvent(ftxui::Event::Character('z'));
    EXPECT_EQ(app.inputText(), "");

    app.handleEvent(ftxui::Event::Character('y'));
    ASSERT_TRUE(app.waitForIdle(10s));

    EXPECT_EQ(agent.confirmToolRuns.load(), 1);
    EXPECT_FALSE(store->isAlwaysAllowed("confirm_me")) << "allow-once must not persist";
    EXPECT_FALSE(app.modalVisible());
}

// A key typed while the agent was working is delivered *after* the modal
// opens (FTXUI drains its queue before drawing). It must not answer a modal
// the user never saw - 'a' would otherwise permanently whitelist the tool.
TEST(TuiApp, KeysQueuedBeforeTheModalWasDrawnDoNotAnswerIt) {
    bench::MockLlmServer mock;
    mock.pushResponse(kToolCall);
    mock.pushResponse(kAnswer);

    TempDir dir;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    auto store = std::make_shared<AllowedToolsStore>(dir.str());
    agent.toolRegistry().setAllowedToolsStore(store);

    TuiApp app(agent);
    submitText(app, "run the tool");
    ASSERT_TRUE(waitFor([&] { return app.modalVisible(); }));

    // No frame drawn yet: these must be swallowed, not treated as answers.
    app.handleEvent(ftxui::Event::Character('a'));
    app.handleEvent(ftxui::Event::Character('y'));
    EXPECT_TRUE(app.modalVisible()) << "an unseen modal was answered by a queued key";
    EXPECT_FALSE(store->isAlwaysAllowed("confirm_me"));
    EXPECT_EQ(app.inputText(), "") << "modal keys must not leak into the input either";

    // Once the modal has been on screen, the same key decides.
    app.renderFrame();
    app.handleEvent(ftxui::Event::Character('n'));
    ASSERT_TRUE(app.waitForIdle(10s));
    EXPECT_EQ(agent.confirmToolRuns.load(), 0);
    EXPECT_FALSE(store->isAlwaysAllowed("confirm_me"));
}

TEST(TuiApp, ConfirmModalDenyBlocksTheTool) {
    bench::MockLlmServer mock;
    mock.pushResponse(kToolCall);
    mock.pushResponse(kAnswer);

    TempDir dir;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    agent.toolRegistry().setAllowedToolsStore(std::make_shared<AllowedToolsStore>(dir.str()));

    TuiApp app(agent);
    submitText(app, "run the tool");

    ASSERT_TRUE(waitFor([&] { return app.modalVisible(); }));
    app.renderFrame();  // the modal has to be on screen before a key answers it
    app.handleEvent(ftxui::Event::Character('n'));
    ASSERT_TRUE(app.waitForIdle(10s));

    EXPECT_EQ(agent.confirmToolRuns.load(), 0);
    EXPECT_TRUE(contains(app.renderToString(80, 24), "[!] denied"));
}

TEST(TuiApp, ConfirmModalEscapeDenies) {
    bench::MockLlmServer mock;
    mock.pushResponse(kToolCall);
    mock.pushResponse(kAnswer);

    TempDir dir;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    agent.toolRegistry().setAllowedToolsStore(std::make_shared<AllowedToolsStore>(dir.str()));

    TuiApp app(agent);
    submitText(app, "run the tool");

    ASSERT_TRUE(waitFor([&] { return app.modalVisible(); }));
    app.renderFrame();
    app.handleEvent(ftxui::Event::Escape);
    ASSERT_TRUE(app.waitForIdle(10s));

    EXPECT_EQ(agent.confirmToolRuns.load(), 0);
}

TEST(TuiApp, AlwaysAllowPersistsToTheAllowedToolsStore) {
    bench::MockLlmServer mock;
    mock.pushResponse(kToolCall);
    mock.pushResponse(kAnswer);

    TempDir dir;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    auto store = std::make_shared<AllowedToolsStore>(dir.str());
    agent.toolRegistry().setAllowedToolsStore(store);

    TuiApp app(agent);
    submitText(app, "run the tool");

    ASSERT_TRUE(waitFor([&] { return app.modalVisible(); }));
    app.renderFrame();
    app.handleEvent(ftxui::Event::Character('a'));
    ASSERT_TRUE(app.waitForIdle(10s));

    EXPECT_EQ(agent.confirmToolRuns.load(), 1);
    EXPECT_TRUE(store->isAlwaysAllowed("confirm_me"));

    // A fresh store over the same directory sees the persisted decision.
    AllowedToolsStore reloaded(dir.str());
    EXPECT_TRUE(reloaded.isAlwaysAllowed("confirm_me"));
}

TEST(TuiApp, SecondConfirmSkipsTheModalOnceAlwaysAllowed) {
    bench::MockLlmServer mock;
    mock.pushResponse(kToolCall);
    mock.pushResponse(kAnswer);

    TempDir dir;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    auto store = std::make_shared<AllowedToolsStore>(dir.str());
    store->addAlwaysAllowed("confirm_me");
    agent.toolRegistry().setAllowedToolsStore(store);

    TuiApp app(agent);
    submitText(app, "run the tool");
    ASSERT_TRUE(app.waitForIdle(10s));

    EXPECT_EQ(agent.confirmToolRuns.load(), 1);
    EXPECT_FALSE(app.modalVisible());
}

// ---------------------------------------------------------------------------
// TuiConfirmBroker (the hand-off itself)
// ---------------------------------------------------------------------------

TEST(TuiConfirmBroker, ResolvesTheAgentThreadDecision) {
    TuiConfirmBroker broker;
    std::atomic<bool> notified{false};
    broker.setNotifier([&] { notified.store(true); });

    ToolConfirmResult result = ToolConfirmResult::DENY;
    std::thread agentThread(
        [&] { result = broker.request("danger", json{{"path", "/tmp/x"}}); });

    ASSERT_TRUE(waitFor([&] { return broker.hasPending(); }));
    EXPECT_TRUE(notified.load());
    EXPECT_EQ(broker.pending().toolName, "danger");

    EXPECT_TRUE(broker.resolve(broker.pendingId(), ToolConfirmResult::ALWAYS_ALLOW));
    agentThread.join();
    EXPECT_EQ(result, ToolConfirmResult::ALWAYS_ALLOW);
    EXPECT_FALSE(broker.hasPending());
}

TEST(TuiConfirmBroker, RequestFromTheUiThreadIsRefusedInsteadOfDeadlocking) {
    TuiConfirmBroker broker;
    broker.setUiThread(std::this_thread::get_id());

    // Would hang forever if the broker waited for a decision here.
    EXPECT_EQ(broker.request("danger", json::object()), ToolConfirmResult::DENY);
    EXPECT_FALSE(broker.hasPending());
}

TEST(TuiConfirmBroker, AStaleIdCannotAnswerTheCurrentRequest) {
    TuiConfirmBroker broker;

    ToolConfirmResult result = ToolConfirmResult::ALLOW_ONCE;
    std::thread agentThread([&] { result = broker.request("danger", json::object()); });
    ASSERT_TRUE(waitFor([&] { return broker.hasPending(); }));

    const unsigned long long id = broker.pendingId();
    EXPECT_NE(id, 0u);
    EXPECT_FALSE(broker.resolve(0, ToolConfirmResult::ALWAYS_ALLOW)) << "id 0 must never decide";
    EXPECT_FALSE(broker.resolve(id + 7, ToolConfirmResult::ALWAYS_ALLOW))
        << "a decision aimed at another request must not land here";
    EXPECT_TRUE(broker.hasPending());

    EXPECT_TRUE(broker.resolve(id, ToolConfirmResult::DENY));
    EXPECT_FALSE(broker.resolve(id, ToolConfirmResult::ALWAYS_ALLOW))
        << "a second key must not overwrite the decision";
    agentThread.join();
    EXPECT_EQ(result, ToolConfirmResult::DENY);
}

TEST(TuiConfirmBroker, UiThreadIsRefusedEvenWhileAnotherRequestIsPending) {
    TuiConfirmBroker broker;
    broker.setUiThread(std::this_thread::get_id());

    std::thread agentThread([&] { broker.request("slow", json::object()); });
    ASSERT_TRUE(waitFor([&] { return broker.hasPending(); }));

    // The pending request holds the serialisation lock; the UI thread must not
    // queue behind it.
    EXPECT_EQ(broker.request("from-ui", json::object()), ToolConfirmResult::DENY);
    EXPECT_EQ(broker.pending().toolName, "slow") << "the UI-thread call stole the modal";

    broker.resolve(broker.pendingId(), ToolConfirmResult::DENY);
    agentThread.join();
}

TEST(TuiConfirmBroker, ShutdownDeniesPendingAndFutureRequests) {
    TuiConfirmBroker broker;

    ToolConfirmResult pendingResult = ToolConfirmResult::ALLOW_ONCE;
    std::thread agentThread([&] { pendingResult = broker.request("danger", json::object()); });
    ASSERT_TRUE(waitFor([&] { return broker.hasPending(); }));

    broker.shutdown();
    agentThread.join();
    EXPECT_EQ(pendingResult, ToolConfirmResult::DENY) << "shutdown must fail closed";
    EXPECT_EQ(broker.request("danger", json::object()), ToolConfirmResult::DENY);
}

// ---------------------------------------------------------------------------
// Ctrl-C / cancellation
// ---------------------------------------------------------------------------

TEST(TuiApp, CtrlCCancelsTheTurnWithoutExiting) {
    bench::MockLlmServer mock;
    std::promise<void> release;
    mock.holdNextResponse(release.get_future().share());

    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);

    submitText(app, "long running question");
    // Wait until processQuery() is actually in the HTTP call: it resets the
    // cancel flag on entry, so cancelling before that would be swallowed.
    ASSERT_TRUE(waitFor([&] { return mock.requestCount() >= 1; }));

    app.handleEvent(ftxui::Event::CtrlC);
    EXPECT_TRUE(app.cancelling());
    EXPECT_FALSE(app.exitRequested()) << "Ctrl-C during a turn must not quit";
    EXPECT_TRUE(agent.isCancelled());

    const std::string frame = app.renderToString(80, 24);
    EXPECT_TRUE(contains(frame, "[!] cancelling"));
    // The UI must not claim the in-flight call was aborted.
    EXPECT_TRUE(contains(frame, "cancel requested"));

    release.set_value();
    ASSERT_TRUE(app.waitForIdle(10s));
    EXPECT_FALSE(app.exitRequested());
    EXPECT_FALSE(app.cancelling());
}

TEST(TuiApp, SecondCtrlCDuringACancellingTurnExits) {
    bench::MockLlmServer mock;
    std::promise<void> release;
    mock.holdNextResponse(release.get_future().share());

    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);

    submitText(app, "long running question");
    ASSERT_TRUE(waitFor([&] { return mock.requestCount() >= 1; }));

    app.handleEvent(ftxui::Event::CtrlC);
    app.handleEvent(ftxui::Event::CtrlC);
    EXPECT_TRUE(app.exitRequested());

    release.set_value();
    ASSERT_TRUE(app.waitForIdle(10s));
}

TEST(TuiApp, CtrlCWhenIdleRequestsExit) {
    bench::MockLlmServer mock;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);

    EXPECT_FALSE(app.exitRequested());
    app.handleEvent(ftxui::Event::CtrlC);
    EXPECT_TRUE(app.exitRequested());
}

TEST(TuiApp, ExitAndQuitWordsLeaveTheLoop) {
    bench::MockLlmServer mock;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);

    submitText(app, "exit");
    EXPECT_TRUE(app.exitRequested());
    EXPECT_EQ(mock.requestCount(), 0) << "'exit' must not be sent to the model";
}

TEST(TuiApp, EscapeClearsTheInputWhenIdle) {
    bench::MockLlmServer mock;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);

    typeText(app, "half typed");
    EXPECT_EQ(app.inputText(), "half typed");
    app.handleEvent(ftxui::Event::Escape);
    EXPECT_EQ(app.inputText(), "");
    EXPECT_FALSE(app.exitRequested());
}

// ---------------------------------------------------------------------------
// Input behaviour
// ---------------------------------------------------------------------------

TEST(TuiApp, SlashCommandsGoToTheDispatcherAndNotTheModel) {
    bench::MockLlmServer mock;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);

    std::string dispatched;
    app.setCommandDispatcher([&](const std::string& input) {
        if (input.empty() || input[0] != '/') return false;
        dispatched = input;
        app.transcript().addSystemLine("command output for " + input);
        return true;
    });

    submitText(app, "/help");
    ASSERT_TRUE(app.waitForIdle(5s));
    EXPECT_EQ(dispatched, "/help");
    EXPECT_EQ(mock.requestCount(), 0);
    EXPECT_TRUE(contains(app.renderToString(80, 24), "command output for /help"));
}

// A slash command that runs a CONFIRM tool must raise the modal, not wedge the
// UI thread waiting for a decision only the UI thread can give.
TEST(TuiApp, SlashCommandInvokingAConfirmToolRaisesTheModal) {
    bench::MockLlmServer mock;
    TempDir dir;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    agent.toolRegistry().setAllowedToolsStore(std::make_shared<AllowedToolsStore>(dir.str()));

    TuiApp app(agent);
    app.setCommandDispatcher([&](const std::string& input) {
        if (input != "/danger") return false;
        agent.toolRegistry().executeTool("confirm_me", gaia::json{{"message", "from command"}});
        return true;
    });

    submitText(app, "/danger");

    ASSERT_TRUE(waitFor([&] { return app.modalVisible(); }))
        << "the command deadlocked instead of raising the modal";
    EXPECT_TRUE(contains(app.renderToString(80, 24), "confirm_me"));

    app.renderFrame();
    app.handleEvent(ftxui::Event::Character('y'));
    ASSERT_TRUE(app.waitForIdle(5s));
    EXPECT_EQ(agent.confirmToolRuns.load(), 1);
}

TEST(TuiApp, UnknownCommandWithoutADispatcherNamesTheRemedy) {
    bench::MockLlmServer mock;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);

    submitText(app, "/nope");
    ASSERT_TRUE(app.waitForIdle(5s));
    EXPECT_TRUE(contains(app.renderToString(80, 24), "type /help"));
}

TEST(TuiApp, UnknownCommandNamesTheRemedy) {
    bench::MockLlmServer mock;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);

    submitText(app, "/nope");
    ASSERT_TRUE(app.waitForIdle(5s));
    EXPECT_EQ(mock.requestCount(), 0);
    EXPECT_TRUE(contains(app.renderToString(80, 24), "type /help"));
}

TEST(TuiApp, ArrowKeysRecallInputHistory) {
    bench::MockLlmServer mock;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);
    app.setCommandDispatcher([](const std::string& input) { return !input.empty() && input[0] == '/'; });

    submitText(app, "/first");
    ASSERT_TRUE(app.waitForIdle(5s));
    submitText(app, "/second");
    ASSERT_TRUE(app.waitForIdle(5s));
    EXPECT_EQ(app.inputText(), "");

    app.handleEvent(ftxui::Event::ArrowUp);
    EXPECT_EQ(app.inputText(), "/second");
    app.handleEvent(ftxui::Event::ArrowUp);
    EXPECT_EQ(app.inputText(), "/first");
    app.handleEvent(ftxui::Event::ArrowDown);
    EXPECT_EQ(app.inputText(), "/second");
    app.handleEvent(ftxui::Event::ArrowDown);
    EXPECT_EQ(app.inputText(), "");
}

TEST(TuiApp, PrintableKeysGoToTheInputNotToShortcuts) {
    bench::MockLlmServer mock;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);

    // 'q', 'i', 'd' are hub shortcuts elsewhere; in the chat input they are text.
    typeText(app, "quid");
    EXPECT_EQ(app.inputText(), "quid");
    EXPECT_FALSE(app.exitRequested());
}

TEST(TuiApp, SubmittingWhileBusyIsRefusedWithARemedy) {
    bench::MockLlmServer mock;
    std::promise<void> release;
    mock.holdNextResponse(release.get_future().share());

    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);

    submitText(app, "first question");
    ASSERT_TRUE(waitFor([&] { return mock.requestCount() >= 1; }));

    submitText(app, "second question");
    EXPECT_EQ(app.inputText(), "second question") << "the rejected input must be kept";
    EXPECT_TRUE(contains(app.renderToString(80, 24), "esc to cancel"));

    release.set_value();
    ASSERT_TRUE(app.waitForIdle(10s));
}

TEST(TuiApp, EmptySubmitDoesNothing) {
    bench::MockLlmServer mock;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);

    app.handleEvent(ftxui::Event::Return);
    typeText(app, "   ");
    app.handleEvent(ftxui::Event::Return);

    EXPECT_EQ(mock.requestCount(), 0);
    EXPECT_EQ(app.transcript().entryCount(), 0u);
}

TEST(TuiApp, PageUpScrollsBackAndPageDownFollowsAgain) {
    bench::MockLlmServer mock;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);

    for (int i = 0; i < 40; ++i) {
        app.transcript().addSystemLine("line " + std::to_string(i));
    }
    EXPECT_TRUE(contains(app.renderToString(80, 24), "line 39"));

    app.handleEvent(ftxui::Event::PageUp);
    app.handleEvent(ftxui::Event::PageUp);
    const std::string scrolled = app.renderToString(80, 24);
    EXPECT_TRUE(contains(scrolled, "scrolled back"));
    EXPECT_FALSE(contains(scrolled, "line 39"));

    for (int i = 0; i < 4; ++i) app.handleEvent(ftxui::Event::PageDown);
    EXPECT_TRUE(contains(app.renderToString(80, 24), "line 39"));
}

TEST(TuiApp, ScrolledBackViewStaysPutWhileNewOutputArrives) {
    bench::MockLlmServer mock;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    TuiApp app(agent);

    for (int i = 0; i < 40; ++i) {
        app.transcript().addSystemLine("line " + std::to_string(i));
    }
    app.handleEvent(ftxui::Event::PageUp);
    app.handleEvent(ftxui::Event::PageUp);
    const std::string before = app.renderToString(80, 24);

    // The agent keeps talking while the user reads back.
    for (int i = 0; i < 10; ++i) {
        app.transcript().addSystemLine("late " + std::to_string(i));
    }
    const std::string after = app.renderToString(80, 24);

    EXPECT_EQ(gaia_test::visibleLines(before)[3], gaia_test::visibleLines(after)[3])
        << "the transcript scrolled out from under the reader";
    EXPECT_FALSE(contains(after, "late 9"));

    // Submitting a new line follows the newest entry again.
    submitText(app, "exit");
    EXPECT_TRUE(contains(app.renderToString(80, 24), "late 9"));
}

// ---------------------------------------------------------------------------
// Terminal restoration
// ---------------------------------------------------------------------------

TEST(TerminalResetGuard, EmitsResetSequenceWhenArmed) {
    std::ostringstream out;
    { TerminalResetGuard guard(out); }
    EXPECT_EQ(out.str(), TerminalResetGuard::resetSequence());
}

TEST(TerminalResetGuard, DisarmedGuardWritesNothing) {
    std::ostringstream out;
    {
        TerminalResetGuard guard(out);
        guard.disarm();
    }
    EXPECT_EQ(out.str(), "");
}

TEST(TerminalResetGuard, RestoresOnExceptionUnwind) {
    std::ostringstream out;
    try {
        TerminalResetGuard guard(out);
        throw std::runtime_error("loop blew up");
    } catch (const std::runtime_error&) {
    }
    EXPECT_TRUE(contains(out.str(), "\033[?1049l")) << "alternate screen not left";
    EXPECT_TRUE(contains(out.str(), "\033[?25h")) << "cursor not restored";
}

// ---------------------------------------------------------------------------
// Teardown
// ---------------------------------------------------------------------------

TEST(TuiApp, DestructionWhileAConfirmIsPendingDeniesAndUnblocks) {
    bench::MockLlmServer mock;
    mock.pushResponse(kToolCall);
    mock.pushResponse(kAnswer);

    TempDir dir;
    TuiTestAgent agent(makeConfig(mock.baseUrl()));
    agent.toolRegistry().setAllowedToolsStore(std::make_shared<AllowedToolsStore>(dir.str()));

    {
        TuiApp app(agent);
        submitText(app, "run the tool");
        ASSERT_TRUE(waitFor([&] { return app.modalVisible(); }));
        // ~TuiApp must wake the blocked agent thread instead of hanging.
    }

    EXPECT_EQ(agent.confirmToolRuns.load(), 0) << "a torn-down modal must deny";
}

#endif // GAIA_HAS_TUI
