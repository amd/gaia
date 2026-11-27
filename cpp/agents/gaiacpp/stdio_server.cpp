// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "stdio_server.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <random>
#include <set>
#include <stdexcept>
#include <utility>
#include <vector>

#include <gaia/console.h>
#include <gaia/http_client.h>
#include <gaia/lemonade_client.h>
#include <gaia/memory_store.h>
#include <gaia/shell_guard.h>
#include <gaia/workspace.h>

#include "gaia_agent.h"
#include "wire_util.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

using wire::dumps;
using wire::randomHex;

const char* const kQueryKey = "gaia_query";
const char* const kControlKey = "gaia_control";
const std::string kClearQuery("\0gaia:clear_conversation\0", 25);
const std::string kMemoryDumpQuery("\0gaia:memory_dump\0", 18);
const std::string kWarmUpQuery("\0gaia:warm_up\0", 14);
const char* const kLlmFailure = "Unable to complete task due to LLM error: ";
// The TUI answers within its own 10 minutes; this only fires when nobody can.
constexpr auto kOrphanedConfirmTimeout = std::chrono::minutes(15);
constexpr int kStatusProbeSeconds = 3;
constexpr auto kTokenBatch = std::chrono::milliseconds(30);
constexpr size_t kMaxEventData = 20000;

std::string lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

std::string clip(const std::string& s, size_t n) {
    if (s.size() <= n) return s;
    size_t cut = n;
    while (cut > 0 && (static_cast<unsigned char>(s[cut]) & 0xC0) == 0x80) --cut;
    return s.substr(0, cut) + "…";
}

std::string argOf(const json& args, const char* key) {
    if (args.is_object() && args.contains(key) && args[key].is_string()) return args[key].get<std::string>();
    return "";
}

// One plain line on what a call is doing, for the TUI's activity log.
std::string narrate(const std::string& tool, const json& args) {
    if (tool == "file_read") return "Reading " + argOf(args, "path");
    if (tool == "file_write") return "Writing " + argOf(args, "path");
    if (tool == "file_edit") return "Editing " + argOf(args, "path");
    if (tool == "file_search") return "Searching files";
    if (tool == "bash_execute") return "Running " + clip(argOf(args, "command"), 120);
    if (tool == "web_fetch") return "Reading " + argOf(args, "url");
    if (tool == "remember") return "Saving to memory";
    if (tool == "recall") return "Checking memory";
    if (tool == "load_skill") return "Loading the " + argOf(args, "name") + " skill";
    if (tool == "query_documents") return "Searching the documents";
    if (tool == "index_document") return "Indexing " + argOf(args, "path");
    if (tool == "search_code") return "Searching the code";
    if (tool == "delegate_task") {
        const size_t n = args.is_object() && args.contains("tasks") && args["tasks"].is_array() ? args["tasks"].size() : 0;
        return n > 0 ? "Handing " + std::to_string(n) + (n == 1 ? " task" : " tasks") + " to helpers"
                     : "Handing work to helpers";
    }
    return "";
}

std::string counted(long long n, const char* one, const char* many) {
    return std::to_string(n) + " " + (n == 1 ? one : many);
}

std::string leafOf(const std::string& path) {
    const size_t cut = path.find_last_of("/\\");
    return cut == std::string::npos ? path : path.substr(cut + 1);
}

// What a finished call did, when its result is counts and flags and not text.
std::string outcomeOf(const std::string& tool, const json& r) {
    if (r.contains("error")) return "";
    const std::string name = r.contains("path") && r["path"].is_string() ? leafOf(r["path"].get<std::string>()) : "the file";
    const auto number = [&r](const char* key) { return r.contains(key) && r[key].is_number_integer(); };
    if (tool == "file_edit" && number("replacements")) {
        return "Changed " + counted(r["replacements"].get<long long>(), "place", "places") + " in " + name;
    }
    if (tool == "file_write" && number("bytes_written")) {
        return "Saved " + name + " (" + counted(r["bytes_written"].get<long long>(), "byte", "bytes") + ")";
    }
    if (tool == "file_read" && number("lines")) {
        return "Read " + counted(r["lines"].get<long long>(), "line", "lines") + " from " + name;
    }
    if (tool == "file_search" && number("total")) {
        return "Found " + counted(r["total"].get<long long>(), "match", "matches");
    }
    if (tool == "bash_execute" && number("exit_code")) {
        if (r.value("timed_out", false)) return "Timed out";
        std::string said;
        for (const char* key : {"stdout", "stderr"}) {
            if (said.empty() && r.contains(key) && r[key].is_string()) said = r[key].get<std::string>();
        }
        const long long code = r["exit_code"].get<long long>();
        if (code == 0) return said.empty() ? "Finished with no output" : clip(said, 200);
        return "Failed (exit " + std::to_string(code) + ")" + (said.empty() ? "" : ": " + clip(said, 160));
    }
    if (tool == "web_fetch" && number("total_chars")) {
        return "Read " + counted(r["total_chars"].get<long long>(), "character", "characters") + " from the page";
    }
    return "";
}

// A result with no line of text, as "key: value" pairs instead of its JSON.
std::string plainPairs(const json& result) {
    std::string out;
    for (const auto& [key, value] : result.items()) {
        if (!value.is_primitive() || value.is_null()) continue;
        std::string word = key;
        std::replace(word.begin(), word.end(), '_', ' ');
        if (!out.empty()) out += ", ";
        out += word + ": " + (value.is_string() ? value.get<std::string>() : dumps(value));
    }
    return out;
}

std::string previewOf(const std::string& tool, const json& result) {
    if (result.is_string()) return clip(result.get<std::string>(), 200);
    if (result.is_object()) {
        const std::string outcome = outcomeOf(tool, result);
        if (!outcome.empty()) return outcome;
        // A delegation's "error" also carries advice meant for the model; its summary is the line for a person.
        if (result.contains("helpers") && result["helpers"].is_array() && result.contains("summary") &&
            result["summary"].is_string()) {
            return clip(result["summary"].get<std::string>(), 200);
        }
        for (const char* key : {"error", "message", "output", "stdout", "content", "summary"}) {
            if (result.contains(key) && result[key].is_string() && !result[key].get<std::string>().empty()) {
                return clip(result[key].get<std::string>(), 200);
            }
        }
        const std::string pairs = plainPairs(result);
        return pairs.empty() ? "Done" : clip(pairs, 200);
    }
    return clip(dumps(result), 200);
}

// Cut to about n bytes and count what was left out: a bare "…" reads as the whole value.
std::string clipCounted(const std::string& s, size_t n) {
    if (s.size() <= n) return s;
    size_t cut = n;
    while (cut > 0 && (static_cast<unsigned char>(s[cut]) & 0xC0) == 0x80) --cut;
    long long hidden = 0;
    for (size_t i = cut; i < s.size(); ++i) hidden += (static_cast<unsigned char>(s[i]) & 0xC0) != 0x80;
    return s.substr(0, cut) + "… [+" + counted(hidden, "more character", "more characters") + " not shown]";
}

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

// How visible() writes a field. Prose keeps its line breaks; Line codes them.
// Name (a path, URL, id, program or "always allow" label) codes every character
// kept in prose and every variation selector too, CR included, as Python's
// visible_strict does; Command does the same to a command's words but keeps
// its tabs and line breaks.
enum class Shown { Prose, Line, Name, Command };

// Characters that hide or reorder text written as [U+XXXX], as the Python agent's
// approval cards show them, so the card reads as the call. Bytes that aren't
// UTF-8 pass through unchanged.
std::string visible(const std::string& raw, Shown how = Shown::Prose) {
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

// Up to 12 lines of text a call would write, indented, with the rest counted.
std::string shownBlock(const std::string& text) {
    constexpr size_t kLines = 12;
    std::string out;
    size_t lines = 0, start = 0;
    while (start < text.size()) {
        const size_t end = (std::min)(text.find('\n', start), text.size());
        if (++lines <= kLines) {
            std::string line = text.substr(start, end - start);
            if (!line.empty() && line.back() == '\r') line.pop_back();
            std::string spaced;
            for (const char ch : line) spaced += ch == '\t' ? std::string("    ") : std::string(1, ch);
            out += "    " + clipCounted(visible(spaced), 200) + "\n";
        }
        start = end + 1;
    }
    if (lines > kLines) {
        out += "    … [+" + counted(static_cast<long long>(lines - kLines), "more line", "more lines") + " not shown]\n";
    }
    if (!out.empty()) out.pop_back();
    return out.empty() ? "    (nothing)" : out;
}

// The terminal UI's own name for the folder prompt, as the Python agent raises it.
constexpr const char* kPathAccessPrompt = "allow_path_access";

// The body of an approval prompt: what the call will do, with no tool name or JSON.
// The command keeps its line breaks; a path, URL or id is one line, so its
// own text can't add a line that reads as part of the card, and a joiner or
// mark in a name can't make it read as another.
std::string promptSummary(const std::string& tool, const json& args) {
    const auto one = [&args](const char* key) { return visible(argOf(args, key), Shown::Name); };
    if (tool == kPathAccessPrompt) {
        const std::string kind = argOf(args, "kind");
        if (kind == "folder") return "Allow GAIA to use the folder " + one("path") + " and everything in it for this session?";
        if (kind == "file") return "Allow GAIA to use the file " + one("path") + " for this session?";
        return "Allow GAIA to use " + one("path") + " for this session?";
    }
    if (tool == "bash_execute") return visible(argOf(args, "command"), Shown::Command);
    if (tool == "file_write") {
        return one("path") + "\n\nWhat will be saved:\n" + shownBlock(argOf(args, "content"));
    }
    if (tool == "file_edit") {
        return one("path") + "\n\nBefore:\n" + shownBlock(argOf(args, "old_string")) + "\n\nAfter:\n" +
               shownBlock(argOf(args, "new_string"));
    }
    if (tool == "web_fetch") return one("url");
    if (tool == "forget") return "Memory " + one("id");
    if (tool == "take_screenshot") {
        const std::string to = one("output_path");
        return to.empty() ? "The whole screen" : "The whole screen, saved to " + to;
    }
    if (tool == "generate_image") {
        return clipCounted(visible(argOf(args, "prompt"), Shown::Line), 600) + "\nSaved to " + one("output_path");
    }
    std::string name = tool;
    std::replace(name.begin(), name.end(), '_', ' ');
    std::string out = "Use " + name;
    if (args.is_object()) {
        for (const auto& [key, value] : args.items()) {
            if (value.is_string() && value.get<std::string>().find('\n') != std::string::npos) {
                out += "\n" + visible(key, Shown::Name) + ":\n" + shownBlock(value.get<std::string>());
                continue;
            }
            const std::string text = value.is_string() ? value.get<std::string>() : dumps(value);
            out += "\n" + visible(key, Shown::Name) + ": " + clipCounted(visible(text, Shown::Line), 300);
        }
    }
    return out;
}

// Same as sidecar.cpp's toolRisk and grantLabel; this label's text goes through visible().
std::string toolRisk(const std::string& tool) {
    if (tool == "bash_execute") return "execute";
    if (tool == "web_fetch" || tool == "take_screenshot") return "read";
    return "write";
}

std::string grantLabel(const std::string& key) {
    const std::string shell = "bash_execute:";
    if (key.rfind(shell, 0) == 0) {
        const std::string scope = key.substr(shell.size());
        if (scope.rfind("cmd:", 0) == 0) return "Run " + visible(scope.substr(4), Shown::Name) + " commands";
        if (scope.rfind("exact:", 0) == 0) return "Run `" + visible(scope.substr(6), Shown::Name) + "`";
    }
    return "Use " + visible(key.substr(0, key.find(':')), Shown::Name);
}

// The field as a string, "" when absent; a value of another type is a malformed message.
std::string controlString(const json& message, const char* key) {
    if (!message.contains(key) || message[key].is_null()) return "";
    if (!message[key].is_string()) {
        throw std::invalid_argument(std::string("'") + key + "' must be a string, got " + message[key].type_name());
    }
    return message[key].get<std::string>();
}

bool controlFlag(const json& message, const char* key) {
    if (!message.contains(key) || !message[key].is_boolean()) {
        throw std::invalid_argument(std::string("'") + key + "' must be true or false");
    }
    return message[key].get<bool>();
}

std::vector<std::string> splitList(const std::string& s, char sep) {
    std::vector<std::string> out;
    size_t start = 0;
    while (start <= s.size()) {
        const size_t end = s.find(sep, start);
        const std::string part = s.substr(start, end == std::string::npos ? std::string::npos : end - start);
        if (!part.empty()) out.push_back(part);
        if (end == std::string::npos) break;
        start = end + 1;
    }
    return out;
}

#ifdef _WIN32
constexpr char kPathListSep = ';';
#else
constexpr char kPathListSep = ':';
#endif

// Path as a comparable key: the filesystem's own spelling, case-folded on Windows.
std::string pathKey(const fs::path& p) {
    std::error_code ec;
    fs::path real = fs::weakly_canonical(p, ec);
    if (ec) real = p.lexically_normal();
    std::string key = real.generic_u8string();
    while (key.size() > 1 && key.back() == '/' && key[key.size() - 2] != ':') key.pop_back();
#ifdef _WIN32
    key = lower(key);
#endif
    return key;
}

bool isHidden(const std::string& part) { return part.size() > 1 && part[0] == '.' && part != ".."; }

// A home folder or a drive root holds everything the user owns.
bool tooBroad(const fs::path& root) {
    std::error_code ec;
    const fs::path real = fs::weakly_canonical(root, ec);
    if (ec) return true;
    if (!real.has_relative_path()) return true;
    const std::string home = getEnvVar("USERPROFILE", getEnvVar("HOME"));
    return !home.empty() && pathKey(real) == pathKey(fs::u8path(home));
}

// Writing here can change what runs with no approval.
bool landsWhereCodeRuns(const fs::path& resolved) {
    for (const auto& part : resolved) {
        if (lower(part.u8string()) == ".git") return true;
    }
    const std::string parent = pathKey(resolved.parent_path());
    const std::vector<std::string> pathDirs = splitList(getEnvVar("PATH"), kPathListSep);
    for (const auto& dir : pathDirs) {
        if (pathKey(fs::u8path(dir)) == parent) return true;
    }
#ifdef _WIN32
    // cmd.exe runs a git.bat in the current folder before the real git.
    const std::string ext = lower(resolved.extension().u8string());
    const std::vector<std::string> exts = splitList(lower(getEnvVar("PATHEXT", ".COM;.EXE;.BAT;.CMD")), ';');
    if (!ext.empty() && std::find(exts.begin(), exts.end(), ext) != exts.end()) {
        const std::string stem = resolved.stem().u8string();
        for (const auto& dir : pathDirs) {
            for (const auto& e : exts) {
                std::error_code ec;
                if (fs::exists(fs::u8path(dir) / fs::u8path(stem + e), ec)) return true;
            }
        }
    }
#endif
    return false;
}

// Agent activity -> canonical events. Tool args and results arrive through
// prettyPrintJson with their titles, the way the agent loop reports them.
class CanonicalEmitter : public OutputHandler {
public:
    explicit CanonicalEmitter(StdioServer& server) : server_(server) {}

    void printProcessingStart(const std::string&, int, const std::string&) override {
        resetStream();
        status("Thinking");
    }
    void printStepHeader(int step, int limit) override {
        if (server_.dev()) status("Step " + std::to_string(step) + " of " + std::to_string(limit));
    }
    void printStateInfo(const std::string& m) override {
        if (server_.dev()) status(m);
    }
    void printThought(const std::string&) override {}
    void printGoal(const std::string&) override {}
    void printPlan(const json&, int) override {}
    void printToolUsage(const std::string& toolName) override {
        flushTokens();
        tool_ = toolName;
    }
    void printToolComplete() override {}
    void prettyPrintJson(const json& data, const std::string& title) override {
        if (title == "Tool Args") {
            json event = {{"type", "tool_call"}, {"tool", tool_}, {"args", data.is_object() ? data : json::object()}};
            const std::string narration = narrate(tool_, data);
            if (!narration.empty()) event["narration"] = narration;
            server_.write(event);
        } else if (title == "Tool Result") {
            json shown = data;
            const std::string dumped = dumps(data);
            if (dumped.size() > kMaxEventData) shown = {{"truncated", true}, {"text", clip(dumped, kMaxEventData)}};
            server_.write({{"type", "tool_result"}, {"tool", tool_}, {"data", shown}, {"preview", previewOf(tool_, data)}});
        }
    }
    void printError(const std::string& m) override { status(m); }
    void printWarning(const std::string& m) override { status(m); }
    void printInfo(const std::string& m) override { status(m); }
    void printToolProgress(const std::string& phase, const std::string& m) override {
        server_.write({{"type", "status"}, {"message", m}, {"phase", phase}});
    }
    void printReasoningProgress(size_t words) override {
        server_.write({{"type", "status"}, {"phase", "reasoning"}, {"words", words},
                       {"message", "Reasoning - " + std::to_string(words) + " words so far"}});
    }
    void startProgress(const std::string&) override {}
    void stopProgress() override {}
    void printFinalAnswer(const std::string&, const UsageStats&) override {}
    void printCompletion(int, int) override {}
    void printStreamEnd() override {
        if (!inThink_) pending_ += held_;
        flushTokens();
        resetStream();
    }
    void printStreamToken(const std::string& token) override {
        // Reasoning stays out of the answer. A tag may be split across tokens,
        // so a possible tag prefix is held back.
        std::string text = held_ + token;
        held_.clear();
        std::string visible;
        size_t i = 0;
        while (i < text.size()) {
            const std::string marker = inThink_ ? "</think>" : "<think>";
            const size_t at = text.find(marker, i);
            if (at == std::string::npos) {
                size_t keep = 0;
                for (size_t k = std::min(marker.size() - 1, text.size() - i); k > 0; --k) {
                    if (text.compare(text.size() - k, k, marker, 0, k) == 0) {
                        keep = k;
                        break;
                    }
                }
                if (!inThink_) visible += text.substr(i, text.size() - i - keep);
                held_ = text.substr(text.size() - keep);
                break;
            }
            if (!inThink_) visible += text.substr(i, at - i);
            inThink_ = !inThink_;
            i = at + marker.size();
        }
        // A fast model writes hundreds of tokens a second. One event each fills
        // the pipe faster than a UI draws them, and a cancel then waits behind
        // that backlog, so tokens are sent in small batches.
        pending_ += visible;
        if (std::chrono::steady_clock::now() - lastTokens_ >= kTokenBatch) flushTokens();
    }

private:
    void status(const std::string& message) {
        flushTokens();
        if (!message.empty()) server_.write({{"type", "status"}, {"message", message}});
    }
    void flushTokens() {
        if (pending_.empty()) return;
        server_.write({{"type", "token"}, {"delta", pending_}});
        pending_.clear();
        lastTokens_ = std::chrono::steady_clock::now();
    }
    void resetStream() {
        inThink_ = false;
        held_.clear();
        pending_.clear();
    }

    StdioServer& server_;
    std::string tool_;
    bool inThink_ = false;
    std::string held_;
    std::string pending_;
    std::chrono::steady_clock::time_point lastTokens_{};
};

}  // namespace

std::string cloudProviderOf(const std::string& modelId) {
    const size_t dot = modelId.find('.');
    if (dot == std::string::npos || dot + 1 >= modelId.size()) return "";
    const std::string provider = modelId.substr(0, dot);
    return provider == "fireworks" || provider == "amd" ? provider : "";
}

void applyStdioModel(AgentConfig& cfg, const std::string& modelId, NativeToolCalls launchMode) {
    cfg.modelId = modelId;
    // Cloud models behind Lemonade speak native tool calls.
    cfg.nativeToolCalls = modelId.rfind("fireworks.", 0) == 0 ? NativeToolCalls::Always : launchMode;
    // A prompt-JSON model streams its tool calls as text, which is not an answer.
    cfg.streaming = cfg.useNativeToolCalls();
}

std::string workingFolderUtf8() {
    std::error_code ec;
    return fs::current_path(ec).u8string();
}

bool editIsInside(const std::string& target, const std::vector<std::string>& roots) {
    // Judged where the write lands, past every link and junction, even one to a file not there yet.
    const std::string landing = resolveWriteTarget(target);
    if (landing.empty()) return false;
    const fs::path resolved = fs::u8path(landing);
    if (landsWhereCodeRuns(resolved)) return false;
    const std::string key = pathKey(resolved);
    for (const auto& r : roots) {
        const fs::path root = fs::u8path(r);
        if (tooBroad(root)) continue;
        const std::string rootKey = pathKey(root);
        if (key.size() <= rootKey.size() + 1 || key.compare(0, rootKey.size(), rootKey) != 0 ||
            key[rootKey.size()] != '/') {
            continue;
        }
        const std::vector<std::string> parts = splitList(key.substr(rootKey.size() + 1), '/');
        return std::none_of(parts.begin(), parts.end(), isHidden);
    }
    return false;
}

const std::vector<std::string>& StdioServer::eventTypes() {
    static const std::vector<std::string> types = {"status", "token", "tool_call", "tool_result",
                                                   "needs_confirmation", "final", "error"};
    return types;
}

const std::vector<std::string>& StdioServer::controlVerbs() {
    static const std::vector<std::string> verbs = {"tool_decision", "full_access", "accept_edits", "cancel",
                                                   "clear_history"};
    return verbs;
}

const std::vector<std::string>& StdioServer::decisions() {
    static const std::vector<std::string> answers = {"allow", "deny", "always", "timeout"};
    return answers;
}

StdioServer::StdioServer(GaiaAgent& agent, std::istream& in, std::ostream& out, StdioOptions options)
    : agent_(agent),
      in_(in),
      out_(out),
      options_(std::move(options)),
      grants_(std::make_shared<AllowedToolsStore>("")),
      fullAccess_(options_.fullAccess),
      acceptEdits_(options_.acceptEdits) {
    if (options_.editRoots.empty()) options_.editRoots.push_back(workingFolderUtf8());
    // The terminal UI starts this program itself, so its user has no --max-tokens to pass.
    AgentConfig cfg = agent_.config();
    cfg.replyLimitRemedy = GaiaAgent::kChatReplyLimitRemedy;
    agent_.setConfig(cfg);
}

StdioServer::~StdioServer() {
    if (reader_.joinable()) reader_.join();
}

std::ostream& StdioServer::log() { return options_.log ? *options_.log : std::cerr; }

void StdioServer::write(const json& event) {
    const std::string type = event.value("type", "");
    const auto& known = eventTypes();
    if (std::find(known.begin(), known.end(), type) == known.end()) {
        throw std::logic_error("gaiacpp --stdio tried to write a '" + type + "' event, which the TUI can't read");
    }
    if (hostGone_) return;
    bool failed = false;
    {
        std::lock_guard<std::mutex> lock(outMutex_);
        try {
            out_ << dumps(event) << "\n";
            out_.flush();
            failed = !out_;
        } catch (const std::ios_base::failure&) {
            failed = true;
        }
    }
    if (failed) hostLeft("a write to stdout failed");
}

void StdioServer::hostLeft(const std::string& why) {
    if (hostGone_.exchange(true)) return;
    {
        std::lock_guard<std::mutex> lock(logMutex_);
        log() << "[gaiacpp] the host left (" << why << "): stopping" << std::endl;
    }
    // A model call or a tool in flight can't be interrupted, and nobody is left to read its result.
    if (options_.exitWhenHostGone) std::_Exit(0);
    std::lock_guard<std::mutex> lock(mutex_);
    cancelInFlightLocked("the host left");
    queue_.clear();
    queue_.push_back({Item::Stop, ""});
    queueCv_.notify_all();
    doneCv_.notify_all();
}

// After stdin closes, notice a host that is gone without waiting for the next write to fail.
void StdioServer::watchHost() {
    if (!options_.hostAlive) return;
    for (;;) {
        {
            std::unique_lock<std::mutex> lock(mutex_);
            if (doneCv_.wait_for(lock, std::chrono::milliseconds(200), [this] { return finished_ || hostGone_; })) {
                return;
            }
        }
        if (!options_.hostAlive()) {
            hostLeft("stdin and stdout are both closed");
            return;
        }
    }
}

json StdioServer::statusEvent() {
    const AgentConfig cfg = agent_.config();
    const std::string provider = cloudProviderOf(cfg.modelId);
    const std::string base = LemonadeClient(LemonadeClientConfig{cfg.baseUrl}).baseUrl();
    json event = {{"type", "status"},
                  {"message", ""},
                  {"model_id", cfg.modelId},
                  {"model_display", cfg.modelId},
                  {"model_backend", provider.empty() ? "lemonade" : provider},
                  {"model_remote", !provider.empty()},
                  {"lemonade_reachable", false},
                  {"lemonade_base_url", base},
                  {"agent_version", options_.agentVersion},
                  {"gaia_version", options_.agentVersion},
                  // What typing /<name> can run. Empty: gaiacpp doesn't start a skill that way yet, and a
                  // listed name would promise it does.
                  {"skills", json::array()}};
    // Its own short-lived client: a server that doesn't answer must not hold up the first line.
    try {
        HttpClientConfig hc;
        hc.baseUrl = base;
        hc.timeoutSec = kStatusProbeSeconds;
        hc.connectTimeoutSec = kStatusProbeSeconds;
        if (!cfg.apiKey.empty()) hc.defaultHeaders["Authorization"] = "Bearer " + cfg.apiKey;
        HttpClient http(hc);
        const bool lemonade = base.size() >= 7 && base.compare(base.size() - 7, 7, "/api/v1") == 0;
        const json health = json::parse(http.get(lemonade ? "/health" : "/models").body, nullptr, false);
        event["lemonade_reachable"] = true;
        if (health.is_object() && health.contains("version") && health["version"].is_string()) {
            event["lemonade_version"] = health["version"];
        }
    } catch (const std::exception& e) {
        std::lock_guard<std::mutex> lock(logMutex_);
        log() << "[gaiacpp] the model server at " << base << " did not answer: " << e.what() << std::endl;
    }
    return event;
}

void StdioServer::cancelInFlightLocked(const std::string& why) {
    if (!inFlight_) return;
    cancelled_ = true;
    agent_.requestCancel();
    decisionCv_.notify_all();
    std::lock_guard<std::mutex> lock(logMutex_);
    log() << "[gaiacpp] stopping the running turn: " << why << std::endl;
}

void StdioServer::applyControl(const json& message) {
    const std::string verb = controlString(message, kControlKey);
    std::unique_lock<std::mutex> lock(mutex_);
    auto note = [this](const std::string& line) {
        std::lock_guard<std::mutex> logLock(logMutex_);
        log() << "[gaiacpp] " << line << std::endl;
    };
    if (verb == "full_access") {
        fullAccess_ = controlFlag(message, "enabled");
        note(std::string("full access ") + (fullAccess_ ? "ENABLED: approval prompts are off" : "disabled"));
    } else if (verb == "accept_edits") {
        acceptEdits_ = controlFlag(message, "enabled");
        note(std::string("accept edits ") + (acceptEdits_ ? "ENABLED" : "disabled"));
    } else if (verb == "cancel") {
        // A query read but not started yet is the one the host means.
        auto queued = std::find_if(queue_.begin(), queue_.end(),
                                   [](const Item& i) { return i.kind == Item::Query && !i.cancelled; });
        if (inFlight_) {
            cancelInFlightLocked("the host asked to cancel");
        } else if (queued != queue_.end()) {
            queued->cancelled = true;
            note("cancelled a queued query before it started");
        } else {
            note("cancel with no turn running: nothing to stop");
        }
    } else if (verb == "clear_history") {
        queue_.push_back({Item::Clear, ""});
        queueCv_.notify_all();
    } else if (verb == "tool_decision") {
        std::string decision, id;
        try {
            decision = controlString(message, "decision");
            id = controlString(message, "confirm_id");
        } catch (const std::invalid_argument&) {
            // An unreadable answer is not consent: the open approval is denied.
            if (!pendingConfirmId_.empty()) {
                decision_ = "deny";
                decisionCv_.notify_all();
            }
            throw;
        }
        const auto& known = decisions();
        if (std::find(known.begin(), known.end(), decision) == known.end()) {
            note("unknown tool decision '" + decision + "', denying");
            decision = "deny";
        }
        if (pendingConfirmId_.empty()) {
            note("dropped a '" + decision + "' decision: nothing is waiting for one");
        } else if (!id.empty() && id != pendingConfirmId_) {
            note("dropped a '" + decision + "' decision for " + id + ": the open approval is " + pendingConfirmId_);
        } else if (id.empty() && decision != "deny" && decision != "timeout") {
            // A late yes for an approval that already closed must not approve the next one.
            note("dropped a '" + decision + "' decision with no confirm_id: approving needs the id of " +
                 pendingConfirmId_);
        } else {
            decision_ = decision;
            decisionCv_.notify_all();
        }
    } else {
        note("ignored unknown control verb '" + verb + "'");
    }
}

void StdioServer::pumpStdin() {
    for (std::string line; std::getline(in_, line);) {
        const size_t first = line.find_first_not_of(" \t\r");
        if (first == std::string::npos) continue;
        line = line.substr(first, line.find_last_not_of(" \t\r") - first + 1);
        if (line.front() == '{') {
            const json msg = json::parse(line, nullptr, false);
            if (msg.is_discarded()) {
                // Likely a cut-off message; queued so its error lands in turn order.
                std::lock_guard<std::mutex> lock(mutex_);
                queue_.push_back({Item::Reject, "A line starting with '{' was not valid JSON, so it was not "
                                                "sent to the model. Send one complete JSON object per line, "
                                                "or plain text that does not start with '{'."});
                queueCv_.notify_all();
                continue;
            }
            if (msg.is_object() && msg.contains(kControlKey)) {
                try {
                    applyControl(msg);
                } catch (const std::exception& e) {
                    std::lock_guard<std::mutex> lock(logMutex_);
                    log() << "[gaiacpp] rejected a control message: " << e.what() << std::endl;
                }
                continue;
            }
            if (msg.is_object() && msg.contains(kQueryKey) && msg[kQueryKey].is_string()) {
                line = msg[kQueryKey].get<std::string>();
            }
        }
        std::lock_guard<std::mutex> lock(mutex_);
        queue_.push_back({Item::Query, line});
        queueCv_.notify_all();
    }
    // Queries already read still run while stdout has a reader (a one-line
    // pipe gets its answer), but nobody is left to answer an approval.
    if (options_.hostAlive && !options_.hostAlive()) hostLeft("stdin and stdout are both closed");
    {
        std::lock_guard<std::mutex> lock(mutex_);
        inputClosed_ = true;
        decisionCv_.notify_all();
        queue_.push_back({Item::Stop, ""});
        queueCv_.notify_all();
    }
    watchHost();
}

bool StdioServer::askForPath(const std::string& path) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (runId_ != declinedRun_) {
            declinedRun_ = runId_;
            declinedPaths_.clear();
        }
    }
    // One prompt per place per turn: a no is not asked again for what is inside it.
    for (const auto& d : declinedPaths_) {
        if (path.rfind(d, 0) == 0 &&
            (path.size() == d.size() || path[d.size()] == '/' || path[d.size()] == '\\')) {
            return false;
        }
    }
    json args = {{"path", path}};
    std::error_code ec;
    if (fs::is_directory(fs::u8path(path), ec)) args["kind"] = "folder";
    else if (fs::is_regular_file(fs::u8path(path), ec)) args["kind"] = "file";
    const bool granted = confirm(kPathAccessPrompt, args) == ToolConfirmResult::ALLOW_ONCE;
    if (!granted) declinedPaths_.insert(path);
    return granted;
}

ToolConfirmResult StdioServer::confirm(const std::string& tool, const json& args) {
    const bool pathAccess = tool == kPathAccessPrompt;
    // "Always" is only offered where the grant is narrower than the tool: a
    // write to one file must not approve every later write.
    const ToolInfo* info = agent_.tools().findTool(tool);
    const bool shell = tool == "bash_execute";
    // A line of several commands gets no "always": a label for it reads as covering less than it does.
    // One program's grant (`cmd:`) is the exception: `cd folder && one command`, where cd never asks.
    const std::string scope = info && info->grantScope ? info->grantScope(args) : "";
    const bool chained = shell && scope.rfind("cmd:", 0) != 0 &&
                         judgeShellCommand(argOf(args, "command"), "", {}, nullptr).compound;
    const std::string key = info && info->grantScope && !chained ? tool + ":" + scope : "";
    // Read off the lock: it looks at the files the command names.
    const std::string unchecked = shell ? agent_.shellApprovalNote(argOf(args, "command")) : "";
    const bool edit = tool == "file_write" || tool == "file_edit";
    // Judged off the lock: it reads the filesystem and PATH.
    const bool acceptable = edit && editIsInside(argOf(args, "path"), options_.editRoots);
    auto note = [this](const std::string& line) {
        std::lock_guard<std::mutex> logLock(logMutex_);
        log() << "[gaiacpp] " << line << std::endl;
    };
    std::unique_lock<std::mutex> lock(mutex_);
    // Full access here turns tool prompts off; it never opens another folder unasked.
    if (fullAccess_ && !pathAccess) return ToolConfirmResult::ALLOW_ONCE;
    if (acceptEdits_ && acceptable) return ToolConfirmResult::ALLOW_ONCE;
    if (cancelled_ || inputClosed_ || hostGone_) {
        note("denied " + tool + ": " + (cancelled_ ? "the turn was cancelled" : "nobody is left to approve it"));
        return ToolConfirmResult::UNATTENDED;
    }
    const std::string confirmId = randomHex(16);
    // The terminal UI shows the summary as the card's body, so the warning goes under the command.
    const std::string summary =
        promptSummary(tool, args) + (unchecked.empty() ? "" : "\n\n" + visible(unchecked, Shown::Line));
    json event = {{"type", "needs_confirmation"},
                  {"run_id", runId_},
                  {"action", tool},
                  {"summary", summary},
                  {"confirm_id", confirmId}};
    // The terminal UI labels the folder prompt by its action.
    if (!pathAccess) event["risk"] = toolRisk(tool);
    if (!key.empty()) event["always_scope"] = grantLabel(key);
    pendingConfirmId_ = confirmId;
    decision_.clear();
    lock.unlock();
    agent_.releaseNpu();
    write(event);
    lock.lock();
    const bool answered = decisionCv_.wait_for(lock, kOrphanedConfirmTimeout, [this] {
        return !decision_.empty() || cancelled_ || inputClosed_ || hostGone_;
    });
    const std::string decision = decision_;
    pendingConfirmId_.clear();
    decision_.clear();
    if (decision == "allow") return ToolConfirmResult::ALLOW_ONCE;
    if (decision == "always") {
        if (!key.empty()) return ToolConfirmResult::ALWAYS_ALLOW;
        if (!pathAccess) note("'always' was not offered for " + tool + ": allowed this one call");
        return ToolConfirmResult::ALLOW_ONCE;
    }
    if (decision.empty()) {
        note("denied " + tool + ": " +
             (!answered ? "no answer to the approval request in 15 minutes"
                        : cancelled_ ? "the turn was cancelled" : "the host left before answering"));
    } else if (decision == "timeout") {
        note("denied " + tool + ": the host's approval prompt timed out");
    }
    // Only an answered "deny" is the user's no.
    return decision == "deny" ? ToolConfirmResult::DENY : ToolConfirmResult::UNATTENDED;
}

json StdioServer::switchableModels() {
    static const std::set<std::string> notChat = {"embeddings", "image", "reranker", "reranking", "transcription",
                                                  "realtime-transcription", "tts", "audio-generation",
                                                  "voice-design", "classification", "upscaling", "3d"};
    const json catalog = agent_.lemonade().listModels(true);
    if (!catalog.is_object() || !catalog.contains("data") || !catalog["data"].is_array()) {
        throw std::runtime_error("its model list had no 'data' array");
    }
    json ids = json::array();
    for (const auto& m : catalog["data"]) {
        if (!m.is_object() || !m.contains("id") || !m["id"].is_string()) continue;
        const std::string id = m["id"].get<std::string>();
        const bool cloud = !cloudProviderOf(id).empty() || m.value("recipe", "") == "cloud";
        if (!cloud && m.contains("downloaded") && m["downloaded"].is_boolean() && !m["downloaded"].get<bool>()) continue;
        bool chat = true;
        if (m.contains("labels") && m["labels"].is_array()) {
            bool saysChat = false, saysOther = false;
            for (const auto& l : m["labels"]) {
                if (!l.is_string()) continue;
                saysChat = saysChat || l.get<std::string>() == "chat";
                saysOther = saysOther || notChat.count(l.get<std::string>()) > 0;
            }
            chat = saysChat || !saysOther;
        }
        if (chat) ids.push_back(id);
    }
    return ids;
}

json StdioServer::runModelCommand(const std::string& arg) {
    const AgentConfig current = agent_.config();
    json models;
    std::string listError;
    try {
        models = switchableModels();
    } catch (const std::exception& e) {
        listError = "The model server at " + current.baseUrl + " did not list its models (" + e.what() + ").";
    }
    if (arg.empty()) {
        std::string text;
        if (!listError.empty()) {
            text = listError + "\n\n";
        } else {
            text = "**Models this server can run now:**\n";
            for (const auto& id : models) {
                text += "- `" + id.get<std::string>() + "`" + (id == current.modelId ? " ← current" : "") + "\n";
            }
            if (models.empty()) text += "- (none downloaded)\n";
            text += "\n";
        }
        return {{"type", "final"},
                {"answer", text + "Currently running: **" + current.modelId + "**. Switch with `/model <id>`."}};
    }
    if (!listError.empty()) {
        return {{"type", "error"}, {"detail", "Could not switch to " + arg + ". " + listError + " The model was not changed."}};
    }
    std::string target;
    for (const auto& id : models) {
        const std::string name = id.get<std::string>();
        // The server's own spelling: it lists a registered user.X as X, or the other way round.
        if (name == arg) target = name;
        else if (target.empty() && ("user." + name == arg || name == "user." + arg)) target = name;
    }
    if (target.empty()) {
        std::string names;
        for (const auto& id : models) names += (names.empty() ? "" : ", ") + id.get<std::string>();
        return {{"type", "error"},
                {"detail", "Unknown model '" + arg + "'. Models this server can run now: " +
                               (names.empty() ? std::string("(none downloaded)") : names) + "."}};
    }
    AgentConfig cfg = current;
    applyStdioModel(cfg, target, options_.nativeTools);
    agent_.setConfig(cfg);
    agent_.rebuildSystemPrompt();
    write(statusEvent());
    const std::string provider = cloudProviderOf(target);
    return {{"type", "final"},
            {"answer", "Switched to **" + target + "** " +
                           (provider.empty() ? "(runs on this PC)." : "(cloud: chat is sent to " + provider + ").")}};
}

json StdioServer::warmUp() {
    const AgentConfig cfg = agent_.config();
    if (!cloudProviderOf(cfg.modelId).empty()) return {{"type", "final"}, {"answer", "warm_up_skipped"}};
    try {
        write({{"type", "status"}, {"message", "Getting " + cfg.modelId + " ready"}});
        agent_.lemonade().ensureModelLoaded();
    } catch (const std::exception& e) {
        return {{"type", "error"},
                {"detail", std::string("Could not get the model ready ahead of time. Chat still works, but the "
                                       "first answer will take longer. ") + e.what()}};
    }
    return {{"type", "final"}, {"answer", "warmed_up"}};
}

// The store keeps no confidence score and no last-use time, so those keys are
// left out rather than sent as a number that would read as a measurement.
json StdioServer::memoryDump() {
    if (!options_.memory) {
        return {{"available", false}, {"reason", "Memory is off for this session (--no-memory)."}};
    }
    const std::string path = options_.memoryPath.empty() ? MemoryStore::defaultPath() : options_.memoryPath;
    std::error_code ec;
    if (!fs::exists(path, ec)) return {{"available", false}, {"reason", "Nothing has been remembered yet."}};
    MemoryStore store(path);
    const auto entries = store.recall("", "", 300, true);
    json items = json::array(), byCategory = json::object(), byContext = json::object();
    int sensitive = 0;
    std::set<std::string> entities;
    for (const auto& e : entries) {
        const std::string ctx = e.domain.empty() ? "global" : e.domain;
        json item = {{"id", e.id}, {"category", e.category}, {"content", e.content}, {"context", ctx},
                     {"sensitive", e.sensitive}, {"created_at", e.createdAt}, {"updated_at", e.updatedAt}};
        if (!e.entity.empty()) {
            item["entity"] = e.entity;
            entities.insert(e.entity);
        }
        items.push_back(std::move(item));
        byCategory[e.category] = byCategory.value(e.category, 0) + 1;
        byContext[ctx] = byContext.value(ctx, 0) + 1;
        if (e.sensitive) ++sensitive;
    }
    json contexts = json::array();
    for (auto& [ctx, n] : byContext.items()) contexts.push_back({{"context", ctx}, {"count", n}});
    const size_t total = store.count();
    return {{"available", true},
            {"stats", {{"total_knowledge", total}, {"by_category", byCategory}, {"by_context", byContext},
                       {"sensitive_count", sensitive}, {"entity_count", entities.size()}}},
            {"contexts", contexts}, {"shown", items.size()}, {"total", total}, {"items", items}};
}

json StdioServer::runTurn(const std::string& query) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        runId_ = "run-" + std::to_string(++turnCounter_);
    }
    const json result = agent_.processQuery(query);
    const std::string answer = result.value("result", "");
    const std::string status = result.value("status", "success");
    if (status == "cancelled") return {{"type", "final"}, {"answer", ""}};
    if (status == "error" && answer.rfind(kLlmFailure, 0) == 0) {
        return {{"type", "error"},
                {"detail", "The model server at " + agent_.config().baseUrl + " did not answer: " +
                               answer.substr(std::string(kLlmFailure).size())}};
    }
    json terminal = {{"type", "final"}, {"answer", answer}};
    json usage = json::object();
    if (result.value("steps_taken", 0) > 0) usage["steps"] = result["steps_taken"];
    if (result.contains("usage") && result["usage"].is_object()) {
        const json& u = result["usage"];
        if (u.value("prompt_tokens", 0) > 0) {
            usage["input_tokens"] = u["prompt_tokens"];
            usage["cached_tokens"] = u.value("cached_tokens", 0);
        }
        if (u.value("completion_tokens", 0) > 0) usage["tokens"] = u["completion_tokens"];
    }
    if (result.value("tool_calls", 0) > 0) usage["tools_used"] = result["tool_calls"];
    if (!usage.empty()) terminal["usage"] = usage;
    return terminal;
}

// A fresh conversation starts with nothing pre-approved: the "always" answers went with the old one.
void StdioServer::clearConversation() {
    agent_.clearHistory();
    grants_->clearAll();
}

void StdioServer::runQuery(const std::string& query) {
    json terminal;
    try {
        if (cancelled_) {
            terminal = {{"type", "final"}, {"answer", ""}};
        } else if (query == kClearQuery) {
            clearConversation();
            terminal = {{"type", "final"}, {"answer", "conversation_cleared"}};
        } else if (query == kMemoryDumpQuery) {
            terminal = {{"type", "final"}, {"answer", dumps(memoryDump())}};
        } else if (query == kWarmUpQuery) {
            terminal = warmUp();
        } else {
            const size_t first = query.find_first_not_of(" \t\r\n");
            const size_t last = query.find_last_not_of(" \t\r\n");
            const std::string trimmed = first == std::string::npos ? "" : query.substr(first, last - first + 1);
            if (trimmed == "/model" || trimmed.rfind("/model ", 0) == 0) {
                const size_t at = trimmed.find_first_not_of(' ', 6);
                terminal = runModelCommand(at == std::string::npos ? "" : trimmed.substr(at));
            } else {
                terminal = runTurn(query);
            }
        }
    } catch (const std::exception& e) {
        terminal = {{"type", "error"}, {"detail", e.what()}};
    } catch (...) {
        // Anything else would end the process and the session with it.
        terminal = {{"type", "error"}, {"detail", "The turn failed with an unknown error"}};
    }
    {
        // Over before its terminal event goes out: a cancel from here on is for a later query.
        std::lock_guard<std::mutex> lock(mutex_);
        inFlight_ = false;
        cancelled_ = false;
    }
    write(terminal);
}

int StdioServer::run() {
    int code = 0;
    std::string failure;
    try {
        agent_.setOutputHandler(std::make_unique<CanonicalEmitter>(*this));
        agent_.setAllowedToolsStore(grants_);
        agent_.setToolConfirmCallback(
            [this](const std::string& tool, const json& args) { return confirm(tool, args); });
        agent_.setPathAccessPrompt([this](const std::string& path) { return askForPath(path); });
        // A cancel that lands before the agent resets its own flag still holds.
        agent_.setCancelCheck([this] { return cancelled_.load(); });
        if (options_.fullAccess) {
            std::lock_guard<std::mutex> lock(logMutex_);
            log() << "[gaiacpp] full access ENABLED at launch: approval prompts are off" << std::endl;
        }
        write(statusEvent());
        reader_ = std::thread([this] { pumpStdin(); });
        for (;;) {
            Item item;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                queueCv_.wait(lock, [this] { return !queue_.empty(); });
                item = queue_.front();
                queue_.pop_front();
                // In the same step as leaving the queue, so a cancel always finds the query.
                if (item.kind == Item::Query) {
                    inFlight_ = true;
                    cancelled_ = item.cancelled;
                }
            }
            if (item.kind == Item::Stop) break;
            if (item.kind == Item::Clear) {
                clearConversation();
                continue;
            }
            if (item.kind == Item::Reject) {
                write({{"type", "error"}, {"detail", item.text}});
                continue;
            }
            runQuery(item.text);
        }
    } catch (const std::exception& e) {
        failure = e.what();
    } catch (...) {
        failure = "unknown error";
    }
    if (!failure.empty()) {
        code = 1;
        {
            std::lock_guard<std::mutex> lock(logMutex_);
            log() << "[gaiacpp] stopped on an internal error: " << failure << std::endl;
        }
        bool owed;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            owed = inFlight_;
            inFlight_ = false;
        }
        // The query in flight still gets its one terminal event.
        if (owed) {
            try {
                write({{"type", "error"}, {"detail", "gaiacpp stopped on an internal error: " + failure}});
            } catch (const std::exception& e) {
                std::lock_guard<std::mutex> lock(logMutex_);
                log() << "[gaiacpp] could not report it on stdout: " << e.what() << std::endl;
            }
        }
    }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        finished_ = true;
    }
    doneCv_.notify_all();
    // After an error or with the host gone, stdin may never close: the reader
    // is left to the destructor and the caller decides whether to wait for it.
    if (code == 0 && !hostGone_ && reader_.joinable()) reader_.join();
    return code;
}

}  // namespace gaia
