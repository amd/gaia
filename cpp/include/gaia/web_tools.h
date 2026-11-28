// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Web tools: fetch a page as readable text. Mirrors the fetch half of
// Python's BrowserToolsMixin (fetch_page).

#pragma once

#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>

#include "gaia/export.h"
#include "gaia/tool_registry.h"
#include "gaia/types.h"

namespace gaia {

/// Pages web_fetch has read, by URL without its #fragment. The owner clears it
/// each turn; until then another look at a page (a later part, a find) is served
/// from here, with no approval card and no network request.
class GAIA_API WebPageCache {
public:
    struct Page {
        std::string url;          // after redirects
        std::string contentType;
        std::string text;
    };

    std::optional<Page> get(const std::string& url) const;
    bool has(const std::string& url) const;
    void put(const std::string& url, Page page);
    void clear();

    /// The cache key: the URL with any #fragment removed.
    static std::string keyOf(const std::string& url);

private:
    static constexpr size_t kMaxPages = 16;
    static constexpr size_t kMaxBytes = 64u * 1024u * 1024u;
    mutable std::mutex mu_;
    std::map<std::string, Page> pages_;
    std::deque<std::string> order_;  // oldest first
    size_t bytes_ = 0;
};

class GAIA_API WebTools {
public:
    /// With a cache, a page is fetched once until the cache is cleared.
    static void registerAll(ToolRegistry& registry, std::shared_ptr<WebPageCache> cache = nullptr);

    /// web_fetch: GET an http(s) URL and return its text (HTML reduced to text).
    /// Redirects are followed by hand (at most 5), and every hop that resolves
    /// to a loopback, private, link-local or metadata address is refused unless
    /// GAIA_WEB_FETCH_ALLOW_PRIVATE=1. Bodies past 8 MB are cut. A long page is
    /// returned a part at a time: `offset` reads on, `find` returns the passages
    /// around a word, and a cut part names the offset of the next one.
    /// Policy: CONFIRM — a request can carry data off the machine. A page already
    /// in the cache is served without asking, since nothing is sent.
    static ToolInfo webFetch(std::shared_ptr<WebPageCache> cache = nullptr);

    /// HTML to readable text: drops script/style/head, turns block tags into
    /// line breaks, decodes common entities, collapses blank runs.
    static std::string htmlToText(const std::string& html);

private:
    static json doWebFetch(const json& args, const std::shared_ptr<WebPageCache>& cache);
};

} // namespace gaia
