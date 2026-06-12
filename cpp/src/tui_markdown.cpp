// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Standalone markdown-to-FTXUI element renderer.
//
// Supported syntax (C++17 parser -- no external deps):
//   - # Headings (H1-H3: bold + color)
//   - **bold** text
//   - `inline code` (dim/inverted)
//   - ```fenced code blocks``` (bordered, with optional language label)
//   - - Bullet lists (indented)
//   - > Blockquotes (dim + border)
//   - Regular paragraphs (word-wrapped)
//
// Unsupported syntax is rendered as plain text (graceful degradation).

#ifdef GAIA_HAS_TUI

#include <string>
#include <vector>

#include <ftxui/dom/elements.hpp>

namespace gaia {

using namespace ftxui;

// ---------------------------------------------------------------------------
// Inline formatting: scan a single line for **bold** and `inline code`
// ---------------------------------------------------------------------------
namespace {

/// Parse inline formatting within a single line and return an hbox of Elements.
Element parseInline(const std::string& line) {
    if (line.empty()) {
        return text("");
    }

    Elements parts;
    size_t i = 0;
    std::string current;

    auto flushCurrent = [&]() {
        if (!current.empty()) {
            parts.push_back(text(current));
            current.clear();
        }
    };

    while (i < line.size()) {
        // Check for **bold**
        if (i + 1 < line.size() && line[i] == '*' && line[i + 1] == '*') {
            flushCurrent();
            size_t end = line.find("**", i + 2);
            if (end != std::string::npos) {
                std::string boldText = line.substr(i + 2, end - (i + 2));
                parts.push_back(text(boldText) | bold);
                i = end + 2;
                continue;
            }
            // Unclosed **: treat as literal
            current += '*';
            ++i;
            continue;
        }

        // Check for `inline code`
        if (line[i] == '`') {
            flushCurrent();
            size_t end = line.find('`', i + 1);
            if (end != std::string::npos) {
                std::string codeText = line.substr(i + 1, end - (i + 1));
                parts.push_back(text(codeText) | dim | inverted);
                i = end + 1;
                continue;
            }
            // Unclosed `: treat as literal
            current += '`';
            ++i;
            continue;
        }

        current += line[i];
        ++i;
    }

    flushCurrent();

    if (parts.empty()) {
        return text("");
    }
    if (parts.size() == 1) {
        return parts[0];
    }
    return hbox(std::move(parts));
}

/// Split a string by a delimiter character.
std::vector<std::string> splitLines(const std::string& s) {
    std::vector<std::string> result;
    std::string line;
    for (char c : s) {
        if (c == '\n') {
            result.push_back(line);
            line.clear();
        } else {
            line += c;
        }
    }
    // Include last line even without trailing newline
    result.push_back(line);
    return result;
}

/// Trim leading whitespace from a string.
std::string ltrim(const std::string& s) {
    size_t start = s.find_first_not_of(" \t");
    return (start == std::string::npos) ? "" : s.substr(start);
}

/// Check if a line starts with a given prefix.
bool startsWith(const std::string& s, const std::string& prefix) {
    return s.size() >= prefix.size() && s.compare(0, prefix.size(), prefix) == 0;
}

} // anonymous namespace

// ---------------------------------------------------------------------------
// renderMarkdown — public entry point
// ---------------------------------------------------------------------------

Element renderMarkdown(const std::string& markdown) {
    if (markdown.empty()) {
        return text("");
    }

    auto lines = splitLines(markdown);
    Elements blocks;

    enum class State { NORMAL, IN_CODE_BLOCK, IN_BLOCKQUOTE };
    State state = State::NORMAL;

    std::string codeLang;
    Elements codeLines;
    Elements quoteLines;

    auto flushCodeBlock = [&]() {
        Element codeContent;
        if (codeLines.empty()) {
            codeContent = text("");
        } else {
            codeContent = vbox(std::move(codeLines));
        }

        Elements codeBox;
        if (!codeLang.empty()) {
            codeBox.push_back(text(" " + codeLang + " ") | dim | bold);
        }
        codeBox.push_back(codeContent | dim);

        blocks.push_back(vbox(std::move(codeBox)) | borderLight);
        codeLines.clear();
        codeLang.clear();
    };

    auto flushBlockquote = [&]() {
        if (quoteLines.empty()) return;
        Element content = vbox(std::move(quoteLines));
        blocks.push_back(
            hbox(text(" ") | dim, separatorLight(), text(" "), content) | dim
        );
        quoteLines.clear();
    };

    // Process a single line in NORMAL state. Extracted so that the blockquote
    // exit path can re-process the current line without goto.
    auto processNormal = [&](const std::string& rawLine) {
        std::string trimmed = ltrim(rawLine);

        // Empty line: paragraph break
        if (trimmed.empty()) {
            blocks.push_back(text(""));
            return;
        }

        // Fenced code block start
        if (startsWith(trimmed, "```")) {
            codeLang = trimmed.substr(3);
            // Trim the language tag
            size_t end = codeLang.find_first_of(" \t\n\r");
            if (end != std::string::npos) {
                codeLang = codeLang.substr(0, end);
            }
            state = State::IN_CODE_BLOCK;
            return;
        }

        // Blockquote
        if (startsWith(rawLine, "> ") || rawLine == ">") {
            state = State::IN_BLOCKQUOTE;
            if (startsWith(rawLine, "> ")) {
                quoteLines.push_back(parseInline(rawLine.substr(2)));
            } else {
                quoteLines.push_back(text(""));
            }
            return;
        }

        // Headings (check longest prefix first to avoid false matches)
        if (startsWith(trimmed, "### ")) {
            std::string heading = trimmed.substr(4);
            blocks.push_back(text(heading) | bold);
            return;
        }
        if (startsWith(trimmed, "## ")) {
            std::string heading = trimmed.substr(3);
            blocks.push_back(
                text(heading) | bold | color(Color::Blue)
            );
            return;
        }
        if (startsWith(trimmed, "# ")) {
            std::string heading = trimmed.substr(2);
            blocks.push_back(text(heading) | bold | underlined);
            return;
        }

        // Bullet list item
        if (startsWith(trimmed, "- ")) {
            std::string item = trimmed.substr(2);
            blocks.push_back(
                hbox(text("  * ") | bold, parseInline(item))
            );
            return;
        }

        // Regular paragraph line with inline formatting
        blocks.push_back(parseInline(trimmed));
    };

    for (const auto& rawLine : lines) {
        switch (state) {
            case State::IN_CODE_BLOCK: {
                if (startsWith(ltrim(rawLine), "```")) {
                    flushCodeBlock();
                    state = State::NORMAL;
                } else {
                    codeLines.push_back(text(rawLine));
                }
                break;
            }

            case State::IN_BLOCKQUOTE: {
                if (startsWith(rawLine, "> ")) {
                    quoteLines.push_back(parseInline(rawLine.substr(2)));
                } else if (rawLine == ">") {
                    quoteLines.push_back(text(""));
                } else {
                    // End of blockquote — re-process line in NORMAL state
                    flushBlockquote();
                    state = State::NORMAL;
                    processNormal(rawLine);
                }
                break;
            }

            case State::NORMAL: {
                processNormal(rawLine);
                break;
            }
        }
    }

    // Flush any unclosed blocks (graceful degradation)
    if (state == State::IN_CODE_BLOCK) {
        flushCodeBlock();
    }
    if (state == State::IN_BLOCKQUOTE) {
        flushBlockquote();
    }

    if (blocks.empty()) {
        return text("");
    }
    return vbox(std::move(blocks));
}

} // namespace gaia

#endif // GAIA_HAS_TUI
