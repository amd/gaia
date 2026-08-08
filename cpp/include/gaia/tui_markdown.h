// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Standalone markdown -> FTXUI element renderer.
// Implementation in src/tui_markdown.cpp (no external dependencies).

#pragma once

#ifdef GAIA_HAS_TUI

#include <string>

#include <ftxui/dom/elements.hpp>

#include "gaia/export.h"

namespace gaia {

/// Render a markdown string as an FTXUI element.
///
/// Supported: `#`/`##`/`###` headings, `**bold**`, `` `inline code` ``,
/// fenced code blocks, `-` bullet lists, `>` blockquotes, and paragraphs.
/// Unsupported syntax degrades to plain text.
GAIA_API ftxui::Element renderMarkdown(const std::string& markdown);

} // namespace gaia

#endif // GAIA_HAS_TUI
