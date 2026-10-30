// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/flagship_config.h"

#include <algorithm>
#include <cctype>
#include <cerrno>
#include <filesystem>
#include <fstream>
#include <stdexcept>

#include "gaia/model_registry.h"
#include "gaia/types.h"

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <csignal>
#include <sys/types.h>
#endif

namespace fs = std::filesystem;

namespace gaia {

namespace {

std::string trimmed(std::string s) {
    s.erase(0, s.find_first_not_of(" \t\r\n"));
    s.erase(s.find_last_not_of(" \t\r\n") + 1);
    return s;
}

std::string expandUser(const std::string& raw) {
    if (!raw.empty() && raw[0] == '~' && (raw.size() == 1 || raw[1] == '/' || raw[1] == '\\')) {
        return userHomeDir() + raw.substr(1);
    }
    return raw;
}

std::string envTrimmed(const char* name) {
    return trimmed(getEnvVar(name));
}

}  // namespace

std::string userHomeDir() {
#ifdef _WIN32
    std::string home = getEnvVar("USERPROFILE");
    if (home.empty()) {
        std::string drive = getEnvVar("HOMEDRIVE");
        std::string path = getEnvVar("HOMEPATH");
        if (!drive.empty() && !path.empty()) home = drive + path;
    }
#else
    std::string home = getEnvVar("HOME");
#endif
    if (home.empty()) {
        throw std::runtime_error(
            "Cannot find the home directory: neither USERPROFILE nor HOME is set. "
            "Set GAIA_HOME to the folder GAIA should keep its state in.");
    }
    return home;
}

std::string gaiaHomeDir() {
    for (const char* var : {"GAIA_HOME", "GAIA_CONFIG_DIR"}) {
        std::string raw = envTrimmed(var);
        if (!raw.empty()) return expandUser(raw);
    }
    return (fs::u8path(userHomeDir()) / ".gaia").u8string();
}

std::string gaiaConfigFile() {
    std::string file = envTrimmed("GAIA_CONFIG_FILE");
    if (!file.empty()) return expandUser(file);
    std::string dir = envTrimmed("GAIA_CONFIG_DIR");
    if (!dir.empty()) return (fs::u8path(expandUser(dir)) / "config.json").u8string();
    return (fs::u8path(gaiaHomeDir()) / "config.json").u8string();
}

std::string userSkillsDir() {
    std::string base = envTrimmed("GAIA_CONFIG_DIR");
    if (base.empty()) base = (fs::u8path(userHomeDir()) / ".gaia").u8string();
    return (fs::u8path(expandUser(base)) / "skills").u8string();
}

json loadGaiaConfig() {
    const std::string path = gaiaConfigFile();
    std::error_code ec;
    if (!fs::exists(fs::u8path(path), ec)) return json::object();
    std::ifstream in(fs::u8path(path), std::ios::binary);
    if (!in) {
        throw std::runtime_error("Cannot read GAIA config at " + path +
                                 ". Check its permissions, or set GAIA_CONFIG_FILE to another file.");
    }
    json j;
    try {
        in >> j;
    } catch (const json::parse_error& e) {
        throw std::runtime_error("GAIA config at " + path + " is not valid JSON (" + e.what() +
                                 "). Fix or delete the file; `gaia config show` prints it.");
    }
    if (!j.is_object()) {
        throw std::runtime_error("GAIA config at " + path +
                                 " must hold a JSON object. Fix or delete the file.");
    }
    return j;
}

std::string resolveDefaultChatModel() {
    json cfg = loadGaiaConfig();
    if (cfg.contains("default_model") && cfg["default_model"].is_string()) {
        std::string model = trimmed(cfg["default_model"].get<std::string>());
        if (!model.empty()) return model;
    }
    return kFlagshipDefaultModel;
}

std::optional<bool> envFlag(const std::string& name) {
    std::string raw = trimmed(getEnvVar(name.c_str()));
    if (raw.empty()) return std::nullopt;
    std::transform(raw.begin(), raw.end(), raw.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    if (raw == "1" || raw == "true" || raw == "yes" || raw == "on") return true;
    if (raw == "0" || raw == "false" || raw == "no" || raw == "off") return false;
    throw std::invalid_argument(name + "='" + raw +
                                "' is not a boolean. Use 1/0, true/false, yes/no or on/off.");
}

std::optional<int> envPositiveInt(const std::string& name) {
    std::string raw = trimmed(getEnvVar(name.c_str()));
    if (raw.empty()) return std::nullopt;
    std::size_t used = 0;
    int value = 0;
    try {
        value = std::stoi(raw, &used);
    } catch (const std::exception&) {
        used = 0;
    }
    if (used != raw.size() || value <= 0) {
        throw std::invalid_argument(name + "='" + raw + "' must be a positive integer.");
    }
    return value;
}

std::optional<std::vector<std::string>> envAllowedPaths() {
    std::string raw = trimmed(getEnvVar("GAIA_ALLOWED_PATHS"));
    if (raw.empty()) return std::nullopt;
#ifdef _WIN32
    const char sep = ';';
#else
    const char sep = ':';
#endif
    std::vector<std::string> out;
    std::size_t start = 0;
    while (start <= raw.size()) {
        std::size_t end = raw.find(sep, start);
        if (end == std::string::npos) end = raw.size();
        std::string entry = trimmed(raw.substr(start, end - start));
        start = end + 1;
        if (entry.empty()) continue;
        entry = expandUser(entry);
        std::error_code ec;
        fs::path p = fs::u8path(entry);
        if (!p.is_absolute() || !fs::is_directory(p, ec)) {
            throw std::invalid_argument(
                "GAIA_ALLOWED_PATHS entry '" + entry +
                "' is not an existing absolute folder. Fix or remove it; entries are separated by '" +
                std::string(1, sep) + "'.");
        }
        out.push_back(fs::weakly_canonical(p, ec).u8string());
    }
    return out;
}

namespace {

bool processAlive(long long pid) {
    if (pid <= 0) return false;
#ifdef _WIN32
    HANDLE h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, static_cast<DWORD>(pid));
    if (!h) return GetLastError() == ERROR_ACCESS_DENIED;  // exists, owned by someone else
    DWORD code = 0;
    const bool alive = GetExitCodeProcess(h, &code) && code == STILL_ACTIVE;
    CloseHandle(h);
    return alive;
#else
    return ::kill(static_cast<pid_t>(pid), 0) == 0 || errno == EPERM;
#endif
}

// GAIA's embedded server, or an empty object when there is none running.
// A record that exists but cannot be read sets @p unreadable instead.
json embeddedLemonadeState(std::string& unreadable) {
    const fs::path path = fs::u8path(gaiaHomeDir()) / "lemonade" / "state.json";
    std::ifstream in(path, std::ios::binary);
    if (!in) return json::object();
    const json state = json::parse(in, nullptr, false);
    const long long port = state.is_object() && state.contains("port") && state["port"].is_number_integer()
                               ? state["port"].get<long long>()
                               : 0;
    if (port < 1 || port > 65535) {
        unreadable = "GAIA's model server record " + path.u8string() +
                     " is unreadable (it needs a JSON object with a port from 1 to 65535). Restart GAIA's "
                     "model server so it writes the record again, or delete the file if that server is no "
                     "longer used.";
        return json::object();
    }
    // A server killed without `stop` leaves its record behind.
    if (state.contains("pid") && state["pid"].is_number_integer() && !processAlive(state["pid"].get<long long>())) {
        return json::object();
    }
    return state;
}

std::string withApiPath(std::string url) {
    while (!url.empty() && url.back() == '/') url.pop_back();
    const auto scheme = url.find("://");
    const auto slash = url.find('/', scheme == std::string::npos ? 0 : scheme + 3);
    return slash == std::string::npos ? url + "/api/v1" : url;
}

}  // namespace

LemonadeEndpoint resolveLemonadeEndpoint() {
    const bool embeddedOnly = envFlag("GAIA_LEMONADE_EMBEDDED").value_or(false);
    std::string unreadable;
    const json state = embeddedLemonadeState(unreadable);
    const std::string embeddedUrl =
        state.empty() ? "" : "http://localhost:" + std::to_string(state["port"].get<long long>()) + "/api/v1";
    const std::string embeddedKey = state.empty() ? "" : trimmed(state.value("api_key", ""));
    const std::string envKey = embeddedOnly ? "" : trimmed(getEnvVar("LEMONADE_API_KEY"));

    // The embedded key goes only to the embedded server's own port.
    auto keyFor = [&](const std::string& url) {
        if (!envKey.empty()) return envKey;
        if (embeddedUrl.empty()) return std::string();
        const auto port = ":" + std::to_string(state["port"].get<long long>());
        for (const char* host : {"http://localhost", "http://127.0.0.1", "http://[::1]"}) {
            const std::string base = std::string(host) + port;
            if (url.rfind(base, 0) == 0 && (url.size() == base.size() || url[base.size()] == '/')) return embeddedKey;
        }
        return std::string();
    };

    if (!embeddedOnly) {
        const std::string env = trimmed(getEnvVar("LEMONADE_BASE_URL"));
        if (!env.empty()) {
            const std::string url = withApiPath(env);
            return {url, keyFor(url), "env"};
        }
        const json cfg = loadGaiaConfig();
        if (cfg.value("lemonade_mode", "") == "external") {
            const std::string url = trimmed(cfg.value("lemonade_base_url", ""));
            if (url.empty()) {
                throw std::runtime_error(
                    "lemonade_mode is 'external' in " + gaiaConfigFile() +
                    " but lemonade_base_url is not set. Run `gaia lemonade connect <url>`, or remove "
                    "lemonade_mode to use GAIA's own server.");
            }
            return {withApiPath(url), keyFor(withApiPath(url)), "config"};
        }
    }
    if (!embeddedUrl.empty()) return {embeddedUrl, embeddedKey, "embedded"};
    // Guessing the default port over a broken record would reach another server, or none.
    if (!unreadable.empty()) throw std::runtime_error(unreadable);
    return {"http://localhost:13305/api/v1", envKey, "default"};
}

NativeToolCalls flagshipNativeToolCalls(const std::string& modelId) {
    for (const auto& m : knownModels()) {
        if (m.modelId == modelId) return m.toolCalling ? NativeToolCalls::Always : NativeToolCalls::Never;
    }
    return NativeToolCalls::Always;
}

std::string findExecutable(const std::string& name) {
    if (name.empty()) return "";
#ifdef _WIN32
    const char sep = ';';
    std::vector<std::string> exts = {""};
    std::string pathext = getEnvVar("PATHEXT", ".COM;.EXE;.BAT;.CMD");
    for (std::size_t b = 0; b <= pathext.size();) {
        std::size_t e = pathext.find(';', b);
        if (e == std::string::npos) e = pathext.size();
        if (e > b) exts.push_back(pathext.substr(b, e - b));
        b = e + 1;
    }
#else
    const char sep = ':';
    const std::vector<std::string> exts = {""};
#endif
    const std::string path = getEnvVar("PATH");
    for (std::size_t b = 0; b <= path.size();) {
        std::size_t e = path.find(sep, b);
        if (e == std::string::npos) e = path.size();
        const std::string dir = path.substr(b, e - b);
        b = e + 1;
        if (dir.empty()) continue;
        for (const auto& ext : exts) {
            std::error_code ec;
            fs::path candidate = fs::u8path(dir) / fs::u8path(name + ext);
            if (fs::is_regular_file(candidate, ec)) return candidate.u8string();
        }
    }
    return "";
}

}  // namespace gaia
