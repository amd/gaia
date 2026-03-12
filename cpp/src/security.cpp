// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/security.h"

#include <algorithm>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

#include "gaia/clean_console.h"

#ifdef _WIN32
#   include <windows.h>
#   include <direct.h>
#else
#   include <climits>
#   include <cstdlib>
#   include <sys/stat.h>
#endif

#include <nlohmann/json.hpp>

namespace gaia {

using json = nlohmann::json;

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

namespace {

/// Create all missing directories along the given path.
void mkdirp(const std::string& path) {
#ifdef _WIN32
    // Walk path segments and create each one.
    for (size_t i = 0; i < path.size(); ++i) {
        if ((path[i] == '\\' || path[i] == '/') && i > 0) {
            std::string sub = path.substr(0, i);
            ::CreateDirectoryA(sub.c_str(), nullptr);
        }
    }
    ::CreateDirectoryA(path.c_str(), nullptr);
#else
    for (size_t i = 1; i < path.size(); ++i) {
        if (path[i] == '/') {
            std::string sub = path.substr(0, i);
            ::mkdir(sub.c_str(), 0755);
        }
    }
    ::mkdir(path.c_str(), 0755);
#endif
}

std::string canonicalize(const std::string& path) {
#ifdef _WIN32
    char buf[MAX_PATH];
    if (::GetFullPathNameA(path.c_str(), MAX_PATH, buf, nullptr) == 0) {
        return "";
    }
    return std::string(buf);
#else
    char buf[PATH_MAX];
    if (::realpath(path.c_str(), buf) == nullptr) {
        return "";
    }
    return std::string(buf);
#endif
}

} // anonymous namespace

// ---------------------------------------------------------------------------
// validatePath
// ---------------------------------------------------------------------------

bool validatePath(const std::string& basePath, const std::string& requestedPath) {
    if (basePath.empty() || requestedPath.empty()) {
        return false;
    }

    std::string base = canonicalize(basePath);
    std::string req  = canonicalize(requestedPath);

    if (base.empty() || req.empty()) {
        return false;
    }

#ifdef _WIN32
    // GetFullPathNameA returns backslashes; normalize to forward slash so the
    // prefix comparison below works regardless of which separator appears.
    std::replace(base.begin(), base.end(), '\\', '/');
    std::replace(req.begin(),  req.end(),  '\\', '/');
#endif

    // Ensure base ends with separator for prefix comparison
    if (base.back() != '/') {
        base += '/';
    }

    return req.substr(0, base.size()) == base || req == base.substr(0, base.size() - 1);
}

// ---------------------------------------------------------------------------
// isSafeShellArg
// ---------------------------------------------------------------------------

bool isSafeShellArg(const std::string& arg) {
    if (arg.empty()) {
        return false;
    }
    // Reject characters with special meaning in POSIX or Windows shells.
    // Backslash is intentionally excluded: it is a path separator on Windows
    // and must be accepted for paths like "C:\Users\foo". Callers that target
    // POSIX shells exclusively should add their own backslash check.
    static const std::string kUnsafe = " \t\n\r;|&<>$`\"'!{}()[]~*?#^%=";
    return arg.find_first_of(kUnsafe) == std::string::npos;
}

// ---------------------------------------------------------------------------
// makeStdinConfirmCallback
// ---------------------------------------------------------------------------

ToolConfirmCallback makeStdinConfirmCallback() {
    return [](const std::string& toolName, const json& /*args*/) -> ToolConfirmResult {
        std::cerr << "\n"
                  << "  \"" << toolName << "\" requires confirmation. Allow this tool to run?\n"
                  << color::CYAN
                  << "  ============================================\n"
                  << color::RESET
                  << color::YELLOW << "  [1] " << color::RESET << color::WHITE << "Allow once\n"    << color::RESET
                  << color::YELLOW << "  [2] " << color::RESET << color::WHITE << "Always allow\n"  << color::RESET
                  << color::YELLOW << "  [3] " << color::RESET << color::WHITE << "Deny\n"          << color::RESET
                  << color::CYAN
                  << "  ============================================\n"
                  << color::RESET
                  << "  Choice: " << std::flush;

        std::string input;
        if (!std::getline(std::cin, input)) return ToolConfirmResult::DENY;
        if (input == "1" || input == "o" || input == "O") return ToolConfirmResult::ALLOW_ONCE;
        if (input == "2" || input == "a" || input == "A") return ToolConfirmResult::ALWAYS_ALLOW;
        return ToolConfirmResult::DENY;  // 3, d, D, or anything else
    };
}

// ---------------------------------------------------------------------------
// AllowedToolsStore
// ---------------------------------------------------------------------------

std::string AllowedToolsStore::defaultConfigDir() {
#ifdef _WIN32
    const char* profile = std::getenv("USERPROFILE");
    std::string home = profile ? profile : "C:\\Users\\Default";
    return home + "\\.gaia\\security";
#else
    const char* home = std::getenv("HOME");  // NOLINT(concurrency-mt-unsafe)
    std::string h = home ? home : "/tmp";
    return h + "/.gaia/security";
#endif
}

AllowedToolsStore::AllowedToolsStore()
    : AllowedToolsStore(defaultConfigDir()) {}

AllowedToolsStore::AllowedToolsStore(const std::string& dir) {
    mkdirp(dir);
#ifdef _WIN32
    filePath_ = dir + "\\allowed_tools.json";
#else
    filePath_ = dir + "/allowed_tools.json";
#endif
    load();
}

bool AllowedToolsStore::isAlwaysAllowed(const std::string& toolName) const {
    return allowed_.count(toolName) > 0;
}

void AllowedToolsStore::addAlwaysAllowed(const std::string& toolName) {
    allowed_.insert(toolName);
    save();
}

void AllowedToolsStore::removeAlwaysAllowed(const std::string& toolName) {
    allowed_.erase(toolName);
    save();
}

void AllowedToolsStore::clearAll() {
    allowed_.clear();
    save();
}

std::vector<std::string> AllowedToolsStore::allAllowed() const {
    return std::vector<std::string>(allowed_.begin(), allowed_.end());
}

void AllowedToolsStore::load() {
    std::ifstream f(filePath_);
    if (!f.is_open()) {
        return; // File doesn't exist yet — start empty
    }
    try {
        json j = json::parse(f);
        if (j.contains("allowed_tools") && j["allowed_tools"].is_array()) {
            for (const auto& item : j["allowed_tools"]) {
                if (item.is_string()) {
                    allowed_.insert(item.get<std::string>());
                }
            }
        }
    } catch (...) {
        // Corrupt file — start empty
        allowed_.clear();
    }
}

void AllowedToolsStore::save() const {
    json j;
    j["version"] = 1;
    j["allowed_tools"] = json::array();
    for (const auto& name : allowed_) {
        j["allowed_tools"].push_back(name);
    }
    std::ofstream f(filePath_);
    if (f.is_open()) {
        f << j.dump(2) << "\n";
    } else {
        std::cerr << "[gaia] warning: could not write allowed_tools store: " << filePath_ << "\n";
    }
}

} // namespace gaia
