// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/secrets.h"

#include <algorithm>
#include <array>
#include <cctype>
#include <cstring>
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
    std::size_t group;  // the capture the secret starts at; 0 = the whole match
};

struct PatternSource {
    const char* re;
    bool notAfterGpio;
    std::size_t group = 0;
    bool caseSensitive = false;
};

const std::vector<Pattern>& credentialPatterns() {
    static const std::vector<Pattern> patterns = [] {
        const auto flags = std::regex::ECMAScript | std::regex::icase | std::regex::optimize;
        const auto casedFlags = std::regex::ECMAScript | std::regex::optimize;
        const std::vector<PatternSource> sources = {
            {R"(\bsk-ant-[a-zA-Z0-9_-]{10,})", false},
            {R"(\bsk-[a-zA-Z0-9_-]{20,})", false},
            {R"(\bgh[pousr]_[a-zA-Z0-9]{20,})", false},
            {R"(\bgithub_pat_[a-zA-Z0-9_]{20,})", false},
            {R"(\bAKIA[0-9A-Z]{16}\b)", false},
            {R"(\bAIza[0-9A-Za-z_-]{20,})", false},
            {R"(\bxox[baprs]-[a-zA-Z0-9-]{10,})", false},
            {R"(\bglpat-[a-zA-Z0-9_-]{20,})", false},
            {R"(\bBearer\s+[a-zA-Z0-9._-]{20,})", false},
            // Group 1 stands in for a lookbehind: `_` may precede the word (DB_PASSWORD), a letter may not.
            {R"((?:^|[^A-Za-z0-9])((?:password|passphrase|api[_ -]?key|secret(?:[_ -]?(?:access[_ -]?)?key)?|access[_ -]?token|auth[_ -]?token|credential)s?\b\s*(?:is(?!\s+(?:in|on|at|with|not|stored|kept|saved|the|a|an)\b)|:|=)\s*\S+))",
             false, 1},
            // An environment-style name with a value that is not a number, a boolean or a short setting.
            {R"((?:^|[^A-Za-z0-9_])((?:[A-Z0-9]+_)*(?:TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|(?:API|ACCESS|PRIVATE|SECRET)_KEY))"
             R"((?:\s*[=:]\s*(?!(?:[0-9]+|[Tt]rue|TRUE|[Ff]alse|FALSE)[,;]?(?:\s|$))\S+|)"
             R"((?:_[A-Z0-9]+)+\s*[=:]\s*(?!(?:[0-9]+|[Tt]rue|TRUE|[Ff]alse|FALSE)[,;]?(?:\s|$))\S{8,})))",
             false, 1, true},
            {R"(\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})", false},
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
        for (const auto& s : sources) {
            out.push_back({std::regex(s.re, s.caseSensitive ? casedFlags : flags), s.notAfterGpio, s.group});
        }
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

// A PEM private key, from its BEGIN line to its END line (or to the end of the text when cut short).
void privateKeySpans(const std::string& text, std::vector<std::pair<std::size_t, std::size_t>>& spans) {
    static const std::string begin = "-----BEGIN ";
    static const std::string tail = "PRIVATE KEY-----";
    std::size_t at = 0;
    while ((at = text.find(begin, at)) != std::string::npos) {
        const std::size_t dashes = text.find("-----", at + begin.size());
        if (dashes == std::string::npos) break;
        const std::size_t headEnd = dashes + 5;
        if (headEnd - at > 80 || headEnd - at < begin.size() + tail.size() ||
            text.compare(headEnd - tail.size(), tail.size(), tail) != 0) {
            at += begin.size();
            continue;
        }
        std::size_t end = text.size();
        const std::size_t footer = text.find("-----END ", headEnd);
        if (footer != std::string::npos) {
            const std::size_t close = text.find("-----", footer + 9);
            if (close != std::string::npos) end = close + 5;
        }
        spans.emplace_back(at, end);
        at = end;
    }
}

}  // namespace

std::vector<std::pair<std::size_t, std::size_t>> credentialSpans(const std::string& text) {
    std::vector<std::pair<std::size_t, std::size_t>> spans;
    if (text.empty()) return spans;
    cardSpans(text, spans);
    privateKeySpans(text, spans);

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
                    const std::size_t s = w + static_cast<std::size_t>(it->position(p.group));
                    if (p.notAfterGpio && precededByGpio(text, s)) continue;
                    std::size_t e = w + static_cast<std::size_t>(it->position(0) + it->length(0));
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

namespace {

constexpr const char* kRedacted = "[redacted]";
constexpr const char* kHiddenLine = "[credential file content hidden]";

bool isSpace(char c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f'; }
bool isUpper(char c) { return c >= 'A' && c <= 'Z'; }
bool isLower(char c) { return c >= 'a' && c <= 'z'; }
bool isAlpha(char c) { return isUpper(c) || isLower(c); }
bool isAlnum(char c) { return isAlpha(c) || isDigit(c); }
bool isWord(char c) { return isAlnum(c) || c == '_'; }
bool isUpperName(char c) { return isUpper(c) || isDigit(c) || c == '_'; }
char lowerChar(char c) { return isUpper(c) ? static_cast<char>(c - 'A' + 'a') : c; }

std::string lowerCopy(std::string s) {
    for (char& c : s) c = lowerChar(c);
    return s;
}

// @p word is lower case.
bool startsWithNoCase(const std::string& text, std::size_t at, const char* word, std::size_t len) {
    if (at > text.size() || text.size() - at < len) return false;
    for (std::size_t k = 0; k < len; ++k) {
        if (lowerChar(text[at + k]) != word[k]) return false;
    }
    return true;
}

template <typename Pred>
bool allOf(const std::string& s, std::size_t from, std::size_t to, Pred pred) {
    for (std::size_t i = from; i < to; ++i) {
        if (!pred(s[i])) return false;
    }
    return true;
}

struct TokenShape {
    const char* prefix;  // lower case
    const char* extra;   // body characters besides letters and digits
    std::size_t min;
    bool exact;  // exactly min characters, then a word boundary
};

const TokenShape kTokenShapes[] = {
    {"sk-ant-", "_-", 10, false},   {"sk-", "_-", 20, false},    {"ghp_", "", 20, false},
    {"gho_", "", 20, false},        {"ghu_", "", 20, false},     {"ghs_", "", 20, false},
    {"ghr_", "", 20, false},        {"github_pat_", "_", 20, false}, {"akia", "", 16, true},
    {"aiza", "_-", 20, false},      {"xoxb-", "-", 10, false},   {"xoxa-", "-", 10, false},
    {"xoxp-", "-", 10, false},      {"xoxr-", "-", 10, false},   {"xoxs-", "-", 10, false},
    {"glpat-", "_-", 20, false},
};

// End of the provider token starting at @p at, or npos. A prefix glued to a word or '-' is a name.
std::size_t tokenEndAt(const std::string& text, std::size_t at) {
    const char first = lowerChar(text[at]);
    if (first != 's' && first != 'g' && first != 'a' && first != 'x') return std::string::npos;
    if (at > 0 && (isWord(text[at - 1]) || text[at - 1] == '-')) return std::string::npos;
    for (const TokenShape& shape : kTokenShapes) {
        const std::size_t prefixLen = std::strlen(shape.prefix);
        if (!startsWithNoCase(text, at, shape.prefix, prefixLen)) continue;
        const std::size_t bodyStart = at + prefixLen;
        std::size_t end = bodyStart;
        while (end < text.size() &&
               (isAlnum(text[end]) || (text[end] != '\0' && std::strchr(shape.extra, text[end]) != nullptr))) {
            ++end;
        }
        if (end - bodyStart < shape.min) continue;
        if (shape.exact) {
            end = bodyStart + shape.min;
            if (end < text.size() && isWord(text[end])) continue;
        }
        return end;
    }
    return std::string::npos;
}

// A run of 16+ key characters holding both a letter and a digit: what a generated token has and a hyphenated name lacks.
bool looksGenerated(const std::string& text, std::size_t from, std::size_t to) {
    std::size_t run = 0;
    bool digit = false;
    bool alpha = false;
    for (std::size_t i = from; i <= to; ++i) {
        if (i < to && isWord(text[i])) {
            ++run;
            digit = digit || isDigit(text[i]);
            alpha = alpha || isAlpha(text[i]);
            continue;
        }
        if (run >= 16 && digit && alpha) return true;
        run = 0;
        digit = alpha = false;
    }
    return false;
}

// A template or a stand-in for a secret, not a secret.
bool isPlaceholder(const std::string& v) {
    if (v.empty()) return true;
    static const std::set<std::string> words = {"password", "passwd",  "pass",       "pwd",          "secret",
                                                "token",    "changeme", "example",    "mypassword",   "yourpassword"};
    if (words.count(lowerCopy(v))) return true;
    const std::size_t n = v.size();
    if (v[0] == '{') {
        std::size_t i = 1;
        if (i < n && v[i] == '{') ++i;
        while (i < n && v[i] != '{' && v[i] != '}') ++i;
        if (i < n && v[i] == '}') {
            ++i;
            if (i < n && v[i] == '}') ++i;
            if (i == n) return true;
        }
    }
    if (v[0] == '$' && n >= 2) {
        if (v[1] == '{' && n >= 4 && v[n - 1] == '}') {
            const bool env = (isUpper(v[2]) || v[2] == '_') && allOf(v, 3, n - 1, isUpperName);
            const bool camel = isLower(v[2]) && allOf(v, 3, n - 1, isAlnum);
            if (env || camel) return true;
        }
        if ((isUpper(v[1]) || v[1] == '_') && allOf(v, 2, n, isUpperName)) return true;
    }
    const auto noParen = [](char c) { return c != '(' && c != ')'; };
    if (n >= 4 && v[0] == '%' && v[1] == '(' && v[n - 2] == ')' && (v[n - 1] == 's' || v[n - 1] == 'd') &&
        allOf(v, 2, n - 2, noParen)) {
        return true;
    }
    const auto noAngle = [](char c) { return c != '<' && c != '>'; };
    if (n >= 2 && v[0] == '<' && v[n - 1] == '>' && allOf(v, 1, n - 1, noAngle)) return true;
    static const std::string prefixes[] = {"YOUR_", "MY_"};
    for (const std::string& prefix : prefixes) {
        if (n > prefix.size() && v.compare(0, prefix.size(), prefix) == 0 && allOf(v, prefix.size(), n, isUpperName)) {
            return true;
        }
    }
    if (n > 5 && v.compare(n - 5, 5, "_HERE") == 0 && allOf(v, 0, n - 5, isUpperName)) return true;
    const char c = v[0];
    return (c == 'x' || c == 'X' || c == '*' || c == '.') && v.find_first_not_of(c) == std::string::npos;
}

bool endsUserinfo(char c) { return c == '/' || c == '@' || c == '?' || c == '#' || isSpace(c); }

// File text: a bare user ("git@", "postgres@") is a login name, and a placeholder password is no secret.
bool secretUserinfo(const std::string& text, std::size_t start, std::size_t end) {
    const auto first = text.begin() + static_cast<std::ptrdiff_t>(start);
    const auto last = text.begin() + static_cast<std::ptrdiff_t>(end);
    const auto colon = std::find(first, last, ':');
    if (colon == first) return false;
    if (colon == last) return end - start >= 20 && looksGenerated(text, start, end);
    return !isPlaceholder(std::string(colon + 1, last));
}

// What sits between "://" and the "@" before the host.
std::string maskUserinfo(const std::string& text, bool fileText) {
    std::string out;
    std::size_t copied = 0;
    std::size_t at = 0;
    while ((at = text.find("://", at)) != std::string::npos) {
        const std::size_t start = at + 3;
        std::size_t end = start;
        while (end < text.size() && !endsUserinfo(text[end])) ++end;
        at = start;
        if (end == start || end >= text.size() || text[end] != '@') continue;
        at = end;
        if (fileText && !secretUserinfo(text, start, end)) continue;
        out.append(text, copied, start - copied);
        out += kRedacted;
        copied = end;
    }
    if (copied == 0) return text;
    out.append(text, copied, std::string::npos);
    return out;
}

// Start of the value of the secret query parameter named after the '?' or '&' at @p at, or npos.
std::size_t secretQueryValueAt(const std::string& text, std::size_t at) {
    static const char* const names[] = {"access_token", "private_token", "token",  "api_key", "apikey", "key",
                                        "secret",       "password",      "passwd", "auth",    "sig",    "signature"};
    for (const char* name : names) {
        const std::size_t len = std::strlen(name);
        const std::size_t equals = at + 1 + len;
        if (equals < text.size() && text[equals] == '=' && startsWithNoCase(text, at + 1, name, len)) return equals + 1;
    }
    return std::string::npos;
}

bool endsQueryValue(char c, bool fileText) {
    if (c == '&' || c == '#' || c == '"' || c == '\'' || c == '<' || c == '>' || isSpace(c)) return true;
    // In file text a value also ends where code closes the string or the call.
    return fileText && (c == '`' || c == '(' || c == ')');
}

std::string maskSecretQueries(const std::string& text, bool fileText) {
    constexpr std::size_t kMinFileSecret = 8;
    std::string out;
    std::size_t copied = 0;
    for (std::size_t i = 0; i < text.size();) {
        const std::size_t valueStart =
            (text[i] == '?' || text[i] == '&') ? secretQueryValueAt(text, i) : std::string::npos;
        if (valueStart == std::string::npos) {
            ++i;
            continue;
        }
        std::size_t end = valueStart;
        while (end < text.size() && !endsQueryValue(text[end], fileText)) ++end;
        if (end == valueStart) {
            ++i;
            continue;
        }
        i = end;
        if (fileText &&
            (end - valueStart < kMinFileSecret || isPlaceholder(text.substr(valueStart, end - valueStart)))) {
            continue;
        }
        out.append(text, copied, valueStart - copied);
        out += kRedacted;
        copied = end;
    }
    if (copied == 0) return text;
    out.append(text, copied, std::string::npos);
    return out;
}

std::string maskTokens(const std::string& text, bool fileText) {
    std::string out;
    std::size_t copied = 0;
    for (std::size_t i = 0; i < text.size();) {
        const std::size_t end = tokenEndAt(text, i);
        if (end == std::string::npos) {
            ++i;
            continue;
        }
        // Only the bare "sk-" prefix collides with ordinary hyphenated names.
        const bool name = fileText && startsWithNoCase(text, i, "sk-", 3) && !looksGenerated(text, i, end);
        if (!name) {
            out.append(text, copied, i - copied);
            out += kRedacted;
            copied = end;
        }
        i = end;
    }
    if (copied == 0) return text;
    out.append(text, copied, std::string::npos);
    return out;
}

const std::set<std::string>& credentialFileNames() {
    static const std::set<std::string> names = {
        ".env",        ".env.local",   ".env.production", ".env.development", "credentials.json",
        "service_account.json",        "secrets.json",    "id_rsa",           "id_ed25519",
        "id_ecdsa",    "id_dsa",       "authorized_keys", "known_hosts",      "shadow",
        "passwd",      "sudoers",      "htpasswd",        ".netrc",           ".pgpass",
        ".my.cnf",     "wallet.dat",   "keystore.jks",    ".npmrc",           ".pypirc",
    };
    return names;
}

const std::set<std::string>& credentialFileExtensions() {
    static const std::set<std::string> extensions = {".pem", ".key", ".crt", ".cer",
                                                     ".p12", ".pfx", ".jks", ".keystore"};
    return extensions;
}

// The extension after the last dot; a name that is only leading dots and a word (".key") has none.
std::string extensionOf(const std::string& name) {
    const std::size_t dot = name.rfind('.');
    if (dot == std::string::npos || name.find_first_not_of('.') >= dot) return "";
    return name.substr(dot);
}

// Names and extensions, longest first, so ".env.local" is tried before ".env".
const std::vector<std::string>& credentialWords() {
    static const std::vector<std::string> words = [] {
        std::vector<std::string> all(credentialFileNames().begin(), credentialFileNames().end());
        all.insert(all.end(), credentialFileExtensions().begin(), credentialFileExtensions().end());
        std::stable_sort(all.begin(), all.end(),
                         [](const std::string& a, const std::string& b) { return a.size() > b.size(); });
        return all;
    }();
    return words;
}

bool startsCredentialWord(char c) {
    static const std::array<bool, 256> starts = [] {
        std::array<bool, 256> table{};
        for (const auto& word : credentialWords()) table[static_cast<unsigned char>(word[0])] = true;
        return table;
    }();
    return starts[static_cast<unsigned char>(lowerChar(c))];
}

std::string stripAnsi(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (std::size_t i = 0; i < s.size();) {
        if (s[i] == '\x1b' && i + 1 < s.size() && s[i + 1] == '[') {
            std::size_t j = i + 2;
            while (j < s.size() && (isDigit(s[j]) || s[j] == ';')) ++j;
            if (j < s.size() && isAlpha(s[j])) {
                i = j + 1;
                continue;
            }
        }
        out.push_back(s[i++]);
    }
    return out;
}

// How much of @p line to keep when its "file:" prefix names a credential file, or npos when it does not.
std::size_t credentialPrefixEnd(const std::string& line) {
    constexpr std::size_t kPrefixSpan = 4096;
    const bool coloured = line.find('\x1b') != std::string::npos;
    const std::string stripped = coloured ? stripAnsi(line.substr(0, kPrefixSpan * 4)) : std::string();
    const std::string& plain = coloured ? stripped : line;
    // Select-String marks a match line with "> " and indents context lines.
    std::size_t start = 0;
    while (start < plain.size() && (plain[start] == ' ' || plain[start] == '>')) ++start;
    const std::size_t limit = std::min(plain.size(), start + kPrefixSpan);
    std::size_t nameStart = start;
    std::size_t scanned = start;
    for (std::size_t i = start; i < limit;) {
        std::size_t end = 0;
        if (startsCredentialWord(plain[i])) {
            for (const auto& word : credentialWords()) {
                if (i + word.size() <= limit && startsWithNoCase(plain, i, word.c_str(), word.size())) {
                    end = i + word.size();
                    break;
                }
            }
        }
        if (end == 0) {
            ++i;
            continue;
        }
        // A prefix ends at ':' (a match), '-' (a context line) or NUL (grep -Z).
        if (end < plain.size() && (plain[end] == ':' || plain[end] == '-' || plain[end] == '\0')) {
            for (std::size_t k = i; k > scanned; --k) {
                const char c = plain[k - 1];
                if (c != '/' && c != '\\' && c != ':') continue;
                if (k - 1 >= nameStart) nameStart = k;
                break;
            }
            scanned = i;
            // Colour codes move the offsets, so a coloured line goes whole.
            if (isCredentialFileName(plain.substr(nameStart, end - nameStart))) return coloured ? 0 : end + 1;
        }
        i = end;
    }
    return std::string::npos;
}

std::string maskCommandOutput(const std::string& text) { return redactCredentials(maskCredentialFileLines(text)); }

void mapStrings(json& value, std::string (*mask)(const std::string&)) {
    if (value.is_string()) {
        std::string masked = mask(value.get_ref<const std::string&>());
        value = std::move(masked);
    } else if (value.is_structured()) {
        for (auto& item : value) mapStrings(item, mask);
    }
}

}  // namespace

namespace {
// A key printed by a command (cat id_rsa) is a credential no token pattern matches.
std::string maskPrivateKeys(std::string text) {
    std::vector<std::pair<std::size_t, std::size_t>> spans;
    privateKeySpans(text, spans);
    for (auto it = spans.rbegin(); it != spans.rend(); ++it) {
        text.replace(it->first, it->second - it->first, "[redacted private key]");
    }
    return text;
}
}  // namespace

std::string redactCredentials(const std::string& text) {
    return maskPrivateKeys(maskTokens(maskSecretQueries(maskUserinfo(text, false), false), false));
}

std::string redactFileText(const std::string& text) {
    return maskPrivateKeys(maskTokens(maskSecretQueries(maskUserinfo(text, true), true), true));
}

bool holdsCredentials(const std::string& text) { return !text.empty() && redactFileText(text) != text; }

bool isCredentialFileName(const std::string& name) {
    const std::string lowered = lowerCopy(name);
    return credentialFileNames().count(lowered) > 0 || credentialFileExtensions().count(extensionOf(lowered)) > 0;
}

std::string maskCredentialFileLines(const std::string& text) {
    std::string out;
    std::size_t copied = 0;
    std::size_t lineStart = 0;
    while (lineStart < text.size()) {
        std::size_t lineEnd = text.find_first_of("\r\n", lineStart);
        if (lineEnd == std::string::npos) lineEnd = text.size();
        std::size_t next = lineEnd;
        if (next < text.size()) next += (text[next] == '\r' && next + 1 < text.size() && text[next + 1] == '\n') ? 2 : 1;
        if (lineEnd > lineStart) {
            const std::string line = text.substr(lineStart, lineEnd - lineStart);
            const std::size_t keep = credentialPrefixEnd(line);
            if (keep != std::string::npos) {
                out.append(text, copied, lineStart - copied);
                out.append(line, 0, keep);
                out += kHiddenLine;
                copied = lineEnd;
            }
        }
        lineStart = next;
    }
    if (copied == 0) return text;
    out.append(text, copied, std::string::npos);
    return out;
}

json redactToolOutput(json value) {
    mapStrings(value, maskCommandOutput);
    return value;
}

json redactFileContent(json value) {
    mapStrings(value, redactFileText);
    return value;
}

json redactToolResult(const std::string& tool, json value) {
    static const std::set<std::string> printsCommandOutput = {"bash_execute", "git_status", "git_diff", "git_log",
                                                              "git_show"};
    return printsCommandOutput.count(tool) > 0 ? redactToolOutput(std::move(value))
                                               : redactFileContent(std::move(value));
}

}  // namespace gaia
