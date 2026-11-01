// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/tool_output.h"

#include <algorithm>
#include <cmath>
#include <cstdio>

namespace gaia {

namespace {

const char* const kFetchHint =
    "read_tool_output(artifact, entry=n) returns one indexed part verbatim";

// Back a cut point up to a UTF-8 lead byte so a page never splits a character.
std::size_t utf8Boundary(const std::string& s, std::size_t pos) {
    if (pos >= s.size()) return s.size();
    while (pos > 0 && (static_cast<unsigned char>(s[pos]) & 0xC0) == 0x80) --pos;
    return pos;
}

}  // namespace

std::string ToolOutputStore::put(const std::string& text, std::vector<Entry> entries) {
    std::lock_guard<std::mutex> lock(mutex_);
    evictLocked();
    char buf[40];
    std::snprintf(buf, sizeof(buf), "output_%016llx%08x", ++counter_,
                  static_cast<unsigned>(std::chrono::steady_clock::now().time_since_epoch().count()));
    std::string handle = buf;
    items_[handle] = Item{text, std::move(entries), std::chrono::steady_clock::now()};
    order_.push_back(handle);
    totalBytes_ += text.size();
    while (totalBytes_ > kMaxTotalBytes && order_.size() > 1) {
        auto it = items_.find(order_.front());
        if (it != items_.end()) {
            totalBytes_ -= it->second.text.size();
            items_.erase(it);
        }
        order_.erase(order_.begin());
    }
    return handle;
}

void ToolOutputStore::evictLocked() {
    const auto now = std::chrono::steady_clock::now();
    for (auto it = order_.begin(); it != order_.end();) {
        auto item = items_.find(*it);
        if (item == items_.end()) {
            it = order_.erase(it);
            continue;
        }
        if (now - item->second.stored > std::chrono::seconds(kTtlSeconds)) {
            totalBytes_ -= item->second.text.size();
            items_.erase(item);
            it = order_.erase(it);
        } else {
            ++it;
        }
    }
}

json ToolOutputStore::read(const std::string& artifact, int entry, long long offset,
                           long long limit) {
    std::lock_guard<std::mutex> lock(mutex_);
    evictLocked();
    auto it = items_.find(artifact);
    if (it == items_.end()) {
        return {{"status", "error"},
                {"error", "No stored output named '" + artifact +
                              "'. It may have expired (stored output lasts one hour) or the "
                              "handle was mistyped; run the original tool again to get a fresh one."}};
    }
    const Item& item = it->second;
    if (entry > 0 && offset > 0) {
        return {{"status", "error"},
                {"error", "Pass either entry or offset, not both: entry reads one indexed "
                          "part, offset pages through the whole output."}};
    }
    if (offset < 0) {
        return {{"status", "error"}, {"error", "offset must be 0 or more."}};
    }

    std::size_t start = static_cast<std::size_t>(offset);
    std::size_t end = item.text.size();
    std::size_t want = kDefaultReadChars;
    if (entry > 0) {
        if (static_cast<std::size_t>(entry) > item.entries.size()) {
            return {{"status", "error"},
                    {"error", "entry " + std::to_string(entry) + " is out of range; this output has " +
                                  std::to_string(item.entries.size()) + " indexed part(s)."}};
        }
        const Entry& e = item.entries[static_cast<std::size_t>(entry) - 1];
        start = e.offset;
        end = std::min(item.text.size(), e.offset + e.length);
        want = e.length;
    }
    if (limit > 0) want = static_cast<std::size_t>(limit);
    want = std::min(want, kPageChars);

    // Both ends land on character boundaries: back the start up, and when the
    // page is narrower than one character, widen it to the whole character.
    start = utf8Boundary(item.text, start);
    std::size_t stop = utf8Boundary(item.text, std::min(end, start + want));
    if (stop <= start && start < end) {
        stop = start + 1;
        while (stop < end && (static_cast<unsigned char>(item.text[stop]) & 0xC0) == 0x80) ++stop;
    }

    json out = {{"artifact", artifact},
                {"content", item.text.substr(start, stop - start)},
                {"offset", start},
                {"total_chars", item.text.size()}};
    if (stop < item.text.size()) {
        out["next_offset"] = stop;
        out["remaining"] = item.text.size() - stop;
    } else {
        out["next_offset"] = nullptr;
    }
    if (entry > 0) out["entry"] = entry;
    return out;
}

std::size_t ToolOutputStore::size() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return items_.size();
}

void ToolOutputStore::clear() {
    std::lock_guard<std::mutex> lock(mutex_);
    items_.clear();
    order_.clear();
    totalBytes_ = 0;
}

ToolInfo ToolOutputStore::readTool(ToolOutputStore& store) {
    ToolInfo info;
    info.name = "read_tool_output";
    info.description =
        "Read more of a tool result that was cut short to fit the context window. "
        "Pass the `artifact` handle from that result; `entry` reads one indexed part "
        "verbatim, or page through the whole output with `offset` (use the returned "
        "next_offset) and an optional `limit` (max 8000 characters).";
    info.parameters = {
        {"artifact", ToolParamType::STRING, true, "Handle from the truncated result"},
        {"entry", ToolParamType::INTEGER, false, "Indexed part to read (1-based)"},
        {"offset", ToolParamType::INTEGER, false, "Character offset to start at (default 0)"},
        {"limit", ToolParamType::INTEGER, false, "Characters to return (max 8000)"},
    };
    info.callback = [&store](const json& args) -> json {
        auto num = [&args](const char* key) -> long long {
            if (!args.contains(key) || args[key].is_null()) return 0;
            const json& v = args[key];
            if (v.is_number_integer()) return v.get<long long>();
            if (v.is_number()) return static_cast<long long>(v.get<double>());
            if (v.is_string()) {
                try {
                    return std::stoll(v.get<std::string>());
                } catch (const std::exception&) {
                    throw std::invalid_argument(std::string(key) + " must be an integer");
                }
            }
            throw std::invalid_argument(std::string(key) + " must be an integer");
        };
        try {
            return store.read(args.value("artifact", ""), static_cast<int>(num("entry")),
                              num("offset"), num("limit"));
        } catch (const std::invalid_argument& e) {
            return {{"status", "error"}, {"error", e.what()}};
        }
    };
    return info;
}

ToolOutputBudget ToolOutputBudget::forContext(int contextTokens) {
    if (contextTokens <= 0) contextTokens = 32768;
    const double threshold = std::round(static_cast<double>(contextTokens) * 30000.0 / 32768.0);
    return {static_cast<std::size_t>(threshold),
            static_cast<std::size_t>(std::round(threshold * 2.0 / 3.0))};
}

std::string condenseToolOutput(const std::string& toolName, const std::string& serialized,
                               const ToolOutputBudget& budget, ToolOutputStore& store) {
    // A page the model asked for is never cut again.
    if (toolName == "read_tool_output" || serialized.size() <= budget.threshold) {
        return serialized;
    }
    const std::size_t headLen = utf8Boundary(serialized, budget.target * 3 / 4);
    std::size_t tailStart =
        utf8Boundary(serialized, serialized.size() - std::min(serialized.size(), budget.target / 4));
    if (tailStart < headLen) tailStart = headLen;

    ToolOutputStore::Entry omitted{"omitted middle", headLen, tailStart - headLen};
    const std::string handle = store.put(serialized, {omitted});

    json view = {{"truncated", true},
                 {"original_chars", serialized.size()},
                 {"omitted_chars", tailStart - headLen},
                 {"head", serialized.substr(0, headLen)},
                 {"tail", serialized.substr(tailStart)},
                 {"index", json::array({{{"n", 1},
                                         {"label", omitted.label},
                                         {"offset", omitted.offset},
                                         {"length", omitted.length}}})},
                 {"artifact", handle},
                 {"continuation", "read_tool_output"},
                 {"fetch", kFetchHint},
                 {"total_chars", serialized.size()}};
    return view.dump(-1, ' ', false, json::error_handler_t::replace);
}

}  // namespace gaia
