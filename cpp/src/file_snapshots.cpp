// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/file_snapshots.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <ctime>
#include <fstream>
#include <regex>
#include <sstream>

#include "gaia/security.h"
#include "gaia/skill_signing.h"

namespace gaia {

namespace fs = std::filesystem;

namespace {

constexpr int kManifestVersion = 1;
constexpr std::uintmax_t kMaxAfterCopyBytes = 8ull * 1024 * 1024;

std::string checkId(const std::string& kind, const std::string& value) {
    static const std::regex safe(R"(^[A-Za-z0-9_.-]{1,128}$)");
    if (!std::regex_match(value, safe) || value == "." || value == "..") {
        throw SnapshotError("Invalid " + kind + " '" + value + "'.");
    }
    return value;
}

std::string nowIso() {
    const std::time_t t = std::time(nullptr);
    std::tm utc{};
#ifdef _WIN32
    gmtime_s(&utc, &t);
#else
    gmtime_r(&t, &utc);
#endif
    char buf[32];
    std::strftime(buf, sizeof buf, "%Y-%m-%dT%H:%M:%S+00:00", &utc);
    return buf;
}

std::string readAll(const fs::path& p) {
    std::ifstream in(p, std::ios::binary);
    if (!in) throw std::runtime_error("can't read " + p.u8string());
    std::ostringstream out;
    out << in.rdbuf();
    return out.str();
}

std::string sha256File(const fs::path& p) { return skillSha256Hex(readAll(p)); }

// One file reached by two spellings is one entry: Windows paths compare without case.
std::string key(const fs::path& p) {
    std::string s = p.u8string();
#ifdef _WIN32
    std::replace(s.begin(), s.end(), '/', '\\');
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
#endif
    return s;
}

json newEntry(const fs::path& target, const std::string& tool) {
    return {{"path", target.u8string()}, {"tool", tool.empty() ? json(nullptr) : json(tool)},
            {"existed", false},          {"before_blob", nullptr},
            {"before_sha256", nullptr},  {"before_size", nullptr},
            {"mode", nullptr},           {"created_dirs", json::array()},
            {"after_exists", nullptr},   {"after_blob", nullptr},
            {"after_sha256", nullptr}};
}

void blobName(char* buf, size_t n, size_t index, const char* suffix) {
    std::snprintf(buf, n, "%04zu.%s", index, suffix);
}

}  // namespace

TurnRecorder::TurnRecorder(const std::string& sessionId, const std::string& turnId, const fs::path& root) {
    dir_ = root / checkId("session id", sessionId) / checkId("turn id", turnId);
    manifest_ = {{"version", kManifestVersion}, {"session_id", sessionId}, {"turn_id", turnId},
                 {"message_id", nullptr},       {"started_at", nowIso()},  {"finished_at", nullptr},
                 {"restored_at", nullptr},      {"files", json::array()},  {"untracked", json::array()}};
}

fs::path TurnRecorder::defaultRoot() { return fs::u8path(gaiaHome()) / "snapshots"; }

bool TurnRecorder::hasChanges() const {
    if (!manifest_["files"].empty()) return true;
    for (const auto& u : manifest_["untracked"]) {
        if (u.value("may_write", true)) return true;
    }
    return false;
}

void TurnRecorder::save() {
    std::error_code ec;
    fs::create_directories(dir_, ec);
    if (ec) throw SnapshotError("can't create " + dir_.u8string() + ": " + ec.message());
    const fs::path path = dir_ / "manifest.json";
    const fs::path tmp = dir_ / "manifest.json.tmp";
    {
        std::ofstream out(tmp, std::ios::binary | std::ios::trunc);
        out << manifest_.dump(2);
        if (!out) throw SnapshotError("can't write " + tmp.u8string());
    }
    fs::rename(tmp, path, ec);
    if (ec) throw SnapshotError("can't replace " + path.u8string() + ": " + ec.message());
}

bool TurnRecorder::capture(const std::string& path, const std::string& tool) {
    std::lock_guard<std::mutex> lock(mu_);
    std::error_code ec;
    const fs::path absolute = fs::absolute(fs::u8path(path), ec);
    if (ec) throw SnapshotError("Could not resolve " + path + " to save a copy before changing it: " + ec.message());
    const fs::path target = fs::weakly_canonical(absolute, ec);
    if (ec) throw SnapshotError("Could not resolve " + path + " to save a copy before changing it: " + ec.message());
    if (closed_) {
        noteLate("late_write", target.u8string());
        return false;
    }
    for (const auto& e : manifest_["files"]) {
        if (key(fs::u8path(e["path"].get<std::string>())) == key(target)) return false;
    }
    json entry = newEntry(target, tool);
    try {
        if (fs::is_directory(target)) {
            throw SnapshotError(target.u8string() + " is a folder; only files can be saved for undo.");
        }
        if (fs::exists(target)) {
            char blob[32];
            blobName(blob, sizeof blob, manifest_["files"].size(), "before");
            fs::create_directories(dir_ / "blobs");
            fs::copy_file(target, dir_ / "blobs" / blob, fs::copy_options::overwrite_existing);
            entry["existed"] = true;
            entry["before_blob"] = blob;
            entry["before_sha256"] = sha256File(dir_ / "blobs" / blob);
            entry["before_size"] = fs::file_size(target);
            entry["mode"] = static_cast<int>(fs::status(target).permissions() & fs::perms::mask) & 07777;
        } else {
            json created = json::array();
            std::vector<std::string> missing;
            for (fs::path parent = target.parent_path(); parent != parent.parent_path() && !fs::exists(parent);
                 parent = parent.parent_path()) {
                missing.push_back(parent.u8string());
            }
            for (auto it = missing.rbegin(); it != missing.rend(); ++it) created.push_back(*it);
            entry["created_dirs"] = created;
        }
    } catch (const SnapshotError&) {
        throw;
    } catch (const std::exception& e) {
        throw SnapshotError("Could not save a copy of " + target.u8string() + " before changing it, so it was left "
                            "unchanged: " + e.what() + ". Check free space and permissions under " +
                            dir_.parent_path().parent_path().u8string() + ".");
    }
    manifest_["files"].push_back(entry);
    try {
        save();
    } catch (const SnapshotError& e) {
        throw SnapshotError("Could not save the undo record for " + target.u8string() +
                            ", so it was left unchanged: " + e.what());
    }
    return true;
}

void TurnRecorder::discard(const std::string& path) {
    std::lock_guard<std::mutex> lock(mu_);
    if (closed_) return;
    std::error_code ec;
    const fs::path target = fs::weakly_canonical(fs::absolute(fs::u8path(path), ec), ec);
    json& files = manifest_["files"];
    if (files.empty() || key(fs::u8path(files.back()["path"].get<std::string>())) != key(target)) return;
    const json entry = files.back();
    files.erase(files.size() - 1);
    if (entry["before_blob"].is_string()) {
        const fs::path blob = dir_ / "blobs" / entry["before_blob"].get<std::string>();
        fs::remove(blob, ec);
        if (ec) throw SnapshotError("Could not remove the copy at " + blob.u8string() + ": " + ec.message());
    }
    // Folders the tool made before it failed: undo would have removed them, so remove them now.
    const json& made = entry["created_dirs"];
    for (auto it = made.rbegin(); it != made.rend(); ++it) {
        const fs::path dir = fs::u8path(it->get<std::string>());
        if (fs::is_directory(dir, ec) && fs::is_empty(dir, ec)) fs::remove(dir, ec);
    }
    if (hasChanges()) {
        save();
    } else {
        // Nothing left to undo: no record, as for a turn that changed nothing.
        fs::remove_all(dir_, ec);
        if (ec) throw SnapshotError("Could not remove the empty undo record at " + dir_.u8string() + ": " + ec.message());
    }
}

void TurnRecorder::noteLate(const std::string& kind, const std::string& detail) {
    const fs::path path = dir_ / "manifest.json";
    std::error_code ec;
    if (!fs::is_regular_file(path, ec)) return;  // the turn changed nothing and left no record
    std::ifstream in(path, std::ios::binary);
    json onDisk = json::parse(in, nullptr, false);
    in.close();
    if (onDisk.is_discarded() || !onDisk.is_object()) {
        throw SnapshotError("The undo record at " + path.u8string() + " can't be read to note a late write.");
    }
    // The record on disk, not this copy: an undo may already have been saved there.
    onDisk["untracked"].push_back({{"kind", kind},
                                   {"detail", "after the turn ended: " + detail.substr(0, 480)},
                                   {"paths", json::array()},
                                   {"may_write", true}});
    const json kept = manifest_;
    manifest_ = onDisk;
    try {
        save();
    } catch (...) {
        manifest_ = kept;
        throw;
    }
    manifest_ = kept;
}

void TurnRecorder::noteUntracked(const std::string& kind, const std::string& detail, bool mayWrite) {
    std::lock_guard<std::mutex> lock(mu_);
    if (closed_) {
        if (mayWrite) noteLate(kind, detail);
        return;
    }
    manifest_["untracked"].push_back({{"kind", kind},
                                      {"detail", detail.substr(0, 500)},
                                      {"paths", json::array()},
                                      {"may_write", mayWrite}});
    if (mayWrite) save();
}

void TurnRecorder::finish() {
    std::lock_guard<std::mutex> lock(mu_);
    if (finished_) return;
    closed_ = true;
    if (!hasChanges()) {
        finished_ = true;
        return;
    }
    try {
        fs::create_directories(dir_ / "blobs");
        size_t i = 0;
        for (auto& entry : manifest_["files"]) {
            const fs::path target = fs::u8path(entry["path"].get<std::string>());
            std::error_code ec;
            if (fs::is_regular_file(target, ec)) {
                const auto size = fs::file_size(target);
                json blob = nullptr;
                fs::path hashed = target;
                if (size <= kMaxAfterCopyBytes) {
                    char name[32];
                    blobName(name, sizeof name, i, "after");
                    fs::copy_file(target, dir_ / "blobs" / name, fs::copy_options::overwrite_existing);
                    blob = name;
                    hashed = dir_ / "blobs" / name;
                }
                entry["after_exists"] = true;
                entry["after_blob"] = blob;
                entry["after_size"] = size;
                entry["after_sha256"] = sha256File(hashed);
            } else {
                entry["after_exists"] = false;
                entry["after_blob"] = nullptr;
                entry["after_size"] = nullptr;
                entry["after_sha256"] = nullptr;
            }
            ++i;
        }
        manifest_["finished_at"] = nowIso();
        save();
    } catch (const std::exception& e) {
        throw SnapshotError("Could not save the end state of turn " + manifest_["turn_id"].get<std::string>() +
                            ": " + e.what() + ". Undo for this turn may refuse; check free space under " +
                            dir_.parent_path().parent_path().u8string() + ".");
    }
    finished_ = true;
}

}  // namespace gaia
