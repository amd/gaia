// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/file_tools.h"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <vector>

#include "gaia/ignore.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

// ---------------------------------------------------------------------------
// SHA-256 (FIPS 180-4) — self-contained so the framework picks up no new
// dependency for content hashing.
// ---------------------------------------------------------------------------

class Sha256 {
public:
    Sha256() { reset(); }

    void reset() {
        state_ = {0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
                  0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u};
        bufferLen_ = 0;
        totalBits_ = 0;
    }

    void update(const char* data, size_t len) {
        totalBits_ += static_cast<std::uint64_t>(len) * 8u;
        append(reinterpret_cast<const unsigned char*>(data), len);
    }

    std::string hex() {
        // Pad: 0x80, zeros, then the 64-bit big-endian bit count. The padding
        // is appended without touching totalBits_ — it is framing, not message.
        const std::uint64_t bits = totalBits_;

        std::array<unsigned char, 72> pad{};
        pad[0] = 0x80;
        const size_t padLen = (bufferLen_ < 56) ? (56 - bufferLen_)
                                                : (120 - bufferLen_);
        append(pad.data(), padLen);

        std::array<unsigned char, 8> lenBytes{};
        for (int i = 0; i < 8; ++i) {
            lenBytes[static_cast<size_t>(i)] =
                static_cast<unsigned char>((bits >> (56 - 8 * i)) & 0xffu);
        }
        append(lenBytes.data(), 8);

        static const char* kHex = "0123456789abcdef";
        std::string out;
        out.reserve(64);
        for (std::uint32_t word : state_) {
            for (int shift = 24; shift >= 0; shift -= 8) {
                const unsigned byte = (word >> shift) & 0xffu;
                out.push_back(kHex[byte >> 4]);
                out.push_back(kHex[byte & 0x0fu]);
            }
        }
        return out;
    }

private:
    static std::uint32_t rotr(std::uint32_t x, int n) {
        return (x >> n) | (x << (32 - n));
    }

    void append(const unsigned char* data, size_t len) {
        while (len > 0) {
            const size_t take = std::min(len, size_t{64} - bufferLen_);
            std::memcpy(buffer_.data() + bufferLen_, data, take);
            bufferLen_ += take;
            data += take;
            len -= take;
            if (bufferLen_ == 64) {
                transform(buffer_.data());
                bufferLen_ = 0;
            }
        }
    }

    void transform(const unsigned char* block) {
        static const std::uint32_t K[64] = {
            0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu,
            0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u, 0xd807aa98u, 0x12835b01u,
            0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u,
            0xc19bf174u, 0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu,
            0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau, 0x983e5152u,
            0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u,
            0x06ca6351u, 0x14292967u, 0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu,
            0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
            0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u, 0xd192e819u,
            0xd6990624u, 0xf40e3585u, 0x106aa070u, 0x19a4c116u, 0x1e376c08u,
            0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu,
            0x682e6ff3u, 0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u,
            0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u};

        std::uint32_t w[64];
        for (int i = 0; i < 16; ++i) {
            const size_t o = static_cast<size_t>(i) * 4;
            w[i] = (static_cast<std::uint32_t>(block[o]) << 24) |
                   (static_cast<std::uint32_t>(block[o + 1]) << 16) |
                   (static_cast<std::uint32_t>(block[o + 2]) << 8) |
                   (static_cast<std::uint32_t>(block[o + 3]));
        }
        for (int i = 16; i < 64; ++i) {
            const std::uint32_t s0 =
                rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
            const std::uint32_t s1 =
                rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }

        std::uint32_t a = state_[0], b = state_[1], c = state_[2], d = state_[3];
        std::uint32_t e = state_[4], f = state_[5], g = state_[6], h = state_[7];

        for (int i = 0; i < 64; ++i) {
            const std::uint32_t S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
            const std::uint32_t ch = (e & f) ^ (~e & g);
            const std::uint32_t t1 = h + S1 + ch + K[i] + w[i];
            const std::uint32_t S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
            const std::uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
            const std::uint32_t t2 = S0 + maj;

            h = g; g = f; f = e; e = d + t1;
            d = c; c = b; b = a; a = t1 + t2;
        }

        state_[0] += a; state_[1] += b; state_[2] += c; state_[3] += d;
        state_[4] += e; state_[5] += f; state_[6] += g; state_[7] += h;
    }

    std::array<std::uint32_t, 8> state_{};
    std::array<unsigned char, 64> buffer_{};
    size_t bufferLen_ = 0;
    std::uint64_t totalBits_ = 0;
};

/// Short prefix used in error messages — 12 hex chars is plenty to name a
/// divergence and keeps the message readable.
std::string shortHash(const std::string& full) {
    return full.size() > 12 ? full.substr(0, 12) : full;
}

/// Canonical map key so "./a.txt", "a.txt" and "/abs/a.txt" share a record.
std::string trackerKey(const std::string& path) {
    std::error_code ec;
    fs::path p = fs::weakly_canonical(fs::path(path), ec);
    if (ec || p.empty()) {
        p = fs::absolute(fs::path(path), ec);
        if (ec) p = fs::path(path);
    }
    return p.generic_string();
}

/// Read a whole file as raw bytes. Returns false when it cannot be opened.
bool readWholeFile(const std::string& path, std::string& out) {
    std::ifstream file(path, std::ios::binary);
    if (!file.is_open()) return false;
    std::ostringstream buffer;
    buffer << file.rdbuf();
    out = buffer.str();
    return true;
}

/// Locate a whitespace-insensitive near-match for `needle`, so a failed edit
/// can say *why* it failed instead of just "not found".
std::string collapseWhitespace(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    bool pendingSpace = false;
    for (char c : s) {
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n') {
            pendingSpace = !out.empty();
            continue;
        }
        if (pendingSpace) {
            out.push_back(' ');
            pendingSpace = false;
        }
        out.push_back(c);
    }
    return out;
}

} // namespace

// ---------------------------------------------------------------------------
// FileStateTracker
// ---------------------------------------------------------------------------

struct FileStateTracker::Impl {
    struct Record {
        std::string hash;
        std::uint64_t size = 0;
    };
    mutable std::mutex mutex;
    std::map<std::string, Record> records;
};

FileStateTracker::Impl& FileStateTracker::impl() {
    static Impl storage;
    return storage;
}

FileStateTracker& FileStateTracker::instance() {
    static FileStateTracker tracker;
    return tracker;
}

std::string FileStateTracker::hashContent(const std::string& contents) {
    Sha256 sha;
    sha.update(contents.data(), contents.size());
    return sha.hex();
}

std::string FileStateTracker::hashFile(const std::string& path) {
    std::ifstream file(path, std::ios::binary);
    if (!file.is_open()) return "";

    Sha256 sha;
    std::array<char, 64 * 1024> buffer{};
    while (file.good()) {
        file.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
        const std::streamsize got = file.gcount();
        if (got > 0) sha.update(buffer.data(), static_cast<size_t>(got));
        if (got == 0) break;
    }
    // A read that failed part way through would otherwise be recorded as the
    // hash of the whole file, and the anchor would name bytes that never were.
    if (file.bad()) return "";
    return sha.hex();
}

void FileStateTracker::recordRead(const std::string& path,
                                  const std::string& contents) {
    Impl& storage = impl();
    std::lock_guard<std::mutex> lock(storage.mutex);
    Impl::Record record;
    record.hash = hashContent(contents);
    record.size = static_cast<std::uint64_t>(contents.size());
    storage.records[trackerKey(path)] = std::move(record);
}

void FileStateTracker::recordWrite(const std::string& path,
                                   const std::string& contents) {
    recordRead(path, contents);
}

void FileStateTracker::recordHash(const std::string& path,
                                  const std::string& hash,
                                  std::uint64_t size) {
    if (hash.empty()) return;
    Impl& storage = impl();
    std::lock_guard<std::mutex> lock(storage.mutex);
    Impl::Record record;
    record.hash = hash;
    record.size = size;
    storage.records[trackerKey(path)] = std::move(record);
}

std::string FileStateTracker::recordFromDisk(const std::string& path) {
    const std::string hash = hashFile(path);
    if (hash.empty()) return "";

    std::error_code ec;
    const auto size = fs::file_size(fs::path(path), ec);

    Impl& storage = impl();
    std::lock_guard<std::mutex> lock(storage.mutex);
    Impl::Record record;
    record.hash = hash;
    record.size = ec ? 0 : static_cast<std::uint64_t>(size);
    storage.records[trackerKey(path)] = record;
    return hash;
}

FileStateTracker::Divergence FileStateTracker::check(
        const std::string& path, const std::string& currentContents) const {
    Divergence result;

    Impl& storage = impl();
    std::lock_guard<std::mutex> lock(storage.mutex);
    auto it = storage.records.find(trackerKey(path));
    if (it == storage.records.end()) return result;

    const std::string hashNow = hashContent(currentContents);
    result.hashAtRead = shortHash(it->second.hash);
    result.hashNow = shortHash(hashNow);
    result.sizeAtRead = it->second.size;
    result.sizeNow = static_cast<std::uint64_t>(currentContents.size());

    if (hashNow == it->second.hash) return result;

    result.diverged = true;
    result.reason = "content hash at read " + result.hashAtRead +
                    ", on disk now " + result.hashNow + " (" +
                    std::to_string(result.sizeAtRead) + " -> " +
                    std::to_string(result.sizeNow) + " bytes)";
    return result;
}

FileStateTracker::Divergence FileStateTracker::checkFile(
        const std::string& path) const {
    Divergence result;

    // Look the record up first: with nothing to compare against there is no
    // reason to touch the file at all.
    std::string recordedHash;
    std::uint64_t recordedSize = 0;
    {
        Impl& storage = impl();
        std::lock_guard<std::mutex> lock(storage.mutex);
        auto it = storage.records.find(trackerKey(path));
        if (it == storage.records.end()) return result;
        recordedHash = it->second.hash;
        recordedSize = it->second.size;
    }

    result.hashAtRead = shortHash(recordedHash);
    result.sizeAtRead = recordedSize;

    // Streamed, so checking a multi-gigabyte file costs bounded memory.
    const std::string hashNow = hashFile(path);
    if (hashNow.empty()) {
        result.diverged = true;
        result.reason = "the file was readable at hash " + result.hashAtRead +
                        " but can no longer be read (deleted, renamed, or "
                        "permissions changed)";
        return result;
    }

    std::error_code ec;
    const auto sizeNow = fs::file_size(fs::path(path), ec);
    result.sizeNow = ec ? 0 : static_cast<std::uint64_t>(sizeNow);
    result.hashNow = shortHash(hashNow);

    if (hashNow == recordedHash) return result;

    result.diverged = true;
    result.reason = "content hash at read " + result.hashAtRead +
                    ", on disk now " + result.hashNow + " (" +
                    std::to_string(result.sizeAtRead) + " -> " +
                    std::to_string(result.sizeNow) + " bytes)";
    return result;
}

bool FileStateTracker::hasRecord(const std::string& path) const {
    Impl& storage = impl();
    std::lock_guard<std::mutex> lock(storage.mutex);
    return storage.records.count(trackerKey(path)) > 0;
}

void FileStateTracker::forget(const std::string& path) {
    Impl& storage = impl();
    std::lock_guard<std::mutex> lock(storage.mutex);
    storage.records.erase(trackerKey(path));
}

void FileStateTracker::clear() {
    Impl& storage = impl();
    std::lock_guard<std::mutex> lock(storage.mutex);
    storage.records.clear();
}

size_t FileStateTracker::size() const {
    Impl& storage = impl();
    std::lock_guard<std::mutex> lock(storage.mutex);
    return storage.records.size();
}

namespace {

/// Build the rejection payload for a diverged file. Shared by write and edit
/// so the model sees one recovery instruction, not two phrasings of it.
json staleRejection(const std::string& path,
                    const std::string& operation,
                    const FileStateTracker::Divergence& divergence) {
    return json{
        {"error", operation + " rejected: " + path +
                      " changed on disk after it was read — " +
                      divergence.reason +
                      ". Nothing was written. Re-read the file with file_read "
                      "and reissue the change against the current contents."},
        {"stale", true},
        {"path", path},
        {"hash_at_read", divergence.hashAtRead},
        {"hash_now", divergence.hashNow},
    };
}

/// 1-based line number of a character offset.
int lineOfOffset(const std::string& content, std::string::size_type offset) {
    return 1 + static_cast<int>(
                   std::count(content.begin(),
                              content.begin() + static_cast<std::ptrdiff_t>(offset),
                              '\n'));
}

/// Every offset at which `needle` occurs, non-overlapping.
std::vector<std::string::size_type> matchOffsets(const std::string& content,
                                                 const std::string& needle) {
    std::vector<std::string::size_type> offsets;
    if (needle.empty()) return offsets;
    for (std::string::size_type pos = content.find(needle); pos != std::string::npos;
         pos = content.find(needle, pos + needle.size())) {
        offsets.push_back(pos);
    }
    return offsets;
}

/// Line the caller was most likely aiming at: the first non-blank line of
/// `oldStr`, matched ignoring indentation — a wrong indent is the commonest
/// reason a match fails, and the excerpt is useless if it lands on line 1 of a
/// thousand-line file. Falls back to the top when nothing resembles it.
int anchorLineFor(const std::string& content, const std::string& oldStr) {
    std::istringstream probeStream(oldStr);
    std::string line;
    std::string probe;
    while (std::getline(probeStream, line)) {
        const auto first = line.find_first_not_of(" \t\r");
        if (first == std::string::npos) continue;
        const auto last = line.find_last_not_of(" \t\r");
        probe = line.substr(first, last - first + 1);
        break;
    }
    if (probe.empty()) return 1;

    const auto pos = content.find(probe);
    return pos == std::string::npos ? 1 : lineOfOffset(content, pos);
}

/// Lines around `centerLine` (1-based), so a rejected edit hands back the
/// current text instead of costing the model a second read to find it.
json excerptAround(const std::string& content, int centerLine) {
    constexpr int kRadius = 12;
    constexpr std::size_t kMaxChars = 4000;

    std::vector<std::string> lines;
    std::string line;
    std::istringstream stream(content);
    while (std::getline(stream, line)) lines.push_back(line);

    const int total = static_cast<int>(lines.size());
    const int start = std::max(1, centerLine - kRadius);
    const int end = std::min(total, centerLine + kRadius);

    std::string excerpt;
    for (int i = start; i <= end; ++i) {
        if (!excerpt.empty()) excerpt += "\n";
        excerpt += lines[static_cast<std::size_t>(i - 1)];
    }
    const bool truncated = excerpt.size() > kMaxChars;
    if (truncated) excerpt.resize(kMaxChars);

    return json{
        {"current_content", excerpt},
        {"current_content_start_line", start},
        {"current_content_end_line", end},
        {"current_content_total_lines", total},
        {"current_content_truncated", truncated},
    };
}

} // namespace

// ---------------------------------------------------------------------------
// registerAll
// ---------------------------------------------------------------------------

void FileIOTools::registerAll(ToolRegistry& registry) {
    registry.registerTool(fileRead());
    registry.registerTool(fileWrite());
    registry.registerTool(fileEdit());
    registry.registerTool(fileSearch());
}

// ---------------------------------------------------------------------------
// fileRead
// ---------------------------------------------------------------------------

ToolInfo FileIOTools::fileRead() {
    ToolInfo info;
    info.name = "file_read";
    info.description =
        "Read the contents of a file. Optionally specify a line range with "
        "start_line and end_line (1-based, inclusive). The returned "
        "content_hash anchors later edits: file_write and file_edit refuse to "
        "touch the file if it changed after this read.";
    info.policy = ToolPolicy::ALLOW;
    info.parameters = {
        {"path", ToolParamType::STRING, /*required=*/true,
         "Absolute or relative path to the file to read"},
        {"start_line", ToolParamType::INTEGER, /*required=*/false,
         "First line to read (1-based, inclusive). Omit to start from the beginning."},
        {"end_line", ToolParamType::INTEGER, /*required=*/false,
         "Last line to read (1-based, inclusive). Omit to read to the end."},
    };
    info.callback = doFileRead;
    return info;
}

json FileIOTools::doFileRead(const json& args) {
    static constexpr size_t kMaxReadBytes = 32 * 1024;
    static constexpr size_t kChunkBytes = 64 * 1024;

    try {
        std::string path = args.value("path", "");
        if (path.empty()) {
            return json{{"error", "path is required"}};
        }

        // Binary, so what the model is shown is byte-for-byte what file_edit
        // will search: a CRLF file must not read back as LF and then fail to
        // match. One pass, so the hash anchors exactly the bytes returned —
        // a second read could hash a version the model never saw.
        std::ifstream file(path, std::ios::binary);
        if (!file.is_open()) {
            return json{{"error", "Cannot open file: " + path}};
        }

        const int startLine = args.value("start_line", 0);
        const int endLine = args.value("end_line", 0);

        Sha256 sha;
        std::string content;
        std::vector<char> buffer(kChunkBytes);
        int lineNumber = 1;        // line the next byte belongs to
        int emittedLines = 0;      // in-range lines started so far
        bool lineOpen = false;     // a line's first in-range byte was seen
        bool truncated = false;
        bool lastByteWasNewline = false;
        std::uint64_t totalBytes = 0;

        while (file.good()) {
            file.read(buffer.data(), static_cast<std::streamsize>(kChunkBytes));
            const std::streamsize got = file.gcount();
            if (got <= 0) break;

            sha.update(buffer.data(), static_cast<size_t>(got));
            totalBytes += static_cast<std::uint64_t>(got);

            for (std::streamsize i = 0; i < got; ++i) {
                const char c = buffer[static_cast<size_t>(i)];
                lastByteWasNewline = (c == '\n');

                const bool inRange =
                    (startLine <= 0 || lineNumber >= startLine) &&
                    (endLine <= 0 || lineNumber <= endLine);

                if (inRange && !truncated) {
                    if (!lineOpen) {
                        // Included lines are joined by '\n', no trailing one.
                        if (emittedLines > 0) content.push_back('\n');
                        ++emittedLines;
                        lineOpen = true;
                    }
                    if (c != '\n') {
                        if (content.size() < kMaxReadBytes) {
                            content.push_back(c);
                        } else {
                            truncated = true;
                        }
                    }
                }

                if (c == '\n') {
                    lineOpen = false;
                    ++lineNumber;
                }
            }
        }

        if (file.bad()) {
            return json{{"error", "Read failed part way through: " + path}};
        }

        // Trailing '\n' handling: "a\nb\n" is 2 lines, "a\nb" is also 2.
        int totalLines = lineNumber - 1;
        if (totalBytes > 0 && !lastByteWasNewline) ++totalLines;

        if (truncated) {
            content += "\n... [output truncated at 32 KB]";
        }

        const std::string hash = sha.hex();
        FileStateTracker::instance().recordHash(path, hash, totalBytes);

        return json{
            {"content", content},
            {"lines", totalLines},
            {"path", path},
            {"truncated", truncated},
            {"content_hash", shortHash(hash)},
        };
    } catch (const std::exception& e) {
        return json{{"error", std::string("file_read failed: ") + e.what()}};
    }
}

// ---------------------------------------------------------------------------
// fileWrite
// ---------------------------------------------------------------------------

ToolInfo FileIOTools::fileWrite() {
    ToolInfo info;
    info.name = "file_write";
    info.description =
        "Write content to a file. Creates parent directories if they do not "
        "exist. Overwrites the file if it already exists. Rejected if the file "
        "changed on disk after the last file_read — re-read it first.";
    info.policy = ToolPolicy::CONFIRM;
    info.parameters = {
        {"path", ToolParamType::STRING, /*required=*/true,
         "Absolute or relative path to the file to write"},
        {"content", ToolParamType::STRING, /*required=*/true,
         "The text content to write to the file"},
    };
    info.callback = doFileWrite;
    return info;
}

json FileIOTools::doFileWrite(const json& args) {
    try {
        std::string path = args.value("path", "");
        if (path.empty()) {
            return json{{"error", "path is required"}};
        }

        if (!args.contains("content") || !args["content"].is_string()) {
            return json{{"error", "content is required and must be a string"}};
        }
        const std::string& content = args["content"].get_ref<const std::string&>();

        FileStateTracker& tracker = FileStateTracker::instance();
        bool recreated = false;
        std::error_code existsEc;
        if (fs::exists(fs::path(path), existsEc) && !existsEc) {
            const auto divergence = tracker.checkFile(path);
            if (divergence.diverged) {
                return staleRejection(path, "file_write", divergence);
            }
        } else if (tracker.hasRecord(path)) {
            // Read, then deleted, now written again. There is no content to
            // clobber so this is allowed — but someone removed that file on
            // purpose, so say the write brought it back rather than let it
            // look like an ordinary create.
            recreated = true;
            tracker.forget(path);
        }

        // Create parent directories if needed
        fs::path filePath(path);
        if (filePath.has_parent_path()) {
            std::error_code ec;
            fs::create_directories(filePath.parent_path(), ec);
            if (ec) {
                return json{{"error", "Failed to create parent directories: " + ec.message()}};
            }
        }

        std::ofstream file(path, std::ios::binary);
        if (!file.is_open()) {
            return json{{"error", "Cannot open file for writing: " + path}};
        }

        file.write(content.data(), static_cast<std::streamsize>(content.size()));
        if (!file.good()) {
            return json{{"error", "Write failed for: " + path}};
        }
        file.close();

        tracker.recordWrite(path, content);

        json result{
            {"success", true},
            {"path", path},
            {"bytes_written", static_cast<int>(content.size())},
            {"content_hash", shortHash(FileStateTracker::hashContent(content))},
        };
        if (recreated) result["recreated"] = true;
        return result;
    } catch (const std::exception& e) {
        return json{{"error", std::string("file_write failed: ") + e.what()}};
    }
}

// ---------------------------------------------------------------------------
// fileEdit
// ---------------------------------------------------------------------------

ToolInfo FileIOTools::fileEdit() {
    ToolInfo info;
    info.name = "file_edit";
    info.description =
        "Perform surgical string replacement in a file. old_string must match "
        "exactly one place — include enough surrounding lines to make it "
        "unique. Rejected if it matches nowhere, if it matches more than once, "
        "or if the file changed on disk after the last file_read — in every "
        "case the file is left untouched.";
    info.policy = ToolPolicy::CONFIRM;
    info.parameters = {
        {"path", ToolParamType::STRING, /*required=*/true,
         "Absolute or relative path to the file to edit"},
        {"old_string", ToolParamType::STRING, /*required=*/true,
         "The exact text to search for and replace"},
        {"new_string", ToolParamType::STRING, /*required=*/true,
         "The text to replace old_string with"},
    };
    info.callback = doFileEdit;
    return info;
}

json FileIOTools::doFileEdit(const json& args) {
    try {
        std::string path = args.value("path", "");
        if (path.empty()) {
            return json{{"error", "path is required"}};
        }

        std::string oldStr = args.value("old_string", "");
        if (oldStr.empty()) {
            return json{{"error", "old_string is required and must not be empty"}};
        }

        std::string newStr = args.value("new_string", "");

        std::string content;
        if (!readWholeFile(path, content)) {
            return json{{"error", "Cannot open file: " + path}};
        }

        FileStateTracker& tracker = FileStateTracker::instance();
        const auto divergence = tracker.check(path, content);
        if (divergence.diverged) {
            json rejection = staleRejection(path, "file_edit", divergence);
            rejection.update(excerptAround(content, anchorLineFor(content, oldStr)));
            // Handing the content back is a read, so re-anchor — otherwise the
            // ledger keeps the superseded hash and the corrected retry is
            // rejected as stale too. file_write stays strict: it names no
            // old_string, so a blind retry would clobber the newer contents.
            tracker.recordRead(path, content);
            return rejection;
        }

        // old_string must identify exactly one place. Replacing the first of
        // several edits the wrong region; replacing all of them edits regions
        // the model never named. Both report success, so ambiguity is an error.
        const auto offsets = matchOffsets(content, oldStr);

        if (offsets.empty()) {
            // A silent no-op is the failure mode this tool exists to avoid:
            // say what did not match and what to do about it.
            std::string hint;
            if (collapseWhitespace(content).find(collapseWhitespace(oldStr)) !=
                std::string::npos) {
                hint = " A whitespace-insensitive match does exist, so the "
                       "indentation, tabs-vs-spaces, or line endings in "
                       "old_string differ from the file.";
            }
            json result = json{
                {"error", "old_string not found in file: " + path +
                              " — no replacement was made and the file is "
                              "unchanged." + hint +
                              " The current content is included as "
                              "current_content; copy old_string verbatim "
                              "from it."},
                {"path", path},
                {"replacements", 0},
            };
            result.update(excerptAround(content, anchorLineFor(content, oldStr)));
            return result;
        }

        if (offsets.size() > 1) {
            std::string lineList;
            json matchLines = json::array();
            for (const auto offset : offsets) {
                const int lineNo = lineOfOffset(content, offset);
                matchLines.push_back(lineNo);
                if (!lineList.empty()) lineList += ", ";
                lineList += std::to_string(lineNo);
            }
            json result = json{
                {"error", "Ambiguous edit: old_string matches " +
                              std::to_string(offsets.size()) + " locations in " +
                              path + " (lines " + lineList +
                              ") — nothing was written, because there is no way "
                              "to tell which one you meant. Extend old_string "
                              "with enough surrounding lines to match exactly "
                              "one location, then reissue the edit."},
                {"ambiguous", true},
                {"path", path},
                {"replacements", 0},
                {"match_lines", matchLines},
            };
            result.update(excerptAround(content, lineOfOffset(content, offsets[0])));
            return result;
        }

        content.replace(offsets[0], oldStr.size(), newStr);
        const int replacements = 1;

        // Write back
        std::ofstream outFile(path, std::ios::binary);
        if (!outFile.is_open()) {
            return json{{"error", "Cannot open file for writing: " + path}};
        }

        outFile.write(content.data(), static_cast<std::streamsize>(content.size()));
        if (!outFile.good()) {
            return json{{"error", "Write failed for: " + path}};
        }
        outFile.close();

        tracker.recordWrite(path, content);

        return json{
            {"success", true},
            {"path", path},
            {"replacements", replacements},
            {"content_hash", shortHash(FileStateTracker::hashContent(content))},
        };
    } catch (const std::exception& e) {
        return json{{"error", std::string("file_edit failed: ") + e.what()}};
    }
}

// ---------------------------------------------------------------------------
// fileSearch
// ---------------------------------------------------------------------------

ToolInfo FileIOTools::fileSearch() {
    ToolInfo info;
    info.name = "file_search";
    info.description =
        "Search for files by name pattern and/or content. The pattern supports "
        "glob wildcards (*, ?, [abc], and ** for directories); a pattern "
        "containing '/' is matched against the path relative to the search "
        "root, otherwise against the file name. Files excluded by .gitignore "
        "and the .git directory are skipped. Optionally filter by "
        "content_pattern (substring match within file contents).";
    info.policy = ToolPolicy::ALLOW;
    info.parameters = {
        {"pattern", ToolParamType::STRING, /*required=*/true,
         "Glob pattern to match file names or relative paths "
         "(e.g. '*.cpp', 'test_*', 'src/**/*.h')"},
        {"path", ToolParamType::STRING, /*required=*/false,
         "Root directory to search in (default: current directory)"},
        {"content_pattern", ToolParamType::STRING, /*required=*/false,
         "Substring to search for within matched files"},
        {"max_results", ToolParamType::INTEGER, /*required=*/false,
         "Maximum number of results to return (default: 50)"},
    };
    info.callback = doFileSearch;
    return info;
}

json FileIOTools::doFileSearch(const json& args) {
    try {
        static constexpr size_t kMaxPatternBytes = 512;

        std::string pattern = args.value("pattern", "");
        if (pattern.empty()) {
            return json{{"error", "pattern is required"}};
        }
        if (pattern.size() > kMaxPatternBytes) {
            // The matcher's memo table is pattern-length times path-length, so
            // an unbounded pattern is an unbounded allocation per file.
            return json{{"error",
                         "pattern is " + std::to_string(pattern.size()) +
                             " bytes; the limit is " +
                             std::to_string(kMaxPatternBytes) +
                             ". Use a shorter glob and filter the results, or "
                             "pass content_pattern for a substring search."}};
        }

        std::string searchPath = args.value("path", ".");
        std::string contentPattern = args.value("content_pattern", "");
        int maxResults = args.value("max_results", 50);
        if (maxResults <= 0) maxResults = 50;

        if (!fs::exists(searchPath)) {
            return json{{"error", "Search path does not exist: " + searchPath}};
        }

        if (!fs::is_directory(searchPath)) {
            return json{{"error", "Search path is not a directory: " + searchPath}};
        }

        // Rules governing the root are resolved up front; each subdirectory's
        // own .gitignore is folded in as the walk reaches it. Rules are scoped
        // to their own directory, so a nested file cannot affect a sibling.
        GitignoreMatcher ignore = GitignoreMatcher::forDirectory(searchPath);

        std::error_code rootEc;
        fs::path root = fs::weakly_canonical(fs::path(searchPath), rootEc);
        if (rootEc) root = fs::path(searchPath);

        // A pattern with a separator addresses a path; otherwise a file name.
        const bool pathPattern = pattern.find('/') != std::string::npos;
        const GlobOptions globOpts{/*pathMode=*/pathPattern,
                                   /*caseInsensitive=*/false};

        json matches = json::array();
        int total = 0;
        int ignoredSkipped = 0;

        std::error_code ec;
        for (auto it = fs::recursive_directory_iterator(searchPath, fs::directory_options::skip_permission_denied, ec);
             it != fs::recursive_directory_iterator(); it.increment(ec)) {
            if (ec) {
                ec.clear();
                continue;
            }

            const fs::path& entryPath = it->path();
            const std::string filename = entryPath.filename().string();

            if (it->is_directory(ec) && !ec) {
                // .git is never interesting and is huge; it is pruned always
                // and is not counted as an ignore-rule skip, or the counter
                // would be non-zero on every repository and say nothing.
                if (filename == ".git") {
                    it.disable_recursion_pending();
                    continue;
                }
                if (ignore.isIgnored(entryPath.string(), /*isDirectory=*/true)) {
                    it.disable_recursion_pending();
                    ++ignoredSkipped;
                    continue;
                }
                ignore.addFile((entryPath / ".gitignore").string());
                continue;
            }
            ec.clear();

            if (!it->is_regular_file(ec)) continue;
            if (ec) { ec.clear(); continue; }

            if (ignore.isIgnored(entryPath.string(), /*isDirectory=*/false)) {
                ++ignoredSkipped;
                continue;
            }

            bool nameMatches;
            if (pathPattern) {
                std::error_code relEc;
                fs::path rel = fs::relative(entryPath, root, relEc);
                const std::string relStr =
                    relEc ? entryPath.generic_string() : rel.generic_string();
                nameMatches = globMatch(pattern, relStr, globOpts);
            } else {
                nameMatches = globMatch(pattern, filename, globOpts);
            }
            if (!nameMatches) continue;

            // If content_pattern is specified, search within file
            if (!contentPattern.empty()) {
                std::ifstream file(entryPath);
                if (!file.is_open()) continue;

                std::string line;
                int lineNum = 0;
                while (std::getline(file, line)) {
                    ++lineNum;
                    if (line.find(contentPattern) != std::string::npos) {
                        ++total;
                        if (static_cast<int>(matches.size()) < maxResults) {
                            json match;
                            match["path"] = entryPath.generic_string();
                            match["line"] = lineNum;
                            // Trim context to reasonable length
                            std::string context = line;
                            if (context.size() > 200) {
                                context = context.substr(0, 200) + "...";
                            }
                            match["context"] = context;
                            matches.push_back(std::move(match));
                        }
                    }
                }
            } else {
                // Name match only
                ++total;
                if (static_cast<int>(matches.size()) < maxResults) {
                    json match;
                    match["path"] = entryPath.generic_string();
                    matches.push_back(std::move(match));
                }
            }
        }

        return json{
            {"matches", matches},
            {"total", total},
            {"ignored_skipped", ignoredSkipped},
        };
    } catch (const std::exception& e) {
        return json{{"error", std::string("file_search failed: ") + e.what()}};
    }
}

} // namespace gaia
