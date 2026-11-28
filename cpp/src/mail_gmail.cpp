// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// src/gaia/agents/tools/_email/gmail.py. Gmail API v1 resources are flattened
// here into the same summary shape the Graph backend emits.

#include "mail_internal.h"

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdint>
#include <exception>
#include <map>
#include <optional>
#include <set>
#include <stdexcept>
#include <thread>

#include "lower_ascii.h"
#include "mail_outgoing.h"

namespace gaia::mail {

using namespace detail;
using gaia::detail::lowerAscii;

namespace {

const std::string kGmailApiBase = "https://gmail.googleapis.com/gmail/v1";

// Every listed id costs a `messages.get`, so Graph's 999 would be 999 fetches.
constexpr int kGmailMaxLimit = 100;
// Under Gmail's per-user concurrency limit.
constexpr std::size_t kFetchConcurrency = 5;
// Gmail's `fields` syntax has no recursion, so the part tree is spelled out.
constexpr int kPartTreeDepth = 8;

const char* const kGmailEnableUrl = "https://console.cloud.google.com/apis/library/gmail.googleapis.com";

const std::set<std::string> kReservedLabelIds = {"INBOX",   "SENT",    "DRAFT",     "SPAM", "TRASH",
                                                 "UNREAD",  "STARRED", "IMPORTANT", "CHAT"};
const std::string kReservedLabelPrefix = "CATEGORY_";

std::string partFields(int depth) {
    std::string fields = "mimeType,filename,headers,body/size";
    if (depth) fields += ",parts(" + partFields(depth - 1) + ")";
    return fields;
}

const std::string& summaryFields() {
    static const std::string fields =
        "id,threadId,labelIds,snippet,internalDate,payload(" + partFields(kPartTreeDepth) + ")";
    return fields;
}

bool isWordChar(char c, const char* extra) {
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
           std::string(extra).find(c) != std::string::npos;
}

// The shape Gmail issues for a message id. Thread ids share it.
bool isGmailId(const std::string& s) {
    if (s.empty() || s.size() > 128) return false;
    return std::all_of(s.begin(), s.end(), [](char c) { return isWordChar(c, "_-"); });
}

// The shape Gmail documents for `errors[].reason` and `error.status`.
bool isReasonToken(const std::string& s) {
    if (s.empty() || s.size() > 64) return false;
    return std::all_of(s.begin(), s.end(), [](char c) { return isWordChar(c, "_."); });
}

// ---- error detail ----

struct ErrorDetail {
    std::string reason;
    std::string helpUrl;
};

// Gmail's structured error fields only, never the upstream-controlled body.
ErrorDetail errorDetail(const HttpResponse& response) {
    const json parsed = json::parse(response.body, nullptr, false);
    if (parsed.is_discarded()) return {};
    const json& err = field(parsed, "error");
    if (!err.is_object()) return {};
    const json& errors = field(err, "errors");
    const json& first = errors.is_array() && !errors.empty() ? errors[0] : field(json(), "");
    const json& reasonValue = truthy(field(first, "reason")) ? field(first, "reason") : field(err, "status");
    ErrorDetail out;
    out.reason = truthy(reasonValue) ? pyStr(reasonValue) : "";
    if (!isReasonToken(out.reason)) out.reason.clear();
    const json& help = field(first, "extendedHelp");
    out.helpUrl = truthy(help) ? pyStr(help) : "";
    if (out.helpUrl.compare(0, 8, "https://") != 0) out.helpUrl.clear();
    return out;
}

// ---- base64 (binascii.a2b_base64, non-strict) ----

bool a2bBase64(const std::string& in, std::string& out) {
    out.clear();
    int quadPos = 0;
    int pads = 0;
    unsigned leftchar = 0;
    for (const char ch : in) {
        if (ch == '=') {
            if (quadPos >= 2 && quadPos + ++pads >= 4) return true;
            continue;
        }
        int v;
        if (ch >= 'A' && ch <= 'Z') v = ch - 'A';
        else if (ch >= 'a' && ch <= 'z') v = ch - 'a' + 26;
        else if (ch >= '0' && ch <= '9') v = ch - '0' + 52;
        else if (ch == '+') v = 62;
        else if (ch == '/') v = 63;
        else continue;
        pads = 0;
        const auto u = static_cast<unsigned>(v);
        switch (quadPos) {
            case 0:
                quadPos = 1;
                leftchar = u;
                break;
            case 1:
                quadPos = 2;
                out += static_cast<char>(((leftchar << 2) | (u >> 4)) & 0xFF);
                leftchar = u & 0x0F;
                break;
            case 2:
                quadPos = 3;
                out += static_cast<char>(((leftchar << 4) | (u >> 2)) & 0xFF);
                leftchar = u & 0x03;
                break;
            default:
                quadPos = 0;
                out += static_cast<char>(((leftchar << 6) | u) & 0xFF);
                leftchar = 0;
                break;
        }
    }
    return quadPos == 0;
}

// ---- charsets ----

// cp1252 for 0x80-0x9F; 0 where the codec has no character.
const long kCp1252High[32] = {0x20AC, 0,      0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021,
                              0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, 0,      0x017D, 0,
                              0,      0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014,
                              0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0,      0x017E, 0x0178};

// email.charset ALIASES, applied to a lowercased name.
std::string charsetName(const std::string& lowered) {
    if (lowered == "latin_1" || lowered == "latin-1") return "iso-8859-1";
    if (lowered == "ascii") return "us-ascii";
    if (lowered == "utf8") return "utf-8";
    return lowered;
}

// Strict decode to UTF-8; false where Python raises LookupError or UnicodeDecodeError.
bool decodeCharset(const std::string& bytes, const std::string& name, std::string& out) {
    out.clear();
    if (name == "utf-8") {
        out = bytes;
        return utf8Replace(bytes) == bytes;
    }
    if (name == "us-ascii") {
        out = bytes;
        return std::all_of(bytes.begin(), bytes.end(), [](char c) { return static_cast<unsigned char>(c) < 0x80; });
    }
    const bool latin1 = name == "iso-8859-1" || name == "iso8859-1" || name == "iso_8859_1" ||
                        name == "latin1" || name == "l1";
    const bool cp1252 = name == "windows-1252" || name == "cp1252";
    if (!latin1 && !cp1252) return false;
    for (const char ch : bytes) {
        const auto c = static_cast<unsigned char>(ch);
        long cp = c;
        if (cp1252 && c >= 0x80 && c <= 0x9F) {
            cp = kCp1252High[c - 0x80];
            if (cp == 0) return false;
        }
        appendUtf8(out, cp);
    }
    return true;
}

// ---- RFC 2047 (email.header.decode_header + make_header) ----

struct Word {
    std::string text;
    bool encoded = false;
    char encoding = 0;
    std::string charset;
};

// The encoded words and the text between them on one line, as ecre.split gives.
std::vector<Word> splitEncodedWords(const std::string& line) {
    std::vector<Word> out;
    std::string pending;
    std::size_t i = 0;
    while (i < line.size()) {
        if (line.compare(i, 2, "=?") == 0) {
            const std::size_t q1 = line.find('?', i + 2);
            if (q1 != std::string::npos && q1 + 2 < line.size() &&
                std::string("qQbB").find(line[q1 + 1]) != std::string::npos && line[q1 + 2] == '?') {
                const std::size_t close = line.find("?=", q1 + 3);
                if (close != std::string::npos) {
                    Word unencoded;
                    unencoded.text = pending;
                    out.push_back(unencoded);
                    pending.clear();
                    Word w;
                    w.encoded = true;
                    w.charset = line.substr(i + 2, q1 - i - 2);
                    w.encoding = static_cast<char>(std::tolower(static_cast<unsigned char>(line[q1 + 1])));
                    w.text = line.substr(q1 + 3, close - q1 - 3);
                    out.push_back(w);
                    i = close + 2;
                    continue;
                }
            }
        }
        pending += line[i++];
    }
    Word tail;
    tail.text = pending;
    out.push_back(tail);
    return out;
}

bool hasEncodedWord(const std::string& raw) {
    std::size_t start = 0;
    while (start <= raw.size()) {
        const std::size_t nl = raw.find_first_of("\r\n", start);
        const std::string line = raw.substr(start, nl == std::string::npos ? std::string::npos : nl - start);
        if (splitEncodedWords(line).size() > 1) return true;
        if (nl == std::string::npos) break;
        start = nl + 1;
    }
    return false;
}

// Header._nonctext of the first or last code point of `s`.
bool nonCtext(const std::string& s, bool first) {
    if (s.empty()) return false;
    std::string cp;
    if (first) {
        std::size_t n = 1;
        while (n < s.size() && (static_cast<unsigned char>(s[n]) & 0xC0) == 0x80) ++n;
        cp = s.substr(0, n);
    } else {
        std::size_t start = s.size() - 1;
        while (start > 0 && (static_cast<unsigned char>(s[start]) & 0xC0) == 0x80) --start;
        cp = s.substr(start);
    }
    return cp == "(" || cp == ")" || cp == "\\" || pyIsSpace(cp);
}

bool isAsciiCharset(const std::string& cs) { return cs.empty() || cs == "us-ascii"; }

// Throws std::invalid_argument where Python's decode raises one of the caught errors.
std::string decodeHeader(const std::string& raw) {
    std::vector<Word> words;
    std::size_t start = 0;
    while (true) {
        const std::size_t nl = raw.find_first_of("\r\n", start);
        const std::string line = raw.substr(start, nl == std::string::npos ? std::string::npos : nl - start);
        bool first = true;
        for (Word w : splitEncodedWords(line)) {
            if (!w.encoded) {
                if (first) w.text = pyLstrip(w.text);
                first = false;
                if (!w.text.empty()) words.push_back(w);
            } else {
                first = false;
                w.charset = lowerAscii(w.charset);
                words.push_back(w);
            }
        }
        if (nl == std::string::npos) break;
        start = nl + ((raw.compare(nl, 2, "\r\n") == 0) ? 2 : 1);
        if (start >= raw.size()) break;
    }

    std::vector<std::size_t> drop;
    for (std::size_t n = 2; n < words.size(); ++n) {
        if (words[n].encoded && words[n - 2].encoded && pyIsSpace(words[n - 1].text)) drop.push_back(n - 1);
    }
    for (auto it = drop.rbegin(); it != drop.rend(); ++it) words.erase(words.begin() + static_cast<long>(*it));

    // (bytes, charset) with consecutive same-charset runs collapsed.
    std::vector<std::pair<std::string, std::string>> collapsed;
    bool haveLast = false;
    for (const Word& w : words) {
        std::string bytes;
        if (!w.encoded) {
            bytes = w.text;
        } else if (w.encoding == 'q') {
            for (std::size_t k = 0; k < w.text.size(); ++k) {
                const char c = w.text[k];
                if (c == '=' && k + 2 < w.text.size() && std::isxdigit(static_cast<unsigned char>(w.text[k + 1])) &&
                    std::isxdigit(static_cast<unsigned char>(w.text[k + 2]))) {
                    bytes += static_cast<char>(std::stoi(w.text.substr(k + 1, 2), nullptr, 16));
                    k += 2;
                } else {
                    bytes += c == '_' ? ' ' : c;
                }
            }
        } else {
            std::string padded = w.text;
            if (padded.size() % 4) padded += std::string("===").substr(0, 4 - padded.size() % 4);
            if (!a2bBase64(padded, bytes)) throw std::invalid_argument("base64 decoding error");
        }
        const std::string cs = w.encoded ? w.charset : std::string();
        if (!haveLast) {
            collapsed.emplace_back(bytes, cs);
            haveLast = true;
        } else if (cs != collapsed.back().second) {
            collapsed.emplace_back(bytes, cs);
        } else if (cs.empty()) {
            collapsed.back().first += " " + bytes;
        } else {
            collapsed.back().first += bytes;
        }
    }

    // make_header: decode each chunk, merge equal charsets, then join.
    std::vector<std::pair<std::string, std::string>> chunks;
    for (const auto& [bytes, cs] : collapsed) {
        const std::string name = cs.empty() ? "us-ascii" : charsetName(cs);
        std::string decoded;
        if (!decodeCharset(bytes, name, decoded)) throw std::invalid_argument("undecodable header");
        if (!chunks.empty() && chunks.back().second == name) {
            chunks.back().first += " " + decoded;
        } else {
            chunks.emplace_back(decoded, name);
        }
    }
    std::string out;
    std::string lastcs;
    bool lastspace = false;
    bool any = false;
    for (const auto& [s, cs] : chunks) {
        std::string nextcs = cs;
        if (any) {
            const bool hasspace = nonCtext(s, true);
            if (!isAsciiCharset(lastcs)) {
                if (isAsciiCharset(nextcs) && !hasspace) {
                    out += ' ';
                    nextcs.clear();
                }
            } else if (!isAsciiCharset(nextcs) && !lastspace) {
                out += ' ';
            }
        }
        lastspace = nonCtext(s, false);
        lastcs = nextcs;
        out += s;
        any = true;
    }
    return out;
}

// Decode RFC 2047 encoded words. Idempotent on already-plain text.
std::string decodeWords(const std::string& raw) {
    if (raw.empty()) return "";
    if (!hasEncodedWord(raw)) return raw;
    try {
        return decodeHeader(raw);
    } catch (const std::invalid_argument&) {
        return raw;
    }
}

// ---- addresses (email.utils.getaddresses, strict) ----

using Address = std::pair<std::string, std::string>;

class AddrlistParser {
public:
    explicit AddrlistParser(std::string field) : field_(std::move(field)) {}

    std::vector<Address> addrlist() {
        std::vector<Address> result;
        while (pos_ < field_.size()) {
            const auto ad = address();
            if (!ad.empty()) {
                result.insert(result.end(), ad.begin(), ad.end());
            } else {
                result.emplace_back("", "");
            }
        }
        return result;
    }

private:
    static constexpr const char* kSpecials = "()<>@,:;.\"[]";
    static constexpr const char* kLws = " \t";

    bool in(const char* set) const { return pos_ < field_.size() && std::string(set).find(field_[pos_]) != std::string::npos; }
    bool atomEnd() const { return in(kSpecials) || in(" \t\r\n"); }
    bool phraseEnd() const { return in("()<>@,:;\"[]") || in(" \t\r\n"); }

    static std::string join(const std::vector<std::string>& parts, const std::string& sep) {
        std::string out;
        for (std::size_t i = 0; i < parts.size(); ++i) {
            if (i) out += sep;
            out += parts[i];
        }
        return out;
    }

    std::string gotonext() {
        std::string ws;
        while (pos_ < field_.size()) {
            if (in(" \t\n\r")) {
                if (!in("\n\r")) ws += field_[pos_];
                ++pos_;
            } else if (field_[pos_] == '(') {
                comments_.push_back(comment());
            } else {
                break;
            }
        }
        return ws;
    }

    std::vector<Address> address() {
        comments_.clear();
        gotonext();
        const std::size_t oldpos = pos_;
        const auto oldcl = comments_;
        const auto plist = phraselist();
        gotonext();
        std::vector<Address> out;
        if (pos_ >= field_.size()) {
            if (!plist.empty()) out = {{join(comments_, " "), plist[0]}};
        } else if (in(".@")) {
            pos_ = oldpos;
            comments_ = oldcl;
            const std::string spec = addrspec();
            out = {{join(comments_, " "), spec}};
        } else if (field_[pos_] == ':') {
            ++pos_;
            while (pos_ < field_.size()) {
                gotonext();
                if (pos_ < field_.size() && field_[pos_] == ';') {
                    ++pos_;
                    break;
                }
                const auto member = address();
                out.insert(out.end(), member.begin(), member.end());
            }
        } else if (field_[pos_] == '<') {
            const std::string route = routeaddr();
            if (!comments_.empty()) {
                out = {{join(plist, " ") + " (" + join(comments_, " ") + ")", route}};
            } else {
                out = {{join(plist, " "), route}};
            }
        } else {
            if (!plist.empty()) {
                out = {{join(comments_, " "), plist[0]}};
            } else if (in(kSpecials)) {
                ++pos_;
            }
        }
        gotonext();
        if (pos_ < field_.size() && field_[pos_] == ',') ++pos_;
        return out;
    }

    std::string routeaddr() {
        bool expectroute = false;
        ++pos_;
        gotonext();
        std::string adlist;
        while (pos_ < field_.size()) {
            if (expectroute) {
                domain();
                expectroute = false;
            } else if (field_[pos_] == '>') {
                ++pos_;
                break;
            } else if (field_[pos_] == '@') {
                ++pos_;
                expectroute = true;
            } else if (field_[pos_] == ':') {
                ++pos_;
            } else {
                adlist = addrspec();
                ++pos_;
                break;
            }
            gotonext();
        }
        return adlist;
    }

    std::string addrspec() {
        std::vector<std::string> aslist;
        const auto popBlank = [&] {
            if (!aslist.empty() && pyStrip(aslist.back()).empty()) aslist.pop_back();
        };
        gotonext();
        while (pos_ < field_.size()) {
            bool preserveWs = true;
            if (field_[pos_] == '.') {
                popBlank();
                aslist.emplace_back(".");
                ++pos_;
                preserveWs = false;
            } else if (field_[pos_] == '"') {
                std::string quoted;
                for (const char c : quote()) {
                    if (c == '\\' || c == '"') quoted += '\\';
                    quoted += c;
                }
                aslist.push_back("\"" + quoted + "\"");
            } else if (atomEnd()) {
                popBlank();
                break;
            } else {
                aslist.push_back(atom(false));
            }
            const std::string ws = gotonext();
            if (preserveWs && !ws.empty()) aslist.push_back(ws);
        }
        if (pos_ >= field_.size() || field_[pos_] != '@') return join(aslist, "");
        aslist.emplace_back("@");
        ++pos_;
        gotonext();
        const std::string dom = domain();
        if (dom.empty()) return "";
        return join(aslist, "") + dom;
    }

    std::string domain() {
        std::string out;
        while (pos_ < field_.size()) {
            if (in(kLws)) {
                ++pos_;
            } else if (field_[pos_] == '(') {
                comments_.push_back(comment());
            } else if (field_[pos_] == '[') {
                out += "[" + delimited('[', "]\r", false) + "]";
            } else if (field_[pos_] == '.') {
                ++pos_;
                out += '.';
            } else if (field_[pos_] == '@') {
                return "";
            } else if (atomEnd()) {
                break;
            } else {
                out += atom(false);
            }
        }
        return out;
    }

    std::string delimited(char begin, const char* ends, bool allowComments) {
        if (field_[pos_] != begin) return "";
        std::string out;
        bool quoted = false;
        ++pos_;
        while (pos_ < field_.size()) {
            if (quoted) {
                out += field_[pos_];
                quoted = false;
            } else if (in(ends)) {
                ++pos_;
                break;
            } else if (allowComments && field_[pos_] == '(') {
                out += comment();
                continue;
            } else if (field_[pos_] == '\\') {
                quoted = true;
            } else {
                out += field_[pos_];
            }
            ++pos_;
        }
        return out;
    }

    std::string quote() { return delimited('"', "\"\r", false); }
    std::string comment() { return delimited('(', ")\r", true); }

    std::string atom(bool phrase) {
        std::string out;
        while (pos_ < field_.size() && !(phrase ? phraseEnd() : atomEnd())) out += field_[pos_++];
        return out;
    }

    std::vector<std::string> phraselist() {
        std::vector<std::string> plist;
        while (pos_ < field_.size()) {
            if (in(" \t\r\n")) {
                ++pos_;
            } else if (field_[pos_] == '"') {
                plist.push_back(quote());
            } else if (field_[pos_] == '(') {
                comments_.push_back(comment());
            } else if (phraseEnd()) {
                break;
            } else {
                plist.push_back(atom(true));
            }
        }
        return plist;
    }

    std::string field_;
    std::size_t pos_ = 0;
    std::vector<std::string> comments_;
};

// (position, char) pairs with backslash escapes kept together, as _iter_escaped_chars.
std::vector<std::pair<std::size_t, std::string>> escapedChars(const std::string& s) {
    std::vector<std::pair<std::size_t, std::string>> out;
    bool escape = false;
    std::size_t pos = 0;
    for (pos = 0; pos < s.size(); ++pos) {
        if (escape) {
            out.emplace_back(pos, std::string("\\") + s[pos]);
            escape = false;
        } else if (s[pos] == '\\') {
            escape = true;
        } else {
            out.emplace_back(pos, std::string(1, s[pos]));
        }
    }
    if (escape) out.emplace_back(s.empty() ? 0 : s.size() - 1, "\\");
    return out;
}

std::string stripQuotedRealnames(const std::string& s) {
    if (s.find('"') == std::string::npos) return s;
    std::size_t start = 0;
    std::optional<std::size_t> open;
    std::string out;
    for (const auto& [pos, ch] : escapedChars(s)) {
        if (ch != "\"") continue;
        if (!open) {
            open = pos;
        } else {
            if (start != *open) out += s.substr(start, *open - start);
            start = pos + 1;
            open.reset();
        }
    }
    if (start < s.size()) out += s.substr(start);
    return out;
}

bool balancedParens(const std::string& s) {
    int opens = 0;
    for (const auto& [pos, ch] : escapedChars(stripQuotedRealnames(s))) {
        (void)pos;
        if (ch == "(") ++opens;
        if (ch == ")" && --opens < 0) return false;
    }
    return opens == 0;
}

std::vector<Address> getAddresses(const std::string& value) {
    const std::vector<Address> invalid = {{"", ""}};
    if (!balancedParens(value)) return invalid;
    std::vector<Address> result = value.empty() ? std::vector<Address>{} : AddrlistParser(value).addrlist();
    for (auto& a : result) {
        if (a.second.find('[') != std::string::npos) a = {"", ""};
    }
    const std::string bare = stripQuotedRealnames(value);
    const auto expected = static_cast<std::size_t>(1 + std::count(bare.begin(), bare.end(), ','));
    return result.size() == expected ? result : invalid;
}

// One RFC 5322 address header as Graph's `Name <addr>` list.
std::string addressList(const std::string& raw) {
    std::string out;
    for (const auto& [rawName, rawAddr] : getAddresses(decodeWords(raw))) {
        const std::string name = pyStrip(rawName), addr = pyStrip(rawAddr);
        std::string part;
        if (!name.empty() && !addr.empty() && lowerAscii(name) != lowerAscii(addr)) {
            part = name + " <" + addr + ">";
        } else if (!addr.empty() || !name.empty()) {
            part = addr.empty() ? name : addr;
        } else {
            continue;
        }
        if (!out.empty()) out += ", ";
        out += part;
    }
    return out;
}

// ---- html.unescape ----

struct Entity {
    const char* name;
    long cp;
    bool legacy;  // also matched without the closing ';'
};

const Entity kEntities[] = {
    {"amp", '&', true},     {"lt", '<', true},       {"gt", '>', true},      {"quot", '"', true},
    {"AMP", '&', true},     {"LT", '<', true},       {"GT", '>', true},      {"QUOT", '"', true},
    {"COPY", 0xA9, true},   {"REG", 0xAE, true},     {"apos", '\'', false},  {"nbsp", 0xA0, true},
    {"iexcl", 0xA1, true},  {"cent", 0xA2, true},    {"pound", 0xA3, true},  {"curren", 0xA4, true},
    {"yen", 0xA5, true},    {"brvbar", 0xA6, true},  {"sect", 0xA7, true},   {"uml", 0xA8, true},
    {"copy", 0xA9, true},   {"ordf", 0xAA, true},    {"laquo", 0xAB, true},  {"not", 0xAC, true},
    {"shy", 0xAD, true},    {"reg", 0xAE, true},     {"macr", 0xAF, true},   {"deg", 0xB0, true},
    {"plusmn", 0xB1, true}, {"sup2", 0xB2, true},    {"sup3", 0xB3, true},   {"acute", 0xB4, true},
    {"micro", 0xB5, true},  {"para", 0xB6, true},    {"middot", 0xB7, true}, {"cedil", 0xB8, true},
    {"sup1", 0xB9, true},   {"ordm", 0xBA, true},    {"raquo", 0xBB, true},  {"frac14", 0xBC, true},
    {"frac12", 0xBD, true}, {"frac34", 0xBE, true},  {"iquest", 0xBF, true}, {"Agrave", 0xC0, true},
    {"Aacute", 0xC1, true}, {"Acirc", 0xC2, true},   {"Atilde", 0xC3, true}, {"Auml", 0xC4, true},
    {"Aring", 0xC5, true},  {"AElig", 0xC6, true},   {"Ccedil", 0xC7, true}, {"Egrave", 0xC8, true},
    {"Eacute", 0xC9, true}, {"Ecirc", 0xCA, true},   {"Euml", 0xCB, true},   {"Igrave", 0xCC, true},
    {"Iacute", 0xCD, true}, {"Icirc", 0xCE, true},   {"Iuml", 0xCF, true},   {"ETH", 0xD0, true},
    {"Ntilde", 0xD1, true}, {"Ograve", 0xD2, true},  {"Oacute", 0xD3, true}, {"Ocirc", 0xD4, true},
    {"Otilde", 0xD5, true}, {"Ouml", 0xD6, true},    {"times", 0xD7, true},  {"Oslash", 0xD8, true},
    {"Ugrave", 0xD9, true}, {"Uacute", 0xDA, true},  {"Ucirc", 0xDB, true},  {"Uuml", 0xDC, true},
    {"Yacute", 0xDD, true}, {"THORN", 0xDE, true},   {"szlig", 0xDF, true},  {"agrave", 0xE0, true},
    {"aacute", 0xE1, true}, {"acirc", 0xE2, true},   {"atilde", 0xE3, true}, {"auml", 0xE4, true},
    {"aring", 0xE5, true},  {"aelig", 0xE6, true},   {"ccedil", 0xE7, true}, {"egrave", 0xE8, true},
    {"eacute", 0xE9, true}, {"ecirc", 0xEA, true},   {"euml", 0xEB, true},   {"igrave", 0xEC, true},
    {"iacute", 0xED, true}, {"icirc", 0xEE, true},   {"iuml", 0xEF, true},   {"eth", 0xF0, true},
    {"ntilde", 0xF1, true}, {"ograve", 0xF2, true},  {"oacute", 0xF3, true}, {"ocirc", 0xF4, true},
    {"otilde", 0xF5, true}, {"ouml", 0xF6, true},    {"divide", 0xF7, true}, {"oslash", 0xF8, true},
    {"ugrave", 0xF9, true}, {"uacute", 0xFA, true},  {"ucirc", 0xFB, true},  {"uuml", 0xFC, true},
    {"yacute", 0xFD, true}, {"thorn", 0xFE, true},   {"yuml", 0xFF, true},   {"OElig", 0x152, false},
    {"oelig", 0x153, false}, {"Scaron", 0x160, false}, {"scaron", 0x161, false}, {"Yuml", 0x178, false},
    {"fnof", 0x192, false}, {"circ", 0x2C6, false},  {"tilde", 0x2DC, false}, {"ensp", 0x2002, false},
    {"emsp", 0x2003, false}, {"thinsp", 0x2009, false}, {"zwnj", 0x200C, false}, {"zwj", 0x200D, false},
    {"lrm", 0x200E, false}, {"rlm", 0x200F, false},  {"ndash", 0x2013, false}, {"mdash", 0x2014, false},
    {"lsquo", 0x2018, false}, {"rsquo", 0x2019, false}, {"sbquo", 0x201A, false}, {"ldquo", 0x201C, false},
    {"rdquo", 0x201D, false}, {"bdquo", 0x201E, false}, {"dagger", 0x2020, false}, {"Dagger", 0x2021, false},
    {"bull", 0x2022, false}, {"hellip", 0x2026, false}, {"permil", 0x2030, false}, {"lsaquo", 0x2039, false},
    {"rsaquo", 0x203A, false}, {"euro", 0x20AC, false}, {"trade", 0x2122, false},
};

const Entity* findEntity(const std::string& name, bool withSemicolon) {
    for (const Entity& e : kEntities) {
        if (name == e.name && (withSemicolon || e.legacy)) return &e;
    }
    return nullptr;
}

bool invalidCodepoint(long n) {
    return (n >= 0x1 && n <= 0x8) || n == 0xB || (n >= 0xE && n <= 0x1F) || (n >= 0x7F && n <= 0x9F) ||
           (n >= 0xFDD0 && n <= 0xFDEF) || (n & 0xFFFE) == 0xFFFE;
}

std::string numericCharref(long num) {
    std::string out;
    if (num == 0) return "\xEF\xBF\xBD";
    if (num == 0x0D) return "\r";
    if (num >= 0x80 && num <= 0x9F) {
        const long cp = kCp1252High[num - 0x80];
        appendUtf8(out, cp ? cp : num);
        return out;
    }
    if ((num >= 0xD800 && num <= 0xDFFF) || num > 0x10FFFF) return "\xEF\xBF\xBD";
    if (invalidCodepoint(num)) return "";
    appendUtf8(out, num);
    return out;
}

std::string htmlUnescape(const std::string& s) {
    if (s.find('&') == std::string::npos) return s;
    std::string out;
    std::size_t i = 0;
    while (i < s.size()) {
        if (s[i] != '&') {
            out += s[i++];
            continue;
        }
        std::size_t j = i + 1;
        if (j < s.size() && s[j] == '#') {
            const bool hex = j + 1 < s.size() && (s[j + 1] == 'x' || s[j + 1] == 'X');
            std::size_t k = j + (hex ? 2 : 1);
            const std::size_t digits = k;
            long num = 0;
            while (k < s.size() && (hex ? std::isxdigit(static_cast<unsigned char>(s[k])) != 0
                                        : std::isdigit(static_cast<unsigned char>(s[k])) != 0)) {
                const long d = std::isdigit(static_cast<unsigned char>(s[k]))
                                   ? s[k] - '0'
                                   : std::tolower(static_cast<unsigned char>(s[k])) - 'a' + 10;
                num = std::min<long>(num * (hex ? 16 : 10) + d, 0x110000);
                ++k;
            }
            if (k == digits) {
                out += s[i++];
                continue;
            }
            if (k < s.size() && s[k] == ';') ++k;
            out += numericCharref(num);
            i = k;
            continue;
        }
        std::size_t k = j;
        while (k < s.size() && k - j < 32 && std::string("\t\n\f <&#;").find(s[k]) == std::string::npos) ++k;
        if (k == j) {
            out += s[i++];
            continue;
        }
        const std::string name = s.substr(j, k - j);
        const bool semi = k < s.size() && s[k] == ';';
        if (const Entity* e = findEntity(name, semi)) {
            appendUtf8(out, e->cp);
            i = k + (semi ? 1 : 0);
            continue;
        }
        const std::string ref = name + (semi ? ";" : "");
        bool matched = false;
        for (std::size_t x = ref.size() - 1; x >= 2; --x) {
            if (const Entity* e = findEntity(ref.substr(0, x), false)) {
                appendUtf8(out, e->cp);
                out += ref.substr(x);
                matched = true;
                break;
            }
        }
        if (!matched) out += "&" + ref;
        i = k + (semi ? 1 : 0);
    }
    return out;
}

// ---- message parts ----

using HeaderMap = std::map<std::string, std::string>;

HeaderMap headerMap(const json& part) {
    HeaderMap out;
    const json& headers = field(part, "headers");
    if (!headers.is_array()) return out;
    for (const json& header : headers) {
        const std::string name = lowerAscii(text(field(header, "name")));
        if (!name.empty() && !out.count(name)) out[name] = text(field(header, "value"));
    }
    return out;
}

std::string headerOf(const HeaderMap& headers, const std::string& name) {
    const auto it = headers.find(name);
    return it == headers.end() ? std::string() : it->second;
}

std::string received(const json& internalDate) {
    long long millis = 0;
    if (!pyInt(internalDate, millis)) return "";
    long long secs = millis / 1000;
    if (millis % 1000 < 0) --secs;
    long long days = secs / 86400;
    long long rem = secs % 86400;
    if (rem < 0) {
        rem += 86400;
        --days;
    }
    // Days since 1970-01-01 to a civil date (Howard Hinnant's algorithm).
    days += 719468;
    const long long era = (days >= 0 ? days : days - 146096) / 146097;
    const long long doe = days - era * 146097;
    const long long yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    long long y = yoe + era * 400;
    const long long doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    const long long mp = (5 * doy + 2) / 153;
    const long long d = doy - (153 * mp + 2) / 5 + 1;
    const long long m = mp < 10 ? mp + 3 : mp - 9;
    if (m <= 2) ++y;
    if (y < 1 || y > 9999) return "";
    char buf[32];
    std::snprintf(buf, sizeof buf, "%04lld-%02lld-%02lldT%02lld:%02lld:%02lldZ", y, m, d, rem / 3600, rem / 60 % 60,
                  rem % 60);
    return buf;
}

const json* findPart(const json& part, const std::string& mimeType) {
    if (lowerAscii(text(field(part, "mimeType"))) == mimeType && !truthy(field(part, "filename"))) return &part;
    const json& parts = field(part, "parts");
    if (!parts.is_array()) return nullptr;
    for (const json& child : parts) {
        if (const json* found = findPart(child, mimeType)) return found;
    }
    return nullptr;
}

std::string decodePart(const json& part) {
    const std::string data = text(field(field(part, "body"), "data"));
    if (data.empty()) return "";
    const auto badBase64 = [] {
        return MailboxError(
            "Gmail returned a message body that is not valid base64, so it cannot be read. Open the "
            "message in Gmail directly, and report this at https://github.com/amd/gaia/issues if it "
            "repeats.");
    };
    std::string translated;
    for (const char c : data) {
        if (static_cast<unsigned char>(c) >= 0x80) throw badBase64();
        translated += c == '-' ? '+' : c == '_' ? '/' : c;
    }
    translated += std::string((4 - data.size() % 4) % 4, '=');
    std::string raw;
    if (!a2bBase64(translated, raw)) throw badBase64();
    return utf8Replace(raw);
}

std::pair<std::string, std::string> selectBody(const json& payload) {
    static const std::pair<const char*, const char*> kKinds[] = {{"text/plain", "text"}, {"text/html", "html"}};
    for (const auto& [mimeType, kind] : kKinds) {
        if (const json* part = findPart(payload, mimeType)) return {decodePart(*part), kind};
    }
    return {"", "text"};
}

// An inline image (signature logo, cid: reference), not a file. Apple Mail
// marks PDFs inline too, so only images count.
bool isInline(const json& part) {
    if (lowerAscii(text(field(part, "mimeType"))).compare(0, 6, "image/") != 0) return false;
    const HeaderMap headers = headerMap(part);
    const std::string disposition = lowerAscii(pyStrip(headerOf(headers, "content-disposition")));
    if (disposition.compare(0, 10, "attachment") == 0) return false;
    return disposition.compare(0, 6, "inline") == 0 || !headerOf(headers, "content-id").empty();
}

void collectAttachments(const json& part, json& found) {
    const std::string filename = text(field(part, "filename"));
    if (!filename.empty() && !isInline(part)) {
        long long size = 0;
        const json& rawSize = field(field(part, "body"), "size");
        if (truthy(rawSize) && !pyInt(rawSize, size)) {
            throw std::invalid_argument("Gmail returned a part size that is not a number: " + pyRepr(rawSize));
        }
        found.push_back({{"name", decodeWords(filename)},
                         {"mime_type", lowerAscii(text(field(part, "mimeType")))},
                         {"size", size}});
    }
    const json& parts = field(part, "parts");
    if (!parts.is_array()) return;
    for (const json& child : parts) collectAttachments(child, found);
}

json categories(const json& labelIds, const std::map<std::string, json>& labelNames) {
    json out = json::array();
    if (!labelIds.is_array()) return out;
    for (const json& id : labelIds) {
        if (!id.is_string()) continue;
        const std::string lid = id.get<std::string>();
        if (kReservedLabelIds.count(lid) || lid.compare(0, kReservedLabelPrefix.size(), kReservedLabelPrefix) == 0) continue;
        const auto it = labelNames.find(lid);
        out.push_back(it == labelNames.end() ? id : it->second);
    }
    return out;
}

bool hasLabel(const json& labelIds, const char* label) {
    if (!labelIds.is_array()) return false;
    return std::any_of(labelIds.begin(), labelIds.end(), [&](const json& l) { return l.is_string() && l == label; });
}

json messageSummary(const json& msg, const std::map<std::string, json>& labelNames, bool includeBody) {
    const json& payload = field(msg, "payload");
    const HeaderMap headers = headerMap(payload);
    const json& labelIds = field(msg, "labelIds");
    json attachments = json::array();
    collectAttachments(payload, attachments);
    const std::string subject = decodeWords(headerOf(headers, "subject"));
    json summary = {
        {"id", field(msg, "id")},
        {"thread_id", truthy(field(msg, "threadId")) ? field(msg, "threadId") : field(msg, "id")},
        {"subject", subject.empty() ? "(no subject)" : subject},
        {"from", addressList(headerOf(headers, "from"))},
        {"to", addressList(headerOf(headers, "to"))},
        {"cc", addressList(headerOf(headers, "cc"))},
        {"received", received(field(msg, "internalDate"))},
        {"unread", hasLabel(labelIds, "UNREAD")},
        {"flagged", hasLabel(labelIds, "STARRED")},
        {"categories", categories(labelIds, labelNames)},
        // Gmail entity-encodes the snippet where Graph's bodyPreview is plain.
        {"preview", pyStrip(htmlUnescape(text(field(msg, "snippet"))))},
        {"has_attachments", !attachments.empty()},
        {"attachments", attachments},
        {"auto_submitted", decodeWords(headerOf(headers, "auto-submitted"))},
        {"x_autoreply", decodeWords(headerOf(headers, "x-autoreply"))},
    };
    if (includeBody) {
        const auto [body, kind] = selectBody(payload);
        summary["body"] = body;
        summary["body_content_type"] = kind;
    }
    return summary;
}

// ---- drafts and sources ----

Recipients recipients(const std::string& raw) {
    Recipients out;
    for (const auto& [name, addr] : getAddresses(raw)) {
        const std::string address = pyStrip(addr);
        if (!address.empty()) out.push_back({pyStrip(decodeWords(name)), address});
    }
    return out;
}

void attachmentNames(const json& part, std::vector<std::string>& names) {
    if (truthy(field(part, "filename"))) names.push_back(pyStr(field(part, "filename")));
    const json& parts = field(part, "parts");
    if (!parts.is_array()) return;
    for (const json& child : parts) attachmentNames(child, names);
}

std::vector<std::string> attachmentNames(const json& part) {
    std::vector<std::string> names;
    attachmentNames(part, names);
    return names;
}

// MIME's CRLFs and closing newline are not content.
std::string normalizedBody(std::string body) {
    std::string out;
    for (std::size_t i = 0; i < body.size(); ++i) {
        if (body[i] == '\r' && i + 1 < body.size() && body[i + 1] == '\n') continue;
        out += body[i];
    }
    if (!out.empty() && out.back() == '\n') out.pop_back();
    return out;
}

std::optional<std::string> optionalText(const std::string& s) {
    return s.empty() ? std::nullopt : std::optional<std::string>(s);
}

std::optional<std::string> optionalField(const json& obj, const char* key) {
    const json& value = field(obj, key);
    return value.is_null() ? std::nullopt : std::optional<std::string>(pyStr(value));
}

std::string validatedId(const std::string& value, const std::string& kind, const std::string& source) {
    if (!isGmailId(value)) throw std::invalid_argument(kind + " " + pyRepr(value) + " is not a Gmail id. Use one from " + source + ".");
    return value;
}

OutgoingMessage draftMessage(const json& draft) {
    const json& message = truthy(field(draft, "message")) ? field(draft, "message") : json::object();
    const json& payload = truthy(field(message, "payload")) ? field(message, "payload") : json::object();
    const HeaderMap headers = headerMap(payload);
    const auto [body, bodyType] = selectBody(payload);
    OutgoingMessage out;
    out.to = recipients(headerOf(headers, "to"));
    out.cc = recipients(headerOf(headers, "cc"));
    out.bcc = recipients(headerOf(headers, "bcc"));
    out.subject = decodeWords(headerOf(headers, "subject"));
    out.body = normalizedBody(body);
    out.bodyType = bodyType;
    out.threadId = optionalField(message, "threadId");
    out.inReplyTo = optionalText(headerOf(headers, "in-reply-to"));
    out.references = optionalText(headerOf(headers, "references"));
    out.attachments = attachmentNames(payload);
    if (bodyType == "text") {
        if (const json* html = findPart(payload, "text/html")) out.htmlAlternative = normalizedBody(decodePart(*html));
    }
    return out;
}

json messageResource(const OutgoingMessage& message) {
    json resource = {{"raw", outgoing::rawMime(message)}};
    if (message.threadId && !message.threadId->empty()) resource["threadId"] = *message.threadId;
    return resource;
}

// ---- backend ----

class GmailBackend : public ReadBackend, public WriteBackend {
public:
    GmailBackend(TokenSource token, Transport transport)
        : token_(std::move(token)), transport_(std::move(transport)) {}

    std::string getUserEmail() override {
        const json data = get("/users/me/profile");
        const std::string address = pyStrip(text(field(data, "emailAddress")));
        if (address.empty()) {
            throw withConnector(MailboxError("Gmail returned no address for the connected account (`emailAddress` "
                                             "was empty). The connection is in an unusable state and needs "
                                             "reconnecting."),
                                kGoogle);
        }
        return address;
    }

    json listFolders(int limit) override {
        std::vector<std::string> ids;
        for (const json& lab : labels()) ids.push_back(pyStr(lab["id"]));
        const auto keep = static_cast<std::size_t>(clamp(limit));
        if (ids.size() > keep) ids.resize(keep);
        json out = json::array();
        if (ids.empty()) return out;
        std::vector<std::string> paths;
        for (const auto& id : ids) paths.push_back("/users/me/labels/" + percentEncode(id));
        for (const json& detail : fanOut(paths, {})) {
            const json& id = field(detail, "id");
            const json& name = field(detail, "name");
            out.push_back({{"id", id},
                           {"name", truthy(name) ? name : truthy(id) ? id : json("")},
                           {"unread", valueOr(detail, "messagesUnread", 0)},
                           {"total", valueOr(detail, "messagesTotal", 0)}});
        }
        return out;
    }

    json listInbox(int limit, bool unreadOnly) override {
        return fetchSummaries(listIds(unreadOnly ? std::vector<std::string>{"INBOX", "UNREAD"}
                                                 : std::vector<std::string>{"INBOX"},
                                      "", limit));
    }

    json search(const std::string& query, int limit) override {
        if (pyStrip(query).empty()) throw std::invalid_argument("query must be a non-empty search string");
        return fetchSummaries(listIds({}, query, limit));
    }

    json getMessage(const std::string& messageId) override {
        if (pyStrip(messageId).empty()) throw std::invalid_argument("message_id must be a non-empty message id");
        if (!isGmailId(messageId)) {
            throw std::invalid_argument("message_id " + pyRepr(messageId) +
                                        " is not a Gmail message id. Use an id returned by list_inbox or search.");
        }
        const json data = get("/users/me/messages/" + messageId, {{"format", "full"}});
        return messageSummary(data, labelMap(), true);
    }

    json getThread(const std::string& threadId) override {
        if (!isGmailId(threadId)) {
            throw std::invalid_argument("thread_id " + pyRepr(threadId) +
                                        " is not a Gmail thread id. Use the thread_id from a list_inbox or "
                                        "search_email result.");
        }
        const json data = get("/users/me/threads/" + threadId, {{"format", "full"}});
        std::vector<std::pair<long long, json>> raw;
        const json& listed = field(data, "messages");
        if (listed.is_array()) {
            for (const json& m : listed) {
                long long when = 0;
                const json& date = field(m, "internalDate");
                if (truthy(date) && !pyInt(date, when)) {
                    throw std::invalid_argument("Gmail returned an internalDate that is not a number: " + pyRepr(date));
                }
                raw.emplace_back(when, m);
            }
        }
        // Gmail does not promise thread order.
        std::stable_sort(raw.begin(), raw.end(), [](const auto& a, const auto& b) { return a.first < b.first; });
        const auto names = labelMap();
        json messages = json::array();
        for (const auto& [when, msg] : raw) {
            (void)when;
            json summary = messageSummary(msg, names, true);
            const json& labelIds = field(msg, "labelIds");
            summary["draft"] = hasLabel(labelIds, "DRAFT");
            summary["_label_ids"] = truthy(labelIds) && labelIds.is_array() ? labelIds : json::array();
            messages.push_back(summary);
        }
        return {{"thread_id", threadId}, {"messages", messages}, {"truncated", false}};
    }

    json listThreadIds(const std::string& folder, int limit) override {
        std::string label;
        if (folder == "inbox") label = "INBOX";
        else if (folder == "sent") label = "SENT";
        else throw std::invalid_argument("folder must be one of ['inbox', 'sent'], got " + pyRepr(folder));
        const int wanted = clamp(limit);
        const json data = get("/users/me/messages", {{"labelIds", label}, {"maxResults", std::to_string(wanted)}});
        const json& listed = field(data, "messages");
        const json stubs = truthy(listed) && listed.is_array() ? listed : json::array();
        json threadIds = json::array();
        for (const json& stub : stubs) {
            const json& tid = field(stub, "threadId");
            if (!truthy(tid)) {
                throw MailboxError("Gmail listed message " + pyRepr(field(stub, "id")) +
                                   " without a threadId, so it cannot be grouped into a conversation. Retry; "
                                   "report it at https://github.com/amd/gaia/issues if it repeats.");
            }
            if (std::find(threadIds.begin(), threadIds.end(), tid) == threadIds.end()) threadIds.push_back(tid);
        }
        return {{"thread_ids", threadIds},
                {"messages_listed", stubs.size()},
                {"truncated", truthy(field(data, "nextPageToken")) || static_cast<int>(stubs.size()) >= wanted}};
    }


    json listDrafts(int limit) override {
        const json data = get("/users/me/drafts", {{"maxResults", std::to_string(clamp(limit))}});
        std::vector<std::string> paths;
        const json& listed = field(data, "drafts");
        if (listed.is_array()) {
            for (const json& d : listed) {
                if (truthy(field(d, "id"))) paths.push_back("/users/me/drafts/" + pyStr(d["id"]));
            }
        }
        json rows = json::array();
        if (paths.empty()) return rows;
        for (const json& draft : fanOut(paths, {{"format", "metadata"}})) {
            const json& message = truthy(field(draft, "message")) ? field(draft, "message") : json::object();
            const HeaderMap headers =
                headerMap(truthy(field(message, "payload")) ? field(message, "payload") : json::object());
            const std::string subject = decodeWords(headerOf(headers, "subject"));
            rows.push_back({{"draft_id", field(draft, "id")},
                            {"thread_id", field(message, "threadId")},
                            {"to", addressList(headerOf(headers, "to"))},
                            {"subject", subject.empty() ? "(no subject)" : subject},
                            {"preview", pyStrip(htmlUnescape(text(field(message, "snippet"))))}});
        }
        return rows;
    }

    // Gmail replaces a whole draft, and this rewrite carries no files.
    bool editKeepsAttachments() const override { return false; }

    OutgoingMessage getDraft(const std::string& draftId) override {
        validatedId(draftId, "draft_id", "create_draft or list_drafts");
        return draftMessage(get("/users/me/drafts/" + draftId, {{"format", "full"}}));
    }

    SourceMessage getSourceMessage(const std::string& messageId) override {
        validatedId(messageId, "message_id", "list_inbox or search_email");
        const json data = get("/users/me/messages/" + messageId, {{"format", "full"}});
        const json& payload = truthy(field(data, "payload")) ? field(data, "payload") : json::object();
        const HeaderMap headers = headerMap(payload);
        const Recipients senders = recipients(headerOf(headers, "from"));
        const auto [body, bodyType] = selectBody(payload);
        SourceMessage out;
        out.id = messageId;
        if (!senders.empty()) out.sender = senders[0];
        out.replyTo = recipients(headerOf(headers, "reply-to"));
        out.to = recipients(headerOf(headers, "to"));
        out.cc = recipients(headerOf(headers, "cc"));
        out.subject = decodeWords(headerOf(headers, "subject"));
        out.body = normalizedBody(body);
        out.bodyType = bodyType;
        out.threadId = optionalField(data, "threadId");
        out.internetMessageId = optionalText(headerOf(headers, "message-id"));
        out.references = optionalText(headerOf(headers, "references"));
        out.received = received(field(data, "internalDate"));
        out.attachments = attachmentNames(payload);
        return out;
    }

    std::string createDraft(const OutgoingMessage& message) override {
        const json created = write("POST", "/users/me/drafts", json{{"message", messageResource(message)}});
        return idOf(created);
    }

    void updateDraft(const std::string& draftId, const OutgoingMessage& message) override {
        validatedId(draftId, "draft_id", "create_draft or list_drafts");
        write("PUT", "/users/me/drafts/" + draftId, json{{"message", messageResource(message)}});
    }

    void discardDraft(const std::string& draftId) override {
        validatedId(draftId, "draft_id", "create_draft or list_drafts");
        write("DELETE", "/users/me/drafts/" + draftId, std::nullopt);
    }

    json sendDraft(const std::string& draftId) override {
        validatedId(draftId, "draft_id", "create_draft or list_drafts");
        const json sent = write("POST", "/users/me/drafts/send", json{{"id", draftId}});
        return {{"message_id", field(sent, "id")}, {"thread_id", field(sent, "threadId")}};
    }

    json send(const OutgoingMessage& message) override {
        const json sent = write("POST", "/users/me/messages/send", messageResource(message));
        return {{"message_id", field(sent, "id")}, {"thread_id", field(sent, "threadId")}};
    }

private:
    // Python's `created["id"]`.
    static std::string idOf(const json& created) {
        if (!created.is_object() || !created.contains("id")) throw std::out_of_range("'id'");
        return pyStr(created["id"]);
    }

    json write(const std::string& method, const std::string& path, const std::optional<json>& payload) {
        const std::string where = method + " " + path;
        HttpCall call{method, kGmailApiBase + path, {{"Authorization", "Bearer " + token_()}},
                      payload ? payload->dump() : ""};
        if (payload) call.headers.emplace("Content-Type", "application/json");
        HttpResponse response;
        try {
            response = transport_(call);
        } catch (const TransportError& e) {
            if (e.neverSent) {
                throw MailboxError("Could not reach Gmail for " + where + " (" + e.kind +
                                   "), so the request never went out. Check network connectivity, then retry.");
            }
            throw MailboxOutcomeUnknown("The connection to Gmail failed during " + where + " (" + e.kind +
                                        "), so GAIA can't tell whether it took effect. Check Sent and Drafts in "
                                        "Gmail before trying again.");
        } catch (const std::exception&) {
            throw MailboxOutcomeUnknown("The connection to Gmail failed during " + where +
                                        " (TransportError), so GAIA can't tell whether it took effect. Check Sent "
                                        "and Drafts in Gmail before trying again.");
        }
        const int code = response.status;
        if (code == 403) {
            const std::string reason = errorDetail(response).reason;
            if (reason == "insufficientPermissions" || reason == "PERMISSION_DENIED") {
                throw MailboxAuthError("Gmail refused this because the connection can't write mail (403). Reconnect "
                                       "Google in Settings -> Connectors and approve reading, composing and sending. "
                                       "(request: " +
                                       where + ")");
            }
        }
        if (code >= 500) {
            throw MailboxOutcomeUnknown("Gmail answered " + std::to_string(code) + " during " + where +
                                        ", so GAIA can't tell whether it took effect. Check Sent and Drafts in "
                                        "Gmail before trying again.");
        }
        if (code != 200 && code != 204) raise(response, where);
        if (response.body.empty()) return json::object();
        const json parsed = json::parse(response.body, nullptr, false);
        if (parsed.is_discarded()) {
            throw MailboxOutcomeUnknown("Gmail accepted " + where +
                                        " but its answer was unreadable, so GAIA can't confirm the result. Check "
                                        "Sent and Drafts in Gmail before trying again.");
        }
        return parsed;
    }

    static json valueOr(const json& obj, const char* key, int fallback) {
        return obj.is_object() && obj.contains(key) ? obj.at(key) : json(fallback);
    }

    static int clamp(int limit) {
        if (limit < 1) throw std::invalid_argument("limit must be >= 1, got " + std::to_string(limit));
        return std::min(limit, kGmailMaxLimit);
    }

    [[noreturn]] static void raise(const HttpResponse& response, const std::string& where) {
        const ErrorDetail detail = errorDetail(response);
        const int code = response.status;
        if (code == 401) {
            throw withConnector(MailboxAuthError("Gmail rejected the access token (401). The Google connection has "
                                                 "expired or been revoked and needs reconnecting. (request: " +
                                                 where + ")"),
                                kGoogle);
        }
        if (code == 403 && detail.reason == "accessNotConfigured") {
            throw MailboxAuthError(
                "The Gmail API is not enabled for the Google Cloud project behind this connection (403 "
                "accessNotConfigured), so no mailbox can be read. Enable it, wait a minute, then retry: " +
                (detail.helpUrl.empty() ? std::string(kGmailEnableUrl) : detail.helpUrl) + " (request: " + where +
                ")");
        }
        if (code == 403) {
            throw withConnector(
                MailboxAuthError("Gmail refused the request (403 \xE2\x80\x94 insufficient permissions). The connected "
                                 "Google account has not given GAIA permission to read mail, so it needs reconnecting "
                                 "with mail access ticked. (request: " +
                                 where + "; reason: " + (detail.reason.empty() ? "forbidden" : detail.reason) + ")"),
                kGoogle);
        }
        if (code == 404) {
            throw MailboxError("Gmail has no such message or thread (404). The id may be mistyped, or the mail was "
                               "deleted. Use an id returned by list_inbox or search_email. (request: " +
                               where + ")");
        }
        if (code == 429) {
            throw MailboxError("Gmail is rate-limiting this mailbox (429). Retry after " +
                               response.header("Retry-After", "unknown") +
                               "s. Reduce `limit` if this repeats. (request: " + where + ")");
        }
        throw MailboxError("Gmail request failed: " + where + " returned " + std::to_string(code) +
                           (detail.reason.empty() ? "" : " (" + detail.reason + ")") +
                           ". Retry, and check https://www.google.com/appsstatus if it persists.");
    }

    // Re-minted per request unless a fan-out already minted one: a cached token
    // would let a mid-scan revoke look like success.
    json get(const std::string& path, const Params& params = {}, const std::string& token = "") const {
        const std::string bearer = token.empty() ? token_() : token;
        HttpCall call{"GET", kGmailApiBase + path + queryString(params), {{"Authorization", "Bearer " + bearer}}, ""};
        HttpResponse response;
        try {
            response = transport_(call);
        } catch (const std::exception& e) {
            throw MailboxError("Could not reach Gmail for " + path + ": " + e.what() +
                               ". Check network connectivity, then retry.");
        }
        if (response.status != 200) raise(response, "GET " + path);
        return parseAnswer(response, "Gmail", "GET " + path);
    }

    const std::vector<json>& labels() {
        if (!labelCache_) {
            const json data = get("/users/me/labels");
            std::vector<json> kept;
            const json& listed = field(data, "labels");
            if (listed.is_array()) {
                for (const json& lab : listed) {
                    if (truthy(field(lab, "id"))) kept.push_back(lab);
                }
            }
            labelCache_ = std::move(kept);
        }
        return *labelCache_;
    }

    // Label id -> display name; the message resource carries only opaque ids.
    std::map<std::string, json> labelMap() {
        std::map<std::string, json> out;
        for (const json& lab : labels()) {
            const json& name = field(lab, "name");
            out[pyStr(lab["id"])] = truthy(name) ? name : lab["id"];
        }
        return out;
    }

    std::vector<json> fanOut(const std::vector<std::string>& paths, const Params& params) const {
        const std::string token = token_();
        std::vector<json> results(paths.size());
        std::vector<std::exception_ptr> errors(paths.size());
        std::atomic<std::size_t> next{0};
        const auto worker = [&] {
            for (std::size_t i = next++; i < paths.size(); i = next++) {
                try {
                    results[i] = get(paths[i], params, token);
                } catch (...) {
                    errors[i] = std::current_exception();
                }
            }
        };
        std::vector<std::thread> pool;
        for (std::size_t n = 0; n < std::min(kFetchConcurrency, paths.size()); ++n) pool.emplace_back(worker);
        for (auto& t : pool) t.join();
        // A failed subrequest raises rather than shortening the listing into a smaller-looking inbox.
        for (const auto& e : errors) {
            if (e) std::rethrow_exception(e);
        }
        return results;
    }

    std::vector<std::string> listIds(const std::vector<std::string>& labelIds, const std::string& query, int limit) {
        Params params = {{"maxResults", std::to_string(clamp(limit))}};
        for (const auto& id : labelIds) params.emplace_back("labelIds", id);
        if (!query.empty()) params.emplace_back("q", query);
        const json data = get("/users/me/messages", params);
        std::vector<std::string> ids;
        // The key is absent, not empty, when nothing matches.
        const json& listed = field(data, "messages");
        if (!listed.is_array()) return ids;
        for (const json& m : listed) {
            if (truthy(field(m, "id"))) ids.push_back(pyStr(m["id"]));
        }
        return ids;
    }

    json fetchSummaries(const std::vector<std::string>& ids) {
        json out = json::array();
        if (ids.empty()) return out;
        const auto names = labelMap();
        std::vector<std::string> paths;
        for (const auto& id : ids) paths.push_back("/users/me/messages/" + percentEncode(id));
        for (const json& m : fanOut(paths, {{"format", "full"}, {"fields", summaryFields()}})) {
            out.push_back(messageSummary(m, names, false));
        }
        return out;
    }

    TokenSource token_;
    Transport transport_;
    std::optional<std::vector<json>> labelCache_;
};

}  // namespace

std::unique_ptr<ReadBackend> makeGmailReadBackend(TokenSource token, Transport transport) {
    return std::make_unique<GmailBackend>(std::move(token), std::move(transport));
}

std::unique_ptr<WriteBackend> makeGmailWriteBackend(TokenSource token, Transport transport) {
    return std::make_unique<GmailBackend>(std::move(token), std::move(transport));
}

}  // namespace gaia::mail
