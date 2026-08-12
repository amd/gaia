// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#ifdef GAIA_HAS_TUI

#include "gaia/tui_app.h"

#include <algorithm>
#include <iostream>
#include <utility>

#include <ftxui/component/component_options.hpp>
#include <ftxui/dom/node.hpp>
#include <ftxui/screen/screen.hpp>

#include "gaia/agent.h"

namespace gaia {

using namespace ftxui;

namespace {

std::string trimCopy(const std::string& s) {
    auto start = s.find_first_not_of(" \t\r\n");
    if (start == std::string::npos) return "";
    auto end = s.find_last_not_of(" \t\r\n");
    return s.substr(start, end - start + 1);
}

constexpr int kScrollStep = 5;
constexpr auto kTokenRedrawInterval = std::chrono::milliseconds(33);
constexpr std::size_t kArgsPreviewLimit = 240;

} // namespace

// ---------------------------------------------------------------------------
// TuiConfirmBroker
// ---------------------------------------------------------------------------

void TuiConfirmBroker::setNotifier(Notifier notifier) {
    std::lock_guard<std::mutex> lock(mutex_);
    notifier_ = std::move(notifier);
}

void TuiConfirmBroker::setUiThread(std::thread::id id) {
    std::lock_guard<std::mutex> lock(mutex_);
    uiThread_ = id;
}

ToolConfirmResult TuiConfirmBroker::request(const std::string& toolName, const json& args) {
    {
        // Checked before serialMutex_: the UI thread must not block there
        // either. Fail closed rather than hang - the UI thread cannot both
        // wait for a decision and draw the modal that produces one.
        std::lock_guard<std::mutex> lock(mutex_);
        if (uiThread_ != std::thread::id{} && uiThread_ == std::this_thread::get_id()) {
            return ToolConfirmResult::DENY;
        }
    }

    // One modal at a time: a second CONFIRM tool waits for the first decision.
    std::lock_guard<std::mutex> serial(serialMutex_);

    Notifier notifier;
    {
        std::unique_lock<std::mutex> lock(mutex_);
        if (shutdown_) return ToolConfirmResult::DENY;
        pending_ = Request{toolName, args};
        pendingActive_ = true;
        pendingId_ = nextId_++;
        resolved_ = false;
        result_ = ToolConfirmResult::DENY;
        notifier = notifier_;
    }

    if (notifier) notifier();

    ToolConfirmResult decision = ToolConfirmResult::DENY;
    {
        std::unique_lock<std::mutex> lock(mutex_);
        cv_.wait(lock, [this] { return resolved_ || shutdown_; });
        decision = resolved_ ? result_ : ToolConfirmResult::DENY;
        pendingActive_ = false;
        pendingId_ = 0;
        pending_ = Request{};
    }

    if (notifier) notifier();
    return decision;
}

bool TuiConfirmBroker::hasPending() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return pendingActive_;
}

TuiConfirmBroker::Request TuiConfirmBroker::pending() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return pending_;
}

unsigned long long TuiConfirmBroker::pendingId() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return pendingActive_ ? pendingId_ : 0;
}

bool TuiConfirmBroker::resolve(unsigned long long id, ToolConfirmResult result) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        // id must match: a key pressed before this request existed must not
        // answer it, and a second press must not answer the next one.
        if (!pendingActive_ || resolved_ || id == 0 || id != pendingId_) return false;
        result_ = result;
        resolved_ = true;
    }
    cv_.notify_all();
    return true;
}

void TuiConfirmBroker::shutdown() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        shutdown_ = true;
        pendingActive_ = false;  // the modal is gone; nothing left to answer
        pendingId_ = 0;
    }
    cv_.notify_all();
}

bool TuiConfirmBroker::isShutdown() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return shutdown_;
}

// ---------------------------------------------------------------------------
// TerminalResetGuard
// ---------------------------------------------------------------------------

TerminalResetGuard::TerminalResetGuard(std::ostream& out) : out_(out) {}

TerminalResetGuard::~TerminalResetGuard() {
    if (!armed_) return;
    out_ << resetSequence();
    out_.flush();
}

void TerminalResetGuard::disarm() { armed_ = false; }

const char* TerminalResetGuard::resetSequence() {
    // Leave the alternate screen, show the cursor, reset attributes.
    return "\033[?1049l\033[?25h\033[0m";
}

// ---------------------------------------------------------------------------
// TuiApp — construction
// ---------------------------------------------------------------------------

TuiApp::TuiApp(Agent& agent, TuiAppOptions options)
    : agent_(agent), options_(std::move(options)), broker_(std::make_shared<TuiConfirmBroker>()) {
    auto owned = std::make_unique<TuiTranscript>();
    transcript_ = owned.get();
    agent_.setOutputHandler(std::move(owned));
    transcript_->setModelId(agent_.config().modelId);
    transcript_->setChangeCallback([this](TuiChange change) { postRedraw(change); });

    // Replace the stdin confirm callback Agent installs by default: it writes
    // over the screen and fights FTXUI for stdin. The contract is unchanged —
    // the modal still has to produce a decision.
    auto broker = broker_;
    agent_.setToolConfirmCallback(
        [broker](const std::string& toolName, const json& args) -> ToolConfirmResult {
            return broker->request(toolName, args);
        });
    broker_->setNotifier([this] { postRedraw(TuiChange::STRUCTURAL); });
    broker_->setUiThread(std::this_thread::get_id());

    InputOption inputOption;
    inputOption.content = &inputText_;
    inputOption.cursor_position = &cursorPos_;
    inputOption.multiline = false;
    inputOption.on_enter = [this] { submit(); };
    input_ = Input(inputOption);

    auto container = Container::Vertical({input_});
    auto renderer = Renderer(container, [this] { return buildFrame(); });
    root_ = CatchEvent(renderer, [this](const Event& event) { return onEvent(event); });
}

TuiApp::~TuiApp() {
    broker_->shutdown();  // wake a worker blocked on the modal (fail-closed)
    cancelAndJoinWorker();
    if (transcript_) transcript_->setChangeCallback(nullptr);
}

void TuiApp::cancelAndJoinWorker() {
    if (!worker_.joinable()) return;
    // processQuery() clears the cancel flag on entry, so a single
    // requestCancel() can land in the window before the turn starts and be
    // lost. Repeat until the worker is actually done.
    while (busy_.load()) {
        agent_.requestCancel();
        std::unique_lock<std::mutex> lock(idleMutex_);
        idleCv_.wait_for(lock, std::chrono::milliseconds(100),
                         [this] { return !busy_.load(); });
    }
    worker_.join();
}

void TuiApp::setCommandDispatcher(CommandDispatcher dispatcher) {
    dispatcher_ = std::move(dispatcher);
}

// ---------------------------------------------------------------------------
// Event loop
// ---------------------------------------------------------------------------

void TuiApp::run() {
    broker_->setUiThread(std::this_thread::get_id());

    auto screen = ScreenInteractive::Fullscreen();
    // FTXUI exits the loop on Ctrl-C by default; the TUI needs Ctrl-C to
    // cancel the running turn instead (repl.cpp's SIGINT handler is not
    // installed on this path — two handlers for one key is one too many).
    screen.ForceHandleCtrlC(false);
    screen.ForceHandleCtrlZ(false);

    {
        std::lock_guard<std::mutex> lock(screenMutex_);
        screen_ = &screen;
        screenExiting_ = false;
    }

    // Second layer of terminal restoration: FTXUI's Loop restores from its own
    // destructor, this covers anything thrown outside it.
    TerminalResetGuard guard(std::cout);
    try {
        screen.Loop(root_);
    } catch (...) {
        std::lock_guard<std::mutex> lock(screenMutex_);
        screen_ = nullptr;
        throw;
    }

    {
        std::lock_guard<std::mutex> lock(screenMutex_);
        screen_ = nullptr;
    }
    guard.disarm();

    // Leaving mid-turn: say so on the restored terminal instead of appearing
    // to hang while the in-flight model call finishes.
    if (busy_.load()) {
        std::cout << "[..] waiting for the in-flight model call to finish before exit"
                  << std::endl;
    }
    cancelAndJoinWorker();
}

void TuiApp::postRedraw(TuiChange change) {
    std::lock_guard<std::mutex> lock(screenMutex_);
    // Once Exit() is posted, FTXUI destroys the task sender inside the loop;
    // posting after that is a race on a dying object.
    if (!screen_ || screenExiting_) return;
    if (change == TuiChange::TOKEN) {
        auto now = std::chrono::steady_clock::now();
        if (now - lastTokenPost_ < kTokenRedrawInterval) return;
        lastTokenPost_ = now;
    }
    screen_->PostEvent(Event::Custom);
}

void TuiApp::requestExit() {
    exitRequested_.store(true);
    if (queryInFlight_.load()) {
        agent_.requestCancel();
        transcript_->addSystemLine("[..] exiting - the current model call has to finish first");
    } else if (busy_.load()) {
        transcript_->addSystemLine("[..] exiting - the running command has to finish first");
    }
    std::lock_guard<std::mutex> lock(screenMutex_);
    if (screen_) {
        screenExiting_ = true;
        screen_->Exit();
    }
}

bool TuiApp::handleEvent(const Event& event) { return root_->OnEvent(event); }

bool TuiApp::onEvent(const Event& event) {
    if (event == Event::Custom) {
        return true;  // redraw request from the agent thread
    }

    if (broker_->hasPending()) {
        // FTXUI drains every queued event before it draws, so keys typed
        // before this modal existed arrive after it opens. Swallow them until
        // the modal the key is answering has actually been on screen.
        if (!modalAnswerable()) return true;
        return onModalEvent(event);
    }

    if (event == Event::CtrlC) {
        // First Ctrl-C cancels the turn, a second one quits.
        if (busy_.load() && !cancelling_.load()) {
            cancelTurn();
        } else {
            requestExit();
        }
        return true;
    }

    if (event == Event::Escape) {
        if (busy_.load()) {
            cancelTurn();
        } else if (!inputText_.empty()) {
            inputText_.clear();
            cursorPos_ = 0;
        }
        return true;
    }

    if (event == Event::PageUp) {
        const int rows = static_cast<int>(transcript_->entryCount());
        if (following_) anchorRow_ = std::max(0, rows - 1);
        following_ = false;
        anchorRow_ = std::max(0, anchorRow_ - kScrollStep);
        return true;
    }
    if (event == Event::PageDown) {
        const int rows = static_cast<int>(transcript_->entryCount());
        anchorRow_ += kScrollStep;
        if (anchorRow_ >= rows - 1) following_ = true;  // caught up: follow again
        return true;
    }
    // Home/End are left to the input's cursor — the transcript follows the
    // newest entry again on pgdn or on the next submitted line.
    if (event == Event::ArrowUp) {
        recallHistory(-1);
        return true;
    }
    if (event == Event::ArrowDown) {
        recallHistory(1);
        return true;
    }

    return false;  // everything printable belongs to the input (journey rule R4)
}

bool TuiApp::modalAnswerable() const {
    const unsigned long long pending = broker_->pendingId();
    return pending != 0 && pending == renderedModalId_;
}

bool TuiApp::onModalEvent(const Event& event) {
    const unsigned long long id = renderedModalId_;

    // Only log what actually happened: resolve() refuses a stale or already
    // answered request.
    if (event == Event::Character('y') || event == Event::Character('Y')) {
        if (broker_->resolve(id, ToolConfirmResult::ALLOW_ONCE)) {
            transcript_->addSystemLine("[ok] allowed once");
        }
        return true;
    }
    if (event == Event::Character('a') || event == Event::Character('A')) {
        if (broker_->resolve(id, ToolConfirmResult::ALWAYS_ALLOW)) {
            transcript_->addSystemLine("[ok] always allowed - stored in the allowed-tools file");
        }
        return true;
    }
    if (event == Event::Character('n') || event == Event::Character('N') ||
        event == Event::Escape || event == Event::Return || event == Event::CtrlC) {
        if (broker_->resolve(id, ToolConfirmResult::DENY)) {
            transcript_->addSystemLine("[!] denied");
        }
        return true;
    }
    return true;  // the modal is exclusive: swallow everything else
}

// ---------------------------------------------------------------------------
// Input handling
// ---------------------------------------------------------------------------

void TuiApp::recallHistory(int delta) {
    if (history_.empty()) return;

    if (historyIndex_ == history_.size() && delta < 0) {
        historyDraft_ = inputText_;  // remember what was being typed
    }

    auto index = static_cast<long long>(historyIndex_) + delta;
    index = std::max<long long>(0, std::min<long long>(index, static_cast<long long>(history_.size())));
    historyIndex_ = static_cast<std::size_t>(index);

    inputText_ = (historyIndex_ == history_.size()) ? historyDraft_ : history_[historyIndex_];
    cursorPos_ = static_cast<int>(inputText_.size());
}

void TuiApp::submit() {
    std::string line = trimCopy(inputText_);
    if (line.empty()) {
        inputText_.clear();
        cursorPos_ = 0;
        return;
    }

    if (busy_.load()) {
        transcript_->addSystemLine("[!] a turn is already running - press esc to cancel it first");
        return;
    }

    inputText_.clear();
    cursorPos_ = 0;
    following_ = true;

    if (history_.empty() || history_.back() != line) {
        history_.push_back(line);
    }
    historyIndex_ = history_.size();
    historyDraft_.clear();

    transcript_->addUserLine(line);

    if (line == "exit" || line == "quit") {
        requestExit();
        return;
    }

    if (line[0] == '/') {
        startCommand(line);
        return;
    }

    startQuery(line);
}

void TuiApp::startWorker(std::function<void()> work) {
    if (worker_.joinable()) worker_.join();  // the previous turn has finished

    setBusy(true);
    cancelling_.store(false);

    worker_ = std::thread([this, work = std::move(work)]() {
        try {
            work();
        } catch (const std::exception& e) {
            transcript_->printError(std::string("agent error: ") + e.what());
        } catch (...) {
            transcript_->printError("agent error: unknown exception");
        }
        cancelling_.store(false);
        queryInFlight_.store(false);
        setBusy(false);
        postRedraw(TuiChange::STRUCTURAL);
    });
}

void TuiApp::startQuery(const std::string& query) {
    queryInFlight_.store(true);
    startWorker([this, query] { agent_.processQuery(query); });
}

void TuiApp::startCommand(const std::string& command) {
    // Slash commands run on the worker thread too: /run and friends can invoke
    // a CONFIRM tool, and a confirmation answered on the UI thread cannot be
    // waited for on the UI thread.
    if (!dispatcher_) {
        transcript_->printWarning("unknown command: " + command + " - type /help for the list");
        return;
    }
    startWorker([this, command] {
        if (!dispatcher_(command)) {
            transcript_->printWarning("unknown command: " + command +
                                      " - type /help for the list");
        }
    });
}

void TuiApp::cancelTurn() {
    if (!busy_.load()) return;
    if (!queryInFlight_.load()) {
        // Slash commands are not cancellable; do not pretend otherwise.
        transcript_->addSystemLine("[!] a command is running - it cannot be cancelled");
        postRedraw(TuiChange::STRUCTURAL);
        return;
    }
    cancelling_.store(true);
    agent_.requestCancel();
    // Be honest: cancellation is observed between agent steps, so an in-flight
    // HTTP call to the model finishes before the turn stops.
    transcript_->addSystemLine(
        "[..] cancel requested - the step already running finishes first");
    postRedraw(TuiChange::STRUCTURAL);
}

void TuiApp::setBusy(bool busy) {
    {
        std::lock_guard<std::mutex> lock(idleMutex_);
        busy_.store(busy);
    }
    idleCv_.notify_all();
}

bool TuiApp::waitForIdle(std::chrono::milliseconds timeout) {
    std::unique_lock<std::mutex> lock(idleMutex_);
    return idleCv_.wait_for(lock, timeout, [this] { return !busy_.load(); });
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

std::string TuiApp::keyHint() const {
    if (broker_->hasPending()) {
        return "y allow once  |  a always allow  |  n or esc deny";
    }
    if (cancelling_.load()) {
        return "cancel requested  |  ctrl-c again to quit";
    }
    if (busy_.load()) {
        return "esc cancel turn  |  pgup/pgdn scroll  |  ctrl-c cancel";
    }
    return "enter send  |  up/down history  |  pgup/pgdn scroll  |  /help  |  ctrl-c quit";
}

Element TuiApp::buildModal() const {
    auto request = broker_->pending();
    std::string preview = request.args.is_null() ? std::string("{}") : request.args.dump();
    if (preview.size() > kArgsPreviewLimit) {
        preview = preview.substr(0, kArgsPreviewLimit) + "...";
    }

    // ASCII rules instead of border(): FTXUI's border is box-drawing, and the
    // TUI is ASCII by default (tui-user-journey.md R3).
    return vbox({
               separatorCharacter("="),
               text("[!] tool confirmation required") | bold,
               separatorCharacter("-"),
               paragraph("tool: " + request.toolName),
               paragraph("args: " + preview),
               separatorCharacter("-"),
               text("[y] allow once   [a] always allow   [n] deny   [esc] deny"),
               separatorCharacter("="),
           }) |
           size(WIDTH, LESS_THAN, 68) | size(HEIGHT, LESS_THAN, 16);
}

Element TuiApp::buildFrame() {
    auto rows = transcript_->elements();
    if (rows.empty()) {
        rows.push_back(text("[--] ask a question and press enter") | dim);
    }

    const int count = static_cast<int>(rows.size());
    anchorRow_ = std::max(0, std::min(anchorRow_, count - 1));
    const int focusIndex = following_ ? count - 1 : anchorRow_;
    rows[static_cast<std::size_t>(focusIndex)] = rows[static_cast<std::size_t>(focusIndex)] | focus;

    Element header = hbox({
        text(" " + options_.title + " ") | bold | inverted,
        text(following_ ? "" : "  [--] scrolled back - pgdn to follow again") | dim,
        filler(),
    });

    TuiStatusState status;
    status.busy = busy_.load();
    status.cancelling = cancelling_.load();
    status.hint = keyHint();

    Element base = vbox({
        header,
        separatorCharacter("-"),
        vbox(std::move(rows)) | yframe | flex,
        separatorCharacter("-"),
        transcript_->statusBar(status),
        hbox({text(options_.prompt) | bold, input_->Render() | flex}),
    });

    if (broker_->hasPending()) {
        // Record which request this frame shows: only its keys count as an
        // answer (see modalAnswerable()).
        renderedModalId_ = broker_->pendingId();
        return dbox({base, buildModal() | clear_under | center});
    }
    renderedModalId_ = 0;
    return base;
}

Element TuiApp::renderFrame() { return root_->Render(); }

std::string TuiApp::renderToString(int width, int height) {
    auto screen = Screen::Create(Dimension::Fixed(width), Dimension::Fixed(height));
    auto element = renderFrame();
    Render(screen, element);
    return screen.ToString();
}

} // namespace gaia

#endif // GAIA_HAS_TUI
