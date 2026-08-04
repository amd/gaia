// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#ifdef GAIA_HAS_TUI

#include "gaia/tui_transcript.h"

#include <algorithm>
#include <sstream>
#include <utility>

#include "gaia/tui_markdown.h"

namespace gaia {

using namespace ftxui;

namespace {

/// Render text that may contain newlines: one wrapping paragraph per line so
/// nothing is clipped at 80 columns.
Element wrappedText(const std::string& s) {
    Elements lines;
    std::string line;
    std::istringstream stream(s);
    while (std::getline(stream, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        lines.push_back(line.empty() ? text("") : paragraph(line));
    }
    if (lines.empty()) return text("");
    if (lines.size() == 1) return lines[0];
    return vbox(std::move(lines));
}

Element renderRow(const TuiEntry& entry) {
    switch (entry.kind) {
        case TuiEntryKind::USER:
            return hbox(text("> ") | bold, wrappedText(entry.text));
        case TuiEntryKind::ASSISTANT:
            return renderMarkdown(entry.text);
        case TuiEntryKind::TOOL:
            return wrappedText(entry.text) | dim;
        case TuiEntryKind::WARNING:
            return wrappedText(entry.text) | color(Color::Yellow);
        case TuiEntryKind::FAILURE:
            return wrappedText(entry.text) | color(Color::Red) | bold;
        case TuiEntryKind::INFO:
            break;
    }
    return wrappedText(entry.text) | dim;
}

} // namespace

// ---------------------------------------------------------------------------
// Construction
// ---------------------------------------------------------------------------

TuiTranscript::TuiTranscript() = default;
TuiTranscript::~TuiTranscript() = default;

void TuiTranscript::setChangeCallback(ChangeCallback cb) {
    std::lock_guard<std::mutex> lock(callbackMutex_);
    onChange_ = std::move(cb);
}

void TuiTranscript::notify(TuiChange change) const {
    ChangeCallback cb;
    {
        std::lock_guard<std::mutex> lock(callbackMutex_);
        cb = onChange_;
    }
    if (cb) cb(change);
}

void TuiTranscript::evictLocked() {
    if (rows_.size() <= kMaxEntries) return;
    rows_.erase(rows_.begin(),
                rows_.begin() + static_cast<std::ptrdiff_t>(rows_.size() - kMaxEntries));
}

void TuiTranscript::append(TuiEntryKind kind, std::string text, TuiChange change) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        rows_.push_back(Row{TuiEntry{kind, std::move(text)}, nullptr});
        evictLocked();
        ++revision_;
    }
    notify(change);
}

// ---------------------------------------------------------------------------
// OutputHandler — progress and state
// ---------------------------------------------------------------------------

void TuiTranscript::printProcessingStart(const std::string& /*query*/, int maxSteps,
                                         const std::string& modelId) {
    // The query itself is echoed by TuiApp the moment the user presses enter,
    // so it is already on screen before the agent thread starts.
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!modelId.empty()) modelId_ = modelId;
        step_ = 0;
        maxSteps_ = maxSteps;
        streaming_ = false;
        progressMessage_.clear();
        ++revision_;
    }
    notify(TuiChange::STRUCTURAL);
}

void TuiTranscript::printStepHeader(int stepNum, int stepLimit) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        step_ = stepNum;
        maxSteps_ = stepLimit;
        ++revision_;
    }
    notify(TuiChange::STRUCTURAL);
}

void TuiTranscript::printStateInfo(const std::string& message) {
    if (message.empty()) return;
    append(TuiEntryKind::INFO, "[--] " + message);
}

void TuiTranscript::printThought(const std::string& thought) {
    if (thought.empty()) return;
    append(TuiEntryKind::INFO, "[--] thinking: " + thought);
}

void TuiTranscript::printGoal(const std::string& goal) {
    if (goal.empty()) return;
    append(TuiEntryKind::INFO, "[--] goal: " + goal);
}

void TuiTranscript::printPlan(const json& plan, int currentStep) {
    if (!plan.is_array() || plan.empty()) return;

    std::ostringstream oss;
    oss << "[--] plan (" << plan.size() << " steps):";
    int idx = 0;
    for (const auto& step : plan) {
        oss << "\n  " << ((idx == currentStep) ? ">" : " ") << " " << (idx + 1)
            << ". " << step.value("tool", "???");
        ++idx;
    }
    append(TuiEntryKind::INFO, oss.str());
}

// ---------------------------------------------------------------------------
// OutputHandler — tools
// ---------------------------------------------------------------------------

void TuiTranscript::printToolUsage(const std::string& toolName) {
    append(TuiEntryKind::TOOL, "[..] tool: " + toolName);
}

void TuiTranscript::printToolComplete() {
    append(TuiEntryKind::TOOL, "[ok] tool finished");
}

void TuiTranscript::prettyPrintJson(const json& data, const std::string& title) {
    if (data.empty()) return;
    std::ostringstream oss;
    oss << "[--] ";
    if (!title.empty()) oss << title << ":\n";
    oss << data.dump(2);
    append(TuiEntryKind::INFO, oss.str());
}

// ---------------------------------------------------------------------------
// OutputHandler — status messages
// ---------------------------------------------------------------------------

void TuiTranscript::printError(const std::string& message) {
    if (message.empty()) return;
    append(TuiEntryKind::FAILURE, "[!] " + message);
}

void TuiTranscript::printWarning(const std::string& message) {
    if (message.empty()) return;
    append(TuiEntryKind::WARNING, "[!] " + message);
}

void TuiTranscript::printInfo(const std::string& message) {
    if (message.empty()) return;
    append(TuiEntryKind::INFO, "[--] " + message);
}

// ---------------------------------------------------------------------------
// OutputHandler — progress indicator
// ---------------------------------------------------------------------------

void TuiTranscript::startProgress(const std::string& message) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        progressMessage_ = message;
        ++revision_;
    }
    notify(TuiChange::STRUCTURAL);
}

void TuiTranscript::stopProgress() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        progressMessage_.clear();
        ++revision_;
    }
    notify(TuiChange::STRUCTURAL);
}

// ---------------------------------------------------------------------------
// OutputHandler — completion
// ---------------------------------------------------------------------------

void TuiTranscript::printFinalAnswer(const std::string& answer, const UsageStats& usage) {
    bool duplicate = false;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        usage_ = usage;
        // With structuredEvents the agent emits both the stream and the final
        // answer; do not print the same text twice.
        duplicate = !rows_.empty() && rows_.back().entry.kind == TuiEntryKind::ASSISTANT &&
                    rows_.back().entry.text == answer;
        ++revision_;
    }
    if (answer.empty() || duplicate) {
        notify(TuiChange::STRUCTURAL);
        return;
    }
    append(TuiEntryKind::ASSISTANT, answer);
}

void TuiTranscript::printCompletion(int stepsTaken, int stepsLimit) {
    std::ostringstream oss;
    oss << "[ok] completed in " << stepsTaken << "/" << stepsLimit << " steps";
    append(TuiEntryKind::INFO, oss.str());
}

void TuiTranscript::printDecisionMenu(const std::vector<Decision>& decisions) {
    if (decisions.empty()) return;
    std::ostringstream oss;
    oss << "[--] choose an option:";
    for (std::size_t i = 0; i < decisions.size(); ++i) {
        oss << "\n  [" << (i + 1) << "] " << decisions[i].label;
        if (!decisions[i].description.empty()) oss << " - " << decisions[i].description;
    }
    append(TuiEntryKind::INFO, oss.str());
}

// ---------------------------------------------------------------------------
// OutputHandler — streaming
// ---------------------------------------------------------------------------

void TuiTranscript::printStreamToken(const std::string& token) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!streaming_ || rows_.empty() || rows_.back().entry.kind != TuiEntryKind::ASSISTANT) {
            rows_.push_back(Row{TuiEntry{TuiEntryKind::ASSISTANT, ""}, nullptr});
            evictLocked();
            streaming_ = true;
        }
        rows_.back().entry.text += token;
        rows_.back().cached = nullptr;  // the growing entry re-renders each frame
        ++revision_;
    }
    notify(TuiChange::TOKEN);
}

void TuiTranscript::printStreamEnd() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        streaming_ = false;
        ++revision_;
    }
    notify(TuiChange::STRUCTURAL);
}

// ---------------------------------------------------------------------------
// TUI-originated writes
// ---------------------------------------------------------------------------

void TuiTranscript::addUserLine(const std::string& text) {
    append(TuiEntryKind::USER, text);
}

void TuiTranscript::addSystemLine(const std::string& text) {
    if (text.empty()) return;
    append(TuiEntryKind::INFO, text);
}

void TuiTranscript::setModelId(const std::string& modelId) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        modelId_ = modelId;
        ++revision_;
    }
    notify(TuiChange::STRUCTURAL);
}

void TuiTranscript::clear() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        rows_.clear();
        streaming_ = false;
        progressMessage_.clear();
        ++revision_;
    }
    notify(TuiChange::STRUCTURAL);
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

std::vector<Element> TuiTranscript::elements() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<Element> out;
    out.reserve(rows_.size() + 1);
    for (std::size_t i = 0; i < rows_.size(); ++i) {
        const auto& row = rows_[i];
        // The still-growing streaming row is drawn as plain wrapped text: it
        // changes every frame, and half-arrived markdown is not worth
        // re-parsing 30 times a second.
        if (streaming_ && i + 1 == rows_.size()) {
            out.push_back(wrappedText(row.entry.text));
            continue;
        }
        if (!row.cached) {
            row.cached = renderRow(row.entry);
        }
        out.push_back(row.cached);
    }
    if (!progressMessage_.empty()) {
        out.push_back(text("[..] " + progressMessage_) | dim);
    }
    return out;
}

Element TuiTranscript::statusBar(const TuiStatusState& state) const {
    std::lock_guard<std::mutex> lock(mutex_);

    std::string marker = "[ok] ready";
    if (state.cancelling) {
        marker = "[!] cancelling";
    } else if (state.busy) {
        marker = "[..] working";
    }

    std::ostringstream steps;
    steps << "step " << step_ << "/" << maxSteps_;

    // Two rows: state on the first, what the keys do right now on the second.
    // The hint gets its own row so it is never the part that gets clipped at
    // 80 columns (tui-user-journey.md R5).
    return vbox({
        hbox({
            text(marker) | bold,
            separatorCharacter("|"),
            text(modelId_.empty() ? std::string("no model") : modelId_),
            separatorCharacter("|"),
            text(steps.str()),
            separatorCharacter("|"),
            text("tokens " + std::to_string(usage_.totalTokens)),
            filler(),
        }),
        text(state.hint) | dim,
    });
}

// ---------------------------------------------------------------------------
// Introspection
// ---------------------------------------------------------------------------

std::size_t TuiTranscript::entryCount() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return rows_.size();
}

std::vector<TuiEntry> TuiTranscript::entries() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<TuiEntry> out;
    out.reserve(rows_.size());
    for (const auto& row : rows_) out.push_back(row.entry);
    return out;
}

UsageStats TuiTranscript::usage() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return usage_;
}

std::string TuiTranscript::modelId() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return modelId_;
}

std::string TuiTranscript::progressMessage() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return progressMessage_;
}

bool TuiTranscript::streaming() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return streaming_;
}

unsigned long long TuiTranscript::revision() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return revision_;
}

} // namespace gaia

#endif // GAIA_HAS_TUI
