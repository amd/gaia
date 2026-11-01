// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The flagship's media tools, under the Python names and result shapes:
// image generation (src/gaia/sd/mixin.py), transcription
// (src/gaia/agents/tools/audio_tools.py) and screen capture
// (src/gaia/agents/tools/screenshot_tools.py).

#include "gaia/flagship_tools.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <map>
#include <random>
#include <regex>
#include <set>
#include <sstream>

#include "gaia/file_tools.h"
#include "gaia/flagship_config.h"
#include "gaia/lemonade_client.h"
#include "gaia/process.h"
#include "gaia/tool_registry.h"
#include "miniz.h"

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace fs = std::filesystem;

namespace gaia {

namespace {

const std::vector<std::string> kSdModels = {"SD-1.5", "SD-Turbo", "SDXL-Base-1.0", "SDXL-Turbo"};
const std::vector<std::string> kSdSizes = {"512x512", "768x768", "1024x1024"};
const char* const kSdDefaultModel = "SDXL-Turbo";
struct SdDefaults {
    int steps;
    double cfg;
    const char* size;
};
const std::map<std::string, SdDefaults> kSdModelDefaults = {{"SD-1.5", {20, 7.5, "512x512"}},
                                                            {"SD-Turbo", {4, 1.0, "512x512"}},
                                                            {"SDXL-Base-1.0", {20, 7.5, "1024x1024"}},
                                                            {"SDXL-Turbo", {4, 1.0, "512x512"}}};

const char* const kAsrModel = "Whisper-Large-v3-Turbo";
constexpr double kConfidenceThreshold = 0.6;
constexpr std::size_t kContextWords = 6;
constexpr std::size_t kPreviewChars = 1200;
constexpr std::size_t kMaxReportedSpans = 25;
constexpr double kTurnGapSeconds = 0.8;
constexpr std::size_t kTurnsPerCall = 40;
constexpr std::size_t kMinTurnsPerCall = 4;
constexpr std::size_t kMaxSpeakers = 8;
// Lemonade rejects request bodies over 100 MiB; 40 minutes of 16 kHz mono is ~73 MiB.
constexpr double kMaxChunkSeconds = 40 * 60;
constexpr double kChunkOverlapSeconds = 30.0;
const char* const kMediaDocs = "https://amd-gaia.ai/docs/reference/troubleshooting";

std::string trim(std::string s) {
    s.erase(0, s.find_first_not_of(" \t\r\n"));
    s.erase(s.find_last_not_of(" \t\r\n") + 1);
    return s;
}

std::string lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

std::string str(const json& args, const char* key, const std::string& def = "") {
    if (!args.is_object() || !args.contains(key) || args[key].is_null()) return def;
    const json& v = args[key];
    if (v.is_string()) return v.get<std::string>();
    if (v.is_number() || v.is_boolean()) return v.dump();
    return def;
}

std::optional<double> number(const json& args, const char* key) {
    if (!args.is_object() || !args.contains(key) || args[key].is_null()) return std::nullopt;
    const json& v = args[key];
    if (v.is_number()) return v.get<double>();
    if (v.is_string()) {
        const std::string s = trim(v.get<std::string>());
        if (s.empty()) return std::nullopt;
        std::size_t used = 0;
        double out = 0;
        try {
            out = std::stod(s, &used);
        } catch (const std::exception&) {
            used = 0;
        }
        if (used == s.size()) return out;
    }
    throw std::invalid_argument(std::string("'") + key + "' must be a number, got " + v.dump());
}

json error(const std::string& message) { return {{"status", "error"}, {"error", message}}; }

double dbl(const json& j, const char* key) {
    return j.is_object() && j.contains(key) && j[key].is_number() ? j[key].get<double>() : 0.0;
}

std::string text(const json& j, const char* key) {
    return j.is_object() && j.contains(key) && j[key].is_string() ? j[key].get<std::string>() : std::string();
}

double round(double v, int places) {
    const double f = std::pow(10.0, places);
    return std::round(v * f) / f;
}

std::size_t codePoints(const std::string& s) {
    std::size_t n = 0;
    for (unsigned char c : s) n += (c & 0xC0) != 0x80;
    return n;
}

// The first @p n code points of @p s.
std::string firstChars(const std::string& s, std::size_t n) {
    std::size_t seen = 0;
    for (std::size_t i = 0; i < s.size(); ++i) {
        if ((static_cast<unsigned char>(s[i]) & 0xC0) != 0x80) {
            if (seen == n) return s.substr(0, i);
            ++seen;
        }
    }
    return s;
}

std::string pyList(const std::vector<std::string>& items) {
    std::string out = "[";
    for (std::size_t i = 0; i < items.size(); ++i) out += (i ? ", '" : "'") + items[i] + "'";
    return out + "]";
}

std::string localTime(const char* format) {
    const std::time_t t = std::time(nullptr);
    std::tm tm{};
#ifdef _WIN32
    localtime_s(&tm, &t);
#else
    localtime_r(&t, &tm);
#endif
    char buf[64];
    std::strftime(buf, sizeof buf, format, &tm);
    return buf;
}

bool readWhole(const fs::path& p, std::string& out) {
    std::ifstream in(p, std::ios::binary);
    if (!in) return false;
    std::ostringstream ss;
    ss << in.rdbuf();
    out = ss.str();
    return true;
}

void writeWhole(const fs::path& p, const std::string& bytes) {
    std::error_code ec;
    if (p.has_parent_path()) fs::create_directories(p.parent_path(), ec);
    if (ec) throw std::runtime_error("Cannot create folder " + p.parent_path().u8string() + ": " + ec.message());
    std::ofstream f(p, std::ios::binary);
    if (!f) throw std::runtime_error("Cannot write " + p.u8string());
    f << bytes;
    f.close();
    if (!f) {
        fs::remove(p, ec);
        throw std::runtime_error("Writing " + p.u8string() + " failed (disk full or permission denied); nothing was saved.");
    }
}

std::string base64Decode(const std::string& in) {
    static const std::string chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    out.reserve(in.size() * 3 / 4);
    unsigned buf = 0;
    int bits = 0;
    for (char c : in) {
        if (c == '=' || c == '\n' || c == '\r' || c == ' ') continue;
        const auto pos = chars.find(c);
        if (pos == std::string::npos) throw std::runtime_error("the image data is not valid base64");
        buf = (buf << 6) | static_cast<unsigned>(pos);
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            out.push_back(static_cast<char>((buf >> bits) & 0xFF));
        }
    }
    return out;
}

// ~ expanded and relative to @p workDir, made absolute.
fs::path absolutePath(const std::string& raw, const std::string& workDir) {
    std::string s = trim(raw);
    if (!s.empty() && s[0] == '~' && (s.size() == 1 || s[1] == '/' || s[1] == '\\')) s = userHomeDir() + s.substr(1);
    fs::path p = fs::u8path(s);
    if (p.is_relative()) p = fs::u8path(workDir) / p;
    std::error_code ec;
    fs::path c = fs::weakly_canonical(p, ec);
    return ec ? p.lexically_normal() : c;
}

bool inside(const fs::path& p, const fs::path& root) {
#ifdef _WIN32
    const fs::path a = fs::u8path(lower(p.u8string())), b = fs::u8path(lower(root.u8string()));
#else
    const fs::path& a = p;
    const fs::path& b = root;
#endif
    const fs::path rel = a.lexically_relative(b);
    return !rel.empty() && *rel.begin() != "..";
}

// Python's `Path.home() / ".gaia" / name`: the home folder, not GAIA_HOME.
fs::path homeGaia(const char* name) { return fs::u8path(userHomeDir()) / ".gaia" / name; }

// ---------------------------------------------------------------------------
// WAV
// ---------------------------------------------------------------------------

struct Wav {
    int format = 0, channels = 0, rate = 0, bits = 0, blockAlign = 0;
    std::size_t dataOffset = 0, dataSize = 0;
};

std::uint32_t le32(const std::string& b, std::size_t at) {
    return static_cast<std::uint32_t>(static_cast<unsigned char>(b[at])) |
           static_cast<std::uint32_t>(static_cast<unsigned char>(b[at + 1])) << 8 |
           static_cast<std::uint32_t>(static_cast<unsigned char>(b[at + 2])) << 16 |
           static_cast<std::uint32_t>(static_cast<unsigned char>(b[at + 3])) << 24;
}
int le16(const std::string& b, std::size_t at) {
    return static_cast<unsigned char>(b[at]) | static_cast<unsigned char>(b[at + 1]) << 8;
}

bool parseWav(const std::string& b, Wav& w) {
    if (b.size() < 12 || b.compare(0, 4, "RIFF") != 0 || b.compare(8, 4, "WAVE") != 0) return false;
    bool fmt = false;
    for (std::size_t at = 12; at + 8 <= b.size();) {
        const std::string id = b.substr(at, 4);
        const std::size_t size = le32(b, at + 4);
        const std::size_t body = at + 8;
        if (id == "fmt " && size >= 16 && body + 16 <= b.size()) {
            w.format = le16(b, body);
            w.channels = le16(b, body + 2);
            w.rate = static_cast<int>(le32(b, body + 4));
            w.blockAlign = le16(b, body + 12);
            w.bits = le16(b, body + 14);
            // WAVE_FORMAT_EXTENSIBLE carries the real format in its sub-format GUID.
            if (w.format == 0xFFFE && size >= 26 && body + 26 <= b.size()) w.format = le16(b, body + 24);
            fmt = true;
        } else if (id == "data") {
            w.dataOffset = body;
            w.dataSize = std::min(size, b.size() - body);
            return fmt;
        }
        at = body + size + (size & 1);
    }
    return false;
}

// The only shape the transcription endpoint takes without a decode.
bool isAsrReady(const Wav& w) { return w.format == 1 && w.channels == 1 && w.rate == 16000 && w.bits == 16; }

std::string wavHeader(std::size_t dataSize) {
    std::string h = "RIFF";
    auto put32 = [&h](std::uint32_t v) {
        for (int i = 0; i < 4; ++i) h.push_back(static_cast<char>((v >> (8 * i)) & 0xFF));
    };
    auto put16 = [&h](int v) {
        h.push_back(static_cast<char>(v & 0xFF));
        h.push_back(static_cast<char>((v >> 8) & 0xFF));
    };
    put32(static_cast<std::uint32_t>(36 + dataSize));
    h += "WAVEfmt ";
    put32(16);
    put16(1);
    put16(1);
    put32(16000);
    put32(32000);
    put16(2);
    put16(16);
    h += "data";
    put32(static_cast<std::uint32_t>(dataSize));
    return h;
}

std::string findFfmpeg() {
    std::string found = findExecutable("ffmpeg");
    if (!found.empty()) return found;
    // A package manager may install where this process's PATH does not reach yet.
    std::vector<fs::path> extra;
#ifdef _WIN32
    const std::string local = getEnvVar("LOCALAPPDATA");
    if (!local.empty()) extra.push_back(fs::u8path(local) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe");
#else
    for (const char* d : {"/usr/local/bin", "/opt/homebrew/bin", "/usr/bin"}) extra.push_back(fs::path(d) / "ffmpeg");
#endif
    std::error_code ec;
    for (const auto& p : extra) {
        if (fs::is_regular_file(p, ec)) return p.u8string();
    }
    return "";
}

std::string ffmpegInstallHint() {
#if defined(_WIN32)
    return "Install it with: winget install Gyan.FFmpeg";
#elif defined(__APPLE__)
    return "Install it with: brew install ffmpeg";
#else
    return "Install it with: sudo apt-get install ffmpeg or sudo dnf install ffmpeg or sudo pacman -S ffmpeg";
#endif
}

std::string quoted(const std::string& s) {
#ifdef _WIN32
    return "\"" + s + "\"";
#else
    std::string out = "'";
    for (char c : s) out += (c == '\'') ? std::string("'\\''") : std::string(1, c);
    return out + "'";
#endif
}

// ---------------------------------------------------------------------------
// Transcripts
// ---------------------------------------------------------------------------

struct Word {
    std::string word;
    double start = 0, end = 0, probability = 0;
};
struct Segment {
    double start = 0, end = 0;
    std::string text;
    double avgLogprob = 0;
    std::vector<Word> words;
};
struct Transcript {
    std::vector<Segment> segments;
    std::string language, model, rawText;
    double duration = 0;
    std::string fullText() const {
        if (segments.empty()) return trim(rawText);
        std::string out;
        for (const auto& s : segments) out += s.text;
        return trim(out);
    }
};

Transcript parseTranscript(const json& payload, const std::string& model) {
    Transcript t;
    t.model = model;
    const json segments = payload.contains("segments") ? payload["segments"] : json::array();
    if (!segments.is_null() && !segments.is_array()) {
        throw std::runtime_error("Lemonade returned a non-list 'segments' field. Check the Lemonade Server version.");
    }
    for (const auto& raw : segments.is_array() ? segments : json::array()) {
        Segment s;
        s.start = dbl(raw, "start");
        s.end = dbl(raw, "end");
        s.text = text(raw, "text");
        s.avgLogprob = dbl(raw, "avg_logprob");
        if (raw.contains("words") && raw["words"].is_array()) {
            for (const auto& w : raw["words"]) s.words.push_back({text(w, "word"), dbl(w, "start"), dbl(w, "end"), dbl(w, "probability")});
        }
        t.segments.push_back(std::move(s));
    }
    t.language = text(payload, "language");
    if (t.language.empty()) t.language = text(payload, "detected_language");
    t.duration = dbl(payload, "duration");
    t.rawText = text(payload, "text");
    return t;
}

struct Chunk {
    double start, end;
};

std::vector<Chunk> chunkPlan(double duration) {
    std::vector<Chunk> chunks;
    const double stride = kMaxChunkSeconds - kChunkOverlapSeconds;
    for (double start = 0;; start += stride) {
        const double end = std::min(start + kMaxChunkSeconds, duration);
        chunks.push_back({start, end});
        if (end >= duration) break;
    }
    return chunks;
}

// Shift each chunk onto the recording's timeline and split each overlap at its midpoint.
Transcript mergeChunks(const std::vector<Transcript>& parts, const std::vector<Chunk>& chunks) {
    if (parts.size() == 1) return parts[0];
    std::vector<double> cuts;
    for (std::size_t i = 0; i + 1 < chunks.size(); ++i) cuts.push_back((chunks[i].end + chunks[i + 1].start) / 2);
    Transcript out;
    for (std::size_t i = 0; i < parts.size(); ++i) {
        const double offset = chunks[i].start;
        for (const auto& seg : parts[i].segments) {
            const double start = seg.start + offset;
            if (i > 0 && start < cuts[i - 1]) continue;
            if (i + 1 < chunks.size() && start >= cuts[i]) continue;
            Segment s = seg;
            s.start = start;
            s.end = seg.end + offset;
            for (auto& w : s.words) {
                w.start += offset;
                w.end += offset;
            }
            out.segments.push_back(std::move(s));
        }
    }
    out.language = parts[0].language;
    out.model = parts[0].model;
    out.duration = chunks.back().end;
    return out;
}

std::string joinWords(const std::vector<Word>& words, std::size_t from, std::size_t to) {
    std::string out;
    for (std::size_t i = from; i < to; ++i) out += words[i].word;
    return trim(out);
}

json lowConfidenceSpans(const Transcript& t) {
    json spans = json::array();
    if (t.segments.empty()) return spans;
    const bool anyWords = std::any_of(t.segments.begin(), t.segments.end(), [](const Segment& s) { return !s.words.empty(); });
    if (!anyWords) {
        throw std::runtime_error("Transcript from model '" + t.model +
                                 "' has no word-level confidence, so low-confidence spans cannot be computed. "
                                 "Re-transcribe with a whispercpp model such as '" + std::string(kAsrModel) + "'.");
    }
    for (const auto& seg : t.segments) {
        const auto& w = seg.words;
        for (std::size_t i = 0; i < w.size();) {
            if (w[i].probability >= kConfidenceThreshold) {
                ++i;
                continue;
            }
            std::size_t end = i;
            double minP = 1.0;
            while (end < w.size() && w[end].probability < kConfidenceThreshold) minP = std::min(minP, w[end++].probability);
            const std::size_t cs = i >= kContextWords ? i - kContextWords : 0;
            const std::size_t ce = std::min(w.size(), end + kContextWords);
            spans.push_back({{"text", joinWords(w, i, end)}, {"context", joinWords(w, cs, ce)},
                             {"start", round(w[i].start, 2)}, {"end", round(w[end - 1].end, 2)},
                             {"min_probability", round(minP, 3)}});
            i = end;
        }
    }
    return spans;
}

std::string clockText(double seconds) {
    const long long s = std::max(0LL, static_cast<long long>(seconds));
    if (s < 60) return std::to_string(s) + "s";
    char buf[32];
    if (s < 3600) {
        std::snprintf(buf, sizeof buf, "%lldm %02llds", s / 60, s % 60);
    } else {
        std::snprintf(buf, sizeof buf, "%lldh %02lldm", s / 3600, (s % 3600) / 60);
    }
    return buf;
}

std::string transcribeNextStep(const std::string& destination, std::size_t chars) {
    return "Raw transcript saved to " + destination + " (" + std::to_string(chars) +
           " chars). Tell the user this path. Then, in order:\n"
           "1. Call refine_transcript('" + destination +
           "') — it fixes mis-hearings and labels the speakers, and returns the path of a better transcript.\n"
           "2. Call summarize_document on the file refine_transcript returns, for the summary and action items.\n"
           "Do NOT summarise this raw file, do NOT summarise from `preview` (it is the first 1200 characters "
           "only), and do NOT use query_documents for a summary — it returns only the top few matching chunks "
           "and would miss most of the meeting.";
}

const char* const kNoDiarization =
    "Speaker identification did not run, so this transcript has no speaker labels. Reason: this build "
    "(gaia-native) has no speaker diarization. Say this plainly in your reply rather than presenting the "
    "summary as if speakers were identified.";

using Turn = std::vector<json>;  // segments: {start, end, text}
using Named = std::vector<std::pair<std::string, std::string>>;

std::string turnText(const Turn& turn) {
    std::string out;
    for (const auto& seg : turn) out += (out.empty() ? "" : " ") + text(seg, "text");
    return trim(out);
}

std::vector<Turn> turnsFromGaps(const std::vector<json>& segments) {
    std::vector<Turn> turns;
    for (const auto& seg : segments) {
        if (!turns.empty() && dbl(seg, "start") - dbl(turns.back().back(), "end") < kTurnGapSeconds) {
            turns.back().push_back(seg);
        } else {
            turns.push_back({seg});
        }
    }
    return turns;
}

std::vector<Turn> turnsFromSpeakers(const std::vector<json>& segments, const json& spans, std::vector<std::string>& labels) {
    std::vector<Turn> turns;
    for (const auto& seg : segments) {
        std::string best;
        double bestOverlap = 0;
        for (const auto& sp : spans) {
            const double overlap = std::min(dbl(seg, "end"), dbl(sp, "end")) - std::max(dbl(seg, "start"), dbl(sp, "start"));
            if (overlap > bestOverlap) {
                best = text(sp, "speaker");
                bestOverlap = overlap;
            }
        }
        // A gap in the diarization is missing evidence, not a new person.
        if (best.empty()) best = labels.empty() ? "Speaker 1" : labels.back();
        if (!labels.empty() && best == labels.back()) {
            turns.back().push_back(seg);
        } else {
            turns.push_back({seg});
            labels.push_back(best);
        }
    }
    return turns;
}

std::vector<std::string> parseTurnLabels(const std::string& reply, std::size_t count) {
    std::map<std::size_t, std::string> found;
    std::istringstream in(reply);
    for (std::string line; std::getline(in, line);) {
        std::size_t i = line.find_first_not_of(" \t\r");
        if (i == std::string::npos || !std::isdigit(static_cast<unsigned char>(line[i]))) continue;
        std::size_t j = i;
        while (j < line.size() && std::isdigit(static_cast<unsigned char>(line[j]))) ++j;
        std::size_t index = 0;
        try {
            index = std::stoul(line.substr(i, j - i));
        } catch (const std::exception&) {
            continue;
        }
        while (j < line.size() && (line[j] == ' ' || line[j] == '\t')) ++j;
        if (j >= line.size() || (line[j] != ':' && line[j] != '.')) continue;
        std::string label = trim(line.substr(j + 1));
        while (!label.empty() && label.back() == ':') label.pop_back();
        label = trim(label);
        if (index >= 1 && index <= count && !label.empty()) found.emplace(index, label);
    }
    std::vector<std::string> labels;
    for (std::size_t i = 1; i <= count; ++i) {
        auto it = found.find(i);
        labels.push_back(it != found.end() ? it->second : (labels.empty() ? "Speaker A" : labels.back()));
    }
    return labels;
}

std::map<std::string, std::string> parseAliasMap(const std::string& reply, const std::set<std::string>& known) {
    std::map<std::string, std::string> mapping;
    auto strip = [](std::string s) {
        s = trim(s);
        s.erase(0, s.find_first_not_of('`'));
        s.erase(s.find_last_not_of('`') + 1);
        return s;
    };
    std::istringstream in(reply);
    for (std::string line; std::getline(in, line);) {
        const std::string t = trim(line);
        const auto arrow = t.find("->", 1);
        if (t.empty() || arrow == std::string::npos) continue;
        const std::string from = strip(t.substr(0, arrow)), to = strip(t.substr(arrow + 2));
        if (known.count(from) && !to.empty() && from != to) mapping[from] = to;
    }
    // A chain (A->B, B->C) resolves to its end.
    for (auto& [key, value] : mapping) {
        std::set<std::string> seen = {key};
        std::string v = value;
        while (mapping.count(v) && !seen.count(v)) {
            seen.insert(v);
            v = mapping[v];
        }
        value = v;
    }
    return mapping;
}

// Labels in first-appearance order, each with its texts.
std::vector<std::pair<std::string, std::vector<std::string>>> roster(const Named& named) {
    std::vector<std::pair<std::string, std::vector<std::string>>> out;
    for (const auto& [name, t] : named) {
        auto it = std::find_if(out.begin(), out.end(), [&](const auto& e) { return e.first == name; });
        if (it == out.end()) out.push_back({name, {t}});
        else it->second.push_back(t);
    }
    return out;
}

std::string rosterSample(const Named& named, std::size_t perLabel, std::size_t chars) {
    std::string sample;
    for (const auto& [label, texts] : roster(named)) {
        if (!sample.empty()) sample += "\n";
        sample += label + ": ";
        for (std::size_t i = 0; i < texts.size() && i < perLabel; ++i) sample += (i ? " / " : "") + firstChars(texts[i], chars);
    }
    return sample;
}

std::set<std::string> labelsOf(const Named& named) {
    std::set<std::string> out;
    for (const auto& [n, t] : named) out.insert(n);
    return out;
}

std::vector<std::string> distinctNames(const Named& named) {
    std::vector<std::string> out;
    for (const auto& [n, t] : named) {
        if (std::find(out.begin(), out.end(), n) == out.end()) out.push_back(n);
    }
    return out;
}

std::string renderRefined(const fs::path& source, const std::vector<std::string>& speakers,
                          const std::vector<std::string>& blocks, bool acoustic) {
    std::string key;
    for (const auto& s : speakers) key += (key.empty() ? "- " : "\n- ") + s;
    if (key.empty()) key = "- (none identified)";
    const std::string how =
        acoustic ? "Voices were separated from the audio itself. Any real names are inferred from what was said, so "
                   "treat the names — not the voice separation — as best-effort."
                 : "Turns were split on pauses in the recording and attributed from the conversation, without voice "
                   "identification, so treat the speaker labels as best-effort.";
    std::string body;
    for (std::size_t i = 0; i < blocks.size(); ++i) body += (i ? "\n\n" : "") + blocks[i];
    return "# Transcript — " + source.stem().u8string() + "\n\nSource: " + source.u8string() + "\n\n" + how +
           "\n\n## Speakers\n\n" + key + "\n\n## Transcript\n\n" + body + "\n";
}

}  // namespace

// ---------------------------------------------------------------------------
// PNG and screen capture
// ---------------------------------------------------------------------------

std::string encodePng(const std::vector<std::uint8_t>& rgb, int width, int height) {
    if (width <= 0 || height <= 0 || rgb.size() != static_cast<std::size_t>(width) * height * 3) {
        throw std::runtime_error("encodePng: pixel buffer does not match " + std::to_string(width) + "x" +
                                 std::to_string(height));
    }
    std::size_t size = 0;
    void* png = tdefl_write_image_to_png_file_in_memory_ex(rgb.data(), width, height, 3, &size, MZ_DEFAULT_LEVEL, MZ_FALSE);
    if (!png) throw std::runtime_error("PNG encoding failed (out of memory?)");
    std::string out(static_cast<const char*>(png), size);
    mz_free(png);
    return out;
}

std::vector<std::uint8_t> captureScreen(int& width, int& height) {
#ifdef _WIN32
    // Physical pixels, not the DPI-scaled ones an unaware process is shown.
    using SetCtx = HANDLE(WINAPI*)(HANDLE);
    auto setCtx = reinterpret_cast<SetCtx>(
        reinterpret_cast<void*>(GetProcAddress(GetModuleHandleW(L"user32.dll"), "SetThreadDpiAwarenessContext")));
    HANDLE previous = setCtx ? setCtx(reinterpret_cast<HANDLE>(static_cast<LONG_PTR>(-4))) : nullptr;
    struct Restore {
        SetCtx fn;
        HANDLE prev;
        ~Restore() {
            if (fn && prev) fn(prev);
        }
    } restore{setCtx, previous};

    const int x = GetSystemMetrics(SM_XVIRTUALSCREEN), y = GetSystemMetrics(SM_YVIRTUALSCREEN);
    width = GetSystemMetrics(SM_CXVIRTUALSCREEN);
    height = GetSystemMetrics(SM_CYVIRTUALSCREEN);
    if (width <= 0 || height <= 0) throw std::runtime_error("no desktop is attached to this session");
    HDC screen = GetDC(nullptr);
    if (!screen) throw std::runtime_error("GetDC failed (error " + std::to_string(GetLastError()) + ")");
    HDC mem = CreateCompatibleDC(screen);
    HBITMAP bmp = mem ? CreateCompatibleBitmap(screen, width, height) : nullptr;
    std::string failure;
    std::vector<std::uint8_t> bgra(static_cast<std::size_t>(width) * height * 4);
    if (!bmp) {
        failure = "could not allocate a " + std::to_string(width) + "x" + std::to_string(height) + " bitmap";
    } else {
        HGDIOBJ old = SelectObject(mem, bmp);
        if (!BitBlt(mem, 0, 0, width, height, screen, x, y, SRCCOPY | CAPTUREBLT)) {
            failure = "BitBlt failed (error " + std::to_string(GetLastError()) + "); the desktop may be locked";
        }
        SelectObject(mem, old);
        BITMAPINFO info{};
        info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
        info.bmiHeader.biWidth = width;
        info.bmiHeader.biHeight = -height;  // top-down rows
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        info.bmiHeader.biCompression = BI_RGB;
        if (failure.empty() && GetDIBits(mem, bmp, 0, static_cast<UINT>(height), bgra.data(), &info, DIB_RGB_COLORS) == 0) {
            failure = "GetDIBits failed (error " + std::to_string(GetLastError()) + ")";
        }
    }
    if (bmp) DeleteObject(bmp);
    if (mem) DeleteDC(mem);
    ReleaseDC(nullptr, screen);
    if (!failure.empty()) throw std::runtime_error(failure);
    std::vector<std::uint8_t> rgb(static_cast<std::size_t>(width) * height * 3);
    for (std::size_t i = 0, n = static_cast<std::size_t>(width) * height; i < n; ++i) {
        rgb[i * 3] = bgra[i * 4 + 2];
        rgb[i * 3 + 1] = bgra[i * 4 + 1];
        rgb[i * 3 + 2] = bgra[i * 4];
    }
    return rgb;
#else
    (void)width;
    (void)height;
    throw std::runtime_error(
        "this build captures the screen on Windows only; on this platform the Python flagship uses the mss or "
        "Pillow libraries, which gaia-native does not include");
#endif
}

// ---------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------

LemonadeClient& FlagshipTools::lemonade() {
    if (!lemonade_) throw std::runtime_error("No Lemonade server is configured for this session.");
    return *lemonade_;
}

json FlagshipTools::takeScreenshot(const json& args) {
    const std::string raw = trim(str(args, "output_path"));
    fs::path out;
    if (raw.empty()) {
        const fs::path dir = ctx_.screenshotDir.empty() ? homeGaia("screenshots") : fs::u8path(ctx_.screenshotDir);
        out = dir / ("screenshot_" + localTime("%Y%m%d_%H%M%S") + ".png");
    } else {
        const std::string resolved = resolveAllowed(raw);
        if (resolved.empty()) return error("'" + raw + "' is outside the folders GAIA may write.");
        if (auto why = writeTargetRefusal(resolved); !why.empty()) return error(why);
        out = fs::u8path(resolved);
    }
    int width = 0, height = 0;
    std::string png;
    try {
        png = encodePng(captureScreen(width, height), width, height);
    } catch (const std::runtime_error& e) {
        return error(std::string("Screenshot capture failed: ") + e.what() + ".");
    }
    backup(out.u8string());
    writeWhole(out, png);
    return {{"status", "success"}, {"file_path", out.u8string()}, {"width", width}, {"height", height}, {"method", "gdi"}};
}

json FlagshipTools::listSdModels(const json&) {
    return {{"models",
             json::array({{{"name", "SD-Turbo"}, {"description", "Very fast, 512x512, 4 steps"}, {"recommended_steps", 4},
                           {"recommended_size", "512x512"}, {"speed", "~13s"}},
                          {{"name", "SDXL-Turbo"}, {"description", "Fast stylized, 512x512, 4 steps"}, {"recommended_steps", 4},
                           {"recommended_size", "512x512"}, {"speed", "~17s"}},
                          {{"name", "SD-1.5"}, {"description", "Legacy general purpose, 512x512, 20 steps"},
                           {"recommended_steps", 20}, {"recommended_size", "512x512"}, {"speed", "~88s"}},
                          {{"name", "SDXL-Base-1.0"}, {"description", "Photorealistic, 1024x1024, 20 steps (slow)"},
                           {"recommended_steps", 20}, {"recommended_size", "1024x1024"}, {"speed", "~9min"}}})},
            {"default_model", kSdDefaultModel}};
}

json FlagshipTools::getGenerationHistory(const json& args) {
    std::lock_guard<std::mutex> lock(mediaMutex_);
    long long limit = 10;
    try {
        if (auto n = number(args, "limit")) limit = static_cast<long long>(*n);
    } catch (const std::invalid_argument& e) {
        return error(e.what());
    }
    const std::size_t total = generations_.size();
    const std::size_t show = limit > 0 ? std::min<std::size_t>(total, static_cast<std::size_t>(limit)) : total;
    json recent = json::array();
    for (std::size_t i = total - show; i < total; ++i) recent.push_back(generations_[i]);
    return {{"total_generations", total}, {"showing", show}, {"generations", recent}};
}

namespace {

// Python's _describe_client_error. A refused connection is checked first: its
// text names the connect timeout, and a live-but-slow server must not read as down.
std::string describeSdError(const std::string& raw, const std::string& model) {
    const std::string l = lower(raw);
    if (l.find("could not connect") != std::string::npos || l.find("not reachable") != std::string::npos) {
        return "Cannot reach Lemonade Server; start it, or set LEMONADE_BASE_URL to a running server. (" + raw + ")";
    }
    if (l.find("timed out") != std::string::npos || l.find("timeout") != std::string::npos) {
        return "Timed out waiting for " + model +
               "; the server is running but did not answer in time. First use of an SD model both downloads and "
               "loads several GB. Pre-fetch " + model + " in Lemonade, confirm it loads, then retry. (" + raw + ")";
    }
    return "Image generation failed for " + model + ": " + raw;
}

}  // namespace

json FlagshipTools::generateImage(const json& args) {
    const std::string prompt = str(args, "prompt");
    if (trim(prompt).empty()) return error("prompt is required");
    std::string model = trim(str(args, "model"));
    if (model.empty()) model = kSdDefaultModel;
    if (std::find(kSdModels.begin(), kSdModels.end(), model) == kSdModels.end()) {
        return error("Invalid model '" + model + "'. Choose from: " + pyList(kSdModels));
    }
    const SdDefaults& d = kSdModelDefaults.at(model);
    std::string size = trim(str(args, "size"));
    if (size.empty()) size = d.size;
    std::optional<double> steps, cfg, seedArg;
    try {
        steps = number(args, "steps");
        cfg = number(args, "cfg_scale");
        seedArg = number(args, "seed");
    } catch (const std::invalid_argument& e) {
        return error(e.what());
    }
    const int stepCount = steps ? static_cast<int>(*steps) : d.steps;
    const double cfgScale = cfg ? *cfg : d.cfg;
    if (std::find(kSdSizes.begin(), kSdSizes.end(), size) == kSdSizes.end()) {
        return error("Invalid size '" + size + "'. Choose from: " + pyList(kSdSizes));
    }
    std::uint64_t seed = 0;
    if (seedArg) {
        seed = static_cast<std::uint64_t>(*seedArg);
    } else {
        std::random_device rd;
        seed = std::uniform_int_distribution<std::uint64_t>(0, 0xFFFFFFFFull)(rd);
    }

    LemonadeClient& client = lemonade();
    try {
        // Python's load_model(auto_download=True): pull first when the catalog says it is not on disk.
        const json catalog = client.listModels(true);
        if (catalog.contains("data") && catalog["data"].is_array()) {
            for (const auto& m : catalog["data"]) {
                if (m.is_object() && m.value("id", "") == model && m.contains("downloaded") && m["downloaded"] == false) {
                    client.pullModel(model);
                }
            }
        }
        try {
            client.loadModel(model, 0, 600);
        } catch (const std::exception& e) {
            if (lower(e.what()).find("already loaded") == std::string::npos) throw;
        }
    } catch (const std::exception& e) {
        return error(describeSdError(e.what(), model));
    }

    const json payload = {{"prompt", prompt}, {"model", model}, {"size", size}, {"n", 1}, {"response_format", "b64_json"},
                          {"cfg_scale", cfgScale}, {"steps", stepCount}, {"seed", seed}};
    // SDXL-Base-1.0 at 1024px takes ~9 minutes.
    const int timeout = (model.find("Base") != std::string::npos && size == "1024x1024") ? 900 : 300;
    const auto start = std::chrono::steady_clock::now();
    std::string image;
    try {
        const json response = client.generateImage(payload, timeout);
        if (!response.contains("data") || !response["data"].is_array() || response["data"].empty() ||
            !response["data"][0].contains("b64_json") || !response["data"][0]["b64_json"].is_string()) {
            return error("Image generation failed: Lemonade's reply has no data[0].b64_json: " + response.dump().substr(0, 300));
        }
        image = base64Decode(response["data"][0]["b64_json"].get<std::string>());
    } catch (const std::exception& e) {
        return error(describeSdError(e.what(), model));
    }
    if (image.empty()) return error("Image generation failed: Lemonade returned an empty image.");
    const double seconds = round(std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count(), 1);

    std::string safe;
    for (unsigned char c : firstChars(prompt, 40)) {
        if (std::isalnum(c) || c == '_' || c == '-' || c >= 0x80) safe += static_cast<char>(c);
        else if (std::isspace(c)) safe += ' ';
    }
    safe = trim(safe);
    std::replace(safe.begin(), safe.end(), ' ', '_');
    const std::string hash = FileStateTracker::hashContent(image);
    const fs::path dir = !ctx_.imageDir.empty() ? fs::u8path(ctx_.imageDir)
                         : !getEnvVar("GAIA_CONFIG_DIR").empty()
                             ? absolutePath(getEnvVar("GAIA_CONFIG_DIR"), ctx_.workDir) / "cache" / "sd" / "images"
                             : fs::u8path(gaiaHomeDir()) / "cache" / "sd" / "images";
    // The content hash keeps two fast Turbo runs in the same second apart.
    const fs::path path = dir / (safe + "_" + model + "_" + localTime("%Y%m%d_%H%M%S") + "_" + hash.substr(0, 8) + ".png");
    backup(path.u8string());
    writeWhole(path, image);

    json result = {{"status", "success"}, {"image_path", path.u8string()}, {"prompt", prompt}, {"model", model},
                   {"size", size}, {"steps", stepCount}, {"seed", seed}, {"image_hash", hash.substr(0, 16)},
                   {"generation_time_s", seconds}};
    std::lock_guard<std::mutex> lock(mediaMutex_);
    json entry = result;
    entry["created_at"] = localTime("%Y-%m-%dT%H:%M:%S");
    generations_.push_back(std::move(entry));
    return result;
}

std::string FlagshipTools::transcriptFolder() const {
    return ctx_.transcriptDir.empty() ? homeGaia("transcripts").u8string() : ctx_.transcriptDir;
}

// GAIA's transcript folder is exempt: the follow-up calls must read back what transcribe_media wrote.
json FlagshipTools::transcriptAccessError(const std::string& path, bool write) const {
    std::error_code ec;
    fs::path folder = fs::weakly_canonical(fs::u8path(transcriptFolder()), ec);
    if (ec) folder = fs::u8path(transcriptFolder());
    if (inside(fs::u8path(path), folder)) return nullptr;
    if (resolveAllowed(path).empty()) {
        return error("'" + path + "' is outside the folders GAIA may " + (write ? "write." : "read."));
    }
    const std::string why = write ? writeTargetRefusal(path) : credentialRefusal(path);
    return why.empty() ? json(nullptr) : error(why);
}

json FlagshipTools::transcribeMedia(const json& args) {
    const std::string raw = str(args, "file_path");
    if (trim(raw).empty()) return error("file_path is required");
    const fs::path source = absolutePath(raw, ctx_.workDir);
    const std::string outRaw = trim(str(args, "output_path"));
    const fs::path destination = outRaw.empty() ? fs::u8path(transcriptFolder()) / (source.stem().u8string() + ".txt")
                                                : absolutePath(outRaw, ctx_.workDir);
    if (json denied = transcriptAccessError(source.u8string(), false); !denied.is_null()) return denied;
    if (!outRaw.empty()) {
        if (json denied = transcriptAccessError(destination.u8string(), true); !denied.is_null()) return denied;
    }
    std::error_code ec;
    if (!fs::is_regular_file(source, ec)) {
        return error("No such file: " + source.u8string() + ". Give the full path to an audio or video file.");
    }
    const fs::path timings = fs::u8path(destination.u8string() + ".timing.json");

    // A follow-up question about the same recording must not redo minutes of decode and transcription.
    if (fs::is_regular_file(destination, ec) && fs::is_regular_file(timings, ec) &&
        fs::last_write_time(source, ec) <= fs::last_write_time(destination, ec)) {
        std::string existing;
        if (readWhole(destination, existing) && !trim(existing).empty()) {
            const std::string d = destination.u8string();
            return {{"status", "success"}, {"transcript_path", d}, {"reused", true}, {"language", ""},
                    {"character_count", codePoints(existing)}, {"preview", firstChars(existing, kPreviewChars)},
                    {"low_confidence_spans", json::array()}, {"low_confidence_span_count", 0},
                    {"next_step", "This recording was already transcribed; " + d +
                                      " is the existing transcript and nothing was re-run.\n\nIf you are answering a "
                                      "question about the meeting, the transcript is already indexed — use "
                                      "query_documents rather than calling this tool again. If you still need the "
                                      "speaker-labelled version, call refine_transcript('" + d + "')."}};
        }
    }

    std::string model = trim(str(args, "model"));
    if (model.empty()) model = kAsrModel;
    static const std::regex flm("(?:^|[-_.])flm(?:$|[-_.])", std::regex::icase);
    if (std::regex_search(model, flm)) {
        return error("Model '" + model + "' runs on the FLM (NPU) backend, which returns no segment or word "
                     "timestamps, so it cannot produce the timing this call requires. Use a whispercpp model such as '" +
                     std::string(kAsrModel) + "'.");
    }
    const std::string language = trim(str(args, "language"));

    std::string wav;
    if (!readWhole(source, wav)) return error("Cannot open " + source.u8string());
    Wav info;
    std::string wavName = source.filename().u8string();
    if (!parseWav(wav, info) || !isAsrReady(info)) {
        // Anything but 16 kHz mono PCM WAV needs ffmpeg, as in Python.
        const std::string ffmpeg = findFfmpeg();
        if (ffmpeg.empty()) {
            return error("ffmpeg is required to decode audio and video but is not installed. " + ffmpegInstallHint() +
                         " Then run this again. See " + kMediaDocs);
        }
        const fs::path scratch = fs::temp_directory_path(ec) /
                                 ("gaia-media-" + FileStateTracker::hashContent(source.u8string() + localTime("%H%M%S")).substr(0, 12));
        fs::create_directories(scratch, ec);
        const fs::path out = scratch / "audio.16k.wav";
        std::string command = quoted(ffmpeg) + " -y -nostdin -loglevel error -i " + quoted(source.u8string()) +
                              " -vn -ar 16000 -ac 1 -c:a pcm_s16le " + quoted(out.u8string());
#ifdef _WIN32
        command = "\"" + command + "\"";  // cmd /c strips one outer pair of quotes
#endif
        const ProcessResult r = ProcessRunner::run(command, 2 * 3600 * 1000, "", {}, 64 * 1024, ctx_.cancelled);
        wav.clear();
        const bool decoded = r.exitCode == 0 && readWhole(out, wav) && !wav.empty();
        fs::remove_all(scratch, ec);
        if (r.cancelled) return error("Transcription of " + source.u8string() + " was cancelled.");
        if (r.exitCode != 0) {
            return error("ffmpeg failed to decode " + source.u8string() + " (exit " + std::to_string(r.exitCode) +
                         "): " + trim(r.stderr_output).substr(0, 500) +
                         "\nVerify the file plays locally and contains an audio stream. See " + kMediaDocs);
        }
        if (!decoded || !parseWav(wav, info)) {
            return error("ffmpeg exited successfully but produced no audio from " + source.u8string() +
                         ". The file may have no audio stream.");
        }
        wavName = "audio.16k.wav";
    }
    const double seconds = static_cast<double>(info.dataSize) / (info.rate * std::max(1, info.blockAlign));

    std::vector<std::pair<std::string, std::string>> fields = {{"model", model}, {"response_format", "verbose_json"}};
    if (!language.empty()) fields.emplace_back("language", language);
    const std::vector<Chunk> chunks = chunkPlan(seconds);
    std::vector<Transcript> parts;
    try {
        for (const auto& c : chunks) {
            if (ctx_.cancelled && ctx_.cancelled()) return error("Transcription of " + source.u8string() + " was cancelled.");
            std::string body;
            if (chunks.size() == 1) {
                body = std::move(wav);
            } else {
                const std::size_t from = static_cast<std::size_t>(c.start * info.rate) * info.blockAlign;
                const std::size_t to = std::min(info.dataSize, static_cast<std::size_t>(c.end * info.rate) * info.blockAlign);
                body = wavHeader(to - from) + wav.substr(info.dataOffset + from, to - from);
            }
            parts.push_back(parseTranscript(lemonade().transcribeAudio(body, wavName, fields), model));
        }
    } catch (const std::exception& e) {
        return error(e.what());
    }
    const Transcript transcript = mergeChunks(parts, chunks);
    const std::string full = transcript.fullText();

    try {
        backup(destination.u8string());
        writeWhole(destination, full);
    } catch (const std::exception& e) {
        return error(e.what());
    }
    json result = {{"status", "success"}, {"transcript_path", destination.u8string()}};
    try {
        json segs = json::array();
        for (const auto& s : transcript.segments) {
            if (trim(s.text).empty()) continue;
            segs.push_back({{"start", round(s.start, 3)}, {"end", round(s.end, 3)}, {"text", trim(s.text)}});
        }
        backup(timings.u8string());
        writeWhole(timings, json{{"duration", transcript.duration}, {"speakers", json::array()}, {"segments", segs}}.dump());
    } catch (const std::exception& e) {
        // The transcript is the artifact; without timings refine_transcript says so.
        result["timings_error"] = e.what();
    }
    json spans;
    try {
        spans = lowConfidenceSpans(transcript);
    } catch (const std::exception& e) {
        json failure = error(e.what());
        failure["transcript_path"] = destination.u8string();
        failure["note"] = "The transcript itself was saved and is complete; the failure happened after it was written.";
        return failure;
    }
    json reported = json::array();
    for (std::size_t i = 0; i < spans.size() && i < kMaxReportedSpans; ++i) reported.push_back(spans[i]);
    result["audio_duration"] = clockText(seconds);
    result["language"] = transcript.language;
    result["model"] = transcript.model;
    result["segment_count"] = transcript.segments.size();
    result["character_count"] = codePoints(full);
    result["preview"] = firstChars(full, kPreviewChars);
    result["next_step"] = transcribeNextStep(destination.u8string(), codePoints(full));
    result["low_confidence_spans"] = reported;
    result["low_confidence_span_count"] = spans.size();
    result["speaker_identification"] = kNoDiarization;
    return result;
}

json FlagshipTools::nameTurns(const std::vector<Turn>& batch, std::vector<std::string>& speakers) {
    std::vector<std::string> texts;
    for (const auto& t : batch) texts.push_back(turnText(t));
    std::string listing;
    for (std::size_t i = 0; i < batch.size(); ++i) {
        if (i) {
            char gap[32];
            std::snprintf(gap, sizeof gap, "%.1f", std::max(dbl(batch[i].front(), "start") - dbl(batch[i - 1].back(), "end"), 0.0));
            listing += "\n" + std::to_string(i + 1) + ". [pause " + gap + "s] " + texts[i];
        } else {
            listing += "1. " + texts[i];
        }
    }
    std::string known;
    if (!speakers.empty()) {
        std::string names;
        for (const auto& s : speakers) names += (names.empty() ? "" : ", ") + s;
        known = "\nSpeakers already identified in this meeting: " + names +
                ". Reuse those exact labels for the same people — do not invent a new label for someone already listed.";
    }
    const std::string prompt =
        "Below are consecutive turns from a meeting transcript. The turns are already split correctly — your only "
        "job is to say WHO speaks each one.\n\nMost meetings have 2 to 5 people. Consecutive turns are often the SAME "
        "person continuing — keep the same label unless there is a reason to change. The `[pause Xs]` before a turn "
        "is how long the silence was: under about 1.5s usually means the same person carried on, and a longer pause "
        "makes a new speaker more likely but does not prove one. Weigh it together with whether the content reads as "
        "a reply or a continuation. Use a real name only if the transcript supports it (a self-introduction, or "
        "someone addressed by name). Otherwise use Speaker A, Speaker B, and so on. Never use more than " +
        std::to_string(kMaxSpeakers) + " distinct labels." + known +
        "\n\nOutput ONLY lines of the form `N: Name`, one per turn, for every turn number below. Example:\n1: "
        "Speaker A\n2: Speaker A\n3: Priya\n\nTurns:\n" + listing;
    std::string reply;
    try {
        reply = ctx_.complete(prompt);
    } catch (const std::runtime_error& e) {
        // A reasoning model can spend its reply budget thinking; halves concatenate.
        if (std::string(e.what()).find("finish_reason") == std::string::npos || batch.size() <= kMinTurnsPerCall) throw;
        const std::size_t mid = batch.size() / 2;
        json first = nameTurns(std::vector<Turn>(batch.begin(), batch.begin() + mid), speakers);
        json second = nameTurns(std::vector<Turn>(batch.begin() + mid, batch.end()), speakers);
        for (auto& p : second) first.push_back(std::move(p));
        return first;
    }
    const std::vector<std::string> labels = parseTurnLabels(reply, texts.size());
    for (const auto& name : labels) {
        if (std::find(speakers.begin(), speakers.end(), name) == speakers.end() && speakers.size() < kMaxSpeakers) {
            speakers.push_back(name);
        }
    }
    // A label past the cap is a hallucinated extra voice; it joins the previous speaker.
    json named = json::array();
    for (std::size_t i = 0; i < labels.size(); ++i) {
        std::string name = labels[i];
        if (std::find(speakers.begin(), speakers.end(), name) == speakers.end()) {
            name = named.empty() ? "Speaker A" : named.back()[0].get<std::string>();
        }
        named.push_back(json::array({name, texts[i]}));
    }
    return named;
}

json FlagshipTools::refineTranscript(const json& args) {
    const std::string raw = str(args, "transcript_path");
    if (trim(raw).empty()) return error("transcript_path is required");
    if (!ctx_.complete) return error("refine_transcript needs the session's model, and none is connected.");
    const fs::path source = absolutePath(raw, ctx_.workDir);
    const std::string outRaw = trim(str(args, "output_path"));
    fs::path destination = absolutePath(outRaw, ctx_.workDir);
    if (outRaw.empty()) {
        destination = source;
        destination.replace_extension(".transcript.md");
    }
    if (json denied = transcriptAccessError(source.u8string(), false); !denied.is_null()) return denied;
    if (json denied = transcriptAccessError(destination.u8string(), true); !denied.is_null()) return denied;
    std::error_code ec;
    if (!fs::is_regular_file(source, ec)) {
        return error("No transcript at " + source.u8string() +
                     ". Run transcribe_media first and pass the transcript_path it returns.");
    }
    std::string body;
    if (!readWhole(source, body)) return error("Cannot open " + source.u8string());
    if (trim(body).empty()) return error(source.u8string() + " is empty.");

    const fs::path timingsFile = fs::u8path(source.u8string() + ".timing.json");
    std::vector<json> segments;
    json speakerSpans = json::array();
    std::string timingsText;
    if (readWhole(timingsFile, timingsText)) {
        const json t = json::parse(timingsText, nullptr, false);
        if (t.is_object() && t.contains("segments") && t["segments"].is_array()) {
            for (const auto& s : t["segments"]) segments.push_back(s);
        }
        if (t.is_object() && t.contains("speakers") && t["speakers"].is_array()) speakerSpans = t["speakers"];
    }
    if (segments.empty()) {
        return error("No segment timings found next to " + source.u8string() + " (expected " +
                     timingsFile.filename().u8string() +
                     "). Speaker turns are derived from pauses in the recording; without them the result would be "
                     "invented. Re-run transcribe_media on the original media file to regenerate both files.");
    }

    const bool acoustic = !speakerSpans.empty();
    std::vector<std::string> acousticLabels;
    const std::vector<Turn> turns = acoustic ? turnsFromSpeakers(segments, speakerSpans, acousticLabels) : turnsFromGaps(segments);
    Named named;
    try {
        if (acoustic) {
            // Voices are already separated; only the names are unknown.
            for (std::size_t i = 0; i < turns.size(); ++i) named.emplace_back(acousticLabels[i], turnText(turns[i]));
            const std::string prompt =
                "Each label below is a distinct voice from a meeting recording, with a few things that voice said.\n\n"
                "Give each one a real name ONLY if the transcript supports it — the person introduces themselves, or "
                "someone addresses them by name. If there is no evidence, keep the label exactly as it is. A "
                "confidently wrong name is worse than an anonymous one.\n\nOutput ONLY lines of the form `Label -> "
                "Name`, one per label, including labels that stay unchanged.\n\nVoices:\n" + rosterSample(named, 4, 150);
            auto mapping = parseAliasMap(ctx_.complete(prompt), labelsOf(named));
            // Never let naming merge two distinct voices into one person.
            std::map<std::string, int> uses;
            for (const auto& [k, v] : mapping) ++uses[v];
            for (auto& [label, t] : named) {
                auto it = mapping.find(label);
                if (it != mapping.end() && uses[it->second] == 1) label = it->second;
            }
        } else {
            std::vector<std::string> speakers;
            for (std::size_t i = 0; i < turns.size(); i += kTurnsPerCall) {
                if (ctx_.cancelled && ctx_.cancelled()) return error("Refinement of " + source.u8string() + " was cancelled.");
                const std::vector<Turn> batch(turns.begin() + i, turns.begin() + std::min(turns.size(), i + kTurnsPerCall));
                for (const auto& p : nameTurns(batch, speakers)) named.emplace_back(p[0].get<std::string>(), p[1].get<std::string>());
            }
            // Batched naming over-splits; one pass over the whole roster folds duplicates together.
            if (roster(named).size() >= 3) {
                const std::string prompt =
                    "A meeting transcript was labelled in batches, so the same person may have been given more than "
                    "one label. Below is each label with a few of its lines.\n\nDecide which labels are the SAME "
                    "person. Merge freely — most meetings have 2 to 5 people, and splitting one person across labels "
                    "is the common error here.\n\nOutput ONLY lines of the form `OldLabel -> FinalLabel`, one per "
                    "label, including labels that stay as they are. Use a real name as the final label when one is "
                    "evident.\n\nLabels:\n" + rosterSample(named, 3, 120);
                const auto mapping = parseAliasMap(ctx_.complete(prompt), labelsOf(named));
                for (auto& [label, t] : named) {
                    auto it = mapping.find(label);
                    if (it != mapping.end()) label = it->second;
                }
            }
        }
    } catch (const std::exception& e) {
        json failure = error(std::string("Speaker identification failed: ") + e.what() + ". The raw transcript at " +
                             source.u8string() + " and its timings are unchanged; fix the cause and call "
                             "refine_transcript on it again.");
        failure["source_transcript"] = source.u8string();
        return failure;
    }

    Named merged;
    for (const auto& [name, t] : named) {
        if (!merged.empty() && merged.back().first == name) merged.back().second = trim(merged.back().second + " " + t);
        else merged.emplace_back(name, t);
    }
    std::vector<std::string> blocks;
    for (const auto& [name, t] : merged) blocks.push_back(name + ": " + t);
    const std::vector<std::string> speakers = distinctNames(named);
    try {
        backup(destination.u8string());
        writeWhole(destination, renderRefined(source, speakers, blocks, acoustic));
    } catch (const std::exception& e) {
        return error(e.what());
    }

    // Indexed here, not left to the model: summarize_document and follow-up questions need it.
    bool indexed = false;
    std::string indexError;
    if (!ctx_.indexDocument) {
        indexError = "document indexing is not available in this session";
    } else {
        try {
            const json r = ctx_.indexDocument(destination.u8string());
            indexed = r.is_object() && r.value("status", "") == "success";
            if (!indexed) indexError = r.is_object() ? r.value("error", r.dump()) : r.dump();
        } catch (const std::exception& e) {
            indexError = e.what();
        }
    }
    const std::string d = destination.u8string();
    json result = {{"status", "success"}, {"refined_path", d}, {"speakers", speakers}, {"turns", blocks.size()},
                   {"indexed", indexed}, {"source_transcript", source.u8string()},
                   {"next_step",
                    "Speaker-labelled transcript saved to " + d +
                        " and indexed, so questions about this meeting can now be answered from it.\n\nDo these two "
                        "things, in this order:\n1. Call summarize_document('" + d +
                        "', summary_type='detailed') and build the brief and action items from what it returns. It "
                        "folds the whole transcript forward in sections, so the brief covers the entire meeting. Do "
                        "NOT use query_documents for this — it returns only the top few matching chunks, so the brief "
                        "would silently miss most of what was said.\n2. Tell the user where both files are, and that "
                        "they can now ask questions about specific details of the meeting.\n\nAnswering with a "
                        "question instead of a summary is not acceptable here: the user already asked for one."}};
    if (!indexed) result["index_error"] = indexError;
    if (!acoustic) {
        result["speaker_identification"] =
            "This build has no voice separation: turns were split on pauses and named from what was said, so the "
            "speaker labels are best-effort guesses, not identified voices. Say so when you report who said what.";
    }
    return result;
}

void FlagshipTools::registerMedia(ToolRegistry& registry) {
    using T = ToolParamType;
    auto add = [&](const std::string& name, const std::string& desc, std::vector<ToolParameter> params,
                   json (FlagshipTools::*fn)(const json&)) {
        ToolInfo info;
        info.name = name;
        info.description = desc;
        info.parameters = std::move(params);
        info.callback = [this, fn](const json& args) -> json {
            try {
                return (this->*fn)(args);
            } catch (const std::exception& e) {
                return error(e.what());
            }
        };
        registry.registerTool(std::move(info));
    };
    add("take_screenshot",
        "Capture a screenshot of the current screen and save it to a PNG file (default "
        "~/.gaia/screenshots/screenshot_<timestamp>.png). Returns file_path, width and height.",
        {{"output_path", T::STRING, false, "Where to save the PNG"}}, &FlagshipTools::takeScreenshot);
    if (!lemonade_) return;
    add("generate_image",
        "Generate an image from a text prompt using Stable Diffusion. Returns image_path.",
        {{"prompt", T::STRING, true, "Text description of the image. Be detailed for best results."},
         {"model", T::STRING, false,
          "SDXL-Turbo (default), SD-Turbo (faster, lower quality), SDXL-Base-1.0 (photorealistic, slow), or SD-1.5"},
         {"size", T::STRING, false, "512x512, 768x768, or 1024x1024"},
         {"steps", T::INTEGER, false, "Inference steps (4 recommended for Turbo models)"},
         {"cfg_scale", T::NUMBER, false, "CFG scale (1.0 for Turbo, 7.5 for Base)"},
         {"seed", T::INTEGER, false, "Random seed for reproducibility"}},
        &FlagshipTools::generateImage);
    add("list_sd_models", "List available SD models.", {}, &FlagshipTools::listSdModels);
    add("get_generation_history", "Get recent generations from this session.",
        {{"limit", T::INTEGER, false, "How many (default 10)"}}, &FlagshipTools::getGenerationHistory);
    add("transcribe_media",
        "Transcribe an audio or video file and SAVE it to a text file. Takes any format ffmpeg decodes (mp4, mkv, "
        "m4a, mp3, wav). Returns a path and a 1200-character preview, never the transcript: to summarize it, "
        "index_document(transcript_path) then summarize_document or query_documents. A 45-minute recording takes "
        "about 5 minutes.",
        {{"file_path", T::STRING, true, "Path to the audio or video file"},
         {"language", T::STRING, false, "ISO 639-1 code such as \"en\". Empty auto-detects."},
         {"model", T::STRING, false, "Lemonade transcription model. Empty uses the default."},
         {"output_path", T::STRING, false, "Where to write the transcript. Empty writes to ~/.gaia/transcripts/<name>.txt"}},
        &FlagshipTools::transcribeMedia);
    if (ctx_.complete) {
        add("refine_transcript",
            "Correct mis-hearings and label the speakers in a raw transcript. Call on the file transcribe_media "
            "produced, BEFORE summarizing. Writes a new corrected file — summarize that one, not the raw one.",
            {{"transcript_path", T::STRING, true, "The raw transcript from transcribe_media"},
             {"output_path", T::STRING, false, "Where to write the refined transcript. Empty writes alongside as <name>.transcript.md"}},
            &FlagshipTools::refineTranscript);
    }
}

}  // namespace gaia
