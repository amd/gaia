// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Transcript model for the interactive TUI.
//
// TuiTranscript is the agent's OutputHandler while the TUI owns the screen.
// It differs from a plain console handler in two ways that a render loop needs:
//
//   1. It notifies on every mutation, so the agent thread can wake the UI
//      thread (TuiApp turns the notification into screen.PostEvent()).
//   2. It renders each entry once and caches the resulting Element, so a
//      redraw does not re-parse markdown for the whole transcript.
//
// Rendering follows docs/plans/tui-user-journey.md: ASCII only, and every
// state carries a text marker ([ok] [!] [..] [--]) so nothing is signalled by
// colour alone.

#pragma once

#ifdef GAIA_HAS_TUI

#include <cstddef>
#include <functional>
#include <mutex>
#include <string>
#include <vector>

#include <ftxui/dom/elements.hpp>

#include "gaia/console.h"
#include "gaia/export.h"

namespace gaia {

/// Kind of a transcript entry. Drives styling only — the text already carries
/// its own ASCII marker so the entry reads correctly without colour.
/// FAILURE rather than ERROR: <windows.h> defines ERROR as a macro.
enum class TuiEntryKind { USER, ASSISTANT, TOOL, INFO, WARNING, FAILURE };

/// Why the transcript changed. Streaming tokens arrive far faster than a
/// terminal can usefully redraw, so TuiApp rate-limits TOKEN and always
/// redraws on STRUCTURAL.
enum class TuiChange { TOKEN, STRUCTURAL };

struct TuiEntry {
    TuiEntryKind kind = TuiEntryKind::INFO;
    std::string text;
};

/// State the status bar renders that the transcript itself does not own.
struct TuiStatusState {
    bool busy = false;        ///< an agent turn is in flight
    bool cancelling = false;  ///< cancel requested, turn not finished yet
    std::string hint;         ///< what the keys do right now (never empty)
};

/// Thread-safe transcript: written by the agent thread, rendered by the UI thread.
class GAIA_API TuiTranscript : public OutputHandler {
public:
    /// Invoked after every mutation, on whichever thread mutated. Must not
    /// call back into the transcript.
    using ChangeCallback = std::function<void(TuiChange)>;

    TuiTranscript();
    ~TuiTranscript() override;

    void setChangeCallback(ChangeCallback cb);

    // --- OutputHandler ---
    void printProcessingStart(const std::string& query, int maxSteps,
                              const std::string& modelId) override;
    void printStepHeader(int stepNum, int stepLimit) override;
    void printStateInfo(const std::string& message) override;
    void printThought(const std::string& thought) override;
    void printGoal(const std::string& goal) override;
    void printPlan(const json& plan, int currentStep) override;
    void printToolUsage(const std::string& toolName) override;
    void printToolComplete() override;
    void prettyPrintJson(const json& data, const std::string& title) override;
    void printError(const std::string& message) override;
    void printWarning(const std::string& message) override;
    void printInfo(const std::string& message) override;
    void startProgress(const std::string& message) override;
    void stopProgress() override;
    void printFinalAnswer(const std::string& answer,
                          const UsageStats& usage = {}) override;
    void printCompletion(int stepsTaken, int stepsLimit) override;
    void printDecisionMenu(const std::vector<Decision>& decisions) override;
    void printStreamToken(const std::string& token) override;
    void printStreamEnd() override;

    // --- Transcript writes that do not come from the agent ---

    /// Echo a line the user typed (query or slash command).
    void addUserLine(const std::string& text);

    /// Add a neutral line from the TUI itself (banner, slash-command output).
    void addSystemLine(const std::string& text);

    /// Drop every entry (used by /clear).
    void clear();

    /// Seed the model name shown in the status bar before the first turn.
    void setModelId(const std::string& modelId);

    // --- Rendering ---

    /// One Element per entry, in order. Markdown is parsed once per entry and
    /// cached; only entries whose text changed since the last call re-render.
    std::vector<ftxui::Element> elements() const;

    /// Status bar: state marker, model, step, token count, and the key hint.
    ftxui::Element statusBar(const TuiStatusState& state) const;

    // --- Introspection (status bar, tests) ---
    std::size_t entryCount() const;
    std::vector<TuiEntry> entries() const;
    UsageStats usage() const;
    std::string modelId() const;
    std::string progressMessage() const;
    bool streaming() const;
    /// Monotonic counter bumped on every mutation.
    unsigned long long revision() const;

private:
    struct Row {
        TuiEntry entry;
        mutable ftxui::Element cached;  // null until first render
    };

    /// Append an entry, then notify outside the lock.
    void append(TuiEntryKind kind, std::string text, TuiChange change = TuiChange::STRUCTURAL);
    /// Drop the oldest rows past kMaxEntries. Caller holds mutex_.
    void evictLocked();
    void notify(TuiChange change) const;

    mutable std::mutex mutex_;
    std::vector<Row> rows_;
    static constexpr std::size_t kMaxEntries = 2000;  // evict oldest beyond this

    std::string modelId_;
    int step_ = 0;
    int maxSteps_ = 0;
    UsageStats usage_;
    std::string progressMessage_;
    bool streaming_ = false;
    unsigned long long revision_ = 0;

    ChangeCallback onChange_;
    mutable std::mutex callbackMutex_;
};

} // namespace gaia

#endif // GAIA_HAS_TUI
