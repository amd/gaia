// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/file_tools.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <mutex>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#include "gaia/ignore.h"
#include "gaia/secrets.h"

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
    fs::path p = fs::weakly_canonical(fs::u8path(path), ec);
    if (ec || p.empty()) {
        p = fs::absolute(fs::u8path(path), ec);
        if (ec) p = fs::u8path(path);
    }
    return p.generic_u8string();
}

/// Read a whole file as raw bytes. Returns false when it cannot be opened.
bool readWholeFile(const std::string& path, std::string& out) {
    std::ifstream file(fs::u8path(path), std::ios::binary);
    if (!file.is_open()) return false;
    std::ostringstream buffer;
    buffer << file.rdbuf();
    out = buffer.str();
    return true;
}

struct LineBreaks {
    size_t crlf = 0;
    size_t loneLf = 0;
    char previous = 0;

    void add(char c) {
        if (c == '\n') ++(previous == '\r' ? crlf : loneLf);
        previous = c;
    }
    void add(const char* data, size_t len) {
        for (size_t i = 0; i < len; ++i) add(data[i]);
    }
    bool crlfFile() const { return crlf > loneLf; }
    bool mixed() const { return crlf > 0 && loneLf > 0; }
};

LineBreaks lineBreaksOf(const std::string& text) {
    LineBreaks breaks;
    breaks.add(text.data(), text.size());
    return breaks;
}

/// False when the file cannot be read to the end.
bool lineBreaksOfFile(const std::string& path, LineBreaks& breaks) {
    std::ifstream file(fs::u8path(path), std::ios::binary);
    if (!file.is_open()) return false;
    std::array<char, 64 * 1024> buffer{};
    while (file.good()) {
        file.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
        const std::streamsize got = file.gcount();
        if (got <= 0) break;
        breaks.add(buffer.data(), static_cast<size_t>(got));
    }
    return !file.bad();
}

std::string crlfToLf(const std::string& text) {
    std::string out;
    out.reserve(text.size());
    for (size_t i = 0; i < text.size(); ++i) {
        if (text[i] == '\r' && i + 1 < text.size() && text[i + 1] == '\n') continue;
        out.push_back(text[i]);
    }
    return out;
}

std::string lfToCrlf(const std::string& text) {
    std::string out;
    out.reserve(text.size() + text.size() / 16);
    for (size_t i = 0; i < text.size(); ++i) {
        if (text[i] == '\n' && (i == 0 || text[i - 1] != '\r')) out.push_back('\r');
        out.push_back(text[i]);
    }
    return out;
}

/// Where byte `lfOffset` of crlfToLf(raw) sits in `raw`; a "\n" that was "\r\n" maps to its "\r".
size_t rawOffset(const std::string& raw, size_t lfOffset) {
    size_t at = 0;
    for (size_t seen = 0; seen < lfOffset && at < raw.size(); ++seen) {
        at += (raw[at] == '\r' && at + 1 < raw.size() && raw[at + 1] == '\n') ? 2 : 1;
    }
    return at;
}

/// "" when `path` can be opened as a file; else why not. A device name is
/// refused by its spelling: asking Windows about CON or COM1 can itself block.
std::string notAFileReason(const std::string& path) {
#ifdef _WIN32
    const bool devicePath = path.size() >= 4 && (path[0] == '\\' || path[0] == '/') &&
                            (path[1] == '\\' || path[1] == '/') && path[2] == '.' &&
                            (path[3] == '\\' || path[3] == '/');
    // Windows ignores an extension, a stream name and trailing spaces on a device name.
    std::string stem = path.substr(path.find_last_of("/\\") + 1);
    stem = stem.substr(0, stem.find_first_of(".:"));
    while (!stem.empty() && stem.back() == ' ') stem.pop_back();
    std::transform(stem.begin(), stem.end(), stem.begin(),
                   [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
    static const std::set<std::string> devices = {
        "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"};
    if (devicePath || devices.count(stem)) {
        return path + " names a Windows device, not a file, and was not opened. Use a different file name.";
    }
#endif
    std::error_code ec;
    const fs::file_status status = fs::status(fs::u8path(path), ec);
    if (ec || !fs::exists(status) || fs::is_regular_file(status)) return "";
    if (fs::is_directory(status)) {
        return path + " is a folder, not a file. Use file_search with this path to list what is in it.";
    }
    return path + " is not a regular file (a device, pipe or socket) and was not opened.";
}

/// "" when the agent may open `path`; else why a credential file stays closed.
/// Judged by the real file's name, so a link called notes.txt to .env is refused too.
std::string credentialFileReason(const std::string& path) {
    const fs::path given = fs::u8path(path);
    std::error_code ec;
    const fs::path real = fs::weakly_canonical(given, ec);
    const std::string name = (ec ? given : real).filename().u8string();
    if (!isCredentialFileName(name)) return "";
    return "'" + name + "' holds credentials, keys or secrets, so the agent may not read or change it. "
           "Open it yourself if you need its contents.";
}

size_t countOf(const std::string& text, const std::string& what) {
    size_t count = 0;
    for (size_t at = text.find(what); at != std::string::npos; at = text.find(what, at + what.size())) ++count;
    return count;
}

/// "" unless the write would put "[redacted]" where `current` holds a secret a read showed masked.
std::string maskedSecretReason(const std::string& path, const std::string& current,
                               const std::string& oldText, const std::string& newText) {
    static const std::string marker = "[redacted]";
    const size_t inOld = countOf(oldText, marker);
    const bool addsMarker = countOf(newText, marker) > inOld;
    if (inOld == 0 && !addsMarker) return "";
    std::string what;
    if (inOld > 0 && crlfToLf(current).find(crlfToLf(oldText)) == std::string::npos) {
        what = "old_string";
    } else if (addsMarker && holdsCredentials(current)) {
        what = "The new text";
    } else {
        return "";
    }
    return what + " contains " + marker + ", which stands in for a secret hidden from what you read; it is not "
           "text in " + path + ". Nothing was written. Leave the lines that hold the secret out of old_string "
           "and the new text, or ask the user to change them.";
}

/// Replace the file at `path` with `content` through a temporary file beside
/// it, so a write that fails leaves the original intact. "" on success, else
/// what went wrong.
std::string writeInPlace(const fs::path& target, const std::string& path, const std::string& content) {
    std::ofstream out(target, std::ios::binary | std::ios::trunc);
    if (!out.is_open()) return "Cannot open file for writing: " + path;
    out.write(content.data(), static_cast<std::streamsize>(content.size()));
    out.close();
    if (out.fail()) return "Write failed for: " + path + " (the disk may be full).";
    return "";
}

std::string replaceFileContents(const std::string& path, const std::string& content) {
    fs::path target = fs::u8path(path);
    std::error_code ec;
    if (fs::is_symlink(target, ec)) {
        // Written through the link, as opening it would: renaming over it would replace the link itself.
        const fs::path resolved = fs::canonical(target, ec);
        if (ec) return writeInPlace(target, path, content);  // a link to a file not there yet
        target = resolved;
    }
    const bool existed = fs::exists(target, ec);
    // A rename would detach the other names of a hard-linked file.
    if (existed && fs::hard_link_count(target, ec) > 1 && !ec) return writeInPlace(target, path, content);
    if (existed) {
        // A rename would replace a file this process may not write to.
        std::ofstream probe(target, std::ios::binary | std::ios::app);
        if (!probe.is_open()) return "Cannot open file for writing: " + path;
    }

    static std::atomic<unsigned> counter{0};
    fs::path temp = target;
    temp += ".gaia-tmp-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + "-" +
            std::to_string(counter.fetch_add(1));
    const auto discard = [&temp] {
        std::error_code removeEc;
        fs::remove(temp, removeEc);
    };
    {
        std::ofstream out(temp, std::ios::binary | std::ios::trunc);
        // No new file can be made beside it (a folder that only lets this file be written).
        if (!out.is_open()) return writeInPlace(target, path, content);
        out.write(content.data(), static_cast<std::streamsize>(content.size()));
        out.close();
        if (out.fail()) {
            discard();
            return "Write failed for: " + path + " (the disk may be full). The file is unchanged.";
        }
    }
    if (existed) {
        const fs::perms mode = fs::status(target, ec).permissions();
        if (!ec) fs::permissions(temp, mode, ec);
        if (ec) {
            const std::string why = ec.message();
            discard();
            return "Write failed for: " + path + " (could not keep its permissions: " + why +
                   "). The file is unchanged.";
        }
    }
    fs::rename(temp, target, ec);
    if (ec) {
        // Another program holds the file open, or it is a mount point: it can still be written.
        discard();
        return writeInPlace(target, path, content);
    }
    return "";
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

/// A file in the same folder whose name is within two edits of the missing one, or "".
std::string nearNamedFile(const std::string& path) {
    std::error_code ec;
    const fs::path asked = fs::u8path(path);
    const fs::path dir = asked.has_parent_path() ? asked.parent_path() : fs::path(".");
    if (fs::exists(asked, ec) || !fs::is_directory(dir, ec)) return "";
    auto lower = [](std::string s) {
        for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        return s;
    };
    const std::string want = lower(asked.filename().u8string());
    if (want.size() < 4) return "";
    std::string best;
    size_t bestDistance = 3, seen = 0;
    for (const auto& entry : fs::directory_iterator(dir, ec)) {
        if (++seen > 2000) break;
        if (!entry.is_regular_file(ec)) continue;
        const std::string have = lower(entry.path().filename().u8string());
        const size_t gap = have.size() > want.size() ? have.size() - want.size() : want.size() - have.size();
        if (gap > 2) continue;
        std::vector<size_t> row(have.size() + 1);
        for (size_t j = 0; j <= have.size(); ++j) row[j] = j;
        for (size_t i = 1; i <= want.size(); ++i) {
            size_t diagonal = row[0];
            row[0] = i;
            for (size_t j = 1; j <= have.size(); ++j) {
                const size_t above = row[j];
                row[j] = std::min({row[j] + 1, row[j - 1] + 1, diagonal + (want[i - 1] != have[j - 1])});
                diagonal = above;
            }
        }
        if (row[have.size()] < bestDistance) {
            bestDistance = row[have.size()];
            best = entry.path().generic_u8string();
        }
    }
    return best;
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
    std::ifstream file(fs::u8path(path), std::ios::binary);
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
    const auto size = fs::file_size(fs::u8path(path), ec);

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
    const auto sizeNow = fs::file_size(fs::u8path(path), ec);
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

/// UTF-16 text as UTF-8; the byte order comes from the mark, else from which bytes are NUL.
static std::string utf16ToUtf8(const std::string& raw) {
    const auto byte = [&raw](size_t i) { return static_cast<unsigned char>(raw[i]); };
    size_t at = 0;
    bool little = true;
    if (raw.size() >= 2 && byte(0) == 0xFF && byte(1) == 0xFE) at = 2;
    else if (raw.size() >= 2 && byte(0) == 0xFE && byte(1) == 0xFF) { at = 2; little = false; }
    else {
        size_t evenNul = 0, oddNul = 0;
        for (size_t i = 0; i < raw.size() && i < 4096; ++i) (i % 2 ? oddNul : evenNul) += raw[i] == '\0';
        little = oddNul >= evenNul;
    }
    auto unit = [&](size_t i) -> std::uint32_t {
        return little ? byte(i) | (byte(i + 1) << 8) : (byte(i) << 8) | byte(i + 1);
    };
    std::string out;
    for (; at + 1 < raw.size(); at += 2) {
        std::uint32_t cp = unit(at);
        if (cp >= 0xD800 && cp < 0xDC00 && at + 3 < raw.size()) {
            const std::uint32_t low = unit(at + 2);
            if (low >= 0xDC00 && low < 0xE000) {
                cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00);
                at += 2;
            }
        }
        if (cp >= 0xD800 && cp < 0xE000) cp = 0xFFFD;  // half of a pair on its own
        if (cp < 0x80) out.push_back(static_cast<char>(cp));
        else if (cp < 0x800) {
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
    }
    return out;
}

/// Rewrites bytes that are not UTF-8 as the Windows-1252 characters they most likely are.
static size_t repairLegacyBytes(std::string& text) {
    static const std::uint16_t kHigh[32] = {0x20AC, 0x0081, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021,
                                            0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, 0x008D, 0x017D, 0x008F,
                                            0x0090, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014,
                                            0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x009D, 0x017E, 0x0178};
    std::string out;
    size_t repaired = 0;
    for (size_t i = 0; i < text.size();) {
        const unsigned char c = static_cast<unsigned char>(text[i]);
        const size_t len = c < 0x80 ? 1 : (c >> 5) == 0x6 ? 2 : (c >> 4) == 0xE ? 3 : (c >> 3) == 0x1E ? 4 : 0;
        bool ok = len > 0;
        for (size_t k = 1; ok && k < len; ++k) {
            ok = i + k < text.size() && (static_cast<unsigned char>(text[i + k]) & 0xC0) == 0x80;
        }
        if (ok) {
            out.append(text, i, len);
            i += len;
            continue;
        }
        const std::uint32_t cp = c < 0xA0 ? kHigh[c - 0x80] : c;
        if (cp < 0x800) {
            out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
        } else {
            out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        }
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        ++repaired;
        ++i;
    }
    if (repaired) text.swap(out);
    return repaired;
}

/// Totals a small model otherwise adds up wrongly: each numeric column, split by each
/// short label column and by month.
static std::string tableSummary(const std::vector<std::string>& names,
                                const std::vector<std::vector<std::string>>& data) {
    if (data.size() < 2 || names.size() < 2 || names.size() > 12) return "";
    auto trim = [](std::string v) {
        while (!v.empty() && std::isspace(static_cast<unsigned char>(v.back()))) v.pop_back();
        size_t lead = 0;
        while (lead < v.size() && std::isspace(static_cast<unsigned char>(v[lead]))) ++lead;
        return v.substr(lead);
    };
    auto lower = [](std::string v) {
        for (auto& c : v) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        return v;
    };
    auto number = [&trim](const std::string& raw, double& value) {
        std::string v;
        bool negative = false;
        std::string cell = trim(raw);
        // 1200,50 is a decimal comma; a thousands comma is followed by three digits.
        const size_t lastComma = cell.rfind(','), lastDot = cell.rfind('.');
        if (lastComma != std::string::npos && lastDot != std::string::npos && lastDot < lastComma) {
            cell.erase(std::remove(cell.begin(), cell.end(), '.'), cell.end());
        }
        const size_t comma = cell.find(',');
        if (comma != std::string::npos && cell.find(',', comma + 1) == std::string::npos &&
            cell.find('.') == std::string::npos && cell.size() - comma - 1 <= 2 && comma + 1 < cell.size()) {
            cell[comma] = '.';
        }
        for (char c : cell) {
            if (c == '(') negative = true;
            else if (c != ')' && c != ',' && c != '$' && c != '%' && c != ' ') v.push_back(c);
        }
        if (v.empty()) return false;
        char* end = nullptr;
        value = std::strtod(v.c_str(), &end);
        if (end != v.c_str() + v.size() || !std::isfinite(value)) return false;
        if (negative) value = -value;
        return true;
    };
    auto month = [&trim](const std::string& raw) -> std::string {
        const std::string v = trim(raw);
        auto digits = [&v](size_t from, size_t count) {
            for (size_t i = from; i < from + count; ++i) {
                if (i >= v.size() || !std::isdigit(static_cast<unsigned char>(v[i]))) return false;
            }
            return true;
        };
        if (v.size() < 10 || !digits(0, 4) || (v[4] != '-' && v[4] != '/') || !digits(5, 2) || v[7] != v[4] ||
            !digits(8, 2)) {
            return "";
        }
        return v.substr(0, 4) + "-" + v.substr(5, 2);
    };

    enum class Kind { Other, Number, Label, Month };
    struct Column {
        Kind kind{};  // Other
        bool whole = true;
        size_t notNumbers = 0;
        std::vector<std::string> keys;         // per row; empty when the cell is
        std::map<std::string, std::string> shown;  // key -> first spelling seen
    };
    std::vector<Column> cols(names.size());
    for (size_t c = 0; c < names.size(); ++c) {
        size_t filled = 0, numeric = 0, dated = 0;
        bool shortText = true;
        Column& col = cols[c];
        for (const auto& row : data) {
            const std::string cell = c < row.size() ? trim(row[c]) : "";
            std::string key = lower(cell);
            if (!cell.empty()) {
                ++filled;
                double v = 0;
                if (number(cell, v)) {
                    ++numeric;
                    if (v != std::floor(v)) col.whole = false;
                }
                const std::string m = month(cell);
                if (!m.empty()) {
                    ++dated;
                    key = m;
                }
                if (cell.size() > 30) shortText = false;
                col.shown.emplace(key, m.empty() ? cell : m);
            }
            col.keys.push_back(key);
        }
        const std::string name = lower(trim(names[c]));
        const bool identifier = name == "id" || name == "year" || name == "zip" ||
                                (name.size() > 2 && (name.compare(name.size() - 2, 2, "id") == 0));
        if (!filled) continue;
        if (dated == filled) col.kind = Kind::Month;
        else if (numeric * 5 >= filled * 4 && !identifier) {
            col.kind = Kind::Number;
            col.notNumbers = filled - numeric;
        } else if (numeric == 0 && shortText && col.shown.size() <= 12) col.kind = Kind::Label;
    }

    auto shown = [](double v, bool whole) {
        char buf[64];
        std::snprintf(buf, sizeof(buf), whole ? "%.0f" : "%.2f", v);
        return std::string(buf);
    };
    static constexpr size_t kMaxBytes = 2400;
    std::string out = "Computed from all " + std::to_string(data.size()) + " data rows:";
    const size_t headerBytes = out.size();
    auto add = [&out](const std::string& line) {
        if (out.size() + line.size() <= kMaxBytes) out += "\n" + line;
    };
    std::vector<size_t> numbers, groups;
    for (size_t c = 0; c < cols.size(); ++c) {
        if (cols[c].kind == Kind::Number && numbers.size() < 4) numbers.push_back(c);
        if ((cols[c].kind == Kind::Label || cols[c].kind == Kind::Month) && groups.size() < 4) groups.push_back(c);
    }
    for (size_t n : numbers) {
        double total = 0, low = 0, high = 0;
        size_t count = 0;
        for (const auto& row : data) {
            double v = 0;
            if (n >= row.size() || !number(row[n], v)) continue;
            low = count ? std::min(low, v) : v;
            high = count ? std::max(high, v) : v;
            total += v;
            ++count;
        }
        std::string line = trim(names[n]) + ": total " + shown(total, cols[n].whole) + " over " +
                           std::to_string(count) + " rows (average " +
                           shown(count ? total / static_cast<double>(count) : 0, false) + ", lowest " +
                           shown(low, cols[n].whole) + ", highest " +
                           shown(high, cols[n].whole) + ")";
        if (cols[n].notNumbers) line += "; " + std::to_string(cols[n].notNumbers) + " cells are not numbers and are left out";
        add(line);
    }
    for (size_t g : groups) {
        const Column& group = cols[g];
        std::map<std::string, size_t> counts;
        for (const auto& key : group.keys) {
            if (!key.empty()) ++counts[key];
        }
        if (numbers.empty() && counts.size() == data.size()) continue;  // every row its own label
        std::string line = "rows by " + trim(names[g]) + ":";
        for (const auto& [key, count] : counts) line += " " + group.shown.at(key) + " " + std::to_string(count) + ",";
        line.pop_back();
        add(line);
        for (size_t n : numbers) {
            std::map<std::string, double> sums;
            std::map<std::string, size_t> cells;
            double total = 0;
            bool signedValues = false;
            for (size_t r = 0; r < data.size(); ++r) {
                double v = 0;
                if (group.keys[r].empty() || n >= data[r].size() || !number(data[r][n], v)) continue;
                sums[group.keys[r]] += v;
                ++cells[group.keys[r]];
                total += v;
                if (v < 0) signedValues = true;
            }
            std::vector<std::pair<std::string, double>> order(sums.begin(), sums.end());
            if (group.kind == Kind::Label) {
                std::stable_sort(order.begin(), order.end(),
                                 [](const auto& a, const auto& b) { return a.second > b.second; });
            }
            line = trim(names[n]) + " by " + trim(names[g]) + ":";
            for (const auto& [key, sum] : order) {
                line += " " + group.shown.at(key) + " " + shown(sum, cols[n].whole);
                if (!signedValues && total > 0) {
                    char pct[32];
                    std::snprintf(pct, sizeof(pct), " (%.1f%%)", 100.0 * sum / total);
                    line += pct;
                }
                line += ",";
            }
            line.pop_back();
            add(line);
            if (group.kind != Kind::Label || sums.size() == data.size()) continue;  // one row each: nothing to average
            for (auto& entry : order) entry.second /= static_cast<double>(cells[entry.first]);
            std::stable_sort(order.begin(), order.end(), [](const auto& a, const auto& b) { return a.second > b.second; });
            line = "average " + trim(names[n]) + " by " + trim(names[g]) + ":";
            for (const auto& [key, mean] : order) line += " " + group.shown.at(key) + " " + shown(mean, false) + ",";
            line.pop_back();
            add(line);
        }
    }
    return out.size() == headerBytes ? "" : out;
}

json FileIOTools::doFileRead(const json& args) {
    static constexpr size_t kMaxReadBytes = 32 * 1024;
    static constexpr size_t kChunkBytes = 64 * 1024;

    try {
        std::string path = args.value("path", "");
        if (path.empty()) {
            return json{{"error", "path is required"}};
        }

        const std::string notAFile = notAFileReason(path);
        if (!notAFile.empty()) return json{{"error", notAFile}};

        const std::string credential = credentialFileReason(path);
        if (!credential.empty()) return json{{"error", credential}};

        // One pass, so the hash anchors exactly the bytes on disk that were
        // shown — a second read could hash a version the model never saw.
        std::ifstream file(fs::u8path(path), std::ios::binary);
        if (!file.is_open()) {
            const std::string near = nearNamedFile(path);
            if (!near.empty()) {
                return json{{"error", path + " does not exist. " + near + " is in the same folder and has nearly the "
                                      "same name. If you read that one, tell the user the name they gave does not "
                                      "exist and which file you used instead."}};
            }
            return json{{"error", "Cannot open file: " + path}};
        }

        const int startLine = args.value("start_line", 0);
        const int endLine = args.value("end_line", 0);

        Sha256 sha;
        std::string content;
        std::string head;  // the first bytes, to tell text from binary
        std::vector<char> buffer(kChunkBytes);
        int lineNumber = 1;        // line the next byte belongs to
        int emittedLines = 0;      // in-range lines started so far
        bool lineOpen = false;     // a line's first in-range byte was seen
        bool truncated = false;
        bool lastByteWasNewline = false;
        bool shownEndsWithCrlfBreak = false;
        LineBreaks breaks;
        std::uint64_t totalBytes = 0;

        while (file.good()) {
            file.read(buffer.data(), static_cast<std::streamsize>(kChunkBytes));
            const std::streamsize got = file.gcount();
            if (got <= 0) break;

            sha.update(buffer.data(), static_cast<size_t>(got));
            totalBytes += static_cast<std::uint64_t>(got);
            if (head.size() < 8192) head.append(buffer.data(), std::min<size_t>(static_cast<size_t>(got), 8192 - head.size()));

            for (std::streamsize i = 0; i < got; ++i) {
                const char c = buffer[static_cast<size_t>(i)];
                const bool afterCr = breaks.previous == '\r';
                breaks.add(c);
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
                        shownEndsWithCrlfBreak = false;
                    }
                    if (c == '\n') shownEndsWithCrlfBreak = afterCr;
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

        const bool crlfFile = breaks.crlfFile();
        if (crlfFile) {
            if (shownEndsWithCrlfBreak && !content.empty() && content.back() == '\r') content.pop_back();
            content = crlfToLf(content);
        }

        if (truncated) {
            content += "\n... [output truncated at 32 KB]";
        }

        const std::string hash = sha.hex();
        FileStateTracker::instance().recordHash(path, hash, totalBytes);

        // Binary bytes shown as text are noise a small model can loop on.
        const TextKind kind = textKind(head);
        bool decodedUtf16 = false;
        if (kind == TextKind::Utf16) {
            constexpr std::uint64_t kMaxDecoded = 256 * 1024;
            if (totalBytes > kMaxDecoded) {
                return json{{"error", path + " is UTF-16 text too large for file_read to decode. Read it with a "
                                          "program, for example Python's open(path, encoding='utf-16')."},
                            {"path", path}};
            }
            std::ifstream again(fs::u8path(path), std::ios::binary);
            const std::string raw((std::istreambuf_iterator<char>(again)), std::istreambuf_iterator<char>());
            std::istringstream lines(utf16ToUtf8(raw));
            content.clear();
            truncated = false;
            totalLines = 0;
            int emitted = 0;
            for (std::string line; std::getline(lines, line);) {
                ++totalLines;
                if (!line.empty() && line.back() == '\r') line.pop_back();
                if ((startLine > 0 && totalLines < startLine) || (endLine > 0 && totalLines > endLine) || truncated) continue;
                if (content.size() + line.size() > kMaxReadBytes) {
                    truncated = true;
                    continue;
                }
                if (emitted++) content.push_back('\n');
                content += line;
            }
            if (truncated) content += "\n... [output truncated at 32 KB]";
            decodedUtf16 = true;
        }
        if (kind == TextKind::LegacyEncoding) {
            return json{{"error", path + " is text in a legacy encoding, not UTF-8 (for example Windows-1252, "
                                      "CP1251 or Shift-JIS). Read it with a program that names the encoding, for "
                                      "example Python's open(path, encoding='cp1252')."},
                        {"path", path}};
        }
        if (kind == TextKind::Binary) {
            return json{{"error", path + " is not a text file (" + std::to_string(totalBytes) +
                                      " bytes of binary data). Don't guess what it says: inspect it with a program "
                                      "suited to its format (a PDF, Office or image library, or `file`), or tell the "
                                      "user it can't be read as text."},
                        {"path", path}};
        }
        if (totalBytes == 0) {
            return json{{"content", ""},
                        {"lines", 0},
                        {"path", path},
                        {"truncated", false},
                        {"content_hash", shortHash(hash)},
                        {"note", "The file is empty (0 bytes). Its data is missing, not zero: look for a log or "
                                 "error beside it that says why, and report the figure as unknown."}};
        }

        // Thousands of one repeated character send a small model into a runaway reply.
        // Long lines only: a column of one value per line is data.
        if (startLine <= 0 && endLine <= 0) {
            static const std::string kCut = "\n... [output truncated at 32 KB]";
            std::string dense;
            const size_t shown = truncated && content.size() >= kCut.size() ? content.size() - kCut.size() : content.size();
            for (size_t i = 0; i < shown; ++i) {
                if (!std::isspace(static_cast<unsigned char>(content[i]))) dense.push_back(content[i]);
            }
            constexpr size_t kMinRepeated = 1000, kMaxUnit = 4, kMinLine = 200;
            const bool longLines = dense.size() / static_cast<size_t>(std::max(1, totalLines)) >= kMinLine;
            for (size_t unit = 1; unit <= kMaxUnit && longLines && dense.size() >= kMinRepeated; ++unit) {
                bool repeats = true;
                for (size_t i = unit; i < dense.size() && repeats; ++i) repeats = dense[i] == dense[i - unit];
                if (!repeats) continue;
                const std::string span = truncated ? "the first 32 KB of its " + std::to_string(totalBytes) + " bytes are"
                                                   : "its " + std::to_string(totalBytes) + " bytes are";
                return json{{"content", dense.substr(0, unit * 8) + "..."},
                            {"lines", totalLines},
                            {"path", path},
                            {"truncated", true},
                            {"content_hash", shortHash(hash)},
                            {"note", "This file holds no real content: " + span + " \"" + dense.substr(0, unit) +
                                         "\" repeated, with nothing else but whitespace. Tell the user that; "
                                         "there is nothing in it to summarize or quote."}};
            }
        }

        // Read to the end, so a character cut by the size limit is not taken for a stray byte.
        const size_t legacyBytes = truncated ? 0 : repairLegacyBytes(content);
        json out{
            {"content", content},
            {"lines", totalLines},
            {"path", path},
            {"truncated", truncated},
            {"content_hash", shortHash(hash)},
        };
        if (crlfFile && !decodedUtf16) out["line_endings"] = "crlf";
        if (decodedUtf16) {
            out["note"] = "This file is UTF-16; the content shown is decoded. A program that opens it must name "
                          "the encoding (Python: encoding='utf-16'), and file_edit cannot change it.";
        }
        if (legacyBytes) {
            out["note"] = std::to_string(legacyBytes) + " byte" + (legacyBytes == 1 ? " is" : "s are") +
                          " not UTF-8 and " + (legacyBytes == 1 ? "is" : "are") +
                          " shown as the Windows-1252 character (a euro sign, an accented letter). "
                          "Quote them as shown.";
        }
        // Small models copy rows into a script and get them wrong; point at the file instead.
        std::string ext = fs::u8path(path).extension().u8string();
        for (auto& c : ext) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        if ((ext == ".csv" || ext == ".tsv") && totalLines > 20) {
            out["note"] = (out.contains("note") ? out["note"].get<std::string>() + " " : std::string()) + "To total, count or compare this data, read the file by its path in a script "
                          "(pandas.read_csv or the csv module). Don't paste its rows into a command.";
        }
        // Rows that are not data, and labels that differ only by case or spacing, are what a
        // small model's script totals wrongly.
        if ((ext == ".csv" || ext == ".tsv") && !truncated && startLine <= 0 && endLine <= 0) {
            std::vector<std::string> rows;
            {
                std::istringstream in(content);
                for (std::string line; std::getline(in, line);) {
                    if (!line.empty() && line.back() == '\r') line.pop_back();
                    rows.push_back(line);
                }
            }
            auto blank = [](const std::string& r) {
                return std::all_of(r.begin(), r.end(), [](unsigned char c) { return std::isspace(c) || c == ',' || c == ';'; });
            };
            auto folded = [](std::string v) {
                while (!v.empty() && std::isspace(static_cast<unsigned char>(v.back()))) v.pop_back();
                size_t lead = 0;
                while (lead < v.size() && std::isspace(static_cast<unsigned char>(v[lead]))) ++lead;
                v.erase(0, lead);
                for (auto& c : v) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
                return v;
            };
            if (!rows.empty() && !blank(rows[0])) {
                const std::string& header = rows[0];
                const char sep = ext == ".tsv" ? '\t'
                                 : (header.find(';') != std::string::npos && header.find(',') == std::string::npos) ? ';' : ',';
                auto cells = [sep](const std::string& r) {
                    std::vector<std::string> out;
                    std::string cell;
                    bool quoted = false;
                    for (char c : r) {
                        if (c == '"') quoted = !quoted;
                        else if (c == sep && !quoted) { out.push_back(cell); cell.clear(); }
                        else cell.push_back(c);
                    }
                    out.push_back(cell);
                    return out;
                };
                const std::vector<std::string> names = cells(header);
                std::vector<std::string> found;
                size_t dataRows = 0, blanks = 0;
                std::vector<std::vector<std::string>> data;
                std::vector<std::map<std::string, std::set<std::string>>> spellings(names.size());
                for (size_t i = 1; i < rows.size(); ++i) {
                    if (blank(rows[i])) {
                        if (i + 1 < rows.size()) ++blanks;
                        continue;
                    }
                    const std::string line = std::to_string(i + 1);
                    if (rows[i] == header) {
                        found.push_back("line " + line + " repeats the header");
                        continue;
                    }
                    const std::vector<std::string> row = cells(rows[i]);
                    const std::string first = folded(row[0]);
                    if (first == "total" || first == "totals" || first == "grand total" || first == "subtotal" ||
                        first == "sum") {
                        found.push_back("line " + line + " is a totals row, not data");
                        continue;
                    }
                    ++dataRows;
                    data.push_back(row);
                    for (size_t c = 0; c < row.size() && c < names.size(); ++c) {
                        const std::string key = folded(row[c]);
                        const bool text = std::any_of(key.begin(), key.end(), [](unsigned char ch) { return std::isalpha(ch); });
                        if (text && key.size() <= 40) spellings[c][key].insert(row[c]);
                    }
                }
                if (blanks) found.push_back(std::to_string(blanks) + " blank line" + (blanks == 1 ? "" : "s") + " among the rows");
                for (size_t c = 0; c < names.size(); ++c) {
                    for (const auto& [key, raw] : spellings[c]) {
                        if (raw.size() < 2) continue;
                        std::string list;
                        for (const auto& r : raw) list += (list.empty() ? "\"" : ", \"") + r + "\"";
                        found.push_back("column " + folded(names[c]) + " spells one value several ways (" + list + ")");
                        break;
                    }
                }
                std::string note;
                if (dataRows == 0) {
                    note = "This file has a header row and no data rows. Its data is missing, not zero: say that, "
                           "report the figure as unknown, and look for a log or error beside it that says why.";
                    std::string logs;
                    std::error_code ec;
                    int budget = 200, named = 0;
                    for (fs::directory_iterator it(fs::u8path(path).parent_path(), ec), end;
                         it != end && !ec && budget-- > 0 && named < 3; it.increment(ec)) {
                        std::string e = it->path().extension().u8string();
                        for (auto& c : e) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
                        if (!it->is_regular_file(ec) || (e != ".log" && e != ".txt" && e != ".err")) continue;
                        logs += (named++ ? ", " : "") + it->path().filename().u8string();
                    }
                    if (named) note += " In the same folder: " + logs + ". Read it and tell the user what it says.";
                } else if (!found.empty() || sep == ';') {
                    if (sep == ';') found.insert(found.begin(), "fields are separated by semicolons (sep=';')");
                    if (found.size() > 6) found.resize(6);
                    for (const auto& f : found) note += (note.empty() ? "Before totalling, note: " : "; ") + f;
                    note += ". Handle these in the script (skip those lines, strip and lower-case that column), "
                            "and tell the user what you cleaned.";
                }
                const std::string summary = tableSummary(names, data);
                if (!summary.empty()) {
                    out["summary"] = summary;
                    note += std::string(note.empty() ? "" : " ") +
                            "The summary field was computed from every data row, with header repeats, blank lines "
                            "and totals rows left out and labels matched ignoring case and spacing. Take totals, "
                            "counts, averages and shares from it. A label or month it doesn't list is not in the file: "
                            "say it is absent instead of reporting 0 for it.";
                }
                if (!note.empty()) out["note"] = out.contains("note") ? out["note"].get<std::string>() + " " + note : note;
            }
        }
        // policy_v1.md beside policy_v2.md, or terms_2024.md beside terms_2026.txt: a small
        // model answers from whichever it opened first.
        static const std::set<std::string> kDocuments = {".md", ".txt", ".pdf", ".docx", ".html", ".htm", ".rst"};
        auto series = [](const fs::path& file, long& number) -> std::string {
            std::string ext = file.extension().u8string();
            for (auto& c : ext) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
            if (!kDocuments.count(ext)) return "";
            std::string stem = file.stem().u8string();
            for (auto& c : stem) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
            size_t digits = stem.size();
            while (digits && std::isdigit(static_cast<unsigned char>(stem[digits - 1]))) --digits;
            const std::string num = stem.substr(digits);
            if (num.empty() || num.size() > 6) return "";
            const bool versioned = digits && stem[digits - 1] == 'v';
            const bool year = num.size() == 4 && (num.compare(0, 2, "19") == 0 || num.compare(0, 2, "20") == 0);
            if (!versioned && !year) return "";
            const std::string key = stem.substr(0, versioned ? digits - 1 : digits);
            if (key.empty() || std::isalnum(static_cast<unsigned char>(key.back()))) return "";
            number = std::stol(num);
            return key;
        };
        long mine = 0;
        const fs::path self = fs::u8path(path);
        const std::string key = series(self, mine);
        if (!key.empty()) {
            std::error_code ec;
            long latest = mine, previous = -1;
            std::string later, earlier;
            int budget = 500;
            for (fs::directory_iterator it(self.parent_path(), ec), end; it != end && !ec && budget-- > 0;
                 it.increment(ec)) {
                long theirs = 0;
                if (!it->is_regular_file(ec) || series(it->path(), theirs) != key) continue;
                if (theirs > latest) {
                    latest = theirs;
                    later = it->path().filename().u8string();
                } else if (theirs < mine && theirs > previous) {
                    previous = theirs;
                    earlier = it->path().filename().u8string();
                }
            }
            if (later.empty() && !earlier.empty()) {
                const std::string note = earlier + " in the same folder looks like an earlier version of this "
                                         "document. Read it too: if the two give different figures, give the user "
                                         "both, the old figure and this one, and say this version replaces the "
                                         "earlier one, since they may be relying on the old figure.";
                out["note"] = out.contains("note") ? out["note"].get<std::string>() + " " + note : note;
            }
            if (!later.empty()) {
                const std::string note = later + " in the same folder looks like a later version of this document. "
                                         "Read it too: where the two disagree, the later one applies unless the "
                                         "user asked about this one. Give the user both figures, the old and the "
                                         "new, and say that this one is superseded.";
                out["note"] = out.contains("note") ? out["note"].get<std::string>() + " " + note : note;
            }
        }
        return out;
    } catch (const std::exception& e) {
        return json{{"error", std::string("file_read failed: ") + e.what()}};
    }
}

TextKind textKind(const std::string& head) {
    if (head.empty()) return TextKind::Utf8;
    const auto byte = [&head](size_t i) { return static_cast<unsigned char>(head[i]); };
    if (head.size() >= 2 && ((byte(0) == 0xFF && byte(1) == 0xFE) || (byte(0) == 0xFE && byte(1) == 0xFF))) {
        return TextKind::Utf16;
    }
    // UTF-16 without a mark: every other byte is NUL.
    size_t evenNul = 0, oddNul = 0;
    for (size_t i = 0; i < head.size(); ++i) (i % 2 ? oddNul : evenNul) += head[i] == '\0';
    const size_t half = head.size() / 2;
    if (half >= 8 && (evenNul * 10 > half * 8 || oddNul * 10 > half * 8)) return TextKind::Utf16;
    if (evenNul + oddNul > 0) return TextKind::Binary;
    size_t invalid = 0, control = 0;
    for (size_t i = 0; i < head.size();) {
        const unsigned char c = byte(i);
        const size_t len = c < 0x80 ? 1 : (c >> 5) == 0x6 ? 2 : (c >> 4) == 0xE ? 3 : (c >> 3) == 0x1E ? 4 : 0;
        if (len > 1 && i + len > head.size()) break;  // a character cut at the end of the sample
        bool ok = len > 0;
        for (size_t k = 1; ok && k < len; ++k) ok = (byte(i + k) & 0xC0) == 0x80;
        if (!ok) {
            ++invalid;
            ++i;
            continue;
        }
        if (len == 1 && c < 0x20 && c != '\t' && c != '\n' && c != '\r' && c != '\f') ++control;
        i += len;
    }
    if (control * 20 > head.size()) return TextKind::Binary;
    if (invalid * 10 > head.size()) return control * 100 > head.size() ? TextKind::Binary : TextKind::LegacyEncoding;
    // A sprinkling of legacy bytes (an accented name in Windows-1252) still reads mostly fine.
    return TextKind::Utf8;
}

bool looksBinary(const std::string& head) { return textKind(head) == TextKind::Binary; }

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
        const std::string& given = args["content"].get_ref<const std::string&>();

        const std::string notAFile = notAFileReason(path);
        if (!notAFile.empty()) return json{{"error", notAFile}};

        const std::string credential = credentialFileReason(path);
        if (!credential.empty()) return json{{"error", credential}};

        FileStateTracker& tracker = FileStateTracker::instance();
        bool recreated = false;
        bool keepCrlf = false;
        std::error_code existsEc;
        if (fs::exists(fs::u8path(path), existsEc) && !existsEc) {
            const auto divergence = tracker.checkFile(path);
            if (divergence.diverged) {
                return staleRejection(path, "file_write", divergence);
            }
            if (given.find("[redacted]") != std::string::npos) {
                std::string current;
                if (!readWholeFile(path, current)) {
                    return json{{"error", "Cannot read " + path +
                                          " to check what the write would replace. Nothing was written."}};
                }
                const std::string masked = maskedSecretReason(path, current, "", given);
                if (!masked.empty()) {
                    return json{{"error", masked}, {"error_type", "redacted_secret"}, {"path", path}};
                }
            }
            if (given.find('\n') != std::string::npos && given.find('\r') == std::string::npos) {
                LineBreaks existing;
                if (!lineBreaksOfFile(path, existing)) {
                    return json{{"error", "Cannot read " + path + " to keep its line endings. Nothing was written."}};
                }
                keepCrlf = existing.crlfFile();
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
        const fs::path filePath = fs::u8path(path);
        if (filePath.has_parent_path()) {
            std::error_code ec;
            fs::create_directories(filePath.parent_path(), ec);
            if (ec) {
                return json{{"error", "Failed to create parent directories: " + ec.message()}};
            }
        }

        const std::string converted = keepCrlf ? lfToCrlf(given) : std::string();
        const std::string& content = keepCrlf ? converted : given;

        const std::string writeError = replaceFileContents(path, content);
        if (!writeError.empty()) return json{{"error", writeError}};

        tracker.recordWrite(path, content);

        json result{
            {"success", true},
            {"path", path},
            {"bytes_written", static_cast<int>(content.size())},
            {"content_hash", shortHash(FileStateTracker::hashContent(content))},
        };
        if (keepCrlf) result["line_endings"] = "crlf";
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

        const std::string notAFile = notAFileReason(path);
        if (!notAFile.empty()) return json{{"error", notAFile}};

        const std::string credential = credentialFileReason(path);
        if (!credential.empty()) return json{{"error", credential}};

        std::string content;
        if (!readWholeFile(path, content)) {
            return json{{"error", "Cannot open file: " + path}};
        }

        const LineBreaks breaks = lineBreaksOf(content);
        const bool crlfFile = breaks.crlfFile();
        const std::string contentLf = breaks.crlf > 0 ? crlfToLf(content) : std::string();
        const std::string& shown = crlfFile ? contentLf : content;
        const std::string oldLf = crlfToLf(oldStr);
        const std::string newLf = crlfToLf(newStr);

        const std::string masked = maskedSecretReason(path, content, oldStr, newStr);
        if (!masked.empty()) {
            return json{{"error", masked}, {"error_type", "redacted_secret"}, {"path", path}};
        }

        FileStateTracker& tracker = FileStateTracker::instance();
        const auto divergence = tracker.check(path, content);
        if (divergence.diverged) {
            json rejection = staleRejection(path, "file_edit", divergence);
            rejection.update(excerptAround(shown, anchorLineFor(shown, oldLf)));
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
        // Mixed endings: the bytes as given first, so an exact edit touches nothing else.
        bool matchedLf = breaks.crlf > 0 && !breaks.mixed();
        auto offsets = matchedLf ? matchOffsets(contentLf, oldLf)
                                 : matchOffsets(content, breaks.mixed() ? oldStr : oldLf);
        if (offsets.empty() && breaks.mixed()) {
            offsets = matchOffsets(contentLf, oldLf);
            matchedLf = !offsets.empty();
        }
        const std::string& searched = matchedLf ? contentLf : content;

        if (offsets.empty()) {
            // A silent no-op is the failure mode this tool exists to avoid:
            // say what did not match and what to do about it.
            std::string hint;
            if (collapseWhitespace(content).find(collapseWhitespace(oldLf)) !=
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
            result.update(excerptAround(shown, anchorLineFor(shown, oldLf)));
            return result;
        }

        if (offsets.size() > 1) {
            std::string lineList;
            json matchLines = json::array();
            for (const auto offset : offsets) {
                const int lineNo = lineOfOffset(searched, offset);
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
            result.update(excerptAround(shown, lineOfOffset(searched, offsets[0])));
            return result;
        }

        if (matchedLf) {
            const size_t from = rawOffset(content, offsets[0]);
            const size_t to = rawOffset(content, offsets[0] + oldLf.size());
            content.replace(from, to - from, crlfFile ? lfToCrlf(newLf) : newLf);
        } else if (breaks.mixed()) {
            content.replace(offsets[0], oldStr.size(), newStr);
        } else {
            content.replace(offsets[0], oldLf.size(), newLf);
        }
        const int replacements = 1;

        const std::string writeError = replaceFileContents(path, content);
        if (!writeError.empty()) return json{{"error", writeError}};

        tracker.recordWrite(path, content);

        json result{
            {"success", true},
            {"path", path},
            {"replacements", replacements},
            {"content_hash", shortHash(FileStateTracker::hashContent(content))},
        };
        if (crlfFile) result["line_endings"] = "crlf";
        return result;
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

        const fs::path searchDir = fs::u8path(searchPath);
        if (!fs::exists(searchDir)) {
            return json{{"error", "Search path does not exist: " + searchPath}};
        }

        if (!fs::is_directory(searchDir)) {
            return json{{"error", "Search path is not a directory: " + searchPath}};
        }

        // Rules governing the root are resolved up front; each subdirectory's
        // own .gitignore is folded in as the walk reaches it. Rules are scoped
        // to their own directory, so a nested file cannot affect a sibling.
        GitignoreMatcher ignore = GitignoreMatcher::forDirectory(searchPath);

        std::error_code rootEc;
        const fs::path root = fs::canonical(searchDir, rootEc);
        if (rootEc) {
            return json{{"error", "Cannot resolve search path " + searchPath + ": " + rootEc.message()}};
        }
        // By resolved path, not by is_symlink: a Windows junction is not a symlink.
        const auto resolvesInsideRoot = [&root](const fs::path& entry) {
            std::error_code linkEc;
            const fs::path target = fs::canonical(entry, linkEc);
            if (linkEc) return false;
            const fs::path fromRoot = target.lexically_relative(root);
            return !fromRoot.empty() && *fromRoot.begin() != fs::path("..");
        };

        // A pattern with a separator addresses a path; otherwise a file name.
        const bool pathPattern = pattern.find('/') != std::string::npos;
        const GlobOptions globOpts{/*pathMode=*/pathPattern,
                                   /*caseInsensitive=*/false};

        json matches = json::array();
        int total = 0;
        int ignoredSkipped = 0;
        int credentialSkipped = 0;
        int tooLargeSkipped = 0;
        // getline reads a file with no line breaks into one string.
        constexpr std::uintmax_t kMaxSearchedFileBytes = 64u * 1024u * 1024u;

        std::error_code ec;
        for (auto it = fs::recursive_directory_iterator(searchDir, fs::directory_options::skip_permission_denied, ec);
             it != fs::recursive_directory_iterator(); it.increment(ec)) {
            if (ec) {
                ec.clear();
                continue;
            }

            const fs::path& entryPath = it->path();
            const std::string filename = entryPath.filename().u8string();

            if (it->is_directory(ec) && !ec) {
                // A folder link is never walked, even one that stays inside: it can loop back on itself.
                const fs::file_status linkStatus = it->symlink_status(ec);
                bool folderLink = ec || fs::is_symlink(linkStatus);
#ifdef _MSC_VER
                folderLink = folderLink || linkStatus.type() == fs::file_type::junction;
#endif
                ec.clear();
                if (folderLink || !resolvesInsideRoot(entryPath)) {
                    it.disable_recursion_pending();
                    continue;
                }
                // .git is never interesting and is huge; it is pruned always
                // and is not counted as an ignore-rule skip, or the counter
                // would be non-zero on every repository and say nothing.
                if (filename == ".git") {
                    it.disable_recursion_pending();
                    continue;
                }
                if (ignore.isIgnored(entryPath.u8string(), /*isDirectory=*/true)) {
                    it.disable_recursion_pending();
                    ++ignoredSkipped;
                    continue;
                }
                ignore.addFile((entryPath / ".gitignore").u8string());
                continue;
            }
            ec.clear();

            if (!it->is_regular_file(ec)) continue;
            if (ec) { ec.clear(); continue; }

            // A file's own folder was checked on the way in, so only a link can lead out.
            const bool fileLink = it->is_symlink(ec) || ec;
            ec.clear();
            if (fileLink && !resolvesInsideRoot(entryPath)) continue;

            if (ignore.isIgnored(entryPath.u8string(), /*isDirectory=*/false)) {
                ++ignoredSkipped;
                continue;
            }

            bool nameMatches;
            if (pathPattern) {
                std::error_code relEc;
                fs::path rel = fs::relative(entryPath, root, relEc);
                const std::string relStr =
                    relEc ? entryPath.generic_u8string() : rel.generic_u8string();
                nameMatches = globMatch(pattern, relStr, globOpts);
            } else {
                nameMatches = globMatch(pattern, filename, globOpts);
            }
            if (!nameMatches) continue;

            // If content_pattern is specified, search within file
            if (!contentPattern.empty()) {
                // A match line would hand over what file_read refuses to show.
                if (isCredentialFileName(filename)) {
                    ++credentialSkipped;
                    continue;
                }
                std::error_code sizeEc;
                if (fs::file_size(entryPath, sizeEc) > kMaxSearchedFileBytes && !sizeEc) {
                    ++tooLargeSkipped;
                    continue;
                }
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
                            match["path"] = entryPath.generic_u8string();
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
                    match["path"] = entryPath.generic_u8string();
                    matches.push_back(std::move(match));
                }
            }
        }

        json result{
            {"matches", matches},
            {"total", total},
            {"ignored_skipped", ignoredSkipped},
        };
        if (credentialSkipped > 0) result["credential_files_skipped"] = credentialSkipped;
        if (tooLargeSkipped > 0) result["files_over_64mb_skipped"] = tooLargeSkipped;
        return result;
    } catch (const std::exception& e) {
        return json{{"error", std::string("file_search failed: ") + e.what()}};
    }
}

} // namespace gaia
