// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// An HTML body as the text outgoing._TextExtractor pulls from it, on
// html.parser.HTMLParser(convert_charrefs=True) fed once and closed.

#pragma once

#include <string>

namespace gaia::mail {

/// The text of `html`: breaks for block tags, nothing from script, style, head or title.
std::string htmlText(const std::string& html);

}  // namespace gaia::mail
