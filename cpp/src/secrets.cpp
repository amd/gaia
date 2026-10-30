// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/secrets.h"

#include <algorithm>
#include <cctype>
#include <regex>
#include <set>
#include <vector>

namespace gaia {

namespace {

// `[-–—]` from the Python patterns, spelled in UTF-8 bytes.
#define GAIA_SEP "(?:is|was|to|:|=|-|\xE2\x80\x93|\xE2\x80\x94)"

struct Pattern {
    std::regex re;
    bool notAfterGpio;  // Python's (?<!gpio ) lookbehind, which std::regex lacks
};

const std::vector<Pattern>& credentialPatterns() {
    static const std::vector<Pattern> patterns = [] {
        const auto flags = std::regex::ECMAScript | std::regex::icase | std::regex::optimize;
        const std::vector<std::pair<const char*, bool>> sources = {
            {R"(\bsk-ant-[a-zA-Z0-9_-]{10,})", false},
            {R"(\bsk-[a-zA-Z0-9_-]{20,})", false},
            {R"(\bgh[pousr]_[a-zA-Z0-9]{20,})", false},
            {R"(\bgithub_pat_[a-zA-Z0-9_]{20,})", false},
            {R"(\bAKIA[0-9A-Z]{16}\b)", false},
            {R"(\bAIza[0-9A-Za-z_-]{20,})", false},
            {R"(\bxox[baprs]-[a-zA-Z0-9-]{10,})", false},
            {R"(\bglpat-[a-zA-Z0-9_-]{20,})", false},
            {R"(\bBearer\s+[a-zA-Z0-9._-]{20,})", false},
            {R"(\b(?:password|passphrase|api[_ -]?key|secret|access[_ -]?token|auth[_ -]?token|credential)s?\b\s*(?:is|:|=)\s*\S+)",
             false},
            {R"(\b[A-Z][A-Z0-9]{2,}[-_][0-9]{2,}[-_][A-Z][A-Z0-9]{2,}\b)", false},
            {R"(\bpin(?:'s)?(?:\s+number)?\s+(?:for|on|at|of)(?:\s+\w+){1,8}?\s*)" GAIA_SEP
             R"(\s*\d{3,8}\b)",
             false},
            {R"(\bpin(?:'s)?(?:\s+number)?\s+(?:for|on|at|of)(?!\s+page\b)(?:\s+\w+){1,8}?\s+\d{3,8}\b)",
             false},
            {R"(\bpin\s+code\s*(?:)" GAIA_SEP R"(\s*)?\d{3,8}\b)", false},
            {R"(\bpin\s+code\s+(?:for|on|at|of)(?:\s+\w+){1,8}?\s{0,3})" GAIA_SEP
             R"(\s{0,3}\d{3,8}\b)",
             false},
            {R"(\bpin\s+code\s+(?:for|on|at|of)(?!\s+page\b)(?:\s+\w+){1,8}?\s+\d{3,8}\b)", false},
            {R"(\bpin(?:'s)?(?!\s+code\b)(?:\s+number)?\s*)" GAIA_SEP R"(\s*\d{3,8}\b)", true},
            {R"(\bpin(?:'s)?(?!\s+(?:code|on|at|for)\b)(?:\s+number)?\s+\d{3,8}\b)", true},
            {R"(\bpin\s{0,3}(?:#|:|=|\()\s{0,3}\d{3,8}\b\s{0,3}\)?)", false},
            {R"(\b\d{3,8}\s+is\s+(?:(?:my|your|their|the)\s+|user(?:'s)?\s+)?pin\b)", false},
            {R"(\b(?:passcode|pass code|cvv2?|cvc|security code)(?:'s)?\b(?:\s{1,3}(?:number|code)\b)?(?:\s{1,3}(?:for|on|at|of)(?:\s{1,3}\w+){1,3}?)?(?:\s{0,3})" GAIA_SEP
             R"()?\s{0,3}\d{3,8}\b)",
             false},
            {R"(\b\d{3}-\d{2}-\d{4}\b)", false},
            {R"(\b(?:ssn|social security(?: number)?)\b(?:\s{0,3}(?:is|was|:|=))?\s{0,3}\d{3}[ -]?\d{2}[ -]?\d{4}\b)",
             false},
        };
        std::vector<Pattern> out;
        out.reserve(sources.size());
        for (const auto& [src, gpio] : sources) out.push_back({std::regex(src, flags), gpio});
        return out;
    }();
    return patterns;
}

#undef GAIA_SEP

bool precededByGpio(const std::string& text, std::size_t pos) {
    if (pos < 5) return false;
    std::string before = text.substr(pos - 5, 5);
    std::transform(before.begin(), before.end(), before.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return before == "gpio ";
}

// Length in bytes of a card-number separator at @p i, or 0: '.', '-', a dash
// U+2010..U+2015, or whitespace other than a line break — ASCII, U+00A0,
// U+1680, U+2000..U+200A, U+202F, U+205F, U+3000.
std::size_t separatorAt(const std::string& s, std::size_t i) {
    const unsigned char c = static_cast<unsigned char>(s[i]);
    if (c == ' ' || c == '\t' || c == '\v' || c == '\f' || c == '.' || c == '-') return 1;
    const auto at = [&s, i](std::size_t k) {
        return i + k < s.size() ? static_cast<unsigned char>(s[i + k]) : 0;
    };
    if (c == 0xC2 && at(1) == 0xA0) return 2;
    if (c == 0xE1 && at(1) == 0x9A && at(2) == 0x80) return 3;
    if (c == 0xE2 && at(1) == 0x80) {
        const unsigned char d = at(2);
        if ((d >= 0x80 && d <= 0x8A) || (d >= 0x90 && d <= 0x95) || d == 0xAF) return 3;
    }
    if (c == 0xE2 && at(1) == 0x81 && at(2) == 0x9F) return 3;
    if (c == 0xE3 && at(1) == 0x80 && at(2) == 0x80) return 3;
    return 0;
}

bool isDigit(char c) { return c >= '0' && c <= '9'; }

const std::set<std::vector<int>>& cardGroupings() {
    static const std::set<std::vector<int>> groupings = [] {
        std::set<std::vector<int>> g;
        for (int k = 13; k <= 19; ++k) g.insert({k});
        for (const auto& v : std::vector<std::vector<int>>{
                 {4, 4, 4, 4}, {4, 4, 4, 4, 3}, {4, 4, 4, 3}, {4, 4, 5}, {4, 6, 5}, {4, 6, 4}, {6, 13}}) {
            g.insert(v);
        }
        return g;
    }();
    return groupings;
}

bool isGroupingPrefix(const std::vector<int>& groups) {
    for (const auto& g : cardGroupings()) {
        if (g.size() >= groups.size() && std::equal(groups.begin(), groups.end(), g.begin())) return true;
    }
    return false;
}

bool luhnOk(const std::string& digits) {
    int sum = 0;
    bool dbl = false;
    for (auto it = digits.rbegin(); it != digits.rend(); ++it) {
        int d = *it - '0';
        if (dbl) {
            d *= 2;
            if (d > 9) d -= 9;
        }
        sum += d;
        dbl = !dbl;
    }
    return sum % 10 == 0;
}

bool cardPrefixOk(const std::string& digits) {
    static const std::regex prefix(
        R"(4\d{12}(?:\d{3}){0,2}|(?:5[1-5]|2[2-7])\d{14}|3[47]\d{13}|6011\d{12,15}|64[4-9]\d{13,16}|(?:65|35|62)\d{14,17}|(?:30[0-5]|36|38)\d{11,16})",
        std::regex::ECMAScript | std::regex::optimize);
    return std::regex_match(digits, prefix);
}

void cardSpans(const std::string& text, std::vector<std::pair<std::size_t, std::size_t>>& spans) {
    std::size_t i = 0;
    while (i < text.size()) {
        if (!isDigit(text[i]) || (i > 0 && isDigit(text[i - 1]))) {
            ++i;
            continue;
        }
        // One run: digits joined by at most 8 separator bytes-worth of characters.
        std::vector<std::size_t> pos;
        std::size_t j = i;
        while (j < text.size() && isDigit(text[j])) {
            pos.push_back(j++);
            std::size_t k = j;
            int seps = 0;
            while (k < text.size() && seps < 8) {
                const std::size_t len = separatorAt(text, k);
                if (len == 0) break;
                k += len;
                ++seps;
            }
            if (k > j && k < text.size() && isDigit(text[k])) j = k;
        }
        const std::size_t runEnd = j;
        const std::size_t n = pos.size();
        if (n >= 13) {
            std::string digits;
            digits.reserve(n);
            for (auto p : pos) digits.push_back(text[p]);
            std::vector<std::size_t> edges{0};
            for (std::size_t k = 1; k < n; ++k) {
                if (pos[k] != pos[k - 1] + 1) edges.push_back(k);
            }
            edges.push_back(n);
            std::size_t resume = 0;
            for (std::size_t a = 0; a + 1 < edges.size(); ++a) {
                const std::size_t start = edges[a];
                if (start < resume) continue;
                std::vector<std::size_t> ends;
                std::vector<int> groups;
                for (std::size_t b = a + 1; b < edges.size(); ++b) {
                    groups.push_back(static_cast<int>(edges[b] - edges[b - 1]));
                    if (!isGroupingPrefix(groups)) break;
                    if (cardGroupings().count(groups)) ends.push_back(edges[b]);
                }
                for (auto e = ends.rbegin(); e != ends.rend(); ++e) {
                    const std::string window = digits.substr(start, *e - start);
                    if (luhnOk(window) && cardPrefixOk(window)) {
                        spans.emplace_back(pos[start], pos[*e - 1] + 1);
                        resume = *e;
                        break;
                    }
                }
            }
        }
        i = runEnd > i ? runEnd : i + 1;
    }
}

}  // namespace

std::vector<std::pair<std::size_t, std::size_t>> credentialSpans(const std::string& text) {
    std::vector<std::pair<std::size_t, std::size_t>> spans;
    if (text.empty()) return spans;
    cardSpans(text, spans);

    // Line by line, in bounded windows: std::regex backtracks recursively, and
    // a credential never needs more than a few hundred characters of context.
    constexpr std::size_t kWindow = 4096;
    constexpr std::size_t kOverlap = 256;
    std::size_t lineStart = 0;
    while (lineStart < text.size()) {
        std::size_t lineEnd = text.find('\n', lineStart);
        if (lineEnd == std::string::npos) lineEnd = text.size();
        for (std::size_t w = lineStart; w < lineEnd; w += kWindow - kOverlap) {
            const std::size_t wEnd = std::min(lineEnd, w + kWindow);
            const std::string chunk = text.substr(w, wEnd - w);
            for (const auto& p : credentialPatterns()) {
                for (std::sregex_iterator it(chunk.begin(), chunk.end(), p.re), end; it != end; ++it) {
                    const std::size_t s = w + static_cast<std::size_t>(it->position(0));
                    if (p.notAfterGpio && precededByGpio(text, s)) continue;
                    std::size_t e = s + static_cast<std::size_t>(it->length(0));
                    // A match cut by the window edge may run on: match it again from its own start.
                    if (e == wEnd && wEnd < lineEnd) {
                        const std::string rest = text.substr(s, std::min(lineEnd, s + kWindow) - s);
                        std::smatch full;
                        if (std::regex_search(rest, full, p.re, std::regex_constants::match_continuous)) {
                            e = std::max(e, s + static_cast<std::size_t>(full.length(0)));
                        }
                    }
                    spans.emplace_back(s, e);
                }
            }
            if (wEnd == lineEnd) break;
        }
        lineStart = lineEnd + 1;
    }

    if (spans.empty()) return spans;
    std::sort(spans.begin(), spans.end());
    std::vector<std::pair<std::size_t, std::size_t>> merged{spans.front()};
    for (std::size_t k = 1; k < spans.size(); ++k) {
        if (spans[k].first <= merged.back().second) {
            merged.back().second = std::max(merged.back().second, spans[k].second);
        } else {
            merged.push_back(spans[k]);
        }
    }
    return merged;
}

bool looksLikeCredential(const std::string& text) { return !credentialSpans(text).empty(); }

std::string maskCredentials(const std::string& text) {
    const auto spans = credentialSpans(text);
    if (spans.empty()) return text;
    std::string out;
    out.reserve(text.size());
    std::size_t cursor = 0;
    for (const auto& [s, e] : spans) {
        out.append(text, cursor, s - cursor);
        out += "[secret removed]";
        cursor = e;
    }
    out.append(text, cursor, std::string::npos);
    return out;
}

}  // namespace gaia
