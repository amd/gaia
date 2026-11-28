// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia_setup.h"

#include <algorithm>
#include <cctype>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>

#ifdef _WIN32
#include <io.h>
#include <windows.h>
#else
#include <signal.h>
#include <unistd.h>
#endif

namespace fs = std::filesystem;

namespace gaia {

namespace {

constexpr const char* kStandardUrl = "http://localhost:13305/api/v1";
// Hybrid's cheap model when hybrid_cheap_model is unset (gaia.llm.hybrid.DEFAULT_CHEAP_MODEL).
constexpr const char* kHybridCheapModel = "amd.DeepSeek-V4.1-Flash";
// In the app GAIA is already open, so "Open GAIA" would send the user nowhere.
std::string startGaia(Surface surface) {
    return surface == Surface::App
               ? "Press Start the model server below, then send your message again."
               : "Open GAIA, or run `gaia daemon start`, then try again.";
}

std::string trim(const std::string& s) {
    const size_t b = s.find_first_not_of(" \t\r\n");
    if (b == std::string::npos) return "";
    return s.substr(b, s.find_last_not_of(" \t\r\n") - b + 1);
}

std::string lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

fs::path userHome() {
#ifdef _WIN32
    const std::string home = getEnvVar("USERPROFILE");
#else
    const std::string home = getEnvVar("HOME");
#endif
    return home.empty() ? fs::path() : fs::u8path(home);
}

fs::path expandUser(const std::string& raw) {
    if (raw == "~" || raw.rfind("~/", 0) == 0 || raw.rfind("~\\", 0) == 0) {
        const fs::path home = userHome();
        if (!home.empty()) return raw.size() <= 2 ? home : home / fs::u8path(raw.substr(2));
    }
    return fs::u8path(raw);
}

bool pidAlive(long long pid) {
    if (pid <= 0 || pid > 0x7fffffffLL) return false;
#ifdef _WIN32
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, static_cast<DWORD>(pid));
    if (!process) return GetLastError() != ERROR_INVALID_PARAMETER;  // denied: it exists
    DWORD code = 0;
    const bool alive = !GetExitCodeProcess(process, &code) || code == STILL_ACTIVE;
    CloseHandle(process);
    return alive;
#else
    return !(kill(static_cast<pid_t>(pid), 0) != 0 && errno == ESRCH);
#endif
}

// A JSON object file, or an empty object when absent. Unreadable or
// malformed files are named, never skipped: a quiet skip would run another model.
json readJsonObject(const fs::path& path) {
    std::error_code ec;
    if (path.empty() || !fs::is_regular_file(path, ec)) return json::object();
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("Cannot read GAIA's settings at " + path.u8string() + ".");
    json j;
    try {
        in >> j;
    } catch (const std::exception& e) {
        throw std::runtime_error("GAIA's settings at " + path.u8string() + " are not valid JSON (" + e.what() +
                                 "). Fix or remove the file, or pass --model and --base-url.");
    }
    if (!j.is_object()) {
        throw std::runtime_error("GAIA's settings at " + path.u8string() +
                                 " must hold a JSON object. Fix or remove the file, or pass --model and --base-url.");
    }
    return j;
}

std::string stringSetting(const json& cfg, const char* key, const fs::path& path) {
    if (!cfg.contains(key) || cfg[key].is_null()) return "";
    if (!cfg[key].is_string()) {
        throw std::runtime_error("GAIA's settings at " + path.u8string() + ": \"" + key + "\" must be a string, got " +
                                 cfg[key].dump() + ". Fix it with `gaia config set " + key + " <value>`.");
    }
    return trim(cfg[key].get<std::string>());
}

// A bare origin gains /api/v1; an explicit path is kept (normalize_external_url).
std::string withApiPath(std::string url) {
    url = trim(url);
    while (!url.empty() && url.back() == '/') url.pop_back();
    const size_t scheme = url.find("://");
    const size_t path = scheme == std::string::npos ? std::string::npos : url.find('/', scheme + 3);
    return path == std::string::npos ? url + "/api/v1" : url;
}

std::string portUrl(int port) { return "http://localhost:" + std::to_string(port) + "/api/v1"; }

int portSetting(const json& j) {
    if (!j.is_object() || !j.contains("port") || !j["port"].is_number_integer()) return 0;
    const long long port = j["port"].get<long long>();
    return port >= 1 && port <= 65535 ? static_cast<int>(port) : 0;
}

bool gaiaServerInstalled(const fs::path& dir) {
    std::error_code ec;
    const fs::path dist = dir / "dist";
    if (!fs::is_directory(dist, ec)) return false;
    for (const auto& version : fs::directory_iterator(dist, ec)) {
        for (const char* name : {"lemond", "lemond.exe"}) {
            if (fs::is_regular_file(version.path() / name, ec)) return true;
        }
    }
    return false;
}

bool readsTerminal() {
#ifdef _WIN32
    return _isatty(_fileno(stdin)) && _isatty(_fileno(stderr));
#else
    return isatty(fileno(stdin)) && isatty(fileno(stderr));
#endif
}

// Nothing answered at `url`: the only case where starting GAIA is the remedy.
std::string unreachable(const std::string& url, const ServerOrigin& origin, std::string why,
                        Surface surface = Surface::Terminal) {
    // The transport's generic remedy would contradict the one given here.
    const size_t generic = why.find(". Check that the service is running");
    if (generic != std::string::npos) why.erase(generic);
    switch (origin.source) {
        case ServerSource::GaiaRunning:
            return surface == Surface::App
                       ? "GAIA's model server stopped: nothing answered at " + url + " (" + why + "). " +
                             startGaia(surface)
                       : "GAIA's model server at " + url + " did not answer (" + why + "). " + startGaia(surface);
        case ServerSource::GaiaStopped:
            return "GAIA's model server isn't running (it last ran at " + url + "). " + startGaia(surface);
        case ServerSource::Default:
            return "No model server answered at " + url + " (" + why +
                   "), and GAIA's own model server isn't set up for this user (nothing in " + origin.label +
                   "). Run `gaia init` to set GAIA up, or pass --base-url (or set LEMONADE_BASE_URL) to a "
                   "model server that is running.";
        default:
            return "The model server at " + url + ", set by " + origin.label + ", did not answer (" + why +
                   "). Check that it is running and that the address is right.";
    }
}

// Other OpenAI-compatible servers have no Lemonade catalog or health check to read.
bool isLemonadeUrl(const std::string& url) {
    const std::string suffix = "/api/v1";
    return url.size() >= suffix.size() && url.compare(url.size() - suffix.size(), suffix.size(), suffix) == 0;
}

std::string addressFrom(const ServerOrigin& origin) {
    switch (origin.source) {
        case ServerSource::GaiaRunning:
            return "GAIA's record of its running model server in " + origin.label;
        case ServerSource::Default:
            return "Lemonade's standard port, used because nothing names a model server and GAIA's own "
                   "isn't set up in " + origin.label;
        default:
            return origin.label;
    }
}

// One line of a reply body, short enough for an error.
std::string bodyText(const std::string& body) {
    std::string out;
    for (const char c : trim(body)) {
        if (out.size() >= 200) return out + "...";
        out += (c == '\n' || c == '\r' || c == '\t') ? ' ' : c;
    }
    return out;
}

// Something answered at `url`, so it is running: the key or the address is what to fix.
// `status` is 0 for a 2xx reply that isn't Lemonade's; `detail` is then what was wrong with it.
std::string answeredWrong(const std::string& url, const ServerOrigin& origin, int status,
                          const std::string& detail, Surface surface = Surface::Terminal) {
    const std::string body = bodyText(detail);
    const std::string said = status ? "HTTP " + std::to_string(status) + (body.empty() ? "" : ": " + body)
                                    : "a reply that is " + body;
    if (origin.source == ServerSource::GaiaStopped) {
        return "GAIA's model server isn't running, and another program answered at its last address " + url +
               " (" + said + "). " + startGaia(surface);
    }
    if (status == 401 || status == 403) {
        const std::string head = "The model server at " + url + " is running but answered " + said + ". ";
        if (origin.gaiaKey) {
            return head + "It refused GAIA's own key, so GAIA's record of its running server is out of date. "
                          "Run `gaia daemon restart`, then try again.";
        }
        if (origin.keyVar.empty()) {
            return head + "It needs a key and none was sent. Export GAIA_API_KEY with that server's key, or name "
                          "the variable that holds it with --api-key-env. The address came from " +
                   addressFrom(origin) + ".";
        }
        if (origin.source == ServerSource::GaiaRunning) {
            return head + "It refused the key in " + origin.keyVar +
                   ". That address is GAIA's own model server, and gaiacpp uses GAIA's key for it when no key "
                   "of yours is set: unset " + origin.keyVar +
                   ", or stop naming it with --api-key-env or apiKeyEnv, and try again.";
        }
        return head + "It refused the key in " + origin.keyVar + ". Set " + origin.keyVar +
               " to the key that server expects, or name another variable with --api-key-env. The address "
               "came from " + addressFrom(origin) + ".";
    }
    std::string fix;
    switch (origin.source) {
        case ServerSource::GaiaRunning:
            fix = "Run `gaia daemon restart` to restart GAIA's model server, then try again.";
            break;
        case ServerSource::Default:
            fix = "Run `gaia init` to set GAIA up, or pass --base-url with a Lemonade server's /api/v1 address.";
            break;
        default:
            fix = "Check that " + origin.label + " holds a Lemonade server's /api/v1 address.";
    }
    return "The address " + url + " answered, but not the way GAIA's model server (Lemonade) does: " + said +
           ". It came from " + addressFrom(origin) + ". " + fix;
}

// A failed request to the model server, told apart by whether anything answered.
std::string serverFailure(const std::string& url, const ServerOrigin& origin, const std::exception& e) {
    if (const auto* http = dynamic_cast<const HttpError*>(&e); http && http->status() != 0) {
        return answeredWrong(url, origin, http->status(), http->body());
    }
    if (dynamic_cast<const json::exception*>(&e)) return answeredWrong(url, origin, 0, "not JSON");
    return unreachable(url, origin, e.what());
}

std::string sizeText(const json& entry) {
    if (!entry.contains("size") || !entry["size"].is_number()) return "";
    const double gb = entry["size"].get<double>();
    if (gb <= 0) return "";
    std::ostringstream s;
    s << " (" << std::fixed << std::setprecision(1) << gb << " GB)";
    return s.str();
}

}  // namespace

fs::path gaiaLemonadeDir() {
    const std::string home = trim(getEnvVar("GAIA_HOME"));
    if (!home.empty()) return expandUser(home) / "lemonade";
    const fs::path user = userHome();
    return user.empty() ? fs::path() : user / ".gaia" / "lemonade";
}

fs::path gaiaConfigFile() {
    const std::string file = trim(getEnvVar("GAIA_CONFIG_FILE"));
    if (!file.empty()) return expandUser(file);
    for (const char* var : {"GAIA_CONFIG_DIR", "GAIA_HOME"}) {
        const std::string dir = trim(getEnvVar(var));
        if (!dir.empty()) return expandUser(dir) / "config.json";
    }
    const fs::path user = userHome();
    return user.empty() ? fs::path() : user / ".gaia" / "config.json";
}

GaiaServer runningGaiaServer() {
    GaiaServer s;
    s.lemonadeDir = gaiaLemonadeDir();
    std::error_code ec;
    if (!s.lemonadeDir.empty() && fs::is_regular_file(s.lemonadeDir / "state.json", ec)) {
        std::ifstream in(s.lemonadeDir / "state.json", std::ios::binary);
        const json state = json::parse(in, nullptr, false);
        const int port = portSetting(state);
        // A server killed without `stop` leaves its record behind; that record is no server.
        const bool alive = !state.contains("pid") || !state["pid"].is_number_integer() ||
                           pidAlive(state["pid"].get<long long>());
        if (port && alive) {
            s.runningPort = port;
            if (state.contains("api_key") && state["api_key"].is_string()) {
                s.runningKey = trim(state["api_key"].get<std::string>());
            }
        }
    }
    // GAIA's own export (env.sh) describes a server whose port and key move: follow the state.
    s.envKeyForeign = trim(getEnvVar("GAIA_LEMONADE_EMBEDDED")) == "1";
    return s;
}

GaiaServer resolveGaiaServer() {
    GaiaServer s = runningGaiaServer();
    s.configFile = gaiaConfigFile();
    const json cfg = readJsonObject(s.configFile);

    const std::string envUrl = s.envKeyForeign ? "" : trim(getEnvVar("LEMONADE_BASE_URL"));
    const std::string ignored = stringSetting(cfg, "lemonade_env_url_ignored", s.configFile);
    if (!envUrl.empty()) {
        if (ignored.empty() || withApiPath(ignored) != withApiPath(envUrl)) {
            s.baseUrl = envUrl;
            s.origin = {ServerSource::LemonadeEnv, "LEMONADE_BASE_URL"};
            return s;
        }
        s.envKeyForeign = true;  // its key belongs to the server the app chose against
    }

    if (stringSetting(cfg, "lemonade_mode", s.configFile) == "external") {
        const std::string url = stringSetting(cfg, "lemonade_base_url", s.configFile);
        if (url.empty()) {
            throw std::runtime_error("GAIA's settings at " + s.configFile.u8string() +
                                     " set lemonade_mode to 'external' without lemonade_base_url. Run "
                                     "`gaia lemonade connect <url>`, or pass --base-url.");
        }
        s.baseUrl = withApiPath(url);
        s.origin = {ServerSource::SavedExternal, "lemonade_base_url in " + s.configFile.u8string()};
        const auto stored = cfg.find("lemonade_api_key_stored");
        s.savedKeyElsewhere = stored != cfg.end() && stored->is_boolean() && stored->get<bool>();
        return s;
    }

    if (s.runningPort) {
        s.baseUrl = portUrl(s.runningPort);
        s.origin = {ServerSource::GaiaRunning, s.lemonadeDir.u8string()};
        return s;
    }

    // Stopped, GAIA's server comes back on its last port; another Lemonade may own the standard one.
    if (!s.lemonadeDir.empty() && gaiaServerInstalled(s.lemonadeDir)) {
        std::ifstream in(s.lemonadeDir / "last_port.json", std::ios::binary);
        const int port = in ? portSetting(json::parse(in, nullptr, false)) : 0;
        if (port) {
            s.baseUrl = portUrl(port);
            s.origin = {ServerSource::GaiaStopped, s.lemonadeDir.u8string()};
            return s;
        }
    }

    s.baseUrl = kStandardUrl;
    s.origin = {ServerSource::Default, s.lemonadeDir.empty() ? std::string("~/.gaia/lemonade") : s.lemonadeDir.u8string()};
    return s;
}

std::string resolveGaiaModel() {
    const fs::path path = gaiaConfigFile();
    const json cfg = readJsonObject(path);
    for (const char* key : {"default_model", "last_model"}) {
        std::string model = stringSetting(cfg, key, path);
        if (lower(model) == "hybrid") {
            model = stringSetting(cfg, "hybrid_cheap_model", path);
            if (model.empty()) model = kHybridCheapModel;
        }
        // Lemonade can't run a Claude id (resolve_local_chat_model skips it too).
        if (model.empty() || model.rfind("claude-", 0) == 0) continue;
        return model;
    }
    return kGaiaFallbackModel;
}

std::string embeddedKeyFor(const GaiaServer& server, const std::string& url) {
    if (!server.runningPort || server.runningKey.empty()) return "";
    const std::string prefix = "http://";
    if (url.rfind(prefix, 0) != 0) return "";
    std::string authority = url.substr(prefix.size());
    authority = authority.substr(0, authority.find_first_of("/?#"));
    if (authority.find('@') != std::string::npos) return "";
    std::string host, port;
    if (!authority.empty() && authority[0] == '[') {
        const size_t close = authority.find(']');
        if (close == std::string::npos) return "";
        host = authority.substr(0, close + 1);
        if (close + 1 < authority.size() && authority[close + 1] == ':') port = authority.substr(close + 2);
    } else {
        const size_t colon = authority.find(':');
        host = authority.substr(0, colon);
        if (colon != std::string::npos) port = authority.substr(colon + 1);
    }
    host = lower(host);
    if (host != "localhost" && host != "127.0.0.1" && host != "[::1]") return "";
    if (port != std::to_string(server.runningPort)) return "";
    return server.runningKey;
}

void dropForeignLemonadeKey(const GaiaServer& server) {
    if (!server.envKeyForeign) return;
#ifdef _WIN32
    _putenv_s("LEMONADE_API_KEY", "");
#else
    unsetenv("LEMONADE_API_KEY");
#endif
}

std::string keyForNamedServer(const GaiaServer& own, const std::string& url, const std::string& lemonadeKey,
                              ServerOrigin& origin) {
    origin.keyVar.clear();
    origin.gaiaKey = false;
    const std::string usersKey = getEnvVar("GAIA_API_KEY");
    if (!usersKey.empty()) {
        origin.keyVar = "GAIA_API_KEY";
        return usersKey;
    }
    if (!lemonadeKey.empty() && !own.envKeyForeign) {
        // The local Lemonade's key never goes to another host.
        if (!isLoopbackUrl(url)) return "";
        origin.keyVar = "LEMONADE_API_KEY";
        return lemonadeKey;
    }
    const std::string key = embeddedKeyFor(own, url);
    origin.gaiaKey = !key.empty();
    return key;
}

std::string serverDownReason(LemonadeClient& client, const ServerOrigin& origin, Surface surface,
                             bool* nothingAnswered) {
    if (nothingAnswered) *nothingAnswered = false;
    if (!isLemonadeUrl(client.baseUrl())) return "";
    LemonadeHealth h = client.healthCheck();
    // A server busy with a load can miss one health check; one that answered has not.
    for (int attempt = 1; attempt < 3 && !h.error.empty() && !h.errorStatus; ++attempt) h = client.healthCheck();
    if (h.running) return "";
    if (h.errorStatus) return answeredWrong(client.baseUrl(), origin, h.errorStatus, h.errorBody, surface);
    if (h.error.empty()) return answeredWrong(client.baseUrl(), origin, 0, "not JSON", surface);
    if (nothingAnswered) *nothingAnswered = true;
    return unreachable(client.baseUrl(), origin, h.error, surface);
}

void noteKeySource(ServerOrigin& origin, const AgentConfig& cfg, bool gaiaKey) {
    origin.gaiaKey = gaiaKey;
    origin.keyVar.clear();
    if (gaiaKey || !cfg.apiKey.empty()) return;
    if (!cfg.apiKeyEnv.empty()) origin.keyVar = cfg.apiKeyEnv;
    else if (!getEnvVar("GAIA_API_KEY").empty()) origin.keyVar = "GAIA_API_KEY";
    // resolveApiKey sends LEMONADE_API_KEY to a loopback address only.
    else if (!resolveApiKey(cfg).empty()) origin.keyVar = "LEMONADE_API_KEY";
}

DownloadPrompt terminalPrompt() { return {readsTerminal(), &std::cin, &std::cerr}; }

void checkServerAndModel(LemonadeClient& client, const std::string& model, const ServerOrigin& origin,
                         bool yes, const DownloadPrompt& prompt) {
    const std::string url = client.baseUrl();
    if (!isLemonadeUrl(url)) return;

    json catalog;
    try {
        // Without show_all, the catalog leaves out every model that isn't downloaded.
        catalog = client.listModels(true);
    } catch (const std::exception& e) {
        throw std::runtime_error(serverFailure(url, origin, e));
    }
    if (!catalog.is_object() || !catalog.contains("data") || !catalog["data"].is_array()) {
        throw std::runtime_error(answeredWrong(url, origin, 0, "not a model list"));
    }
    if (model.empty() || model.rfind("fireworks.", 0) == 0 || model.rfind("amd.", 0) == 0) return;

    const json* entry = nullptr;
    for (const auto& m : catalog["data"]) {
        if (m.is_object() && m.value("id", "") == model) {
            entry = &m;
            break;
        }
    }
    if (!entry) {
        throw std::runtime_error(model + " isn't a model the model server at " + url +
                                 " knows, so gaiacpp can't run it. Run `gaiacpp models list` to see the "
                                 "models it has, and pass one with --model.");
    }
    if (entry->value("recipe", "") == "cloud") return;
    // A server that ignores show_all lists only what is on disk.
    const bool downloaded = !entry->contains("downloaded") || entry->value("downloaded", false) == true;
    if (downloaded) return;

    const std::string missing = model + " is not downloaded" + sizeText(*entry);
    if (!yes) {
        if (!prompt.interactive || !prompt.in || !prompt.err) {
            throw std::runtime_error(missing +
                                     ", and there is no terminal to ask whether to download it, so nothing "
                                     "was downloaded. Pass --yes to download it, or --model with a model you have.");
        }
        *prompt.err << "gaiacpp: " << missing << ". Download it now? [y/N] " << std::flush;
        std::string answer;
        if (!std::getline(*prompt.in, answer)) *prompt.err << "\n";
        answer = lower(trim(answer));
        if (answer != "y" && answer != "yes") {
            throw std::runtime_error("Nothing was downloaded. Run the command again and answer y to download " +
                                     model + ", pass --yes to download without being asked, or pass --model "
                                     "with a model you have.");
        }
    }
    std::ostream& err = prompt.err ? *prompt.err : std::cerr;
    err << "gaiacpp: downloading " << model << sizeText(*entry) << ". Ctrl+C stops the download.\n" << std::flush;
    client.pullModel(model);
    err << "gaiacpp: downloaded " << model << "\n" << std::flush;
}

}  // namespace gaia
