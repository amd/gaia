// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Tests for TuiTranscript — the agent's OutputHandler while the TUI owns the
// screen. Everything is asserted against a real rendered frame
// (Screen::Create + Render + ToString), because "the handler stored it" is
// exactly the bug this class exists to prevent.

#ifdef GAIA_HAS_TUI

#include <gtest/gtest.h>

#include <ftxui/dom/elements.hpp>
#include <ftxui/screen/screen.hpp>

#include <gaia/tui_transcript.h>

#include "support/screen_text.h"

#include <string>
#include <vector>

using namespace gaia;

namespace {

/// Render the transcript's entries into a w x h screen and return its text.
std::string renderTranscript(const TuiTranscript& transcript, int width = 80, int height = 24) {
    auto rows = transcript.elements();
    auto element = ftxui::vbox(std::move(rows)) | ftxui::yframe;
    auto screen = ftxui::Screen::Create(ftxui::Dimension::Fixed(width),
                                        ftxui::Dimension::Fixed(height));
    ftxui::Render(screen, element);
    return screen.ToString();
}

std::string renderStatus(const TuiTranscript& transcript, const TuiStatusState& state,
                         int width = 80) {
    auto element = transcript.statusBar(state);
    auto screen = ftxui::Screen::Create(ftxui::Dimension::Fixed(width), ftxui::Dimension::Fixed(2));
    ftxui::Render(screen, element);
    return screen.ToString();
}

bool contains(const std::string& haystack, const std::string& needle) {
    return haystack.find(needle) != std::string::npos;
}

} // namespace

// ---------------------------------------------------------------------------
// The regression the TUI exists to fix: output must reach a rendered frame
// ---------------------------------------------------------------------------

TEST(TuiTranscript, FinalAnswerReachesTheRenderedFrame) {
    TuiTranscript transcript;
    transcript.printFinalAnswer("The capital of France is Paris.", {});

    EXPECT_TRUE(contains(renderTranscript(transcript), "The capital of France is Paris."));
}

TEST(TuiTranscript, EveryOutputHandlerMethodReachesTheFrame) {
    TuiTranscript transcript;
    transcript.printProcessingStart("q", 5, "mock-model");
    transcript.printGoal("find the answer");
    transcript.printThought("checking the index");
    transcript.printToolUsage("bash_execute");
    transcript.printToolComplete();
    transcript.printInfo("indexed 3 files");
    transcript.printWarning("one file was skipped");
    transcript.printError("disk is full");
    transcript.printFinalAnswer("all done", {});
    transcript.printCompletion(2, 5);

    // 60 rows: assert the content is present, not that it fits one screen.
    const std::string frame = renderTranscript(transcript, 80, 60);
    EXPECT_TRUE(contains(frame, "find the answer"));
    EXPECT_TRUE(contains(frame, "checking the index"));
    EXPECT_TRUE(contains(frame, "bash_execute"));
    EXPECT_TRUE(contains(frame, "indexed 3 files"));
    EXPECT_TRUE(contains(frame, "one file was skipped"));
    EXPECT_TRUE(contains(frame, "disk is full"));
    EXPECT_TRUE(contains(frame, "all done"));
}

// ---------------------------------------------------------------------------
// Streaming
// ---------------------------------------------------------------------------

TEST(TuiTranscript, StreamingTokensRenderIncrementally) {
    TuiTranscript transcript;

    transcript.printStreamToken("Hel");
    EXPECT_TRUE(contains(renderTranscript(transcript), "Hel"));

    transcript.printStreamToken("lo, ");
    transcript.printStreamToken("world");
    const std::string mid = renderTranscript(transcript);
    EXPECT_TRUE(contains(mid, "Hello, world"));
    EXPECT_TRUE(transcript.streaming());

    transcript.printStreamEnd();
    EXPECT_FALSE(transcript.streaming());
    EXPECT_TRUE(contains(renderTranscript(transcript), "Hello, world"));
    EXPECT_EQ(transcript.entryCount(), 1u);  // one growing entry, not one per token
}

TEST(TuiTranscript, FinalAnswerAfterIdenticalStreamIsNotDuplicated) {
    TuiTranscript transcript;
    transcript.printStreamToken("streamed answer");
    transcript.printStreamEnd();
    transcript.printFinalAnswer("streamed answer", {});

    EXPECT_EQ(transcript.entryCount(), 1u);
}

TEST(TuiTranscript, ChangeCallbackDistinguishesTokensFromStructuralChanges) {
    TuiTranscript transcript;
    std::vector<TuiChange> changes;
    transcript.setChangeCallback([&](TuiChange change) { changes.push_back(change); });

    transcript.printStreamToken("a");
    transcript.printStreamEnd();

    ASSERT_EQ(changes.size(), 2u);
    EXPECT_EQ(changes[0], TuiChange::TOKEN);
    EXPECT_EQ(changes[1], TuiChange::STRUCTURAL);
}

TEST(TuiTranscript, RevisionAdvancesOnEveryMutation) {
    TuiTranscript transcript;
    const auto start = transcript.revision();
    transcript.printInfo("one");
    transcript.printInfo("two");
    EXPECT_GE(transcript.revision(), start + 2);
}

// ---------------------------------------------------------------------------
// Render caching — the reason TuiConsole could not drive a render loop
// ---------------------------------------------------------------------------

TEST(TuiTranscript, UnchangedEntriesAreNotReRendered) {
    TuiTranscript transcript;
    transcript.printFinalAnswer("# Heading\n\nSome **markdown** body", {});

    auto first = transcript.elements();
    auto second = transcript.elements();
    ASSERT_EQ(first.size(), 1u);
    ASSERT_EQ(second.size(), 1u);
    EXPECT_EQ(first[0].get(), second[0].get()) << "markdown was re-parsed on redraw";
}

TEST(TuiTranscript, GrowingStreamEntryIsReRendered) {
    TuiTranscript transcript;
    transcript.printStreamToken("one");
    auto first = transcript.elements();
    transcript.printStreamToken(" two");
    auto second = transcript.elements();
    EXPECT_NE(first[0].get(), second[0].get());
}

// ---------------------------------------------------------------------------
// Design rules: text markers, ASCII, 80x24
// ---------------------------------------------------------------------------

TEST(TuiTranscript, EveryStateCarriesATextMarker) {
    TuiTranscript transcript;
    transcript.printToolUsage("bash_execute");
    transcript.printToolComplete();
    transcript.printError("boom");
    transcript.printWarning("careful");
    transcript.printInfo("note");

    const std::string frame = renderTranscript(transcript, 80, 10);
    EXPECT_TRUE(contains(frame, "[..] tool: bash_execute"));
    EXPECT_TRUE(contains(frame, "[ok] tool finished"));
    EXPECT_TRUE(contains(frame, "[!] boom"));
    EXPECT_TRUE(contains(frame, "[!] careful"));
    EXPECT_TRUE(contains(frame, "[--] note"));
}

TEST(TuiTranscript, RenderedOutputIsAsciiOnly) {
    TuiTranscript transcript;
    transcript.printToolUsage("bash_execute");
    transcript.printError("boom");
    transcript.addUserLine("hello");
    transcript.printFinalAnswer("plain answer", {});

    for (unsigned char c : renderTranscript(transcript, 80, 10)) {
        EXPECT_LT(c, 0x80u) << "non-ASCII byte in rendered output";
    }
}

TEST(TuiTranscript, LongTextWrapsInsteadOfBeingClippedAt80Columns) {
    TuiTranscript transcript;
    std::string sentence = "alpha";
    for (int i = 0; i < 40; ++i) sentence += " filler";
    sentence += " omega";
    transcript.printInfo(sentence);

    const auto lines = gaia_test::visibleLines(renderTranscript(transcript, 80, 24));
    ASSERT_EQ(lines.size(), 24u);
    for (const auto& line : lines) {
        EXPECT_LE(line.size(), 80u) << "line wider than 80 columns: " << line;
    }

    const std::string plain = gaia_test::stripAnsi(renderTranscript(transcript, 80, 24));
    EXPECT_TRUE(contains(plain, "alpha"));
    EXPECT_TRUE(contains(plain, "omega")) << "the tail of a long line was clipped, not wrapped";
}

TEST(TuiTranscript, MultiLineEntriesKeepTheirLineBreaks) {
    TuiTranscript transcript;
    transcript.printPlan(json::array({{{"tool", "read_file"}}, {{"tool", "write_file"}}}), 0);

    const std::string plain = gaia_test::stripAnsi(renderTranscript(transcript, 80, 24));
    EXPECT_TRUE(contains(plain, "plan (2 steps)"));
    EXPECT_TRUE(contains(plain, "1. read_file"));
    EXPECT_TRUE(contains(plain, "2. write_file"));
}

// ---------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------

TEST(TuiTranscript, StatusBarReportsTokenUsage) {
    TuiTranscript transcript;
    UsageStats usage;
    usage.promptTokens = 900;
    usage.completionTokens = 334;
    usage.totalTokens = 1234;
    transcript.printFinalAnswer("done", usage);

    TuiStatusState state;
    state.hint = "enter send";
    EXPECT_TRUE(contains(renderStatus(transcript, state), "tokens 1234"));
}

TEST(TuiTranscript, StatusBarShowsModelStepAndState) {
    TuiTranscript transcript;
    transcript.printProcessingStart("q", 20, "mock-model");
    transcript.printStepHeader(3, 20);

    TuiStatusState busy;
    busy.busy = true;
    busy.hint = "esc cancel turn";
    const std::string frame = renderStatus(transcript, busy);
    EXPECT_TRUE(contains(frame, "[..] working"));
    EXPECT_TRUE(contains(frame, "mock-model"));
    EXPECT_TRUE(contains(frame, "step 3/20"));
    EXPECT_TRUE(contains(frame, "esc cancel turn"));

    TuiStatusState cancelling;
    cancelling.busy = true;
    cancelling.cancelling = true;
    cancelling.hint = "ctrl-c again to quit";
    EXPECT_TRUE(contains(renderStatus(transcript, cancelling), "[!] cancelling"));

    TuiStatusState idle;
    idle.hint = "enter send";
    EXPECT_TRUE(contains(renderStatus(transcript, idle), "[ok] ready"));
}

// ---------------------------------------------------------------------------
// Housekeeping
// ---------------------------------------------------------------------------

TEST(TuiTranscript, ProgressMessageIsVisibleWhileActive) {
    TuiTranscript transcript;
    transcript.startProgress("Thinking");
    EXPECT_TRUE(contains(renderTranscript(transcript), "[..] Thinking"));
    transcript.stopProgress();
    EXPECT_FALSE(contains(renderTranscript(transcript), "[..] Thinking"));
}

TEST(TuiTranscript, ClearDropsEveryEntry) {
    TuiTranscript transcript;
    transcript.addUserLine("hello");
    transcript.printFinalAnswer("hi", {});
    ASSERT_EQ(transcript.entryCount(), 2u);

    transcript.clear();
    EXPECT_EQ(transcript.entryCount(), 0u);
    EXPECT_FALSE(contains(renderTranscript(transcript), "hello"));
}

TEST(TuiTranscript, OldEntriesAreEvictedAtTheCap) {
    TuiTranscript transcript;
    for (int i = 0; i < 2100; ++i) {
        transcript.printInfo("line " + std::to_string(i));
    }
    EXPECT_EQ(transcript.entryCount(), 2000u);

    const auto entries = transcript.entries();
    EXPECT_EQ(entries.front().text, "[--] line 100");
    EXPECT_EQ(entries.back().text, "[--] line 2099");
}

TEST(TuiTranscript, EmptyMessagesAreIgnored) {
    TuiTranscript transcript;
    transcript.printInfo("");
    transcript.printError("");
    transcript.printWarning("");
    transcript.printFinalAnswer("", {});
    EXPECT_EQ(transcript.entryCount(), 0u);
}

#endif // GAIA_HAS_TUI
