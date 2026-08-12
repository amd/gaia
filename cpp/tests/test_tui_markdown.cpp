// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Unit tests for the TUI markdown renderer (renderMarkdown).
// Tests the markdown parser only, not FTXUI screen rendering.
// Wrapped in GAIA_HAS_TUI so it compiles away when FTXUI is unavailable.

#ifdef GAIA_HAS_TUI

#include <gtest/gtest.h>
#include <ftxui/dom/elements.hpp>
#include <ftxui/screen/screen.hpp>

#include <gaia/tui_markdown.h>

#include "support/screen_text.h"

// ---- Basic rendering ----

TEST(TuiMarkdown, PlainText) {
    auto elem = gaia::renderMarkdown("Hello world");
    ASSERT_TRUE(elem);
}

TEST(TuiMarkdown, EmptyString) {
    auto elem = gaia::renderMarkdown("");
    ASSERT_TRUE(elem);  // Should not crash
}

TEST(TuiMarkdown, WhitespaceOnly) {
    auto elem = gaia::renderMarkdown("   \n\n  ");
    ASSERT_TRUE(elem);
}

// ---- Headings ----

TEST(TuiMarkdown, HeadingH1) {
    auto elem = gaia::renderMarkdown("# Title\n\nBody text");
    ASSERT_TRUE(elem);
}

TEST(TuiMarkdown, HeadingH2) {
    auto elem = gaia::renderMarkdown("## Subtitle");
    ASSERT_TRUE(elem);
}

TEST(TuiMarkdown, HeadingH3) {
    auto elem = gaia::renderMarkdown("### Minor heading");
    ASSERT_TRUE(elem);
}

TEST(TuiMarkdown, MultipleHeadings) {
    auto elem = gaia::renderMarkdown("# One\n## Two\n### Three");
    ASSERT_TRUE(elem);
}

// ---- Code blocks ----

TEST(TuiMarkdown, CodeBlock) {
    auto elem = gaia::renderMarkdown("```bash\necho hello\n```");
    ASSERT_TRUE(elem);
}

TEST(TuiMarkdown, CodeBlockNoLanguage) {
    auto elem = gaia::renderMarkdown("```\nsome code\n```");
    ASSERT_TRUE(elem);
}

TEST(TuiMarkdown, CodeBlockMultipleLines) {
    std::string md = "```python\ndef hello():\n    print('hello')\n```";
    auto elem = gaia::renderMarkdown(md);
    ASSERT_TRUE(elem);
}

TEST(TuiMarkdown, UnclosedCodeBlock) {
    // Graceful degradation: unclosed code block should not crash
    auto elem = gaia::renderMarkdown("```\nsome code without closing");
    ASSERT_TRUE(elem);
}

// ---- Bullet lists ----

TEST(TuiMarkdown, BulletList) {
    auto elem = gaia::renderMarkdown("- item 1\n- item 2\n- item 3");
    ASSERT_TRUE(elem);
}

TEST(TuiMarkdown, SingleBullet) {
    auto elem = gaia::renderMarkdown("- just one item");
    ASSERT_TRUE(elem);
}

// ---- Blockquotes ----

TEST(TuiMarkdown, Blockquote) {
    auto elem = gaia::renderMarkdown("> This is a quote");
    ASSERT_TRUE(elem);
}

TEST(TuiMarkdown, MultilineBlockquote) {
    auto elem = gaia::renderMarkdown("> Line one\n> Line two\n> Line three");
    ASSERT_TRUE(elem);
}

TEST(TuiMarkdown, BlockquoteFollowedByText) {
    auto elem = gaia::renderMarkdown("> A quote\n\nRegular text after");
    ASSERT_TRUE(elem);
}

// ---- Inline formatting ----

TEST(TuiMarkdown, BoldText) {
    auto elem = gaia::renderMarkdown("Some **bold** text");
    ASSERT_TRUE(elem);
}

TEST(TuiMarkdown, InlineCode) {
    auto elem = gaia::renderMarkdown("Use the `printf` function");
    ASSERT_TRUE(elem);
}

TEST(TuiMarkdown, UnclosedBold) {
    // Graceful degradation: unclosed ** treated as literal
    auto elem = gaia::renderMarkdown("This is **unclosed bold");
    ASSERT_TRUE(elem);
}

TEST(TuiMarkdown, UnclosedInlineCode) {
    // Graceful degradation: unclosed ` treated as literal
    auto elem = gaia::renderMarkdown("This is `unclosed code");
    ASSERT_TRUE(elem);
}

// ---- Mixed content ----

TEST(TuiMarkdown, MixedContent) {
    std::string md =
        "# Header\n"
        "\n"
        "Some **bold** text and `code`.\n"
        "\n"
        "```\n"
        "code block\n"
        "```\n"
        "\n"
        "- list item\n"
        "- another item\n"
        "\n"
        "> A blockquote";
    auto elem = gaia::renderMarkdown(md);
    ASSERT_TRUE(elem);
}

TEST(TuiMarkdown, LongDocument) {
    // Stress test: many lines of mixed content
    std::string md;
    for (int i = 0; i < 50; ++i) {
        md += "## Section " + std::to_string(i) + "\n";
        md += "Some text with **bold** and `code`.\n";
        md += "- bullet " + std::to_string(i) + "\n";
        md += "\n";
    }
    auto elem = gaia::renderMarkdown(md);
    ASSERT_TRUE(elem);
}

TEST(TuiMarkdown, NoMarkdown) {
    // Plain text with no markdown syntax should still render
    auto elem = gaia::renderMarkdown("Just a plain sentence with no special formatting.");
    ASSERT_TRUE(elem);
}

// ---- Rendered output (not just non-null) ----

namespace {

std::string renderToText(const std::string& markdown, int width = 80, int height = 24) {
    auto screen = ftxui::Screen::Create(ftxui::Dimension::Fixed(width),
                                        ftxui::Dimension::Fixed(height));
    auto element = gaia::renderMarkdown(markdown);
    ftxui::Render(screen, element);
    // Styles are stripped: a styled word boundary would otherwise put escape
    // bytes in the middle of the text being searched for.
    return gaia_test::stripAnsi(screen.ToString());
}

bool hasText(const std::string& haystack, const std::string& needle) {
    return haystack.find(needle) != std::string::npos;
}

} // namespace

TEST(TuiMarkdownRender, PlainTextAppearsOnTheScreen) {
    EXPECT_TRUE(hasText(renderToText("hello world"), "hello world"));
}

TEST(TuiMarkdownRender, LongParagraphWrapsInsteadOfBeingClipped) {
    std::string paragraph = "alpha";
    for (int i = 0; i < 40; ++i) paragraph += " filler";
    paragraph += " omega";

    const std::string frame = renderToText(paragraph);
    EXPECT_TRUE(hasText(frame, "alpha"));
    EXPECT_TRUE(hasText(frame, "omega")) << "long answers must wrap, not be cut at 80 columns";
}

TEST(TuiMarkdownRender, BoldAndCodeSurviveWrapping) {
    std::string markdown = "Run **make build** then `ctest --output-on-failure` to check.";
    const std::string frame = renderToText(markdown);
    EXPECT_TRUE(hasText(frame, "make build"));
    EXPECT_TRUE(hasText(frame, "ctest"));
}

TEST(TuiMarkdownRender, CodeBlocksAndQuotesAreAsciiOnly) {
    const std::string markdown =
        "# Title\n"
        "\n"
        "```bash\n"
        "echo hi\n"
        "```\n"
        "\n"
        "- bullet one\n"
        "\n"
        "> quoted line\n";

    const std::string frame = renderToText(markdown);
    EXPECT_TRUE(hasText(frame, "echo hi"));
    EXPECT_TRUE(hasText(frame, "bullet one"));
    EXPECT_TRUE(hasText(frame, "quoted line"));
    for (unsigned char c : frame) {
        EXPECT_LT(c, 0x80u) << "non-ASCII byte in rendered markdown";
    }
}

#endif // GAIA_HAS_TUI
