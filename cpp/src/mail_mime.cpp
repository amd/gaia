// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The parts of Python's email package gmail.raw_mime runs (header parsing and
// folding, RFC 2047 words, the content-transfer-encoding choice), so Gmail
// receives the same bytes from either runtime. Lengths are code points, as
// Python counts them.

#include "mail_mime.h"

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
using text::isAscii;
using text::join;

namespace {

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
    return "=?utf-8?b?" + mime::base64(bytes) + "?=";
}

// binascii.a2b_base64(data, strict_mode=strict); false where Python raises binascii.Error.
bool a2bBase64(const std::string& data, bool strict, std::string& out) {
    static const std::string kAlphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    out.clear();
    int quadPos = 0, pads = 0;
    unsigned leftchar = 0;
    bool paddingStarted = false;
    for (size_t i = 0; i < data.size(); ++i) {
        if (data[i] == '=') {
            paddingStarted = true;
            if (strict && quadPos == 0) return false;
            if (quadPos >= 2 && quadPos + ++pads >= 4) return !(strict && i + 1 < data.size());
            continue;
        }
        const size_t v = kAlphabet.find(data[i]);
        if (v == std::string::npos) {
            if (strict) return false;
            continue;
        }
        if (strict && paddingStarted) return false;
        pads = 0;
        const unsigned c = static_cast<unsigned>(v);
        switch (quadPos) {
            case 0:
                leftchar = c;
                break;
            case 1:
                out += static_cast<char>(((leftchar << 2) | (c >> 4)) & 0xFF);
                leftchar = c & 0x0F;
                break;
            case 2:
                out += static_cast<char>(((leftchar << 4) | (c >> 2)) & 0xFF);
                leftchar = c & 0x03;
                break;
            default:
                out += static_cast<char>(((leftchar << 6) | c) & 0xFF);
                leftchar = 0;
        }
        quadPos = (quadPos + 1) % 4;
    }
    return quadPos == 0;
}

// _encoded_words.decode_b: strict with the padding fixed, then lenient, then the text itself.
std::string decodeB(const std::string& encoded) {
    const size_t padErr = encoded.size() % 4;
    std::string out;
    if (a2bBase64(encoded + std::string(padErr ? 4 - padErr : 0, '='), true, out)) return out;
    if (a2bBase64(encoded, false, out)) return out;
    if (a2bBase64(encoded + "==", false, out)) return out;
    return encoded;
}

// _encoded_words.decode_q
std::string decodeQ(const std::string& encoded) {
    std::string out;
    for (size_t i = 0; i < encoded.size(); ++i) {
        const char c = encoded[i];
        if (c == '=' && i + 2 < encoded.size() && std::isxdigit(static_cast<unsigned char>(encoded[i + 1])) &&
            std::isxdigit(static_cast<unsigned char>(encoded[i + 2]))) {
            out += static_cast<char>(std::stoi(encoded.substr(i + 1, 2), nullptr, 16));
            i += 2;
        } else {
            out += c == '_' ? ' ' : c;
        }
    }
    return out;
}

// Strict UTF-8 as Python's codec reads it: no overlongs, surrogates or code points past U+10FFFF.
bool validUtf8(const std::string& s) {
    for (size_t i = 0; i < s.size();) {
        const auto b = static_cast<unsigned char>(s[i]);
        size_t n = 0;
        char32_t cp = 0, min = 0;
        if (b < 0x80) {
            ++i;
            continue;
        } else if ((b & 0xE0) == 0xC0) {
            n = 1, cp = b & 0x1F, min = 0x80;
        } else if ((b & 0xF0) == 0xE0) {
            n = 2, cp = b & 0x0F, min = 0x800;
        } else if ((b & 0xF8) == 0xF0) {
            n = 3, cp = b & 0x07, min = 0x10000;
        } else {
            return false;
        }
        if (i + n >= s.size()) return false;
        for (size_t k = 1; k <= n; ++k) {
            const auto c = static_cast<unsigned char>(s[i + k]);
            if ((c & 0xC0) != 0x80) return false;
            cp = (cp << 6) | (c & 0x3F);
        }
        if (cp < min || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) return false;
        i += n + 1;
    }
    return true;
}

// encodings.normalize_encoding, lowercased, as codecs.lookup reads a charset name.
std::string codecName(const std::string& charset) {
    std::string out;
    bool punct = false;
    for (char c : charset) {
        const auto u = static_cast<unsigned char>(c);
        if (std::isalnum(u) || c == '.') {
            if (punct && !out.empty()) out += '_';
            out += static_cast<char>(std::tolower(u));
            punct = false;
        } else {
            punct = true;
        }
    }
    return out;
}

// bstring.decode(charset): UTF-8 and Latin-1 fully, and ASCII bytes under any other name, as every
// ASCII-compatible codec and Python's unknown-charset path read them. False where it can't match Python.
bool decodeCharset(const std::string& bytes, const std::string& charset, Cps& out) {
    static const std::set<std::string> kUtf8{"utf_8", "utf8", "u8", "utf", "utf8_ucs2", "utf8_ucs4", "cp65001"};
    static const std::set<std::string> kLatin1{"latin_1", "latin1", "latin", "l1", "iso_8859_1", "iso8859_1", "8859",
                                               "cp819", "csisolatin1", "ibm819", "iso_8859_1_1987", "iso_ir_100"};
    const std::string name = codecName(charset);
    if (kUtf8.count(name)) {
        if (!validUtf8(bytes)) return false;
        out = text::decode(bytes);
        return true;
    }
    const bool latin1 = kLatin1.count(name) > 0;
    const bool asciiIncompatible = name.rfind("utf_16", 0) == 0 || name.rfind("utf16", 0) == 0 ||
                                   name.rfind("utf_32", 0) == 0 || name.rfind("utf32", 0) == 0 ||
                                   name.rfind("utf_7", 0) == 0 || name == "utf7" || name == "u16" || name == "u32";
    if (asciiIncompatible || (!latin1 && !isAscii(bytes))) return false;
    out.clear();
    for (char c : bytes) out += static_cast<char32_t>(static_cast<unsigned char>(c));
    return true;
}

bool isHex(char32_t c) { return c < 0x80 && std::isxdigit(static_cast<int>(c)); }

// get_fws: a WSP character, then every whitespace character after it (str.lstrip).
size_t fwsLength(const Cps& value) {
    size_t n = 0;
    while (n < value.size() && text::isSpace(value[n])) ++n;
    return n;
}

// _wsp_splitter(value, 1)[0]: the text up to the first run of WSP.
size_t wordLength(const Cps& value) {
    size_t n = 0;
    while (n < value.size() && !isWsp(value[n])) ++n;
    return n;
}

// rfc2047_matcher.search(tok)
bool looksEncoded(const Cps& tok) {
    for (size_t p = tok.find(U"=?"); p != Cps::npos; p = tok.find(U"=?", p + 1)) {
        const size_t q = tok.find(U'?', p + 2);
        if (q == Cps::npos || q + 2 >= tok.size()) continue;
        const char32_t cte = tok[q + 1];
        if ((cte == U'q' || cte == U'Q' || cte == U'b' || cte == U'B') && tok[q + 2] == U'?' &&
            tok.find(U"?=", q + 3) != Cps::npos) {
            return true;
        }
    }
    return false;
}

// get_fws / _wsp_splitter applied to an encoded word's decoded text.
std::vector<TokPtr> wordsOf(const Cps& text) {
    std::vector<TokPtr> out;
    for (Cps rest = text; !rest.empty();) {
        const size_t n = isWsp(rest[0]) ? fwsLength(rest) : wordLength(rest);
        out.push_back(term(rest.substr(0, n), isWsp(rest[0]) ? "fws" : "utext"));
        rest = rest.substr(n);
    }
    return out;
}

enum class EwParse { NotAWord, Invalid, Word };

// The split get_encoded_word makes before decoding: `tok` is charset?cte?payload.
EwParse encodedWordShape(const Cps& value, Cps& tok, Cps& remainder) {
    const Cps body = value.substr(2);
    const size_t end = body.find(U"?=");
    if (end == Cps::npos) return EwParse::NotAWord;
    tok = body.substr(0, end);
    remainder = body.substr(end + 2);
    if (remainder.size() > 1 && isHex(remainder[0]) && isHex(remainder[1]) &&
        std::count(tok.begin(), tok.end(), U'?') < 2) {
        const size_t next = remainder.find(U"?=");
        tok += U"?=" + remainder.substr(0, next);
        remainder = next == Cps::npos ? Cps() : remainder.substr(next + 2);
    }
    if (std::count(tok.begin(), tok.end(), U'?') != 2) return EwParse::Invalid;
    const size_t q1 = tok.find(U'?'), q2 = tok.find(U'?', q1 + 1);
    const Cps cte = tok.substr(q1 + 1, q2 - q1 - 1);
    const Cps payload = tok.substr(q2 + 1);
    if (cte != U"q" && cte != U"Q" && cte != U"b" && cte != U"B") return EwParse::Invalid;
    if (std::any_of(payload.begin(), payload.end(), [](char32_t c) { return c >= 0x80; })) return EwParse::Invalid;
    return EwParse::Word;
}

// get_encoded_word(value, "utext"): `word` and `rest` when it parses.
EwParse getEncodedWord(const Cps& value, TokPtr& word, Cps& rest) {
    Cps tok, remainder;
    const EwParse shape = encodedWordShape(value, tok, remainder);
    if (shape != EwParse::Word) return shape;
    const size_t q1 = tok.find(U'?'), q2 = tok.find(U'?', q1 + 1);
    Cps charset = tok.substr(0, q1);
    charset = charset.substr(0, charset.find(U'*'));
    const Cps cte = tok.substr(q1 + 1, q2 - q1 - 1);
    const Cps payload = tok.substr(q2 + 1);
    const std::string ascii = text::encode(payload);
    const std::string bytes = (cte == U"q" || cte == U"Q") ? decodeQ(ascii) : decodeB(ascii);
    Cps decoded;
    if (!decodeCharset(bytes, text::encode(charset), decoded)) return EwParse::Invalid;
    word = list(wordsOf(decoded), "encoded-word");
    rest = remainder;
    return EwParse::Word;
}

// get_unstructured(value): text and whitespace, with RFC 2047 words read as Python reads them.
std::vector<TokPtr> parseUnstructured(Cps value) {
    std::vector<TokPtr> out;
    while (!value.empty()) {
        if (isWsp(value[0])) {
            const size_t n = fwsLength(value);
            out.push_back(term(value.substr(0, n), "fws"));
            value = value.substr(n);
            continue;
        }
        bool validEw = true;
        if (value.compare(0, 2, U"=?") == 0) {
            TokPtr word;
            Cps rest;
            const EwParse parsed = getEncodedWord(value, word, rest);
            if (parsed == EwParse::Invalid) validEw = false;
            if (parsed == EwParse::Word) {
                const bool haveWs = out.empty() || out.back()->type == "fws";
                if (haveWs && out.size() > 1 && out[out.size() - 2]->type == "encoded-word") {
                    out.back() = term(Cps(), "fws");
                }
                out.push_back(word);
                value = rest;
                continue;
            }
        }
        size_t n = wordLength(value);
        if (validEw && looksEncoded(value.substr(0, n))) n = value.find(U"=?");
        if (n == 0) throw std::logic_error("header text did not advance at an encoded word");
        out.push_back(term(value.substr(0, n), "utext"));
        value = value.substr(n);
    }
    return out;
}

// str(get_unstructured(s))
Cps decodeUnstructured(const Cps& s) { return str(*list(parseUnstructured(s), "unstructured")); }

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
        // A stale index slices as Python does: past the end is the empty string.
        const size_t at = std::min(*lastEw, lines.back().size());
        toEncode = decodeUnstructured(lines.back().substr(at) + toEncode);
        lines.back() = lines.back().substr(0, at);
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

// `n` more code points still fit on `line`. A line already past the limit has no room, as in Python.
bool fits(size_t n, const Cps& line) {
    return static_cast<long>(n) <= static_cast<long>(kMaxLineLength) - static_cast<long>(line.size());
}

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
                        if (!fits(encoded.size(), lines.back())) {
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
        if (fits(tstr.size(), lines.back())) {
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

// get_unstructured(value)
TokPtr unstructuredTree(const std::string& value) { return list(parseUnstructured(text::decode(value)), "unstructured"); }

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
    for (size_t i = 0; i < data.size(); i += per) out += mime::base64(data.substr(i, per)) + "\n";
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
    const size_t sniffBase64 = mime::base64(sniff).size() + 1;
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

namespace mime {

std::string text(const OutgoingMessage& message, const std::string& boundary) {
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

std::string raw(const OutgoingMessage& message) {
    std::string encoded = base64(text(message, newBoundary()));
    for (char& c : encoded) {
        if (c == '+') c = '-';
        if (c == '/') c = '_';
    }
    while (!encoded.empty() && encoded.back() == '=') encoded.pop_back();
    return encoded;
}

bool startsWithEncodedWord(const std::string& value) {
    const Cps cps = text::decode(value);
    Cps tok, remainder;
    return cps.size() > 1 && cps[0] == U'=' && cps[1] == U'?' && encodedWordShape(cps, tok, remainder) == EwParse::Word;
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

}  // namespace mime

}  // namespace gaia::mail
