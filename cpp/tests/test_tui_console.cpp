// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Unit tests for the TUI markdown renderer (renderMarkdown).
// Tests the markdown parser only, not FTXUI screen rendering.
// Wrapped in GAIA_HAS_TUI so it compiles away when FTXUI is unavailable.

#ifdef GAIA_HAS_TUI

#include <gtest/gtest.h>
#include <ftxui/dom/elements.hpp>

// Declare the function (defined in tui_markdown.cpp).
namespace gaia {
ftxui::Element renderMarkdown(const std::string& markdown);
}

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

#endif // GAIA_HAS_TUI
