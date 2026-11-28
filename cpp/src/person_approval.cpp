// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Ported from src/gaia/agents/base/person_approval.py.

#include "gaia/person_approval.h"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <mutex>
#include <stdexcept>
#include <utility>
#include <vector>


namespace gaia::approval {

namespace {

struct CodeRange {
    char32_t low, high;
};

// The sets src/gaia/agents/base/person_approval.py shows as a code;
// tests/unit/test_gaiacpp_hidden_text_parity.py keeps them equal.
// Every control, format, line/paragraph separator and default-ignorable
// character except tab, newline, ZWNJ, ZWJ, LRM and RLM, and the variation selectors.
constexpr CodeRange kHidden[] = {
    {0x0000, 0x0008}, {0x000B, 0x001F}, {0x007F, 0x009F}, {0x00AD, 0x00AD},
    {0x034F, 0x034F}, {0x0600, 0x0605}, {0x061C, 0x061C}, {0x06DD, 0x06DD},
    {0x070F, 0x070F}, {0x0890, 0x0891}, {0x08E2, 0x08E2}, {0x115F, 0x1160},
    {0x17B4, 0x17B5}, {0x180B, 0x180F}, {0x200B, 0x200B}, {0x2028, 0x202E},
    {0x2060, 0x206F}, {0x2800, 0x2800}, {0x3164, 0x3164}, {0xFEFF, 0xFEFF},
    {0xFFA0, 0xFFA0}, {0xFFF0, 0xFFFB}, {0x110BD, 0x110BD}, {0x110CD, 0x110CD},
    {0x13430, 0x1343F}, {0x1BCA0, 0x1BCA3}, {0x1D173, 0x1D17A}, {0xE0000, 0xE00FF},
    {0xE01F0, 0xE0FFF},
};
// Extended_Pictographic: an emoji that FE0E/FE0F may follow.
constexpr CodeRange kPictographic[] = {
    {0x00A9, 0x00A9}, {0x00AE, 0x00AE}, {0x203C, 0x203C}, {0x2049, 0x2049},
    {0x2122, 0x2122}, {0x2139, 0x2139}, {0x2194, 0x2199}, {0x21A9, 0x21AA},
    {0x231A, 0x231B}, {0x2328, 0x2328}, {0x23CF, 0x23CF}, {0x23E9, 0x23F3},
    {0x23F8, 0x23FA}, {0x24C2, 0x24C2}, {0x25AA, 0x25AB}, {0x25B6, 0x25B6},
    {0x25C0, 0x25C0}, {0x25FB, 0x25FE}, {0x2600, 0x2604}, {0x260E, 0x260E},
    {0x2611, 0x2611}, {0x2614, 0x2615}, {0x2618, 0x2618}, {0x261D, 0x261D},
    {0x2620, 0x2620}, {0x2622, 0x2623}, {0x2626, 0x2626}, {0x262A, 0x262A},
    {0x262E, 0x262F}, {0x2638, 0x263A}, {0x2640, 0x2640}, {0x2642, 0x2642},
    {0x2648, 0x2653}, {0x265F, 0x2660}, {0x2663, 0x2663}, {0x2665, 0x2666},
    {0x2668, 0x2668}, {0x267B, 0x267B}, {0x267E, 0x267F}, {0x2692, 0x2697},
    {0x2699, 0x2699}, {0x269B, 0x269C}, {0x26A0, 0x26A1}, {0x26A7, 0x26A7},
    {0x26AA, 0x26AB}, {0x26B0, 0x26B1}, {0x26BD, 0x26BE}, {0x26C4, 0x26C5},
    {0x26C8, 0x26C8}, {0x26CE, 0x26CF}, {0x26D1, 0x26D1}, {0x26D3, 0x26D4},
    {0x26E9, 0x26EA}, {0x26F0, 0x26F5}, {0x26F7, 0x26FA}, {0x26FD, 0x26FD},
    {0x2702, 0x2702}, {0x2705, 0x2705}, {0x2708, 0x270D}, {0x270F, 0x270F},
    {0x2712, 0x2712}, {0x2714, 0x2714}, {0x2716, 0x2716}, {0x271D, 0x271D},
    {0x2721, 0x2721}, {0x2728, 0x2728}, {0x2733, 0x2734}, {0x2744, 0x2744},
    {0x2747, 0x2747}, {0x274C, 0x274C}, {0x274E, 0x274E}, {0x2753, 0x2755},
    {0x2757, 0x2757}, {0x2763, 0x2764}, {0x2795, 0x2797}, {0x27A1, 0x27A1},
    {0x27B0, 0x27B0}, {0x27BF, 0x27BF}, {0x2934, 0x2935}, {0x2B05, 0x2B07},
    {0x2B1B, 0x2B1C}, {0x2B50, 0x2B50}, {0x2B55, 0x2B55}, {0x3030, 0x3030},
    {0x303D, 0x303D}, {0x3297, 0x3297}, {0x3299, 0x3299}, {0x1F004, 0x1F004},
    {0x1F02C, 0x1F02F}, {0x1F094, 0x1F09F}, {0x1F0AF, 0x1F0B0}, {0x1F0C0, 0x1F0C0},
    {0x1F0CF, 0x1F0D0}, {0x1F0F6, 0x1F0FF}, {0x1F170, 0x1F171}, {0x1F17E, 0x1F17F},
    {0x1F18E, 0x1F18E}, {0x1F191, 0x1F19A}, {0x1F1AE, 0x1F1E5}, {0x1F201, 0x1F20F},
    {0x1F21A, 0x1F21A}, {0x1F22F, 0x1F22F}, {0x1F232, 0x1F23A}, {0x1F23C, 0x1F23F},
    {0x1F249, 0x1F25F}, {0x1F266, 0x1F321}, {0x1F324, 0x1F393}, {0x1F396, 0x1F397},
    {0x1F399, 0x1F39B}, {0x1F39E, 0x1F3F0}, {0x1F3F3, 0x1F3F5}, {0x1F3F7, 0x1F3FA},
    {0x1F400, 0x1F4FD}, {0x1F4FF, 0x1F53D}, {0x1F549, 0x1F54E}, {0x1F550, 0x1F567},
    {0x1F56F, 0x1F570}, {0x1F573, 0x1F57A}, {0x1F587, 0x1F587}, {0x1F58A, 0x1F58D},
    {0x1F590, 0x1F590}, {0x1F595, 0x1F596}, {0x1F5A4, 0x1F5A5}, {0x1F5A8, 0x1F5A8},
    {0x1F5B1, 0x1F5B2}, {0x1F5BC, 0x1F5BC}, {0x1F5C2, 0x1F5C4}, {0x1F5D1, 0x1F5D3},
    {0x1F5DC, 0x1F5DE}, {0x1F5E1, 0x1F5E1}, {0x1F5E3, 0x1F5E3}, {0x1F5E8, 0x1F5E8},
    {0x1F5EF, 0x1F5EF}, {0x1F5F3, 0x1F5F3}, {0x1F5FA, 0x1F64F}, {0x1F680, 0x1F6C5},
    {0x1F6CB, 0x1F6D2}, {0x1F6D5, 0x1F6E5}, {0x1F6E9, 0x1F6E9}, {0x1F6EB, 0x1F6F0},
    {0x1F6F3, 0x1F6FF}, {0x1F7DA, 0x1F7FF}, {0x1F80C, 0x1F80F}, {0x1F848, 0x1F84F},
    {0x1F85A, 0x1F85F}, {0x1F888, 0x1F88F}, {0x1F8AE, 0x1F8AF}, {0x1F8BC, 0x1F8BF},
    {0x1F8C2, 0x1F8CF}, {0x1F8D9, 0x1F8FF}, {0x1F90C, 0x1F93A}, {0x1F93C, 0x1F945},
    {0x1F947, 0x1F9FF}, {0x1FA58, 0x1FA5F}, {0x1FA6E, 0x1FAFF}, {0x1FC00, 0x1FFFD},
};
// Kept in prose, but coded in a name (Shown::Name): there they only make one name read as another.
// Tab and line feed, then ZWNJ, ZWJ, LRM and RLM.
constexpr CodeRange kKeptInProse[] = {
    {0x0009, 0x000A}, {0x200C, 0x200F},
};
constexpr CodeRange kVariationSelectors[] = {
    {0xFE00, 0xFE0F}, {0xE0100, 0xE01EF},
};
// FE0F followed by U+20E3 makes a keycap of one of these.
constexpr char kKeycapBases[] = "0123456789#*";
// CJK ideographs, which an ideographic variation selector (E0100-E01EF) may follow.
constexpr CodeRange kIdeographs[] = {
    {0x3400, 0x4DBF}, {0x4E00, 0x9FFF}, {0xF900, 0xFAFF}, {0x20000, 0x2A6DF},
    {0x2A700, 0x2B73F}, {0x2B740, 0x2B81F}, {0x2B820, 0x2CEAF}, {0x2CEB0, 0x2EBEF},
    {0x2EBF0, 0x2EE5F}, {0x2F800, 0x2FA1F}, {0x30000, 0x3134F}, {0x31350, 0x323AF},
};

template <size_t N>
bool among(const CodeRange (&ranges)[N], char32_t cp) {
    for (const auto& r : ranges) {
        if (cp >= r.low && cp <= r.high) return true;
    }
    return false;
}

// One code point decoded from s at i, with its byte length; length 0 when s[i] starts no valid UTF-8.
std::pair<char32_t, size_t> decodeAt(const std::string& s, size_t i) {
    const auto c = static_cast<unsigned char>(s[i]);
    if (c < 0x80) return {c, 1};
    const size_t len = c >= 0xF5 ? 0 : c >= 0xF0 ? 4 : c >= 0xE0 ? 3 : c >= 0xC2 ? 2 : 0;
    if (len == 0 || i + len > s.size()) return {0, 0};
    char32_t cp = c & (0x7F >> len);
    for (size_t k = 1; k < len; ++k) {
        const auto b = static_cast<unsigned char>(s[i + k]);
        if ((b & 0xC0) != 0x80) return {0, 0};
        cp = (cp << 6) | (b & 0x3F);
    }
    const char32_t least = len == 2 ? 0x80 : len == 3 ? 0x800 : 0x10000;
    if (cp < least || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) return {0, 0};
    return {cp, len};
}

// Whether the variation selector at cps[at] does what Unicode defines, so it stays as written.
bool selectorFits(const std::vector<char32_t>& cps, size_t at) {
    if (at == 0) return false;
    const char32_t cp = cps[at], previous = cps[at - 1];
    if (cp == 0xFE0E || cp == 0xFE0F) {
        if (among(kPictographic, previous)) return true;
        const bool keycapBase = previous != 0 && previous < 0x80 &&
                                std::strchr(kKeycapBases, static_cast<char>(previous)) != nullptr;
        return cp == 0xFE0F && keycapBase && at + 1 < cps.size() && cps[at + 1] == 0x20E3;
    }
    if (cp >= 0xE0100 && cp <= 0xE01EF) return among(kIdeographs, previous);
    return false;
}


}  // namespace

const char* const kHiddenNote =
    "This text holds characters that don't show. Those that hide or reorder text "
    "appear as [U+XXXX]; ideograph variant marks are kept as written.";

std::string visible(const std::string& raw, Shown how) {
    const bool strict = how == Shown::Name || how == Shown::Command;
    std::string s;
    s.reserve(raw.size());
    for (size_t i = 0; i < raw.size(); ++i) {
        if (how != Shown::Name && raw[i] == '\r' && i + 1 < raw.size() && raw[i + 1] == '\n') continue;
        s += raw[i];
    }
    std::vector<char32_t> cps;
    std::vector<size_t> starts, lengths;
    for (size_t i = 0; i < s.size();) {
        const auto [cp, len] = decodeAt(s, i);
        cps.push_back(len ? cp : 0xFFFD);
        starts.push_back(i);
        lengths.push_back(len ? len : 1);
        i += len ? len : 1;
    }
    std::string out;
    out.reserve(s.size());
    for (size_t k = 0; k < cps.size(); ++k) {
        const char32_t cp = cps[k];
        const bool decoded = !(cp == 0xFFFD && lengths[k] == 1);
        const bool selector = among(kVariationSelectors, cp);
        const bool layout = cp == '\t' || cp == '\n';
        const bool code =
            decoded && ((cp == '\n' && how == Shown::Line) || among(kHidden, cp) ||
                        (strict && among(kKeptInProse, cp) && !(how == Shown::Command && layout)) ||
                        (selector && (strict || !selectorFits(cps, k))));
        if (code) {
            char buf[16];
            std::snprintf(buf, sizeof buf, "[U+%04X]", static_cast<unsigned>(cp));
            out += buf;
        } else {
            out.append(s, starts[k], lengths[k]);
        }
    }
    return out;
}

bool hasUnseen(const std::string& text) {
    std::string unfolded;
    for (size_t i = 0; i < text.size(); ++i) {
        if (text[i] == '\r' && i + 1 < text.size() && text[i + 1] == '\n') continue;
        unfolded += text[i];
    }
    if (visible(text) != unfolded) return true;
    for (size_t i = 0; i < text.size();) {
        const auto [cp, len] = decodeAt(text, i);
        if (cp >= 0xE0100 && cp <= 0xE01EF) return true;
        i += len ? len : 1;
    }
    return false;
}

namespace {

std::mutex& summariesMutex() {
    static std::mutex mu;
    return mu;
}

std::map<std::string, Summary>& summaries() {
    static std::map<std::string, Summary> registered;
    return registered;
}

}  // namespace

void setSummary(const std::string& tool, Summary summary) {
    std::lock_guard<std::mutex> lock(summariesMutex());
    summaries()[tool] = std::move(summary);
}

std::string summaryOf(const std::string& tool, const nlohmann::json& shownArgs) {
    Summary summary;
    {
        std::lock_guard<std::mutex> lock(summariesMutex());
        const auto it = summaries().find(tool);
        if (it == summaries().end()) return "";
        summary = it->second;
    }
    return summary(shownArgs);
}

}  // namespace gaia::approval
