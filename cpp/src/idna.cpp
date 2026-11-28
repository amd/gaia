// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Ported from idna/core.py (encode, alabel, ulabel, check_label, check_bidi,
// valid_contextj, valid_contexto, uts46_remap), Python's punycode codec and
// unicodedata.normalize("NFC"). Each error carries idna's own message.

#include "gaia/idna.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "mail_text.h"

namespace gaia::idna {

namespace {

// Strings are offsets into kPool rather than pointers: a table of pointers costs a
// relocation per row in every object file and shared library.
struct IdnaMap {
    char32_t start;
    char status;
    uint16_t offset;
    uint8_t length;
};
struct IdnaRange {
    char32_t low, high;
};
struct IdnaBidi {
    char32_t low, high;
    uint8_t cls;  // index into kBidiClasses
};
struct IdnaCcc {
    char32_t low, high;
    int ccc;
};
struct IdnaDecomposition {
    char32_t cp;
    uint16_t offset;
    uint8_t length;
};
struct IdnaComposition {
    char32_t first, second, composed;
};

#include "idna_tables.inc"

constexpr size_t kMaxInput = 1024;
constexpr size_t kMaxDomain = 253;

template <size_t N>
bool among(const IdnaRange (&ranges)[N], char32_t cp) {
    const auto it = std::upper_bound(std::begin(ranges), std::end(ranges), cp,
                                     [](char32_t c, const IdnaRange& r) { return c < r.low; });
    return it != std::begin(ranges) && cp <= std::prev(it)->high;
}

std::string bidi(char32_t cp) {
    const auto it = std::upper_bound(std::begin(kBidi), std::end(kBidi), cp,
                                     [](char32_t c, const IdnaBidi& r) { return c < r.low; });
    if (it == std::begin(kBidi) || cp > std::prev(it)->high) return "";
    return kBidiClasses[std::prev(it)->cls];
}

int combining(char32_t cp) {
    const auto it = std::upper_bound(std::begin(kCombining), std::end(kCombining), cp,
                                     [](char32_t c, const IdnaCcc& r) { return c < r.low; });
    if (it == std::begin(kCombining) || cp > std::prev(it)->high) return 0;
    return std::prev(it)->ccc;
}

std::string unot(char32_t cp) {
    char buf[16];
    std::snprintf(buf, sizeof buf, "U+%04X", static_cast<unsigned>(cp));
    return buf;
}

std::string repr(const std::u32string& s) { return mail::text::pyRepr(mail::text::encode(s)); }

std::string pos(size_t p) { return std::to_string(p); }

// ---- NFC ----

constexpr char32_t kSBase = 0xAC00, kLBase = 0x1100, kVBase = 0x1161, kTBase = 0x11A7;
constexpr char32_t kLCount = 19, kVCount = 21, kTCount = 28, kNCount = kVCount * kTCount,
                   kSCount = kLCount * kNCount;

void decompose(char32_t cp, std::u32string& out) {
    if (cp >= kSBase && cp < kSBase + kSCount) {
        const char32_t s = cp - kSBase;
        out += kLBase + s / kNCount;
        out += kVBase + (s % kNCount) / kTCount;
        if (s % kTCount) out += kTBase + s % kTCount;
        return;
    }
    const auto it = std::lower_bound(std::begin(kDecompositions), std::end(kDecompositions), cp,
                                     [](const IdnaDecomposition& d, char32_t c) { return d.cp < c; });
    if (it != std::end(kDecompositions) && it->cp == cp) {
        out.append(kPool + it->offset, it->length);
    } else {
        out += cp;
    }
}

char32_t compose(char32_t a, char32_t b) {
    if (a >= kLBase && a < kLBase + kLCount && b >= kVBase && b < kVBase + kVCount) {
        return kSBase + ((a - kLBase) * kVCount + (b - kVBase)) * kTCount;
    }
    if (a >= kSBase && a < kSBase + kSCount && (a - kSBase) % kTCount == 0 && b > kTBase &&
        b < kTBase + kTCount) {
        return a + (b - kTBase);
    }
    const auto it = std::lower_bound(std::begin(kCompositions), std::end(kCompositions), std::make_pair(a, b),
                                     [](const IdnaComposition& c, const std::pair<char32_t, char32_t>& k) {
                                         return c.first < k.first || (c.first == k.first && c.second < k.second);
                                     });
    if (it != std::end(kCompositions) && it->first == a && it->second == b) return it->composed;
    return 0;
}

}  // namespace

std::u32string nfc(const std::u32string& text) {
    std::u32string d;
    for (char32_t cp : text) decompose(cp, d);
    // Canonical order: a stable sort of each run of non-starters by class.
    for (size_t i = 0; i < d.size();) {
        if (combining(d[i]) == 0) {
            ++i;
            continue;
        }
        size_t j = i;
        while (j < d.size() && combining(d[j]) != 0) ++j;
        std::stable_sort(d.begin() + static_cast<std::ptrdiff_t>(i), d.begin() + static_cast<std::ptrdiff_t>(j),
                         [](char32_t a, char32_t b) { return combining(a) < combining(b); });
        i = j;
    }
    std::u32string out;
    size_t starter = std::u32string::npos;
    int last = -1;  // class of the last character kept after the starter
    for (char32_t cp : d) {
        const int ccc = combining(cp);
        if (starter != std::u32string::npos && (last < ccc || (last == -1 && ccc == 0))) {
            if (char32_t c = compose(out[starter], cp)) {
                out[starter] = c;
                continue;
            }
        }
        if (ccc == 0) {
            starter = out.size();
            last = -1;
        } else {
            last = ccc;
        }
        out += cp;
    }
    return out;
}

namespace {

// ---- punycode (Python's codec) ----

std::string punycodeEncode(const std::u32string& input) {
    constexpr uint32_t base = 36, tmin = 1, tmax = 26, skew = 38, damp = 700;
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
    for (char32_t c : input) {
        if (c < 0x80) out += static_cast<char>(c);
    }
    const uint32_t basic = static_cast<uint32_t>(out.size());
    uint32_t handled = basic;
    if (basic > 0) out += '-';
    uint32_t n = 0x80, delta = 0, bias = 72;
    while (handled < input.size()) {
        uint32_t m = 0x10FFFF;
        for (char32_t c : input) {
            if (c >= n && c < m) m = c;
        }
        delta += (m - n) * (handled + 1);
        n = m;
        for (char32_t c : input) {
            if (c < n) ++delta;
            if (c != n) continue;
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
        ++delta;
        ++n;
    }
    return out;
}

struct PunycodeError {};

// encodings/punycode.py punycode_decode, errors="strict".
std::u32string punycodeDecode(const std::string& text) {
    const size_t dash = text.rfind('-');
    std::u32string out;
    std::string extended;
    if (dash == std::string::npos) {
        extended = text;
    } else {
        for (size_t i = 0; i < dash; ++i) out += static_cast<unsigned char>(text[i]);
        extended = text.substr(dash + 1);
    }
    for (char& c : extended) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    int64_t ch = 0x80, position = -1, bias = 72;
    size_t extpos = 0;
    auto threshold = [](int64_t j, int64_t b) { return std::clamp<int64_t>(36 * (j + 1) - b, 1, 26); };
    auto adapt = [](int64_t delta, bool first, int64_t chars) {
        delta = first ? delta / 700 : delta / 2;
        delta += delta / chars;
        int64_t divisions = 0;
        while (delta > 455) {
            delta /= 35;
            divisions += 36;
        }
        return divisions + (36 * delta / (delta + 38));
    };
    constexpr int64_t kCap = int64_t(1) << 40;
    while (extpos < extended.size()) {
        const bool first = extpos == 0;
        int64_t result = 0, w = 1, j = 0;
        while (true) {
            if (extpos >= extended.size()) throw PunycodeError{};
            const char c = extended[extpos++];
            int64_t d;
            if (c >= 'A' && c <= 'Z') d = c - 'A';
            else if (c >= '0' && c <= '9') d = c - 22;
            else throw PunycodeError{};
            const int64_t t = threshold(j, bias);
            result += d * w;
            if (result > kCap) throw PunycodeError{};
            if (d < t) break;
            w *= 36 - t;
            if (w > kCap) throw PunycodeError{};
            ++j;
        }
        position += result + 1;
        const int64_t length = static_cast<int64_t>(out.size()) + 1;
        ch += position / length;
        if (ch > 0x10FFFF) throw PunycodeError{};
        position %= length;
        out.insert(out.begin() + position, static_cast<char32_t>(ch));
        bias = adapt(result, first, static_cast<int64_t>(out.size()));
    }
    return out;
}

// ---- idna/core.py ----

bool isAscii(const std::u32string& s) {
    return std::all_of(s.begin(), s.end(), [](char32_t c) { return c < 0x80; });
}

std::string asciiBytes(const std::u32string& s) {
    std::string out;
    for (char32_t c : s) out += static_cast<char>(c);
    return out;
}

void checkBidi(const std::u32string& label) {
    if (label.size() > kMaxInput) throw IdnaError("Label too long");
    bool rtlLabel = false;
    for (size_t i = 0; i < label.size(); ++i) {
        const std::string d = bidi(label[i]);
        if (d.empty()) {
            throw IdnaError("Unknown directionality in label " + repr(label) + " at position " + pos(i + 1));
        }
        if (d == "R" || d == "AL" || d == "AN") rtlLabel = true;
    }
    if (!rtlLabel) return;
    const std::string firstDir = bidi(label[0]);
    bool rtl;
    if (firstDir == "R" || firstDir == "AL") rtl = true;
    else if (firstDir == "L") rtl = false;
    else throw IdnaError("First codepoint in label " + repr(label) + " must be directionality L, R or AL");
    static const std::vector<std::string> rtlAllowed = {"R", "AL", "AN", "EN", "ES", "CS", "ET", "ON", "BN", "NSM"};
    static const std::vector<std::string> ltrAllowed = {"L", "EN", "ES", "CS", "ET", "ON", "BN", "NSM"};
    auto in = [](const std::vector<std::string>& set, const std::string& d) {
        return std::find(set.begin(), set.end(), d) != set.end();
    };
    bool validEnding = false;
    std::string numberType;
    for (size_t i = 0; i < label.size(); ++i) {
        const std::string d = bidi(label[i]);
        if (rtl) {
            if (!in(rtlAllowed, d)) {
                throw IdnaError("Invalid direction for codepoint at position " + pos(i + 1) +
                                " in a right-to-left label");
            }
            if (d == "R" || d == "AL" || d == "EN" || d == "AN") validEnding = true;
            else if (d != "NSM") validEnding = false;
            if (d == "AN" || d == "EN") {
                if (numberType.empty()) numberType = d;
                else if (numberType != d) throw IdnaError("Can not mix numeral types in a right-to-left label");
            }
        } else {
            if (!in(ltrAllowed, d)) {
                throw IdnaError("Invalid direction for codepoint at position " + pos(i + 1) +
                                " in a left-to-right label");
            }
            if (d == "L" || d == "EN") validEnding = true;
            else if (d != "NSM") validEnding = false;
        }
    }
    if (!validEnding) throw IdnaError("Label ends with illegal codepoint directionality");
}

struct UnknownCodepoint {};

int combiningClassNamed(char32_t cp) {
    const int v = combining(cp);
    if (v == 0 && among(kUnnamed, cp)) throw UnknownCodepoint{};
    return v;
}

const char* joiningType(char32_t cp) {
    if (among(kJoiningD, cp)) return "D";
    if (among(kJoiningL, cp)) return "L";
    if (among(kJoiningR, cp)) return "R";
    if (among(kJoiningT, cp)) return "T";
    return "";
}

bool validContextJ(const std::u32string& label, size_t at) {
    constexpr int kVirama = 9;
    const char32_t cp = label[at];
    if (cp == 0x200C) {
        if (at > 0 && combiningClassNamed(label[at - 1]) == kVirama) return true;
        bool ok = false;
        for (size_t i = at; i-- > 0;) {
            const std::string jt = joiningType(label[i]);
            if (jt == "T") continue;
            ok = jt == "L" || jt == "D";
            break;
        }
        if (!ok) return false;
        ok = false;
        for (size_t i = at + 1; i < label.size(); ++i) {
            const std::string jt = joiningType(label[i]);
            if (jt == "T") continue;
            ok = jt == "R" || jt == "D";
            break;
        }
        return ok;
    }
    if (cp == 0x200D) return at > 0 && combiningClassNamed(label[at - 1]) == kVirama;
    return false;
}

bool validContextO(const std::u32string& label, size_t at) {
    const char32_t cp = label[at];
    if (cp == 0x00B7) return at > 0 && at + 1 < label.size() && label[at - 1] == 0x6C && label[at + 1] == 0x6C;
    if (cp == 0x0375) return at + 1 < label.size() && label.size() > 1 && among(kScriptGreek, label[at + 1]);
    if (cp == 0x05F3 || cp == 0x05F4) return at > 0 && among(kScriptHebrew, label[at - 1]);
    if (cp == 0x30FB) {
        for (char32_t c : label) {
            if (c == 0x30FB) continue;
            if (among(kScriptHiragana, c) || among(kScriptKatakana, c) || among(kScriptHan, c)) return true;
        }
        return false;
    }
    if (cp >= 0x660 && cp <= 0x669) {
        return std::none_of(label.begin(), label.end(), [](char32_t c) { return c >= 0x6F0 && c <= 0x6F9; });
    }
    if (cp >= 0x6F0 && cp <= 0x6F9) {
        return std::none_of(label.begin(), label.end(), [](char32_t c) { return c >= 0x660 && c <= 0x669; });
    }
    return false;
}

void checkLabel(const std::u32string& label) {
    if (label.size() > kMaxInput) throw IdnaError("Label too long");
    if (label.empty()) throw IdnaError("Empty Label");
    if (label.size() > kMaxDomain + 1) throw IdnaError("Label too long");
    if (nfc(label) != label) throw IdnaError("Label must be in Normalization Form C");
    if (label.size() >= 4 && label[2] == U'-' && label[3] == U'-') {
        throw IdnaError("Label has disallowed hyphens in 3rd and 4th position");
    }
    if (label.front() == U'-' || label.back() == U'-') throw IdnaError("Label must not start or end with a hyphen");
    if (among(kMarks, label[0])) throw IdnaError("Label begins with an illegal combining character");
    for (size_t i = 0; i < label.size(); ++i) {
        const char32_t cp = label[i];
        if (among(kPvalid, cp)) continue;
        if (among(kContextJ, cp)) {
            bool ok;
            try {
                ok = validContextJ(label, i);
            } catch (const UnknownCodepoint&) {
                throw IdnaError("Unknown codepoint adjacent to joiner " + unot(cp) + " at position " + pos(i + 1) +
                                " in " + repr(label));
            }
            if (!ok) {
                throw IdnaError("Joiner " + unot(cp) + " not allowed at position " + pos(i + 1) + " in " +
                                repr(label));
            }
        } else if (among(kContextO, cp)) {
            if (!validContextO(label, i)) {
                throw IdnaError("Codepoint " + unot(cp) + " not allowed at position " + pos(i + 1) + " in " +
                                repr(label));
            }
        } else {
            throw IdnaError("Codepoint " + unot(cp) + " at position " + pos(i + 1) + " of " + repr(label) +
                            " not allowed");
        }
    }
    checkBidi(label);
}

// ulabel() of an ASCII label, for what it checks.
void checkAsciiLabel(std::string bytes) {
    if (bytes.size() > kMaxInput) throw IdnaError("Label too long");
    for (char& c : bytes) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    if (bytes.rfind("xn--", 0) != 0) {
        checkLabel(mail::text::decode(bytes));
        return;
    }
    const std::string rest = bytes.substr(4);
    if (rest.empty()) throw IdnaError("Malformed A-label, no Punycode eligible content found");
    if (rest.back() == '-') throw IdnaError("A-label must not end with a hyphen");
    std::u32string label;
    try {
        label = punycodeDecode(rest);
    } catch (const PunycodeError&) {
        throw IdnaError("Invalid A-label");
    }
    if (punycodeEncode(label) != rest) {
        throw IdnaError("A-label is not the canonical Punycode encoding of its U-label");
    }
    checkLabel(label);
}

std::string alabel(const std::u32string& label) {
    if (label.size() > kMaxInput) throw IdnaError("Label too long");
    if (isAscii(label)) {
        const std::string bytes = asciiBytes(label);
        checkAsciiLabel(bytes);
        if (bytes.size() > 63) throw IdnaError("Label too long");
        return bytes;
    }
    checkLabel(label);
    const std::string bytes = "xn--" + punycodeEncode(label);
    if (bytes.size() > 63) throw IdnaError("Label too long");
    return bytes;
}

std::u32string uts46Remap(const std::u32string& domain) {
    if (domain.size() > kMaxInput) throw IdnaError("Domain too long");
    std::u32string out;
    for (size_t i = 0; i < domain.size(); ++i) {
        const char32_t cp = domain[i];
        const auto it = std::upper_bound(std::begin(kUts46), std::end(kUts46), cp,
                                         [](char32_t c, const IdnaMap& m) { return c < m.start; });
        const IdnaMap& row = *std::prev(it);
        switch (row.status) {
            case 'V':
            case 'D':
                out += cp;
                break;
            case 'M':
                out.append(kPool + row.offset, row.length);
                break;
            case 'I':
                break;
            default:
                throw IdnaError("Codepoint " + unot(cp) + " not allowed at position " + pos(i + 1) + " in " +
                                repr(domain));
        }
    }
    return nfc(out);
}

}  // namespace

std::string encodeUts46(const std::u32string& input) {
    if (input.size() > kMaxInput) throw IdnaError("Domain too long");
    std::u32string s = isAscii(input) ? mail::text::decode(mail::text::lower(mail::text::encode(input)))
                                      : uts46Remap(input);
    if (s.size() > kMaxDomain + 1) throw IdnaError("Domain too long");
    std::vector<std::u32string> labels(1);
    for (char32_t c : s) {
        if (c == U'.' || c == 0x3002 || c == 0xFF0E || c == 0xFF61) labels.emplace_back();
        else labels.back().push_back(c);
    }
    if (labels.size() == 1 && labels[0].empty()) throw IdnaError("Empty domain");
    bool trailingDot = false;
    if (labels.back().empty()) {
        labels.pop_back();
        trailingDot = true;
    }
    std::string out;
    for (size_t i = 0; i < labels.size(); ++i) {
        const std::string a = alabel(labels[i]);
        if (a.empty()) throw IdnaError("Empty label");
        out += (i ? "." : "") + a;
    }
    if (trailingDot) out += ".";
    if (out.size() > kMaxDomain + (trailingDot ? 1 : 0)) throw IdnaError("Domain too long");
    return out;
}

}  // namespace gaia::idna
