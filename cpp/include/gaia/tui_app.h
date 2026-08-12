// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Interactive fullscreen TUI for a GAIA agent: an FTXUI ScreenInteractive, a
// component tree, an input line with history, a scrollable transcript, and a
// tool-confirmation modal.
//
// The agent runs on a worker thread; every transcript mutation posts a custom
// event back to the screen so tokens appear as they stream.
//
// Ownership: constructing a TuiApp takes over the agent's output handler and
// its tool-confirmation callback. The agent must outlive the TuiApp.
//
// Testability: the component tree is built in the constructor and driven
// through handleEvent()/renderFrame(), so every behaviour below can be
// asserted headlessly with ftxui::Screen::Create() + Render() + ToString();
// run() only adds the terminal.

#pragma once

#ifdef GAIA_HAS_TUI

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <functional>
#include <memory>
#include <mutex>
#include <ostream>
#include <string>
#include <thread>
#include <vector>

#include <ftxui/component/component.hpp>
#include <ftxui/component/event.hpp>
#include <ftxui/component/screen_interactive.hpp>
#include <ftxui/dom/elements.hpp>

#include "gaia/export.h"
#include "gaia/tui_transcript.h"
#include "gaia/types.h"

namespace gaia {

class Agent;

// ---------------------------------------------------------------------------
// TuiConfirmBroker
// ---------------------------------------------------------------------------

/// Hands a CONFIRM-policy tool call from the agent thread to the UI thread and
/// blocks until the user answers the modal.
///
/// The stdin confirm callback that Agent installs by default cannot be used
/// while FTXUI owns the terminal: it writes to stderr over the screen and
/// competes with FTXUI's input thread for stdin. This broker replaces the
/// callback without changing the confirmation contract — a decision is still
/// required, and shutdown() denies rather than allows.
class GAIA_API TuiConfirmBroker {
public:
    struct Request {
        std::string toolName;
        json args;
    };

    /// Called when a request arrives, so the UI thread can redraw.
    using Notifier = std::function<void()>;

    void setNotifier(Notifier notifier);

    /// Identify the thread that answers modals. A request made from that
    /// thread can never be answered, so it is refused immediately instead of
    /// deadlocking the UI.
    void setUiThread(std::thread::id id);

    /// Agent thread: publish the request and block until resolve() or
    /// shutdown(). Returns DENY once shutdown() has been called — no screen
    /// means nobody can approve.
    ToolConfirmResult request(const std::string& toolName, const json& args);

    /// UI thread: the request awaiting a decision, if any.
    bool hasPending() const;
    Request pending() const;

    /// Id of the pending request. Bumped for every request so a decision can
    /// be tied to the modal the user actually saw. 0 = nothing pending.
    unsigned long long pendingId() const;

    /// UI thread: answer request `id`. Returns false — and changes nothing —
    /// when nothing is pending, when the request was already answered, or when
    /// `id` is not the pending request.
    bool resolve(unsigned long long id, ToolConfirmResult result);

    /// Deny the pending request and every future one.
    void shutdown();

    bool isShutdown() const;

private:
    mutable std::mutex mutex_;
    std::condition_variable cv_;
    std::mutex serialMutex_;  // one in-flight request at a time
    bool pendingActive_ = false;
    bool shutdown_ = false;
    unsigned long long pendingId_ = 0;
    unsigned long long nextId_ = 1;
    std::thread::id uiThread_{};
    Request pending_;
    bool resolved_ = false;
    ToolConfirmResult result_ = ToolConfirmResult::DENY;
    Notifier notifier_;
};

// ---------------------------------------------------------------------------
// Terminal reset guard
// ---------------------------------------------------------------------------

/// Emits the terminal-restore escape sequences on destruction unless disarmed.
///
/// FTXUI's Loop already restores the terminal from its destructor, so this is
/// the second layer: it covers an exception thrown outside Loop and a Loop
/// that never gets to run.
class GAIA_API TerminalResetGuard {
public:
    explicit TerminalResetGuard(std::ostream& out);
    ~TerminalResetGuard();

    TerminalResetGuard(const TerminalResetGuard&) = delete;
    TerminalResetGuard& operator=(const TerminalResetGuard&) = delete;

    /// Terminal was restored normally — do nothing on destruction.
    void disarm();

    /// The sequences this guard writes: leave the alternate screen, show the
    /// cursor, reset attributes.
    static const char* resetSequence();

private:
    std::ostream& out_;
    bool armed_ = true;
};

// ---------------------------------------------------------------------------
// TuiApp
// ---------------------------------------------------------------------------

struct TuiAppOptions {
    std::string title = "GAIA Agent";
    std::string prompt = "> ";
};

class GAIA_API TuiApp {
public:
    /// Returns true when the input was handled as a slash command.
    using CommandDispatcher = std::function<bool(const std::string& input)>;

    explicit TuiApp(Agent& agent, TuiAppOptions options = {});
    ~TuiApp();

    TuiApp(const TuiApp&) = delete;
    TuiApp& operator=(const TuiApp&) = delete;

    void setCommandDispatcher(CommandDispatcher dispatcher);

    /// Blocking. Creates the fullscreen screen and runs the event loop until
    /// the user exits. Restores the terminal on normal return and on exception.
    void run();

    // --- Headless surface: the same code path run() drives ---

    /// Render the current frame. This is exactly what the screen renders.
    ftxui::Element renderFrame();

    /// Feed one event to the component tree. Returns true when consumed.
    bool handleEvent(const ftxui::Event& event);

    /// Render into a w x h screen and return its text. Test helper.
    std::string renderToString(int width = 80, int height = 24);

    TuiTranscript& transcript() { return *transcript_; }
    TuiConfirmBroker& confirmBroker() { return *broker_; }

    bool busy() const { return busy_.load(); }
    bool cancelling() const { return cancelling_.load(); }
    bool modalVisible() const { return broker_->hasPending(); }
    /// True once the pending modal has been drawn at least once — only then do
    /// its keys count as an answer.
    bool modalAnswerable() const;
    bool exitRequested() const { return exitRequested_.load(); }
    const std::string& inputText() const { return inputText_; }

    /// Ask the loop to exit (also used by /exit). Safe from any thread.
    void requestExit();

    /// Wait until no agent turn is in flight. Returns false on timeout.
    bool waitForIdle(std::chrono::milliseconds timeout);

private:
    void submit();
    void startQuery(const std::string& query);
    void startCommand(const std::string& command);
    void startWorker(std::function<void()> work);
    void cancelTurn();
    void cancelAndJoinWorker();
    bool onEvent(const ftxui::Event& event);
    bool onModalEvent(const ftxui::Event& event);
    ftxui::Element buildFrame();
    ftxui::Element buildModal() const;
    void postRedraw(TuiChange change);
    void setBusy(bool busy);
    void recallHistory(int delta);
    std::string keyHint() const;

    Agent& agent_;
    TuiAppOptions options_;

    // Owned by the agent once installed; the TuiApp holds a borrowed pointer.
    TuiTranscript* transcript_ = nullptr;
    // shared_ptr so the confirm callback stays valid even if it outlives the app.
    std::shared_ptr<TuiConfirmBroker> broker_;

    CommandDispatcher dispatcher_;

    ftxui::Component input_;
    ftxui::Component root_;
    std::string inputText_;
    int cursorPos_ = 0;

    std::vector<std::string> history_;
    std::size_t historyIndex_ = 0;
    std::string historyDraft_;

    // Transcript scrolling. Following means "pin to the newest entry"; when
    // scrolled back, the anchor is a row index so incoming output does not
    // drag the viewport forward under the reader.
    bool following_ = true;
    int anchorRow_ = 0;

    // Id of the confirmation request the last rendered frame showed.
    unsigned long long renderedModalId_ = 0;

    std::atomic<bool> busy_{false};
    std::atomic<bool> queryInFlight_{false};
    std::atomic<bool> cancelling_{false};
    std::atomic<bool> exitRequested_{false};
    std::thread worker_;
    mutable std::mutex idleMutex_;
    std::condition_variable idleCv_;

    mutable std::mutex screenMutex_;
    ftxui::ScreenInteractive* screen_ = nullptr;
    bool screenExiting_ = false;  // Exit() posted: the task sender is going away
    std::chrono::steady_clock::time_point lastTokenPost_{};
};

} // namespace gaia

#endif // GAIA_HAS_TUI
