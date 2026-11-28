// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Ported from src/gaia/agents/tools/_email/outgoing.py, and from the parts of
// Python's email package gmail.raw_mime runs (header folding, RFC 2047 words,
// content-transfer-encoding choice), so Gmail receives the same bytes from
// either runtime. Lengths are code points, as Python counts them.

#include "mail_outgoing.h"

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <deque>
#include <random>
#include <set>
#include <stdexcept>

#include "mail_text.h"

namespace gaia::mail {

using text::Cps;

namespace {

bool isCategoryCc(char32_t c) { return c < 0x20 || (c >= 0x7F && c <= 0x9F); }

// unicodedata.category(c) == "Cf", Unicode 15.0 (Python 3.12).
bool isCategoryCf(char32_t c) {
    static const std::pair<char32_t, char32_t> kCf[] = {
        {0xAD, 0xAD},       {0x600, 0x605},     {0x61C, 0x61C},     {0x6DD, 0x6DD},   {0x70F, 0x70F},
        {0x890, 0x891},     {0x8E2, 0x8E2},     {0x180E, 0x180E},   {0x200B, 0x200F}, {0x202A, 0x202E},
        {0x2060, 0x2064},   {0x2066, 0x206F},   {0xFEFF, 0xFEFF},   {0xFFF9, 0xFFFB}, {0x110BD, 0x110BD},
        {0x110CD, 0x110CD}, {0x13430, 0x1343F}, {0x1BCA0, 0x1BCA3}, {0x1D173, 0x1D17A}, {0xE0001, 0xE0001},
        {0xE0020, 0xE007F}};
    for (const auto& [lo, hi] : kCf) {
        if (c >= lo && c <= hi) return true;
    }
    return false;
}

std::string repr(const std::string& s) { return text::pyRepr(json(s)); }

std::string hex4(char32_t c) {
    static const char* kHex = "0123456789ABCDEF";
    std::string out;
    unsigned v = static_cast<unsigned>(c);
    int digits = 4;
    while ((v >> (4 * digits)) != 0) ++digits;
    for (int i = digits - 1; i >= 0; --i) out += kHex[(v >> (4 * i)) & 0xF];
    return out;
}

bool isAscii(const std::string& s) {
    return std::all_of(s.begin(), s.end(), [](char c) { return static_cast<unsigned char>(c) < 0x80; });
}

std::string replaceAll(std::string s, const std::string& from, const std::string& to) {
    size_t at = 0;
    while ((at = s.find(from, at)) != std::string::npos) {
        s.replace(at, from.size(), to);
        at += to.size();
    }
    return s;
}

std::string join(const std::vector<std::string>& items, const std::string& sep) {
    std::string out;
    for (size_t i = 0; i < items.size(); ++i) out += (i ? sep : "") + items[i];
    return out;
}

// ---- checks ----

void refuseControls(const std::string& value, const std::string& field) {
    for (char32_t c : text::decode(value)) {
        if (c != U'\t' && (isCategoryCc(c) || c == 0x2028 || c == 0x2029)) {
            throw std::invalid_argument("`" + field + "` contains a line break or control character (U+" + hex4(c) +
                                        "), which could disguise what is sent. Remove it and try again.");
        }
    }
}

// RFC 3492 punycode for one label.
std::string punycode(const Cps& label) {
    constexpr uint32_t base = 36, tmin = 1, tmax = 26, skew = 38, damp = 700, initialBias = 72, initialN = 128;
    auto digit = [](uint32_t d) { return static_cast<char>(d < 26 ? 'a' + d : '0' + (d - 26)); };
    auto adapt = [&](uint32_t delta, uint32_t points, bool first) {
        delta = first ? delta / damp : delta / 2;
        delta += delta / points;
        uint32_t k = 0;
        while (delta > ((base - tmin) * tmax) / 2) {
            delta /= base - tmin;
            k += base;
        }
        return k + (base - tmin + 1) * delta / (delta + skew);
    };
    std::string out;
    for (char32_t c : label) {
        if (c < 0x80) out += static_cast<char>(c);
    }
    const uint32_t basic = static_cast<uint32_t>(out.size());
    uint32_t handled = basic;
    if (basic) out += '-';
    uint32_t n = initialN, delta = 0, bias = initialBias;
    while (handled < label.size()) {
        uint32_t m = 0x10FFFF + 1;
        for (char32_t c : label) {
            if (c >= n && c < m) m = c;
        }
        delta += (m - n) * (handled + 1);
        n = m;
        for (char32_t c : label) {
            if (c < n) ++delta;
            if (c == n) {
                uint32_t q = delta;
                for (uint32_t k = base;; k += base) {
                    const uint32_t t = k <= bias ? tmin : k >= bias + tmax ? tmax : k - bias;
                    if (q < t) break;
                    out += digit(t + (q - t) % (base - t));
                    q = (q - t) / (base - t);
                }
                out += digit(q);
                bias = adapt(delta, handled + 1, handled == basic);
                delta = 0;
                ++handled;
            }
        }
        ++delta;
        ++n;
    }
    return out;
}

// idna.encode(domain, uts46=True), for the mapping a mail domain needs: case
// folded, each non-ASCII label punycoded.
std::string idnaEncode(const std::string& domain) {
    std::vector<std::string> labels;
    const Cps all = text::decode(text::lower(domain));
    Cps label;
    auto finish = [&labels](const Cps& l) {
        if (l.empty()) throw std::invalid_argument("Empty Label");
        bool ascii = std::all_of(l.begin(), l.end(), [](char32_t c) { return c < 0x80; });
        const std::string encoded = ascii ? text::encode(l) : "xn--" + punycode(l);
        if (encoded.size() > 63) throw std::invalid_argument("Label too long");
        labels.push_back(encoded);
    };
    for (char32_t c : all) {
        if (c == U'.' || c == 0x3002 || c == 0xFF0E || c == 0xFF61) {
            finish(label);
            label.clear();
        } else {
            label.push_back(c);
        }
    }
    if (!label.empty()) finish(label);
    if (labels.empty()) throw std::invalid_argument("Empty domain");
    return join(labels, ".");
}

// _ADDRESS_RE: [^@\s<>",;]+@[^@\s<>",;]+
bool looksLikeAddress(const std::string& address) {
    const Cps cps = text::decode(address);
    size_t at = 0, ats = 0;
    for (size_t i = 0; i < cps.size(); ++i) {
        const char32_t c = cps[i];
        if (c == U'@') {
            ++ats;
            at = i;
        } else if (text::isSpace(c) || c == U'<' || c == U'>' || c == U'"' || c == U',' || c == U';') {
            return false;
        }
    }
    return ats == 1 && at > 0 && at + 1 < cps.size();
}

std::string sendableAddress(const std::string& address, const std::string& field) {
    if (!looksLikeAddress(address)) {
        throw std::invalid_argument("`" + field + "` has an address that can't be sent to: " + repr(address) +
                                    ". Use name@example.com, separating several with commas.");
    }
    const size_t at = address.rfind('@');
    const std::string local = address.substr(0, at), domain = address.substr(at + 1);
    if (!isAscii(local)) {
        throw std::invalid_argument("`" + field + "` has non-ASCII characters before the @ in " + repr(address) +
                                    ", which GAIA can't send. Check the address with the recipient.");
    }
    if (isAscii(domain)) return address;
    std::string ascii;
    try {
        ascii = idnaEncode(domain);
    } catch (const std::invalid_argument& e) {
        throw std::invalid_argument("`" + field + "` has a domain that can't be sent to: " + repr(domain) + " (" +
                                    e.what() + "). Check the spelling.");
    }
    return local + "@" + ascii;
}

Recipient sendableRecipient(const Recipient& recipient, const std::string& field) {
    std::string name = recipient.name;
    refuseControls(name, field);
    refuseControls(recipient.address, field);
    if (text::lower(name) == text::lower(recipient.address)) name.clear();
    const Cps cps = text::decode(name);
    if (std::any_of(cps.begin(), cps.end(), isCategoryCf)) {
        throw std::invalid_argument("`" + field +
                                    "` has a display name with invisible or direction-changing characters, so it "
                                    "may not read as it looks: " +
                                    recipient.address + ". Give the bare address instead.");
    }
    if (name.find_first_of("@<>") != std::string::npos) {
        throw std::invalid_argument("`" + field +
                                    "` has a display name that looks like an address but isn't the one receiving "
                                    "the mail, " +
                                    recipient.address + ". Give the bare address instead.");
    }
    return {name, sendableAddress(recipient.address, field)};
}

Recipients sendableAll(const Recipients& recipients, const std::string& field) {
    Recipients out;
    for (const auto& r : recipients) out.push_back(sendableRecipient(r, field));
    return out;
}

std::string prefixed(const std::string& prefix, const std::string& subject) {
    const std::string lowered = text::lower(subject), p = text::lower(prefix);
    if (lowered.compare(0, p.size(), p) == 0) return subject;
    return text::strip(prefix + " " + subject);
}

Recipients bare(const Recipients& recipients) {
    Recipients out;
    for (const auto& r : recipients) out.push_back({"", r.address});
    return out;
}

Recipients without(const Recipients& recipients, std::set<std::string> seen) {
    Recipients kept;
    for (const auto& r : recipients) {
        const std::string key = text::lower(r.address);
        if (seen.insert(key).second) kept.push_back(r);
    }
    return kept;
}

// ---- HTML to text (outgoing._TextExtractor, on html.parser.HTMLParser) ----

bool isAsciiAlpha(char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); }

std::string lowerAscii(std::string s) {
    for (char& c : s) {
        if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
    }
    return s;
}

// Index just past the '>' closing a tag that starts at `from`, quotes respected; npos if none.
size_t tagEnd(const std::string& html, size_t from) {
    char quote = 0;
    for (size_t i = from; i < html.size(); ++i) {
        const char c = html[i];
        if (quote) {
            if (c == quote) quote = 0;
        } else if (c == '"' || c == '\'') {
            quote = c;
        } else if (c == '>') {
            return i + 1;
        }
    }
    return std::string::npos;
}

std::string tagName(const std::string& html, size_t from) {
    size_t end = from;
    while (end < html.size() && !std::isspace(static_cast<unsigned char>(html[end])) && html[end] != '/' &&
           html[end] != '>') {
        ++end;
    }
    return lowerAscii(html.substr(from, end - from));
}

std::string htmlText(const std::string& html) {
    static const std::set<std::string> kBreaks{"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4"};
    static const std::set<std::string> kHidden{"script", "style", "head", "title"};
    std::string out, data;
    int hidden = 0;
    auto flush = [&] {
        if (!data.empty() && !hidden) out += text::htmlUnescape(data);
        data.clear();
    };
    size_t i = 0;
    while (i < html.size()) {
        if (html[i] != '<') {
            data += html[i++];
            continue;
        }
        if (html.compare(i, 4, "<!--") == 0) {
            const size_t end = html.find("-->", i + 4);
            if (end == std::string::npos) break;
            flush();
            i = end + 3;
        } else if (i + 1 < html.size() && (html[i + 1] == '!' || html[i + 1] == '?')) {
            const size_t end = html.find('>', i);
            if (end == std::string::npos) break;
            flush();
            i = end + 1;
        } else if (i + 1 < html.size() && isAsciiAlpha(html[i + 1])) {
            const size_t end = tagEnd(html, i + 1);
            if (end == std::string::npos) break;
            flush();
            const std::string name = tagName(html, i + 1);
            const bool selfClosing = end >= 2 && html[end - 2] == '/';
            if (kHidden.count(name)) {
                ++hidden;
            } else if (kBreaks.count(name)) {
                out += "\n";
            }
            i = end;
            if (selfClosing) {
                if (kHidden.count(name) && hidden) --hidden;
            } else if (name == "script" || name == "style") {
                // Raw text: nothing inside is a tag until the matching close.
                const std::string lowered = lowerAscii(html);
                const size_t close = lowered.find("</" + name, i);
                i = close == std::string::npos ? html.size() : close;
            }
        } else if (i + 2 < html.size() && html[i + 1] == '/' && isAsciiAlpha(html[i + 2])) {
            const size_t end = tagEnd(html, i + 2);
            if (end == std::string::npos) break;
            flush();
            if (kHidden.count(tagName(html, i + 2)) && hidden) --hidden;
            i = end;
        } else {
            data += html[i++];
        }
    }
    data += html.substr(std::min(i, html.size()));
    flush();
    // re.sub(r"\n{3,}", "\n\n", ...)
    std::string squeezed;
    size_t run = 0;
    for (char c : out) {
        run = c == '\n' ? run + 1 : 0;
        if (run <= 2) squeezed += c;
    }
    return text::strip(squeezed);
}

std::string plainText(const SourceMessage& source) {
    return source.bodyType == "html" ? htmlText(source.body) : source.body;
}

// ---- the preview ----

// _HIDDEN_RE: controls but tab and newline, the line and paragraph
// separators, and the bidi embeddings and isolates.
bool isHidden(char32_t c) {
    return (c <= 0x08) || (c >= 0x0B && c <= 0x1F) || (c >= 0x7F && c <= 0x9F) || c == 0x2028 || c == 0x2029 ||
           (c >= 0x202A && c <= 0x202E) || (c >= 0x2066 && c <= 0x2069);
}

const char* const kHiddenNote = "Hidden characters are shown as [U+XXXX]. They are sent as they are.";

std::string visible(const std::string& s) {
    std::string out;
    for (char32_t c : text::decode(replaceAll(s, "\r\n", "\n"))) {
        out += isHidden(c) ? "[U+" + hex4(c) + "]" : text::encode(c);
    }
    return out;
}

std::string visibleLine(const std::string& s) { return replaceAll(visible(s), "\n", "[U+000A]"); }

bool hasHidden(const std::string& s) {
    const Cps cps = text::decode(replaceAll(s, "\r\n", "\n"));
    return std::any_of(cps.begin(), cps.end(), isHidden);
}

std::string names(const Recipients& recipients) {
    std::vector<std::string> parts;
    for (const auto& r : recipients) parts.push_back(r.str());
    return visibleLine(join(parts, ", "));
}

json strings(const Recipients& recipients) {
    json out = json::array();
    for (const auto& r : recipients) out.push_back(r.str());
    return out;
}

json optionalString(const std::optional<std::string>& s) { return s ? json(*s) : json(); }

// ---- MIME: Python's email header folding (_header_value_parser._refold_parse_tree) ----

constexpr size_t kMaxLineLength = 78;
const std::u32string kWsp = U" \t";

bool isWsp(char32_t c) { return c == U' ' || c == U'\t'; }

// A node of the parse tree Python folds: a terminal (a string with a token
// type) or a list of nodes.
struct Tok {
    bool terminal = false;
    Cps value;
    std::string type;
    bool asEw = true;  // a terminal's own; a list's is all of its children's unless fixed
    bool asEwFixed = false;
    bool ewCombine = true;
    bool syntacticBreak = true;
    bool bareQuoted = false;
    std::vector<std::shared_ptr<Tok>> kids;
};
using TokPtr = std::shared_ptr<Tok>;

TokPtr term(const Cps& value, const std::string& type) {
    auto t = std::make_shared<Tok>();
    t->terminal = true;
    t->value = value;
    t->type = type;
    return t;
}

TokPtr term(const std::string& value, const std::string& type) { return term(text::decode(value), type); }

TokPtr list(std::vector<TokPtr> kids, const std::string& type) {
    auto t = std::make_shared<Tok>();
    t->kids = std::move(kids);
    t->type = type;
    return t;
}

TokPtr fixedNoEw(TokPtr t) {
    t->asEw = false;
    t->asEwFixed = true;
    return t;
}

bool asEwAllowed(const Tok& t) {
    if (t.terminal || t.asEwFixed) return t.asEw;
    return std::all_of(t.kids.begin(), t.kids.end(), [](const TokPtr& k) { return asEwAllowed(*k); });
}

Cps quoteString(const Cps& value) {
    Cps out = U"\"";
    for (char32_t c : value) {
        if (c == U'\\' || c == U'"') out += U'\\';
        out += c;
    }
    return out + U"\"";
}

Cps str(const Tok& t) {
    if (t.terminal) return t.value;
    Cps out;
    for (const auto& k : t.kids) out += str(*k);
    return t.bareQuoted ? quoteString(out) : out;
}

bool startsWithFws(const Tok& t) {
    if (t.terminal) return t.type == "fws";
    return !t.kids.empty() && startsWithFws(*t.kids[0]);
}

// Whitespace runs as fws terminals, the rest as `type` terminals.
std::vector<TokPtr> splitWsp(const Cps& value, const std::string& type) {
    std::vector<TokPtr> out;
    size_t i = 0;
    while (i < value.size()) {
        const bool ws = isWsp(value[i]);
        size_t j = i;
        while (j < value.size() && isWsp(value[j]) == ws) ++j;
        out.push_back(term(value.substr(i, j - i), ws ? "fws" : type));
        i = j;
    }
    return out;
}

// email._encoded_words.encode(s, charset="utf-8"): q unless b is 5+ shorter.
std::string encodeWord(const Cps& s) {
    const std::string bytes = text::encode(s);
    size_t qlen = 0;
    for (unsigned char c : bytes) qlen += (std::isalnum(c) || std::string("-!*+/ ").find(static_cast<char>(c)) != std::string::npos) ? 1 : 3;
    const size_t blen = (bytes.size() + 2) / 3 * 4;
    if (qlen < blen + 5) {
        static const char* kHex = "0123456789ABCDEF";
        std::string q;
        for (unsigned char c : bytes) {
            if (c == ' ') {
                q += '_';
            } else if (std::isalnum(c) || std::string("-!*+/").find(static_cast<char>(c)) != std::string::npos) {
                q += static_cast<char>(c);
            } else {
                q += '=';
                q += kHex[c >> 4];
                q += kHex[c & 0xF];
            }
        }
        return "=?utf-8?q?" + q + "?=";
    }
    return "=?utf-8?b?" + outgoing::base64(bytes) + "?=";
}

bool decodeWord(const Cps& word, Cps& out) {
    const std::string w = text::encode(word);
    std::string lower = w;
    for (char& c : lower) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    if (w.size() < 12 || lower.compare(0, 8, "=?utf-8?") != 0 || w[9] != '?' || w.compare(w.size() - 2, 2, "?=") != 0) {
        return false;
    }
    const std::string payload = w.substr(10, w.size() - 12);
    std::string bytes;
    if (lower[8] == 'q') {
        for (size_t i = 0; i < payload.size(); ++i) {
            if (payload[i] == '_') {
                bytes += ' ';
            } else if (payload[i] == '=') {
                if (i + 2 >= payload.size() || !std::isxdigit(static_cast<unsigned char>(payload[i + 1])) ||
                    !std::isxdigit(static_cast<unsigned char>(payload[i + 2]))) {
                    return false;
                }
                bytes += static_cast<char>(std::stoi(payload.substr(i + 1, 2), nullptr, 16));
                i += 2;
            } else {
                bytes += payload[i];
            }
        }
    } else if (lower[8] == 'b') {
        static const std::string kAlphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        uint32_t acc = 0;
        int bits = 0;
        for (char c : payload) {
            if (c == '=') break;
            acc = (acc << 6) | static_cast<uint32_t>(kAlphabet.find(c));
            bits += 6;
            if (bits >= 8) {
                bits -= 8;
                bytes += static_cast<char>((acc >> bits) & 0xFF);
            }
        }
    } else {
        return false;
    }
    out = text::decode(bytes);
    return true;
}

// str(get_unstructured(s)) for text this folder wrote: its encoded words
// decoded, and the whitespace between two of them dropped.
Cps decodeUnstructured(const Cps& s) {
    struct Piece {
        Cps text;
        bool ws;
        bool ew;
    };
    std::vector<Piece> pieces;
    for (const auto& t : splitWsp(s, "utext")) {
        Cps decoded;
        const bool ws = t->type == "fws";
        const bool ew = !ws && decodeWord(t->value, decoded);
        pieces.push_back({ew ? decoded : t->value, ws, ew});
    }
    Cps out;
    for (size_t i = 0; i < pieces.size(); ++i) {
        if (pieces[i].ws && i > 0 && i + 1 < pieces.size() && pieces[i - 1].ew && pieces[i + 1].ew) continue;
        out += pieces[i].text;
    }
    return out;
}

Cps stealTrailingWsp(std::vector<Cps>& lines) {
    Cps wsp;
    if (!lines.empty() && !lines.back().empty() && isWsp(lines.back().back())) {
        wsp = lines.back().substr(lines.back().size() - 1);
        lines.back().pop_back();
    }
    return wsp;
}

std::optional<size_t> foldAsEw(Cps toEncode, std::vector<Cps>& lines, std::optional<size_t> lastEw, bool combine,
                               Cps leadingWhitespace) {
    const size_t maxlen = kMaxLineLength;
    if (lastEw && combine) {
        toEncode = decodeUnstructured(lines.back().substr(*lastEw) + toEncode);
        lines.back() = lines.back().substr(0, *lastEw);
    } else if (!toEncode.empty() && isWsp(toEncode[0])) {
        const char32_t leading = toEncode[0];
        toEncode = toEncode.substr(1);
        if (lines.back().size() == maxlen) lines.push_back(stealTrailingWsp(lines));
        lines.back() += leading;
    }
    Cps trailing;
    if (!toEncode.empty() && isWsp(toEncode.back())) {
        trailing = toEncode.substr(toEncode.size() - 1);
        toEncode.pop_back();
    }
    size_t newLastEw = lastEw ? *lastEw : lines.back().size();
    const size_t chrome = std::string("utf-8").size() + 7;
    while (!toEncode.empty()) {
        const long remaining = static_cast<long>(maxlen) - static_cast<long>(lines.back().size());
        const long textSpace = remaining - static_cast<long>(chrome) - static_cast<long>(leadingWhitespace.size());
        if (textSpace <= 0) {
            lines.push_back(U" ");
            continue;
        }
        if (lines.size() > 1 && lines.back().size() == 1 && !leadingWhitespace.empty()) {
            lines.back() += text::decode(encodeWord(leadingWhitespace));
            leadingWhitespace.clear();
        }
        Cps chunk = toEncode.substr(0, static_cast<size_t>(textSpace));
        std::string word = encodeWord(chunk);
        while (static_cast<long>(word.size()) - remaining > 0) {
            chunk.pop_back();
            word = encodeWord(chunk);
        }
        lines.back() += text::decode(word);
        toEncode = toEncode.substr(chunk.size());
        leadingWhitespace.clear();
        if (!toEncode.empty()) {
            lines.push_back(U" ");
            newLastEw = lines.back().size();
        }
    }
    lines.back() += trailing;
    return combine ? std::optional<size_t>(newLastEw) : std::nullopt;
}

bool hasAny(const Cps& s, const std::u32string& chars) {
    return std::any_of(s.begin(), s.end(), [&](char32_t c) { return chars.find(c) != std::u32string::npos; });
}

const std::u32string kSpecialsNl = U"()<>@,:;.\\\"[]\r\n";
const std::u32string kNl = U"\r\n";

std::string refold(const Tok& root) {
    const size_t maxlen = kMaxLineLength;
    std::vector<Cps> lines{Cps()};
    Cps leadingWhitespace;
    std::optional<size_t> lastEw;
    std::string lastCharset;
    int blocked = 0;
    bool wantEncoding = false;
    const TokPtr endBlocked = term(Cps(), "wrap_as_ew_blocked");
    std::deque<TokPtr> parts(root.kids.begin(), root.kids.end());
    while (!parts.empty()) {
        const TokPtr part = parts.front();
        parts.pop_front();
        if (part == endBlocked) {
            --blocked;
            continue;
        }
        const Cps tstr = str(*part);
        if (!wantEncoding) {
            wantEncoding = (part->type == "ptext" || part->type == "vtext") ? hasAny(tstr, kSpecialsNl)
                                                                            : hasAny(tstr, kNl);
        }
        std::string charset = "us-ascii";
        if (std::any_of(tstr.begin(), tstr.end(), [](char32_t c) { return c >= 0x80; })) {
            charset = "utf-8";
            wantEncoding = true;
        }
        if (wantEncoding && !blocked) {
            if (!asEwAllowed(*part)) {
                wantEncoding = false;
                lastEw.reset();
                if (part->syntacticBreak) {
                    std::string folded = refold(*part);
                    folded.resize(folded.size() - 2);
                    if (folded.find("\r\n") == std::string::npos) {
                        const Cps encoded = text::decode(folded);
                        if (encoded.size() > maxlen - lines.back().size()) {
                            const Cps newline = stealTrailingWsp(lines);
                            lines.push_back(newline);
                        }
                        lines.back() += encoded;
                        continue;
                    }
                }
            }
            if (!part->terminal) {
                parts.insert(parts.begin(), part->kids.begin(), part->kids.end());
                wantEncoding = false;
                continue;
            } else if (asEwAllowed(*part)) {
                if (lastEw && charset != lastCharset &&
                    (lastCharset == "unknown-8bit" || (lastCharset == "utf-8" && charset != "us-ascii"))) {
                    lastEw.reset();
                }
                lastEw = foldAsEw(tstr, lines, lastEw, part->ewCombine, leadingWhitespace);
                leadingWhitespace.clear();
                lastCharset = charset;
                wantEncoding = false;
                continue;
            } else {
                lastEw.reset();
                wantEncoding = false;
            }
        }
        if (tstr.size() <= maxlen - lines.back().size()) {
            lines.back() += tstr;
            continue;
        }
        leadingWhitespace.clear();
        if (part->syntacticBreak && tstr.size() + 1 <= maxlen) {
            const Cps newline = stealTrailingWsp(lines);
            if (!newline.empty() || startsWithFws(*part)) {
                lines.push_back(newline + tstr);
                for (char32_t c : lines.back()) {
                    if (!isWsp(c)) break;
                    leadingWhitespace += c;
                }
                lastEw.reset();
                continue;
            }
        }
        if (!part->terminal) {
            std::vector<TokPtr> newparts = part->kids;
            if (part->bareQuoted) {
                newparts.clear();
                newparts.push_back(term(U"\"", "ptext"));
                for (const auto& k : part->kids) {
                    Cps pair;
                    for (char32_t c : str(*k)) {
                        if (c == U'\\' || c == U'"') pair += U'\\';
                        pair += c;
                    }
                    newparts.push_back(term(pair, "ptext"));
                }
                newparts.push_back(term(U"\"", "ptext"));
            }
            if (!asEwAllowed(*part)) {
                ++blocked;
                newparts.push_back(endBlocked);
            }
            parts.insert(parts.begin(), newparts.begin(), newparts.end());
            continue;
        }
        if (asEwAllowed(*part) && !blocked) {
            parts.push_front(part);
            wantEncoding = true;
            continue;
        }
        const Cps newline = stealTrailingWsp(lines);
        if (!newline.empty() || startsWithFws(*part)) {
            lines.push_back(newline + tstr);
        } else {
            lines.back() += tstr;
        }
    }
    std::string out;
    for (size_t i = 0; i < lines.size(); ++i) out += (i ? "\r\n" : "") + text::encode(lines[i]);
    return out + "\r\n";
}

TokPtr cfws(const Cps& ws) { return list({term(ws, "fws")}, "cfws"); }

// get_unstructured(value): an encoded word in the text is read as one, as Python reads it.
TokPtr unstructuredTree(const std::string& value) {
    std::vector<TokPtr> tokens = splitWsp(text::decode(value), "utext");
    std::vector<bool> ew(tokens.size(), false);
    for (size_t i = 0; i < tokens.size(); ++i) {
        Cps decoded;
        if (tokens[i]->type == "utext" && decodeWord(tokens[i]->value, decoded)) {
            tokens[i] = term(decoded, "utext");
            ew[i] = true;
        }
    }
    std::vector<TokPtr> kept;
    for (size_t i = 0; i < tokens.size(); ++i) {
        if (tokens[i]->type == "fws" && i > 0 && i + 1 < tokens.size() && ew[i - 1] && ew[i + 1]) continue;
        kept.push_back(tokens[i]);
    }
    return list(kept, "unstructured");
}

// get_address_list(", ".join(str(Address(r.name, addr_spec=r.address)) for r in recipients))
TokPtr addressListTree(const Recipients& recipients) {
    static const std::u32string kSpecials = U"()<>@,:;.\\\"[]";
    std::vector<TokPtr> addresses;
    for (size_t i = 0; i < recipients.size(); ++i) {
        const Recipient& r = recipients[i];
        const bool later = i > 0;
        if (later) {
            auto sep = term(U",", "list-separator");
            sep->asEw = false;
            sep->syntacticBreak = false;
            addresses.push_back(sep);
        }
        const size_t at = r.address.rfind('@');
        const std::string local = r.address.substr(0, at), domain = r.address.substr(at + 1);
        auto addrSpec = [&](bool leading) {
            std::vector<TokPtr> dotAtom;
            if (leading) dotAtom.push_back(cfws(U" "));
            dotAtom.push_back(list({term(local, "atext")}, "dot-atom-text"));
            return fixedNoEw(list({fixedNoEw(list({list(dotAtom, "dot-atom")}, "local-part")),
                                   term("@", "address-at-symbol"),
                                   fixedNoEw(list({list({list({term(domain, "atext")}, "dot-atom-text")}, "dot-atom")},
                                                  "domain"))},
                                  "addr-spec"));
        };
        TokPtr mailbox;
        const Cps name = text::decode(r.name);
        if (name.empty()) {
            mailbox = list({addrSpec(later)}, "mailbox");
        } else {
            std::vector<TokPtr> display;
            if (hasAny(name, kSpecials)) {
                std::vector<TokPtr> quoted;
                if (later) quoted.push_back(cfws(U" "));
                auto bareQuoted = list(splitWsp(name, "ptext"), "bare-quoted-string");
                bareQuoted->bareQuoted = true;
                quoted.push_back(bareQuoted);
                quoted.push_back(cfws(U" "));
                display.push_back(list(quoted, "quoted-string"));
            } else {
                const std::vector<TokPtr> pieces = splitWsp(name + U" ", "atext");
                std::vector<TokPtr> atom;
                bool first = true;
                for (const auto& p : pieces) {
                    if (p->type == "fws") {
                        atom.push_back(cfws(p->value));
                        display.push_back(list(atom, "atom"));
                        atom.clear();
                        continue;
                    }
                    if (first && later) atom.push_back(cfws(U" "));
                    first = false;
                    atom.push_back(p);
                }
            }
            auto displayName = list(display, "display-name");
            displayName->ewCombine = false;
            auto angle = list({term("<", "angle-addr-start"), addrSpec(false), term(">", "angle-addr-end")}, "angle-addr");
            mailbox = list({list({displayName, angle}, "name-addr")}, "mailbox");
        }
        addresses.push_back(list({mailbox}, "address"));
    }
    return list(addresses, "address-list");
}

std::string foldHeader(const std::string& name, const TokPtr& value) {
    std::vector<TokPtr> kids{fixedNoEw(list({term(name, "header-name"), term(":", "header-sep")}, "header-label"))};
    if (!value->kids.empty()) kids.push_back(cfws(U" "));
    kids.push_back(value);
    return refold(*list(kids, "header"));
}

// ---- MIME: the body (contentmanager._encode_text, quoprimime.body_encode) ----

std::vector<std::string> byteLines(const std::string& bytes) {
    std::vector<std::string> lines;
    size_t start = 0, i = 0;
    while (i < bytes.size()) {
        if (bytes[i] == '\r' || bytes[i] == '\n') {
            lines.push_back(bytes.substr(start, i - start));
            i += (bytes[i] == '\r' && i + 1 < bytes.size() && bytes[i + 1] == '\n') ? 2 : 1;
            start = i;
        } else {
            ++i;
        }
    }
    if (start < bytes.size()) lines.push_back(bytes.substr(start));
    return lines;
}

std::string quotedPrintable(const std::string& body) {
    if (body.empty()) return body;
    static const char* kHex = "0123456789ABCDEF";
    static const std::string kSafe =
        " !\"#$%&'()*+,-./0123456789:;<>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~\t\r\n";
    auto quote = [](unsigned char c) { return std::string("=") + kHex[c >> 4] + kHex[c & 0xF]; };
    std::string translated;
    for (unsigned char c : body) translated += kSafe.find(static_cast<char>(c)) != std::string::npos ? std::string(1, c) : quote(c);
    const size_t maxlen = kMaxLineLength, maxlen1 = maxlen - 1;
    std::vector<std::string> encoded;
    for (const std::string& line : byteLines(translated)) {
        size_t start = 0;
        const long laststart = static_cast<long>(line.size()) - 1 - static_cast<long>(maxlen);
        while (static_cast<long>(start) <= laststart) {
            const size_t stop = start + maxlen1;
            if (line[stop - 2] == '=') {
                encoded.push_back(line.substr(start, stop - 1 - start));
                start = stop - 2;
            } else if (line[stop - 1] == '=') {
                encoded.push_back(line.substr(start, stop - start));
                start = stop - 1;
            } else {
                encoded.push_back(line.substr(start, stop - start) + "=");
                start = stop;
            }
        }
        if (!line.empty() && (line.back() == ' ' || line.back() == '\t')) {
            const long room = static_cast<long>(start) - laststart;
            std::string q;
            if (room >= 3) {
                q = quote(static_cast<unsigned char>(line.back()));
            } else if (room == 2) {
                q = std::string(1, line.back()) + "=\n";
            } else {
                q = "=\n" + quote(static_cast<unsigned char>(line.back()));
            }
            encoded.push_back(line.substr(start, line.size() - 1 - start) + q);
        } else {
            encoded.push_back(line.substr(std::min(start, line.size())));
        }
    }
    if (translated.back() == '\r' || translated.back() == '\n') encoded.push_back("");
    return join(encoded, "\n");
}

std::string base64Lines(const std::string& data) {
    const size_t per = kMaxLineLength / 4 * 3;
    std::string out;
    for (size_t i = 0; i < data.size(); i += per) out += outgoing::base64(data.substr(i, per)) + "\n";
    return out;
}

// (cte, payload with "\n" line ends)
std::pair<std::string, std::string> encodeText(const std::string& body) {
    const std::vector<std::string> lines = byteLines(body);
    auto normalBody = [&] { return join(lines, "\n") + "\n"; };
    size_t longest = 0;
    for (const auto& l : lines) longest = std::max(longest, l.size());
    if (longest <= kMaxLineLength) return {isAscii(body) ? "7bit" : "8bit", normalBody()};
    const std::vector<std::string> head(lines.begin(), lines.begin() + std::min<size_t>(10, lines.size()));
    const std::string sniff = join(head, "\r\n") + "\r\n";
    const std::string sniffQp = quotedPrintable(sniff);
    const size_t sniffBase64 = outgoing::base64(sniff).size() + 1;
    if (sniffQp.size() > sniffBase64) return {"base64", base64Lines(join(lines, "\r\n") + "\r\n")};
    if (lines.size() <= 10) return {"quoted-printable", sniffQp};
    return {"quoted-printable", quotedPrintable(normalBody())};
}

std::string crlf(const std::string& s) {
    std::string out;
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '\r') {
            out += "\r\n";
            if (i + 1 < s.size() && s[i + 1] == '\n') ++i;
        } else if (s[i] == '\n') {
            out += "\r\n";
        } else {
            out += s[i];
        }
    }
    return out;
}

// One text part: its content headers, a blank line, the encoded body.
std::string textPart(const std::string& content, const std::string& subtype, bool mimeVersion) {
    const auto [cte, payload] = encodeText(content);
    std::string out = "Content-Type: text/" + subtype + "; charset=\"utf-8\"\r\nContent-Transfer-Encoding: " + cte + "\r\n";
    if (mimeVersion) out += "MIME-Version: 1.0\r\n";
    return out + "\r\n" + crlf(payload);
}

std::string newBoundary() {
    static std::mt19937_64 rng{std::random_device{}()};
    const uint64_t token = rng() % 9223372036854775807ULL;
    std::string digits = std::to_string(token);
    digits.insert(0, 19 - digits.size(), '0');
    return "===============" + digits + "==";
}

}  // namespace

std::string Recipient::str() const {
    if (name.empty() || text::lower(name) == text::lower(address)) return address;
    return "\"" + name + "\" <" + address + ">";
}

bool OutgoingMessage::operator==(const OutgoingMessage& o) const {
    const bool sameReplaces = (!replaces && !o.replaces) || (replaces && o.replaces && *replaces == *o.replaces);
    return to == o.to && cc == o.cc && bcc == o.bcc && subject == o.subject && body == o.body &&
           bodyType == o.bodyType && threadId == o.threadId && inReplyTo == o.inReplyTo &&
           references == o.references && replyToId == o.replyToId && attachments == o.attachments &&
           attachmentsLeftOut == o.attachmentsLeftOut && htmlAlternative == o.htmlAlternative && sameReplaces;
}

namespace outgoing {

OutgoingMessage checked(OutgoingMessage message) {
    refuseControls(message.subject, "subject");
    refuseControls(message.inReplyTo.value_or(""), "in_reply_to");
    refuseControls(message.references.value_or(""), "references");
    message.to = sendableAll(message.to, "to");
    message.cc = sendableAll(message.cc, "cc");
    message.bcc = sendableAll(message.bcc, "bcc");
    return message;
}

Recipients parseRecipients(const std::string& raw, const std::string& field) {
    const std::string value = text::strip(raw);
    if (value.empty()) return {};
    refuseControls(value, field);
    Recipients parsed;
    for (const auto& a : text::getaddresses(value)) parsed.push_back(sendableRecipient({text::strip(a.name), a.addr}, field));
    if (parsed.empty()) throw std::invalid_argument("`" + field + "` has no usable address: " + repr(value) + ".");
    return parsed;
}

void requireRecipient(const OutgoingMessage& message) {
    if (message.to.empty() && message.cc.empty() && message.bcc.empty()) {
        throw std::invalid_argument("This message has no recipients, so it can't be sent. Add one first.");
    }
}

OutgoingMessage newMessage(const std::string& to, const std::string& subject, const std::string& body,
                           const std::string& cc, const std::string& bcc) {
    OutgoingMessage m;
    m.to = parseRecipients(to, "to");
    m.cc = parseRecipients(cc, "cc");
    m.bcc = parseRecipients(bcc, "bcc");
    m.subject = subject;
    m.body = body;
    return m;
}

OutgoingMessage replyMessage(const SourceMessage& source, const std::string& body, const std::string& ownAddress,
                             bool replyAll) {
    const std::string own = text::lower(ownAddress);
    const bool ourselves = source.sender && text::lower(source.sender->address) == own;
    Recipients to;
    if (!source.replyTo.empty()) {
        to = source.replyTo;
    } else if (ourselves) {
        to = source.to;
    } else if (source.sender) {
        to = {*source.sender};
    }
    Recipients cc;
    if (replyAll) {
        if (!ourselves) to.insert(to.end(), source.to.begin(), source.to.end());
        to = without(to, {own});
        std::set<std::string> excluded{own};
        for (const auto& r : to) excluded.insert(text::lower(r.address));
        cc = without(source.cc, excluded);
    }
    if (to.empty() && cc.empty()) {
        throw std::invalid_argument("Message " + repr(source.id) +
                                    " names nobody to reply to. Use send-email/send_now with the address you want.");
    }
    std::vector<std::string> chain;
    for (const auto& part : {source.references, source.internetMessageId}) {
        if (part && !part->empty()) chain.push_back(*part);
    }
    OutgoingMessage m;
    m.to = bare(to);
    m.cc = bare(cc);
    m.subject = prefixed("Re:", source.subject);
    m.body = body;
    m.threadId = source.threadId;
    m.inReplyTo = source.internetMessageId;
    if (!chain.empty()) m.references = join(chain, " ");
    m.replyToId = source.id;
    return m;
}

OutgoingMessage forwardMessage(const SourceMessage& source, const std::string& to, const std::string& note,
                               const std::string& cc, const std::string& bcc) {
    const std::string sender = source.sender ? source.sender->str() : "(unknown sender)";
    std::vector<std::string> recipients;
    for (const auto& r : source.to) recipients.push_back(r.str());
    const std::string quoted = "----- Forwarded message -----\nFrom: " + sender + "\nDate: " + source.received +
                               "\nSubject: " + source.subject + "\nTo: " + join(recipients, ", ") + "\n\n" +
                               plainText(source);
    OutgoingMessage m = newMessage(to, prefixed("Fwd:", source.subject), note.empty() ? quoted : note + "\n\n" + quoted,
                                   cc, bcc);
    m.attachmentsLeftOut = source.attachments;
    return m;
}

std::string renderPreview(const OutgoingMessage& message) {
    std::vector<std::string> lines;
    if (message.sender && !message.sender->empty()) lines.push_back("From: " + visibleLine(*message.sender));
    lines.push_back("To: " + names(message.to));
    if (!message.cc.empty()) lines.push_back("Cc: " + names(message.cc));
    if (!message.bcc.empty()) lines.push_back("Bcc: " + names(message.bcc));
    lines.push_back("Subject: " + visibleLine(message.subject));
    if (!message.attachments.empty()) lines.push_back("Attachments: " + visibleLine(join(message.attachments, ", ")));
    if (!message.attachmentsLeftOut.empty()) {
        lines.push_back("Attachments left out, not sent: " + visibleLine(join(message.attachmentsLeftOut, ", ")));
    }
    if (message.bodyType == "html") lines.push_back("Format: HTML");
    const std::string html = message.htmlAlternative.value_or("");
    std::string shown = join(lines, "\n") + "\n\n" + visible(message.body);
    if (!html.empty()) {
        shown += "\n\n----- HTML version, which most mail apps show instead of the text above -----\n" + visible(html);
    }
    if (hasHidden(message.subject) || hasHidden(message.body) || hasHidden(html)) {
        shown = std::string(kHiddenNote) + "\n\n" + shown;
    }
    if (message.replaces) shown += "\n\n----- This replaces the saved draft -----\n" + renderPreview(*message.replaces);
    return shown;
}

json describe(const OutgoingMessage& message) {
    return {{"to", strings(message.to)},
            {"cc", strings(message.cc)},
            {"bcc", strings(message.bcc)},
            {"subject", message.subject},
            {"body", message.body},
            {"thread_id", optionalString(message.threadId)},
            {"attachments", message.attachments},
            {"attachments_left_out", message.attachmentsLeftOut}};
}

std::string mimeText(const OutgoingMessage& message, const std::string& boundary) {
    std::string out;
    for (const auto& [name, recipients] :
         {std::pair<const char*, const Recipients*>{"To", &message.to}, {"Cc", &message.cc}, {"Bcc", &message.bcc}}) {
        if (!recipients->empty()) out += foldHeader(name, addressListTree(*recipients));
    }
    out += foldHeader("Subject", unstructuredTree(message.subject));
    if (message.inReplyTo && !message.inReplyTo->empty()) {
        out += foldHeader("In-Reply-To", unstructuredTree(*message.inReplyTo));
    }
    if (message.references && !message.references->empty()) {
        out += foldHeader("References", unstructuredTree(*message.references));
    }
    const std::string subtype = message.bodyType == "html" ? "html" : "plain";
    if (!message.htmlAlternative || message.htmlAlternative->empty()) return out + textPart(message.body, subtype, true);
    out += "MIME-Version: 1.0\r\nContent-Type: multipart/alternative;\r\n boundary=\"" + boundary + "\"\r\n\r\n";
    out += "--" + boundary + "\r\n" + textPart(message.body, subtype, false);
    out += "\r\n--" + boundary + "\r\n" + textPart(*message.htmlAlternative, "html", true);
    return out + "\r\n--" + boundary + "--\r\n";
}

std::string rawMime(const OutgoingMessage& message) {
    std::string encoded = base64(mimeText(message, newBoundary()));
    for (char& c : encoded) {
        if (c == '+') c = '-';
        if (c == '/') c = '_';
    }
    while (!encoded.empty() && encoded.back() == '=') encoded.pop_back();
    return encoded;
}

std::string base64(const std::string& bytes) {
    static const char* kAlphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    size_t i = 0;
    for (; i + 2 < bytes.size(); i += 3) {
        const uint32_t n = (static_cast<unsigned char>(bytes[i]) << 16) | (static_cast<unsigned char>(bytes[i + 1]) << 8) |
                           static_cast<unsigned char>(bytes[i + 2]);
        out += kAlphabet[(n >> 18) & 63];
        out += kAlphabet[(n >> 12) & 63];
        out += kAlphabet[(n >> 6) & 63];
        out += kAlphabet[n & 63];
    }
    if (i < bytes.size()) {
        uint32_t n = static_cast<unsigned char>(bytes[i]) << 16;
        if (i + 1 < bytes.size()) n |= static_cast<unsigned char>(bytes[i + 1]) << 8;
        out += kAlphabet[(n >> 18) & 63];
        out += kAlphabet[(n >> 12) & 63];
        out += i + 1 < bytes.size() ? kAlphabet[(n >> 6) & 63] : '=';
        out += '=';
    }
    return out;
}

}  // namespace outgoing

}  // namespace gaia::mail
