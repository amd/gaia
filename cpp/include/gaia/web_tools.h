// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Web tools: fetch a page as readable text. Mirrors the fetch half of
// Python's BrowserToolsMixin (fetch_page).

#pragma once

#include <string>

#include "gaia/export.h"
#include "gaia/tool_registry.h"
#include "gaia/types.h"

namespace gaia {

class GAIA_API WebTools {
public:
    static void registerAll(ToolRegistry& registry);

    /// web_fetch: GET an http(s) URL and return its text (HTML reduced to text).
    /// Redirects are followed by hand (at most 5), and every hop that resolves
    /// to a loopback, private, link-local or metadata address is refused unless
    /// GAIA_WEB_FETCH_ALLOW_PRIVATE=1. Bodies past 8 MB are cut.
    /// Policy: CONFIRM — a request can carry data off the machine.
    static ToolInfo webFetch();

    /// HTML to readable text: drops script/style/head, turns block tags into
    /// line breaks, decodes common entities, collapses blank runs.
    static std::string htmlToText(const std::string& html);

private:
    static json doWebFetch(const json& args);
};

} // namespace gaia
