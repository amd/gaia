// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "mail_text.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <stdexcept>

namespace gaia::mail::text {

namespace {

struct CpRange {
    uint32_t lo;
    uint32_t hi;
};
// Text is an offset into the table's NUL-separated pool (k<Table>Text).
struct CpText {
    uint32_t cp;
    uint32_t at;
};
struct NameText {
    uint32_t name;
    uint32_t text;
};

#include "mail_text_tables.inc"

template <size_t N>
bool inRanges(const CpRange (&table)[N], char32_t cp) {
    const auto* end = table + N;
    const auto* it = std::upper_bound(table, end, static_cast<uint32_t>(cp),
                                      [](uint32_t value, const CpRange& r) { return value < r.lo; });
    return it != table && cp <= (it - 1)->hi;
}

template <size_t N, size_t P>
const char* lookup(const CpText (&table)[N], const char (&pool)[P], char32_t cp) {
    const auto* end = table + N;
    const auto* it = std::lower_bound(table, end, static_cast<uint32_t>(cp),
                                      [](const CpText& e, uint32_t value) { return e.cp < value; });
    return it != end && it->cp == cp ? pool + it->at : nullptr;
}

const char* html5Entity(const std::string& name) {
    const auto* end = kHtml5 + sizeof(kHtml5) / sizeof(kHtml5[0]);
    const auto* it = std::lower_bound(kHtml5, end, name, [](const NameText& e, const std::string& value) {
        return std::strcmp(kHtml5Text + e.name, value.c_str()) < 0;
    });
    return it != end && name == kHtml5Text + it->name ? kHtml5Text + it->text : nullptr;
}

bool contains(const char* set, char c) { return c != '\0' && std::strchr(set, c) != nullptr; }

constexpr char32_t kCapitalSigma = 0x3A3;

bool lineBreak(char32_t cp) {
    switch (cp) {
        case '\n': case '\r': case 0x0B: case 0x0C: case 0x1C: case 0x1D: case 0x1E:
        case 0x85: case 0x2028: case 0x2029:
            return true;
        default:
            return false;
    }
}

}  // namespace

// ---- UTF-8 ----

Cps decode(const std::string& utf8) {
    Cps out;
    out.reserve(utf8.size());
    const auto* s = reinterpret_cast<const unsigned char*>(utf8.data());
    const size_t n = utf8.size();
    size_t i = 0;
    while (i < n) {
        const unsigned char b = s[i];
        if (b < 0x80) {
            out.push_back(b);
            ++i;
            continue;
        }
        size_t len = 0;
        char32_t cp = 0;
        char32_t min = 0;
        if (b >= 0xC2 && b <= 0xDF) { len = 2; cp = b & 0x1F; min = 0x80; }
        else if (b >= 0xE0 && b <= 0xEF) { len = 3; cp = b & 0x0F; min = 0x800; }
        else if (b >= 0xF0 && b <= 0xF4) { len = 4; cp = b & 0x07; min = 0x10000; }
        bool ok = len > 0 && i + len <= n;
        for (size_t k = 1; ok && k < len; ++k) {
            if ((s[i + k] & 0xC0) != 0x80) ok = false;
            else cp = (cp << 6) | (s[i + k] & 0x3F);
        }
        if (ok && (cp < min || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF))) ok = false;
        if (!ok) {
            out.push_back(0xDC00 + b);
            ++i;
            continue;
        }
        out.push_back(cp);
        i += len;
    }
    return out;
}

std::string encode(char32_t cp) {
    std::string out;
    if (cp >= 0xDC80 && cp <= 0xDCFF) {
        out.push_back(static_cast<char>(cp - 0xDC00));
    } else if (cp < 0x80) {
        out.push_back(static_cast<char>(cp));
    } else if (cp < 0x800) {
        out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else if (cp < 0x10000) {
        out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else {
        out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    }
    return out;
}

std::string encode(const Cps& cps) {
    std::string out;
    out.reserve(cps.size());
    for (char32_t cp : cps) out += encode(cp);
    return out;
}

// ---- character classes ----

bool isWord(char32_t cp) { return inRanges(kWord, cp); }
bool isDigit(char32_t cp) { return inRanges(kDigit, cp); }
bool isSpace(char32_t cp) { return inRanges(kSpace, cp); }

bool sameIgnoreCase(char32_t cp, char pattern) {
    const char p = static_cast<char>(pattern >= 'A' && pattern <= 'Z' ? pattern - 'A' + 'a' : pattern);
    if (cp == static_cast<unsigned char>(p)) return true;
    if (p < 'a' || p > 'z') return false;
    const char* folded = lookup(kIgnoreCase, kIgnoreCaseText, cp);
    return folded != nullptr && folded[0] == p;
}

bool matchesIgnoreCase(const Cps& text, size_t at, const char* literal) {
    const size_t len = std::strlen(literal);
    if (at + len > text.size()) return false;
    for (size_t k = 0; k < len; ++k) {
        if (!sameIgnoreCase(text[at + k], literal[k])) return false;
    }
    return true;
}

bool wordBoundary(const Cps& text, size_t at) {
    const bool before = at > 0 && isWord(text[at - 1]);
    const bool after = at < text.size() && isWord(text[at]);
    return before != after;
}

// ---- str methods ----

std::string lower(const std::string& text) {
    if (std::all_of(text.begin(), text.end(), [](char c) { return static_cast<unsigned char>(c) < 0x80; })) {
        std::string out = text;
        for (char& c : out) {
            if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
        }
        return out;
    }
    const Cps cps = decode(text);
    std::string out;
    out.reserve(text.size());
    for (size_t i = 0; i < cps.size(); ++i) {
        const char32_t cp = cps[i];
        if (cp == kCapitalSigma) {
            size_t j = i;
            while (j > 0 && inRanges(kCaseIgnorable, cps[j - 1])) --j;
            bool final = j > 0 && inRanges(kSigmaCased, cps[j - 1]);
            if (final) {
                size_t k = i + 1;
                while (k < cps.size() && inRanges(kCaseIgnorable, cps[k])) ++k;
                final = k == cps.size() || !inRanges(kSigmaCased, cps[k]);
            }
            out += encode(final ? 0x3C2 : 0x3C3);
            continue;
        }
        const char* lowered = lookup(kLower, kLowerText, cp);
        out += lowered ? std::string(lowered) : encode(cp);
    }
    return out;
}

std::string strip(const std::string& text) {
    const Cps cps = decode(text);
    size_t begin = 0;
    size_t end = cps.size();
    while (begin < end && isSpace(cps[begin])) ++begin;
    while (end > begin && isSpace(cps[end - 1])) --end;
    return encode(cps.substr(begin, end - begin));
}

std::vector<std::string> split(const std::string& text) {
    const Cps cps = decode(text);
    std::vector<std::string> out;
    size_t i = 0;
    while (i < cps.size()) {
        while (i < cps.size() && isSpace(cps[i])) ++i;
        const size_t start = i;
        while (i < cps.size() && !isSpace(cps[i])) ++i;
        if (i > start) out.push_back(encode(cps.substr(start, i - start)));
    }
    return out;
}

std::vector<std::string> splitlines(const std::string& text) {
    const Cps cps = decode(text);
    std::vector<std::string> out;
    size_t i = 0;
    while (i < cps.size()) {
        size_t j = i;
        while (j < cps.size() && !lineBreak(cps[j])) ++j;
        const size_t eol = j;
        if (j < cps.size()) j += (cps[j] == '\r' && j + 1 < cps.size() && cps[j + 1] == '\n') ? 2 : 1;
        out.push_back(encode(cps.substr(i, eol - i)));
        i = j;
    }
    return out;
}

// ---- html.unescape ----

namespace {

bool asciiDigit(char32_t c) { return c >= '0' && c <= '9'; }
bool asciiHex(char32_t c) { return asciiDigit(c) || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'); }

std::string numericCharref(uint64_t num, bool huge) {
    if (!huge && num <= 0xFFFF) {
        if (const char* fixed = lookup(kInvalidCharrefs, kInvalidCharrefsText, static_cast<char32_t>(num))) return fixed;
    }
    if (huge || (num >= 0xD800 && num <= 0xDFFF) || num > 0x10FFFF) return encode(0xFFFD);
    if (inRanges(kInvalidCodepoints, static_cast<char32_t>(num))) return "";
    return encode(static_cast<char32_t>(num));
}

}  // namespace

std::string htmlUnescape(const std::string& text) {
    if (text.find('&') == std::string::npos) return text;
    const Cps s = decode(text);
    const size_t n = s.size();
    std::string out;
    out.reserve(text.size());
    size_t i = 0;
    while (i < n) {
        if (s[i] != '&') {
            out += encode(s[i++]);
            continue;
        }
        size_t j = i + 1;
        // &#123;? then &#x1f;?, as the alternation orders them.
        if (j < n && s[j] == '#') {
            size_t k = j + 1;
            const bool hex = k < n && (s[k] == 'x' || s[k] == 'X') && k + 1 < n && asciiHex(s[k + 1]);
            const bool dec = k < n && asciiDigit(s[k]);
            if (dec || hex) {
                if (hex) ++k;
                uint64_t num = 0;
                bool huge = false;
                while (k < n && (hex ? asciiHex(s[k]) : asciiDigit(s[k]))) {
                    const char32_t c = s[k];
                    const uint64_t digit = asciiDigit(c) ? c - '0' : (c | 0x20) - 'a' + 10;
                    if (!huge) {
                        num = num * (hex ? 16 : 10) + digit;
                        if (num > 0x10FFFF) huge = true;
                    }
                    ++k;
                }
                if (k < n && s[k] == ';') ++k;
                out += numericCharref(num, huge);
                i = k;
                continue;
            }
            out += '&';
            ++i;
            continue;
        }
        size_t k = j;
        while (k < n && k - j < 32 && !contains("\t\n\f <&#;", s[k] < 0x80 ? static_cast<char>(s[k]) : 'x')) ++k;
        if (k == j) {
            out += '&';
            ++i;
            continue;
        }
        if (k < n && s[k] == ';') ++k;
        const Cps name = s.substr(j, k - j);
        if (const char* value = html5Entity(encode(name))) {
            out += value;
        } else {
            bool found = false;
            for (size_t x = name.size() - 1; x > 1; --x) {
                if (const char* value = html5Entity(encode(name.substr(0, x)))) {
                    out += value;
                    out += encode(name.substr(x));
                    found = true;
                    break;
                }
            }
            if (!found) out += "&" + encode(name);
        }
        i = k;
    }
    return out;
}

// ---- repr ----

std::string pyRepr(const json& value) {
    if (value.is_null()) return "None";
    if (value.is_boolean()) return value.get<bool>() ? "True" : "False";
    if (!value.is_string()) return value.dump();
    const Cps cps = decode(value.get<std::string>());
    const bool single = std::find(cps.begin(), cps.end(), U'\'') != cps.end();
    const bool dbl = std::find(cps.begin(), cps.end(), U'"') != cps.end();
    const char32_t quote = single && !dbl ? U'"' : U'\'';
    std::string out(1, static_cast<char>(quote));
    for (char32_t cp : cps) {
        char buf[16];
        if (cp == quote || cp == '\\') {
            out += '\\';
            out += static_cast<char>(cp);
        } else if (cp == '\t') {
            out += "\\t";
        } else if (cp == '\n') {
            out += "\\n";
        } else if (cp == '\r') {
            out += "\\r";
        } else if ((cp >= 0xD800 && cp <= 0xDFFF) || inRanges(kUnprintable, cp)) {
            if (cp < 0x100) std::snprintf(buf, sizeof(buf), "\\x%02x", static_cast<unsigned>(cp));
            else if (cp < 0x10000) std::snprintf(buf, sizeof(buf), "\\u%04x", static_cast<unsigned>(cp));
            else std::snprintf(buf, sizeof(buf), "\\U%08x", static_cast<unsigned>(cp));
            out += buf;
        } else {
            out += encode(cp);
        }
    }
    out += static_cast<char>(quote);
    return out;
}

// ---- email.utils address parsing ----

namespace {

constexpr const char* kSpecials = "()<>@,:;.\"[]";
constexpr const char* kLws = " \t";
constexpr const char* kFws = " \t\r\n";
constexpr const char* kAtomEnds = "()<>@,:;.\"[] \t\r\n";
constexpr const char* kPhraseEnds = "()<>@,:;\"[] \t\r\n";

std::string joined(const std::vector<std::string>& parts, const std::string& sep) {
    std::string out;
    for (size_t i = 0; i < parts.size(); ++i) {
        if (i) out += sep;
        out += parts[i];
    }
    return out;
}

// email._parseaddr.AddrlistClass, method for method.
class AddrlistParser {
public:
    explicit AddrlistParser(std::string field) : f_(std::move(field)) {}

    std::vector<Address> getaddrlist() {
        std::vector<Address> result;
        while (pos_ < f_.size()) {
            auto ad = getaddress();
            if (!ad.empty()) result.insert(result.end(), ad.begin(), ad.end());
            else result.push_back({});
        }
        return result;
    }

private:
    char at() const { return f_[pos_]; }
    bool more() const { return pos_ < f_.size(); }

    std::string gotonext() {
        std::string ws;
        while (more()) {
            if (contains(" \t\n\r", at())) {
                if (!contains("\n\r", at())) ws += at();
                ++pos_;
            } else if (at() == '(') {
                comments_.push_back(getcomment());
            } else {
                break;
            }
        }
        return ws;
    }

    std::vector<Address> getaddress() {
        comments_.clear();
        gotonext();
        const size_t oldpos = pos_;
        // Python keeps an alias to the same list here, so restoring it later is a no-op.
        auto plist = getphraselist();
        gotonext();
        std::vector<Address> out;
        if (!more()) {
            if (!plist.empty()) out.push_back({joined(comments_, " "), plist[0]});
        } else if (contains(".@", at())) {
            pos_ = oldpos;
            std::string spec = getaddrspec();
            out.push_back({joined(comments_, " "), spec});
        } else if (at() == ':') {
            const size_t fieldlen = f_.size();
            ++pos_;
            while (more()) {
                gotonext();
                if (pos_ < fieldlen && at() == ';') {
                    ++pos_;
                    break;
                }
                auto inner = getaddress();
                out.insert(out.end(), inner.begin(), inner.end());
            }
        } else if (at() == '<') {
            std::string route = getrouteaddr();
            if (!comments_.empty()) {
                out.push_back({joined(plist, " ") + " (" + joined(comments_, " ") + ")", route});
            } else {
                out.push_back({joined(plist, " "), route});
            }
        } else {
            if (!plist.empty()) out.push_back({joined(comments_, " "), plist[0]});
            else if (contains(kSpecials, at())) ++pos_;
        }
        gotonext();
        if (more() && at() == ',') ++pos_;
        return out;
    }

    std::string getrouteaddr() {
        bool expectroute = false;
        ++pos_;
        gotonext();
        std::string adlist;
        while (more()) {
            if (expectroute) {
                getdomain();
                expectroute = false;
            } else if (at() == '>') {
                ++pos_;
                break;
            } else if (at() == '@') {
                ++pos_;
                expectroute = true;
            } else if (at() == ':') {
                ++pos_;
            } else {
                adlist = getaddrspec();
                ++pos_;
                break;
            }
            gotonext();
        }
        return adlist;
    }

    static std::string quote(const std::string& s) {
        std::string out;
        for (char c : s) {
            if (c == '\\' || c == '"') out += '\\';
            out += c;
        }
        return out;
    }

    std::string getaddrspec() {
        std::vector<std::string> aslist;
        gotonext();
        while (more()) {
            bool preserveWs = true;
            if (at() == '.') {
                if (!aslist.empty() && strip(aslist.back()).empty()) aslist.pop_back();
                aslist.push_back(".");
                ++pos_;
                preserveWs = false;
            } else if (at() == '"') {
                aslist.push_back("\"" + quote(getquote()) + "\"");
            } else if (contains(kAtomEnds, at())) {
                if (!aslist.empty() && strip(aslist.back()).empty()) aslist.pop_back();
                break;
            } else {
                aslist.push_back(getatom(kAtomEnds));
            }
            const std::string ws = gotonext();
            if (preserveWs && !ws.empty()) aslist.push_back(ws);
        }
        if (!more() || at() != '@') return joined(aslist, "");
        aslist.push_back("@");
        ++pos_;
        gotonext();
        const std::string domain = getdomain();
        if (domain.empty()) return "";
        return joined(aslist, "") + domain;
    }

    std::string getdomain() {
        std::vector<std::string> sdlist;
        while (more()) {
            if (contains(kLws, at())) {
                ++pos_;
            } else if (at() == '(') {
                comments_.push_back(getcomment());
            } else if (at() == '[') {
                sdlist.push_back("[" + getdelimited('[', "]\r", false) + "]");
            } else if (at() == '.') {
                ++pos_;
                sdlist.push_back(".");
            } else if (at() == '@') {
                return "";
            } else if (contains(kAtomEnds, at())) {
                break;
            } else {
                sdlist.push_back(getatom(kAtomEnds));
            }
        }
        return joined(sdlist, "");
    }

    std::string getdelimited(char begin, const char* endchars, bool allowComments) {
        if (at() != begin) return "";
        std::string out;
        bool quoted = false;
        ++pos_;
        while (more()) {
            if (quoted) {
                out += at();
                quoted = false;
            } else if (contains(endchars, at())) {
                ++pos_;
                break;
            } else if (allowComments && at() == '(') {
                out += getcomment();
                continue;
            } else if (at() == '\\') {
                quoted = true;
            } else {
                out += at();
            }
            ++pos_;
        }
        return out;
    }

    std::string getquote() { return getdelimited('"', "\"\r", false); }
    std::string getcomment() { return getdelimited('(', ")\r", true); }

    std::string getatom(const char* atomends) {
        std::string out;
        while (more() && !contains(atomends, at())) out += f_[pos_++];
        return out;
    }

    std::vector<std::string> getphraselist() {
        std::vector<std::string> plist;
        while (more()) {
            if (contains(kFws, at())) {
                ++pos_;
            } else if (at() == '"') {
                plist.push_back(getquote());
            } else if (at() == '(') {
                comments_.push_back(getcomment());
            } else if (contains(kPhraseEnds, at())) {
                break;
            } else {
                plist.push_back(getatom(kPhraseEnds));
            }
        }
        return plist;
    }

    std::string f_;
    size_t pos_ = 0;
    std::vector<std::string> comments_;
};

std::vector<Address> addressList(const std::string& field) {
    if (field.empty()) return {};
    return AddrlistParser(field).getaddrlist();
}

// (pos, char) pairs where an escaped char is reported as "\\x" (_iter_escaped_chars).
struct EscapedChar {
    size_t pos;
    char ch;
    bool escaped;
};

std::vector<EscapedChar> escapedChars(const std::string& addr) {
    std::vector<EscapedChar> out;
    bool escape = false;
    size_t pos = 0;
    for (pos = 0; pos < addr.size(); ++pos) {
        if (escape) {
            out.push_back({pos, addr[pos], true});
            escape = false;
        } else if (addr[pos] == '\\') {
            escape = true;
        } else {
            out.push_back({pos, addr[pos], false});
        }
    }
    if (escape) out.push_back({pos ? pos - 1 : 0, '\\', true});
    return out;
}

std::string stripQuotedRealnames(const std::string& addr) {
    if (addr.find('"') == std::string::npos) return addr;
    size_t start = 0;
    bool open = false;
    size_t openPos = 0;
    std::string result;
    for (const auto& e : escapedChars(addr)) {
        if (e.escaped || e.ch != '"') continue;
        if (!open) {
            open = true;
            openPos = e.pos;
        } else {
            if (start != openPos) result += addr.substr(start, openPos - start);
            start = e.pos + 1;
            open = false;
        }
    }
    if (start < addr.size()) result += addr.substr(start);
    return result;
}

bool checkParenthesis(const std::string& addr) {
    int opens = 0;
    for (const auto& e : escapedChars(stripQuotedRealnames(addr))) {
        if (e.escaped) continue;
        if (e.ch == '(') ++opens;
        else if (e.ch == ')' && --opens < 0) return false;
    }
    return opens == 0;
}

std::vector<Address> postParse(std::vector<Address> parsed) {
    for (auto& a : parsed) {
        if (a.addr.find('[') != std::string::npos) a = {};
    }
    return parsed;
}

}  // namespace

std::vector<Address> getaddresses(const std::string& field) {
    const std::string value = checkParenthesis(field) ? field : "('', '')";
    auto result = postParse(addressList(value));
    const std::string bare = stripQuotedRealnames(value);
    const size_t expected = 1 + static_cast<size_t>(std::count(bare.begin(), bare.end(), ','));
    if (result.size() != expected) return {Address{}};
    return result;
}

Address parseaddr(const std::string& field) {
    const std::string value = checkParenthesis(field) ? field : "('', '')";
    auto addrs = postParse(addressList(value));
    if (addrs.size() != 1) return {};
    return addrs[0];
}

// ---- untrusted_text ----

const char* markerLetters(char32_t cp) {
    const char* letters = lookup(kMarkerLetters, kMarkerLettersText, cp);
    return letters ? letters : "";
}

char fenceSide(char32_t cp) {
    if (inRanges(kFenceLt, cp)) return '<';
    if (inRanges(kFenceGt, cp)) return '>';
    return 0;
}

bool fenceRunInvisible(char32_t cp) { return inRanges(kFenceInvisible, cp); }

// ---- JSON as Python sees it ----

bool truthy(const json& value) {
    switch (value.type()) {
        case json::value_t::null: return false;
        case json::value_t::boolean: return value.get<bool>();
        case json::value_t::string: return !value.get_ref<const std::string&>().empty();
        case json::value_t::number_integer: return value.get<int64_t>() != 0;
        case json::value_t::number_unsigned: return value.get<uint64_t>() != 0;
        case json::value_t::number_float: return value.get<double>() != 0.0;
        case json::value_t::array:
        case json::value_t::object: return !value.empty();
        default: return true;
    }
}

std::string textField(const json& obj, const char* key) {
    if (!obj.is_object()) return "";
    const auto it = obj.find(key);
    if (it == obj.end() || !truthy(*it)) return "";
    if (!it->is_string()) {
        throw std::invalid_argument(std::string("message field '") + key + "' must be text, got " + it->dump());
    }
    return it->get<std::string>();
}

std::string join(const std::vector<std::string>& items, const std::string& sep) {
    std::string out;
    for (size_t i = 0; i < items.size(); ++i) out += (i ? sep : "") + items[i];
    return out;
}

bool isAscii(const std::string& bytes) {
    return std::all_of(bytes.begin(), bytes.end(), [](char c) { return static_cast<unsigned char>(c) < 0x80; });
}

}  // namespace gaia::mail::text
