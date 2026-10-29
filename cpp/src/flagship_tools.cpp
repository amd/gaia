// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/flagship_tools.h"

#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <optional>
#include <regex>
#include <set>
#include <sstream>
#include <thread>

#include "gaia/data_file.h"
#include "gaia/documents.h"
#include "gaia/file_tools.h"
#include "gaia/flagship_config.h"
#include "gaia/lemonade_client.h"
#include "gaia/process.h"
#include "gaia/rag.h"
#include "gaia/tool_registry.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

constexpr std::uintmax_t kMaxFileBytes = 10u * 1024u * 1024u;
constexpr std::size_t kPageChars = 8000;
constexpr int kMaxLinesPerRead = 400;
constexpr std::size_t kShellOutputChars = 10000;
constexpr int kBackupGenerations = 5;

const std::set<std::string> kSensitiveNames = {
    ".env", ".env.local", ".env.production", ".env.development", "credentials.json",
    "service_account.json", "secrets.json", "credentials", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
    "authorized_keys", "known_hosts", "shadow", "passwd", "sudoers", "htpasswd", ".netrc", "_netrc",
    ".pgpass", "pgpass.conf", ".my.cnf", "wallet.dat", "keystore.jks", ".npmrc", ".pypirc",
    ".git-credentials", ".credentials.json", "credentials.toml", "kubeconfig",
    // Browser password and cookie stores (Firefox, Chrome, Edge).
    "logins.json", "key3.db", "key4.db", "signons.sqlite", "cookies.sqlite", "login data",
    "login data for account", "cookies", "web data", "local state"};
// .env.<anything> holds secrets, except the committed templates.
const std::set<std::string> kEnvTemplates = {".env.example", ".env.sample", ".env.template", ".env.dist"};
const std::set<std::string> kSensitiveExtensions = {".pem", ".key", ".crt", ".cer",
                                                    ".p12", ".pfx", ".jks", ".keystore"};
// Home subfolders whose every file is a credential, whatever it is called.
const std::vector<std::vector<std::string>> kSecretDirs = {
    {".ssh"}, {".gnupg"}, {".aws"}, {".azure"}, {".kube"}, {".docker"}, {".config", "gcloud"},
    {"AppData", "Roaming", "gcloud"}, {".config", "gh"}, {".local", "share", "keyrings"},
    {"Library", "Keychains"}, {".password-store"}, {"AppData", "Roaming", "Microsoft", "Credentials"},
    {"AppData", "Local", "Microsoft", "Credentials"}, {"AppData", "Roaming", "Microsoft", "Protect"}};
// Files whose contents run on the next login, shell, git command or Python start: a write is persistence.
const std::set<std::string> kStartupFiles = {
    ".bashrc", ".bash_profile", ".bash_login", ".bash_logout", ".bash_aliases", ".profile",
    ".zshrc", ".zshenv", ".zprofile", ".zlogin", ".zlogout", ".kshrc", ".cshrc", ".tcshrc",
    ".login", ".logout", ".inputrc", "config.fish", ".xinitrc", ".xprofile", ".xsession",
    ".xsessionrc", "profile.ps1", "microsoft.powershell_profile.ps1",
    "microsoft.vscode_profile.ps1", ".gitconfig", "gitconfig", "crontab", ".envrc",
    "sitecustomize.py", "usercustomize.py"};
const std::vector<std::vector<std::string>> kStartupDirMarkers = {
    {".git"}, {".config", "autostart"}, {".config", "systemd"}, {".config", "git"}, {".config", "fish"},
    {".config", "environment.d"}, {".bashrc.d"}, {".github", "workflows"}, {"site-packages"},
    {"dist-packages"}, {".ipython"}, {"launchagents"}, {"launchdaemons"}, {"windowspowershell"},
    {"start menu", "programs", "startup"}};
// GAIA state subfolders that hold the user's content, not configuration (as in Python).
const std::set<std::string> kGaiaUserContent = {"documents", "chat", "screenshots", "eval"};
// Commands cmd.exe would run from the current folder instead of the real program.
const std::set<std::string> kShadowableCommands = {"git", "python", "python3", "py", "node", "npm", "npx",
                                                   "pip", "cmd", "powershell", "pwsh", "curl", "where"};
const std::set<std::string> kSkipDirs = {".git", "node_modules", "__pycache__", ".venv", "venv",
                                         ".cache", "AppData", "Library", "$Recycle.Bin", ".gaia"};

std::string lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

std::string trim(std::string s) {
    s.erase(0, s.find_first_not_of(" \t\r\n"));
    s.erase(s.find_last_not_of(" \t\r\n") + 1);
    return s;
}

std::string str(const json& args, const char* key, const std::string& def = "") {
    if (!args.is_object() || !args.contains(key) || args[key].is_null()) return def;
    const json& v = args[key];
    if (v.is_string()) return v.get<std::string>();
    if (v.is_number() || v.is_boolean()) return v.dump();
    return def;
}

std::optional<long long> num(const json& args, const char* key) {
    if (!args.is_object() || !args.contains(key) || args[key].is_null()) return std::nullopt;
    const json& v = args[key];
    if (v.is_number()) return static_cast<long long>(v.get<double>());
    if (v.is_string()) {
        const std::string s = trim(v.get<std::string>());
        if (s.empty()) return std::nullopt;
        std::size_t used = 0;
        long long out = 0;
        try {
            out = std::stoll(s, &used);
        } catch (const std::exception&) {
            used = 0;
        }
        if (used == s.size()) return out;
    }
    throw std::invalid_argument(std::string("'") + key + "' must be a number, got " + v.dump());
}

bool flag(const json& args, const char* key, bool def) {
    if (!args.is_object() || !args.contains(key) || args[key].is_null()) return def;
    const json& v = args[key];
    if (v.is_boolean()) return v.get<bool>();
    const std::string s = lower(trim(v.is_string() ? v.get<std::string>() : v.dump()));
    if (s == "true" || s == "1" || s == "yes") return true;
    if (s == "false" || s == "0" || s == "no") return false;
    throw std::invalid_argument(std::string("'") + key + "' must be true or false");
}

json error(const std::string& message) { return {{"status", "error"}, {"error", message}}; }

std::string humanSize(std::uintmax_t bytes) {
    const char* units[] = {"B", "KB", "MB", "GB", "TB"};
    double v = static_cast<double>(bytes);
    int u = 0;
    while (v >= 1024.0 && u < 4) {
        v /= 1024.0;
        ++u;
    }
    char buf[32];
    std::snprintf(buf, sizeof(buf), u == 0 ? "%.0f %s" : "%.1f %s", v, units[u]);
    return buf;
}

std::string isoTime(fs::file_time_type ft) {
    const auto sys = std::chrono::time_point_cast<std::chrono::system_clock::duration>(
        ft - fs::file_time_type::clock::now() + std::chrono::system_clock::now());
    std::time_t t = std::chrono::system_clock::to_time_t(sys);
    std::tm tm{};
#ifdef _WIN32
    localtime_s(&tm, &t);
#else
    localtime_r(&t, &tm);
#endif
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", &tm);
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

bool validUtf8(const std::string& s) { return isValidUtf8(s); }

// Windows ANSI text (#5766): CP1252 bytes re-encoded as UTF-8.
std::string cp1252ToUtf8(const std::string& s) {
    static const unsigned short high[32] = {
        0x20AC, 0xFFFD, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160,
        0x2039, 0x0152, 0xFFFD, 0x017D, 0xFFFD, 0xFFFD, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022,
        0x2013, 0x2014, 0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0xFFFD, 0x017E, 0x0178};
    std::string out;
    out.reserve(s.size() + s.size() / 4);
    for (unsigned char c : s) {
        unsigned cp = c < 0x80 ? c : c < 0xA0 ? high[c - 0x80] : c;
        if (cp < 0x80) {
            out.push_back(static_cast<char>(cp));
        } else if (cp < 0x800) {
            out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        } else {
            out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        }
    }
    return out;
}

bool looksBinary(const std::string& s) {
    return s.find('\0', 0) != std::string::npos && s.find('\0') < 8192;
}

std::size_t utf8Cut(const std::string& s, std::size_t pos) {
    if (pos >= s.size()) return s.size();
    while (pos > 0 && (static_cast<unsigned char>(s[pos]) & 0xC0) == 0x80) --pos;
    return pos;
}

// @p s without a trailing incomplete UTF-8 sequence (a fixed-size read can end mid-character).
std::string dropPartialTail(const std::string& s) {
    std::size_t lead = s.size();
    for (std::size_t back = 0; lead > 0 && back < 4; ++back) {
        --lead;
        const unsigned char c = static_cast<unsigned char>(s[lead]);
        if ((c & 0xC0) != 0x80) {
            const std::size_t need = c < 0x80 ? 1 : (c >> 5) == 0x6 ? 2 : (c >> 4) == 0xE ? 3 : (c >> 3) == 0x1E ? 4 : 1;
            return lead + need > s.size() ? s.substr(0, lead) : s;
        }
    }
    return s;
}

// File bytes as UTF-8 text: as they are when valid, else decoded as Windows ANSI.
std::string textOf(const std::string& bytes) { return validUtf8(bytes) ? bytes : cp1252ToUtf8(bytes); }

// Windows opens "name." and "name " as "name", so a name is judged without them.
std::string winName(const std::string& s) {
    if (s == "." || s == "..") return s;
    const std::size_t end = s.find_last_not_of(". ");
    return end == std::string::npos ? "" : s.substr(0, end + 1);
}

std::vector<std::string> lowerParts(const fs::path& p) {
    std::vector<std::string> parts;
    for (const auto& part : p.lexically_normal()) parts.push_back(lower(winName(part.u8string())));
    return parts;
}

std::vector<std::string> pathParts(const fs::path& p) {
    std::vector<std::string> parts;
    for (const auto& part : p.lexically_normal()) {
#ifdef _WIN32
        parts.push_back(lower(winName(part.u8string())));
#else
        parts.push_back(part.u8string());
#endif
    }
    while (!parts.empty() && parts.back().empty()) parts.pop_back();
    return parts;
}

bool isWithin(const fs::path& candidate, const fs::path& parent) {
    const auto c = pathParts(candidate);
    const auto p = pathParts(parent);
    return p.size() <= c.size() && std::equal(p.begin(), p.end(), c.begin());
}

bool hasMarker(const std::vector<std::string>& parts, const std::vector<std::vector<std::string>>& markers) {
    for (const auto& marker : markers) {
        std::vector<std::string> low;
        for (const auto& m : marker) low.push_back(lower(m));
        for (std::size_t i = 0; i + low.size() <= parts.size(); ++i) {
            if (std::equal(low.begin(), low.end(), parts.begin() + static_cast<std::ptrdiff_t>(i))) return true;
        }
    }
    return false;
}

// The home folder as configured and as resolved. @throws std::runtime_error when unknown.
std::vector<fs::path> homeRoots() {
    const fs::path raw = fs::u8path(userHomeDir());
    std::error_code ec;
    const fs::path real = fs::weakly_canonical(raw, ec);
    return ec ? std::vector<fs::path>{raw} : std::vector<fs::path>{real, raw};
}

bool sensitiveName(const std::string& name) {
    if (kSensitiveNames.count(name)) return true;
    return name.rfind(".env.", 0) == 0 && !kEnvTemplates.count(name);
}

// Credential files and folders: never read or written.
std::string secretRefusal(const fs::path& p) {
#ifdef _WIN32
    // "file:stream" opens an alternate data stream; "file::$DATA" is the file itself under another name.
    if (p.relative_path().u8string().find(':') != std::string::npos) {
        return "'" + p.u8string() + "' names an NTFS stream; GAIA's file tools only open plain file paths.";
    }
#endif
    const std::string name = lower(winName(p.filename().u8string()));
    if (sensitiveName(name) || kSensitiveExtensions.count(lower(fs::u8path(name).extension().u8string()))) {
        return "'" + p.u8string() + "' is a credential file; GAIA's file tools do not open those.";
    }
    std::vector<fs::path> homes;
    try {
        homes = homeRoots();
    } catch (const std::runtime_error& e) {
        return "'" + p.u8string() + "' was not opened: the credential folders cannot be located (" + e.what() + ")";
    }
    for (const auto& home : homes) {
        for (const auto& sub : kSecretDirs) {
            fs::path dir = home;
            for (const auto& s : sub) dir /= fs::u8path(s);
            if (isWithin(p, dir)) {
                return "'" + p.u8string() + "' is inside a credentials folder; GAIA's file tools do not open it.";
            }
        }
    }
    return "";
}

std::vector<fs::path> gaiaStateDirs() {
    std::vector<fs::path> dirs = {fs::u8path(gaiaHomeDir())};
    for (const auto& h : homeRoots()) dirs.push_back(h / ".gaia");
    const std::string configDir = trim(getEnvVar("GAIA_CONFIG_DIR"));
    if (!configDir.empty()) {
        std::string d = configDir;
        if (d[0] == '~' && (d.size() == 1 || d[1] == '/' || d[1] == '\\')) d = userHomeDir() + d.substr(1);
        dirs.push_back(fs::u8path(d));
    }
    std::error_code ec;
    const std::size_t n = dirs.size();
    for (std::size_t i = 0; i < n; ++i) {
        fs::path real = fs::weakly_canonical(dirs[i], ec);
        if (!ec) dirs.push_back(real);
    }
    return dirs;
}

bool samePath(const fs::path& a, const fs::path& b) { return isWithin(a, b) && isWithin(b, a); }

// Why writing @p p would plant code or config that runs without the user asking, or "".
// @p exempt is a folder of GAIA's own (the scratch folder) that the state-folder rule skips.
std::string writeRefusal(const fs::path& p, const fs::path& exempt = {}) {
    std::string why = secretRefusal(p);
    if (!why.empty()) return why;
    const std::string shown = "'" + p.u8string() + "'";
    const std::string name = lower(winName(p.filename().u8string()));
    if (kStartupFiles.count(name)) {
        return shown + " runs automatically (shell, login, git or Python startup), so GAIA does not write it.";
    }
    const std::string ext = lower(fs::u8path(name).extension().u8string());
    if (ext == ".pth") return shown + " is a Python path file that runs at every Python start, so GAIA does not write it.";
    if (hasMarker(lowerParts(p.parent_path()), kStartupDirMarkers)) {
        return shown + " is in a folder whose files run automatically, so GAIA does not write there.";
    }
    try {
        for (const auto& home : homeRoots()) {
            if (isWithin(p, home / ".local" / "bin")) {
                return shown + " is in ~/.local/bin, whose files run as commands, so GAIA does not write there.";
            }
        }
        for (const auto& state : gaiaStateDirs()) {
            if (!isWithin(p, state) || (!exempt.empty() && isWithin(p, exempt))) continue;
            const auto all = pathParts(p);
            const auto base = pathParts(state);
            const std::string first = all.size() > base.size() + 1 ? lower(all[base.size()]) : "";
            if (!kGaiaUserContent.count(first)) {
                return shown + " is inside GAIA's own settings folder (" + state.u8string() +
                       "); GAIA's file tools do not change its configuration.";
            }
        }
    } catch (const std::runtime_error& e) {
        return shown + " was not written: GAIA's protected folders cannot be located (" + e.what() + ")";
    }
#ifdef _WIN32
    const char sep = ';';
#else
    const char sep = ':';
#endif
    const std::string pathVar = getEnvVar("PATH");
    for (std::size_t b = 0; b <= pathVar.size();) {
        std::size_t e = pathVar.find(sep, b);
        if (e == std::string::npos) e = pathVar.size();
        const std::string dir = trim(pathVar.substr(b, e - b));
        b = e + 1;
        if (dir.empty() || dir == ".") continue;
        std::error_code ec;
        fs::path real = fs::weakly_canonical(fs::u8path(dir), ec);
        if (samePath(p.parent_path(), ec ? fs::u8path(dir) : real)) {
            return shown + " is in a folder on PATH, where any file can run as a command, so GAIA does not write there.";
        }
    }
#ifdef _WIN32
    // cmd.exe runs a program from the current folder before PATH: git.bat here would replace git.
    std::string pathext = lower(getEnvVar("PATHEXT", ".COM;.EXE;.BAT;.CMD")) + ";.ps1";
    const std::string stem = lower(winName(fs::u8path(name).stem().u8string()));
    if (!ext.empty() && (";" + pathext + ";").find(";" + ext + ";") != std::string::npos &&
        (kShadowableCommands.count(stem) || !findExecutable(stem).empty())) {
        return shown + " would run in place of the '" + stem + "' command, so GAIA does not write it.";
    }
#endif
    return "";
}

// Whether @p p, resolved, has a hidden (dot) component below @p root.
bool hiddenBelow(const fs::path& p, const fs::path& root) {
    const auto all = pathParts(p);
    const auto base = pathParts(root);
    for (std::size_t i = base.size(); i < all.size(); ++i) {
        if (!all[i].empty() && all[i][0] == '.' && all[i] != "." && all[i] != "..") return true;
    }
    return false;
}

bool globMatch(const std::string& pattern, const std::string& text) {
    std::size_t p = 0, t = 0, star = std::string::npos, mark = 0;
    while (t < text.size()) {
        if (p < pattern.size() && (pattern[p] == '?' || std::tolower(static_cast<unsigned char>(pattern[p])) ==
                                                            std::tolower(static_cast<unsigned char>(text[t])))) {
            ++p;
            ++t;
        } else if (p < pattern.size() && pattern[p] == '*') {
            star = p++;
            mark = t;
        } else if (star != std::string::npos) {
            p = star + 1;
            t = ++mark;
        } else {
            return false;
        }
    }
    while (p < pattern.size() && pattern[p] == '*') ++p;
    return p == pattern.size();
}

// Visit regular files under @p root, skipping hidden, cache and app-data folders.
template <typename Fn>
void walkFiles(const fs::path& root, int maxDepth, std::size_t maxVisits, Fn&& fn) {
    std::error_code ec;
    fs::recursive_directory_iterator it(root, fs::directory_options::skip_permission_denied, ec), end;
    std::size_t visits = 0;
    for (; !ec && it != end; it.increment(ec)) {
        if (++visits > maxVisits) return;
        const auto& entry = *it;
        const std::string name = entry.path().filename().u8string();
        std::error_code e2;
        if (entry.is_directory(e2)) {
            if (it.depth() >= maxDepth || kSkipDirs.count(name) || (!name.empty() && name[0] == '.')) {
                it.disable_recursion_pending();
            }
            continue;
        }
        // A link could lead out of the allowed folders or to a credential file.
        if (entry.is_symlink(e2) || !entry.is_regular_file(e2)) continue;
        if (!fn(entry)) return;
    }
}

std::string shellQuote(const std::string& s) {
#ifdef _WIN32
    return "\"" + s + "\"";
#else
    std::string out = "'";
    for (char c : s) out += (c == '\'') ? std::string("'\\''") : std::string(1, c);
    return out + "'";
#endif
}

std::string findPython() {
    for (const char* name : {"python3", "python", "py"}) {
        std::string p = findExecutable(name);
        // The Microsoft Store alias opens the Store instead of running Python.
        if (!p.empty() && lower(p).find("windowsapps") == std::string::npos) return p;
    }
    return "";
}

bool validIdentifier(const std::string& s) {
    static const std::regex id(R"([A-Za-z_][A-Za-z0-9_]{0,62})");
    return std::regex_match(s, id);
}

// Python's scratchpad naming: tables live as scratch_<name>, where <name> keeps
// [A-Za-z0-9_] (anything else becomes '_'), gains t_ before a leading digit, and
// stops at 64 characters. "" when nothing is left.
const std::string kScratchPrefix = "scratch_";

std::string scratchName(const std::string& raw) {
    std::string clean;
    for (unsigned char c : trim(raw)) {
        if ((c & 0xC0) == 0x80) continue;  // one '_' per character, as Python counts them
        clean.push_back(std::isalnum(c) || c == '_' ? static_cast<char>(c) : '_');
    }
    if (clean.empty()) return "";
    if (std::isdigit(static_cast<unsigned char>(clean[0]))) clean = "t_" + clean;
    return clean.substr(0, 64);
}

}  // namespace

std::string credentialRefusal(const std::string& path) { return secretRefusal(fs::u8path(path)); }

namespace {

fs::path resolvedTarget(const std::string& raw) {
    std::string s = trim(raw);
    if (!s.empty() && s[0] == '~' && (s.size() == 1 || s[1] == '/' || s[1] == '\\')) s = userHomeDir() + s.substr(1);
    std::error_code ec;
    fs::path p = fs::absolute(fs::u8path(s), ec);
    if (ec) p = fs::u8path(s);
    fs::path real = fs::weakly_canonical(p, ec);
    return ec ? p.lexically_normal() : real;
}

}  // namespace

std::string writeTargetRefusal(const std::string& path) {
    try {
        return writeRefusal(resolvedTarget(path));
    } catch (const std::runtime_error& e) {
        return "'" + path + "' was not written: " + e.what();
    }
}

bool editIsInside(const std::string& target, const std::vector<std::string>& roots) {
    if (trim(target).empty()) return false;
    std::vector<fs::path> homes;
    fs::path p;
    try {
        p = resolvedTarget(target);
        homes = homeRoots();
    } catch (const std::runtime_error&) {
        return false;
    }
    if (!writeRefusal(p).empty()) return false;
    for (const auto& r : roots) {
        if (trim(r).empty()) continue;
        const fs::path root = resolvedTarget(r);
        // A home folder or drive root holds everything the user owns: never a workspace.
        bool tooBroad = samePath(root, root.root_path());
        for (const auto& h : homes) tooBroad = tooBroad || samePath(root, h);
        if (tooBroad || !isWithin(p, root)) continue;
        return !hiddenBelow(p, root);
    }
    return false;
}

FlagshipTools::FlagshipTools(FlagshipToolContext ctx)
    : ctx_(std::move(ctx)), scratch_(ctx_.scratchpadDb.empty() ? ":memory:" : ctx_.scratchpadDb) {
    if (ctx_.allowedPaths.empty()) ctx_.allowedPaths.push_back(userHomeDir());
    std::error_code ec;
    for (auto& p : ctx_.allowedPaths) p = fs::weakly_canonical(fs::u8path(p), ec).u8string();
    if (ctx_.workDir.empty()) ctx_.workDir = fs::current_path(ec).u8string();
    if (ctx_.scratchDir.empty()) ctx_.scratchDir = (fs::u8path(gaiaHomeDir()) / "native" / "scratch").u8string();
    httpGet_ = [this](const std::string& url) { return platformHttpGet(url, allowPrivate_); };
    if (!ctx_.lemonadeUrl.empty()) {
        LemonadeClientConfig lc;
        lc.baseUrl = ctx_.lemonadeUrl;
        lc.apiKey = ctx_.lemonadeApiKey;
        lemonade_ = std::make_unique<LemonadeClient>(lc);
    }
}

FlagshipTools::~FlagshipTools() = default;

std::string FlagshipTools::resolveAllowed(const std::string& raw) const {
    std::string s = trim(raw);
    if (s.empty()) return "";
    if (s[0] == '~' && (s.size() == 1 || s[1] == '/' || s[1] == '\\')) s = userHomeDir() + s.substr(1);
    fs::path p = fs::u8path(s);
    if (p.is_relative()) p = fs::u8path(ctx_.workDir) / p;
    std::error_code ec;
    p = fs::weakly_canonical(p, ec);
    if (ec) p = p.lexically_normal();
    for (const auto& allowed : ctx_.allowedPaths) {
        if (isWithin(p, fs::u8path(allowed))) return p.u8string();
    }
    return "";
}

void FlagshipTools::backup(const std::string& path) {
    std::error_code ec;
    if (!fs::is_regular_file(fs::u8path(path), ec)) return;
    const std::string key = FileStateTracker::hashContent(lower(path)).substr(0, 12);
    const fs::path dir = fs::u8path(ctx_.scratchDir).parent_path() / "backups" / key;
    fs::create_directories(dir, ec);
    if (ec) throw std::runtime_error("cannot create backup folder " + dir.u8string() + ": " + ec.message());
    const std::string base = fs::u8path(path).filename().u8string();
    for (int g = kBackupGenerations - 1; g >= 1; --g) {
        const fs::path from = dir / (base + "." + std::to_string(g));
        if (fs::exists(from, ec)) fs::rename(from, dir / (base + "." + std::to_string(g + 1)), ec);
    }
    fs::copy_file(fs::u8path(path), dir / (base + ".1"), fs::copy_options::overwrite_existing, ec);
    if (ec) throw std::runtime_error("cannot back up " + path + ": " + ec.message());
}

// ---------------------------------------------------------------------------
// Files
// ---------------------------------------------------------------------------

json FlagshipTools::readFile(const json& args) {
    const std::string raw = str(args, "file_path");
    if (trim(raw).empty()) return error("file_path is required");
    const std::string path = resolveAllowed(raw);
    if (path.empty()) return error("'" + raw + "' is outside the folders GAIA may read.");
    const fs::path p = fs::u8path(path);
    if (auto why = secretRefusal(p); !why.empty()) return error(why);
    std::error_code ec;
    if (fs::is_directory(p, ec)) return error("'" + path + "' is a folder; use browse_directory to list it.");
    if (!fs::is_regular_file(p, ec)) return error("File not found: " + path);
    const auto size = fs::file_size(p, ec);
    if (size > kMaxFileBytes) {
        return error("'" + path + "' is " + humanSize(size) + "; read_file opens files up to 10 MB.");
    }
    std::string bytes;
    if (!readWhole(p, bytes)) return error("Cannot open " + path + " for reading.");
    const std::string ext = lower(p.extension().u8string());
    std::string encoding;
    std::string text;
    json docInfo;
    if (isDocumentExtension(ext)) {
        DocumentText doc;
        try {
            doc = extractDocumentText(path);
        } catch (const std::runtime_error& e) {
            return error(e.what());
        }
        if (doc.kind == "pdf" && !doc.emptySections.empty() && ctx_.vision) {
            fillScannedPages(path, doc, ctx_.vision, 10);
        }
        text = doc.text;
        docInfo = {{"document_type", doc.kind}, {doc.kind == "xlsx" ? "sheet_count" : doc.kind == "pptx" ? "slide_count" : "page_count", doc.sections}};
        if (!doc.emptySections.empty()) docInfo["pages_without_text"] = doc.emptySections;
        if (!doc.warnings.empty()) docInfo["warnings"] = doc.warnings;
    } else if (looksBinary(bytes)) {
        return {{"status", "success"}, {"file_path", path}, {"file_type", "binary"}, {"is_binary", true},
                {"size_bytes", size}};
    } else {
        text = bytes;
        if (!validUtf8(text)) {
            text = cp1252ToUtf8(bytes);
            encoding = "cp1252";
        }
    }
    FileStateTracker::instance().recordRead(path, bytes);

    std::size_t lineCount = text.empty() ? 0 : static_cast<std::size_t>(std::count(text.begin(), text.end(), '\n')) +
                                                    (text.back() == '\n' ? 0 : 1);
    json out = {{"status", "success"}, {"file_path", path}, {"line_count", lineCount},
                {"size_bytes", size}, {"file_type", ext.empty() ? "text" : ext.substr(1)}};
    if (!encoding.empty()) out["encoding"] = encoding;
    for (auto it = docInfo.begin(); docInfo.is_object() && it != docInfo.end(); ++it) out[it.key()] = it.value();

    std::optional<long long> startLine, endLine, offset, limit;
    try {
        startLine = num(args, "start_line");
        endLine = num(args, "end_line");
        offset = num(args, "offset");
        limit = num(args, "limit");
    } catch (const std::invalid_argument& e) {
        return error(e.what());
    }
    if ((startLine || endLine) && ((offset && *offset > 0) || limit)) {
        return error("Use start_line/end_line or offset/limit, not both.");
    }
    if (startLine || endLine) {
        long long first = std::max<long long>(1, startLine.value_or(1));
        long long last = endLine.value_or(first + kMaxLinesPerRead - 1);
        if (last < first) return error("end_line must be at or after start_line.");
        last = std::min<long long>(last, first + kMaxLinesPerRead - 1);
        std::istringstream in(text);
        std::string line, content;
        long long n = 0;
        while (std::getline(in, line)) {
            ++n;
            if (n < first) continue;
            if (n > last) break;
            content += line + "\n";
        }
        out["content"] = content;
        out["start_line"] = first;
        out["end_line"] = std::min<long long>(last, n);
        if (last < static_cast<long long>(lineCount)) out["next_start_line"] = last + 1;
        return out;
    }
    if ((offset && *offset > 0) || limit) {
        const std::size_t from = utf8Cut(text, static_cast<std::size_t>(std::max<long long>(0, offset.value_or(0))));
        const std::size_t want = static_cast<std::size_t>(std::clamp<long long>(limit.value_or(kPageChars), 1, kPageChars));
        const std::size_t to = utf8Cut(text, std::min(text.size(), from + want));
        out["content"] = text.substr(from, to - from);
        out["offset"] = from;
        out["total_chars"] = text.size();
        out["next_offset"] = to < text.size() ? json(to) : json(nullptr);
        return out;
    }
    out["content"] = text;
    return out;
}

json FlagshipTools::writeFile(const json& args) {
    const std::string raw = str(args, "file_path");
    if (trim(raw).empty()) return error("file_path is required");
    if (!args.contains("content") || !args["content"].is_string()) return error("content must be a string");
    const std::string content = args["content"].get<std::string>();
    if (content.size() > kMaxFileBytes) return error("content is over the 10 MB write limit.");
    const std::string path = resolveAllowed(raw);
    if (path.empty()) return error("'" + raw + "' is outside the folders GAIA may write.");
    const fs::path p = fs::u8path(path);
    if (auto why = writeRefusal(p); !why.empty()) return error(why);
    std::error_code ec;
    if (fs::is_directory(p, ec)) return error("'" + path + "' is a folder, not a file.");
    bool createDirs = true;
    try {
        createDirs = flag(args, "create_dirs", true);
    } catch (const std::invalid_argument& e) {
        return error(e.what());
    }
    const bool exists = fs::exists(p, ec);
    if (exists) {
        auto& tracker = FileStateTracker::instance();
        if (!tracker.hasRecord(path)) {
            return error("'" + path + "' already exists. Call read_file on it first, so the overwrite "
                         "is based on what is there now.");
        }
        auto div = tracker.checkFile(path);
        if (div.diverged) {
            return error("'" + path + "' changed since it was read (" + div.reason +
                         "). Read it again before overwriting.");
        }
    } else if (!fs::is_directory(p.parent_path(), ec)) {
        if (!createDirs) return error("Folder does not exist: " + p.parent_path().u8string());
        fs::create_directories(p.parent_path(), ec);
        if (ec) return error("Cannot create folder " + p.parent_path().u8string() + ": " + ec.message());
    }
    json out = {{"status", "success"}, {"file_path", path}, {"size_bytes", content.size()},
                {"file_type", p.extension().empty() ? "text" : lower(p.extension().u8string()).substr(1)}};
    if (exists) {
        try {
            backup(path);
        } catch (const std::runtime_error& e) {
            return error(std::string("Not written: ") + e.what());
        }
    }
    std::ofstream f(p, std::ios::binary | std::ios::trunc);
    if (!f) return error("Cannot open " + path + " for writing.");
    f << content;
    f.close();
    if (!f) return error("Writing " + path + " failed (disk full or permission denied).");
    FileStateTracker::instance().recordWrite(path, content);
    return out;
}

json FlagshipTools::editFile(const json& args) {
    const std::string raw = str(args, "file_path");
    if (trim(raw).empty()) return error("file_path is required");
    if (!args.contains("old_content") || !args["old_content"].is_string()) return error("old_content must be a string");
    if (!args.contains("new_content") || !args["new_content"].is_string()) return error("new_content must be a string");
    const std::string oldContent = args["old_content"].get<std::string>();
    const std::string newContent = args["new_content"].get<std::string>();
    if (oldContent.empty()) return error("old_content must not be empty; use write_file to create a file.");
    const std::string path = resolveAllowed(raw);
    if (path.empty()) return error("'" + raw + "' is outside the folders GAIA may write.");
    const fs::path p = fs::u8path(path);
    if (auto why = writeRefusal(p); !why.empty()) return error(why);
    std::error_code ec;
    if (!fs::is_regular_file(p, ec)) return error("File not found: " + path);
    auto& tracker = FileStateTracker::instance();
    if (!tracker.hasRecord(path)) return error("Call read_file on '" + path + "' before editing it.");
    auto div = tracker.checkFile(path);
    if (div.diverged) return error("'" + path + "' changed since it was read (" + div.reason + "). Read it again.");
    std::string text;
    if (!readWhole(p, text)) return error("Cannot open " + path);
    std::vector<std::size_t> hits;
    for (auto pos = text.find(oldContent); pos != std::string::npos; pos = text.find(oldContent, pos + 1)) {
        hits.push_back(pos);
        if (hits.size() > 20) break;
    }
    if (hits.empty()) return error("old_content was not found in " + path + ". Read the file again and copy the text exactly.");
    if (hits.size() > 1) {
        json lines = json::array();
        for (auto h : hits) lines.push_back(std::count(text.begin(), text.begin() + static_cast<std::ptrdiff_t>(h), '\n') + 1);
        return {{"status", "error"},
                {"error", "old_content matches " + std::to_string(hits.size()) +
                              " places; include more surrounding lines so it matches exactly one."},
                {"ambiguous", true},
                {"match_lines", lines}};
    }
    std::string updated = text;
    updated.replace(hits[0], oldContent.size(), newContent);
    if (updated.size() > kMaxFileBytes) return error("The edited file would be over 10 MB.");
    try {
        backup(path);
    } catch (const std::runtime_error& e) {
        return error(std::string("Not edited: ") + e.what());
    }
    std::ofstream f(p, std::ios::binary | std::ios::trunc);
    if (!f) return error("Cannot open " + path + " for writing.");
    f << updated;
    f.close();
    if (!f) return error("Writing " + path + " failed.");
    tracker.recordWrite(path, updated);
    std::string diff;
    std::istringstream a(oldContent), b(newContent);
    for (std::string l; std::getline(a, l);) diff += "-" + l + "\n";
    for (std::string l; std::getline(b, l);) diff += "+" + l + "\n";
    return {{"status", "success"}, {"file_path", path}, {"old_size", text.size()},
            {"new_size", updated.size()}, {"diff", diff}};
}

json FlagshipTools::searchFile(const json& args) {
    const std::string pattern = trim(str(args, "file_pattern"));
    if (pattern.empty()) return error("file_pattern is required");
    std::string dirRaw = str(args, "directory");
    const std::string dir = resolveAllowed(dirRaw.empty() ? ctx_.workDir : dirRaw);
    if (dir.empty()) return error("'" + dirRaw + "' is outside the folders GAIA may search.");
    bool deep = false;
    try {
        deep = flag(args, "deep_search", false);
    } catch (const std::invalid_argument& e) {
        return error(e.what());
    }
    std::set<std::string> types;
    if (args.contains("file_types")) {
        const json& ft = args["file_types"];
        auto addType = [&types](std::string t) {
            t = lower(trim(t));
            if (!t.empty()) types.insert(t[0] == '.' ? t : "." + t);
        };
        if (ft.is_array()) {
            for (const auto& t : ft) if (t.is_string()) addType(t.get<std::string>());
        } else if (ft.is_string()) {
            std::stringstream ss(ft.get<std::string>());
            for (std::string t; std::getline(ss, t, ',');) addType(t);
        }
    }
    std::size_t maxShown = 10;
    try {
        maxShown = static_cast<std::size_t>(std::clamp<long long>(num(args, "max_results").value_or(10), 1, 100));
    } catch (const std::invalid_argument& e) {
        return error(e.what());
    }
    const bool isGlob = pattern.find_first_of("*?") != std::string::npos;
    const std::string needle = lower(pattern);
    json files = json::array();
    std::size_t count = 0;
    walkFiles(fs::u8path(dir), deep ? 15 : 8, deep ? 500000 : 100000, [&](const fs::directory_entry& e) {
        const std::string name = e.path().filename().u8string();
        if (!types.empty() && !types.count(lower(e.path().extension().u8string()))) return true;
        const bool hit = isGlob ? globMatch(pattern, name) : lower(name).find(needle) != std::string::npos;
        if (!hit) return true;
        ++count;
        if (files.size() < maxShown) {
            std::error_code ec;
            files.push_back({{"path", e.path().u8string()}, {"name", name},
                             {"size", humanSize(e.file_size(ec))}, {"modified", isoTime(e.last_write_time(ec))}});
        }
        return count < 1000;
    });
    return {{"status", "success"}, {"files", files}, {"count", count}, {"searched", dir},
            {"display_message", count == 0 ? "No files matching '" + pattern + "' under " + dir
                                           : "Found " + std::to_string(count) + " file(s) matching '" + pattern + "'"}};
}

json FlagshipTools::findFiles(const json& args) {
    const std::string query = trim(str(args, "query"));
    if (query.empty()) return error("query is required");
    if (!trim(str(args, "size_range")).empty() || !trim(str(args, "date_range")).empty()) {
        return error("size_range and date_range filters are not supported by this build; search by name "
                     "or content, then check sizes and dates with get_file_info.");
    }
    const std::string type = lower(trim(str(args, "search_type", "auto")));
    const std::string scope = trim(str(args, "scope", "smart"));
    long long maxResults = 25;
    try {
        maxResults = std::clamp<long long>(num(args, "max_results").value_or(25), 1, 100);
    } catch (const std::invalid_argument& e) {
        return error(e.what());
    }
    std::vector<std::string> dirs;
    const std::string ls = lower(scope);
    if (ls == "smart") dirs = {ctx_.workDir, userHomeDir()};
    else if (ls == "cwd") dirs = {ctx_.workDir};
    else if (ls == "home" || ls == "everywhere") dirs = {userHomeDir()};
    else dirs = {scope};
    json found = json::array();
    std::set<std::string> seen;
    std::size_t total = 0;
    for (const auto& d : dirs) {
        json r;
        if (type == "content") {
            r = searchFileContent({{"pattern", query}, {"directory", d}});
            if (r.value("status", "") != "success") return r;
            for (const auto& m : r["matches"]) {
                if (found.size() >= static_cast<std::size_t>(maxResults)) break;
                found.push_back(m);
            }
            total += r.value("total_matches", 0u);
        } else {
            json sub = {{"file_pattern", query}, {"directory", d}, {"max_results", maxResults}};
            if (args.contains("file_types")) sub["file_types"] = args["file_types"];
            r = searchFile(sub);
            if (r.value("status", "") != "success") return r;
            for (const auto& f : r["files"]) {
                const std::string path = f.value("path", "");
                if (!seen.insert(path).second || found.size() >= static_cast<std::size_t>(maxResults)) continue;
                found.push_back(f);
            }
            total += r.value("count", 0u);
        }
        if (!found.empty()) break;  // smart scope: the working folder wins when it has matches
    }
    return {{"status", "success"}, {"query", query}, {"results", found}, {"count", found.size()},
            {"total_found", total},
            {"display_message", found.empty() ? "Nothing found for '" + query + "'."
                                              : "Found " + std::to_string(found.size()) + " result(s) for '" + query + "'."}};
}

json FlagshipTools::searchFileContent(const json& args) {
    const std::string pattern = str(args, "pattern");
    if (pattern.empty()) return error("pattern is required");
    const std::string dirRaw = str(args, "directory", ".");
    const std::string dir = resolveAllowed(dirRaw);
    if (dir.empty()) return error("'" + dirRaw + "' is outside the folders GAIA may search.");
    const std::string filePattern = trim(str(args, "file_pattern"));
    bool caseSensitive = false;
    long long context = 0;
    try {
        caseSensitive = flag(args, "case_sensitive", false);
        context = std::clamp<long long>(num(args, "context_lines").value_or(0), 0, 5);
    } catch (const std::invalid_argument& e) {
        return error(e.what());
    }
    const std::string needle = caseSensitive ? pattern : lower(pattern);
    json matches = json::array();
    std::size_t total = 0, searched = 0;
    std::error_code ec;
    auto scanFile = [&](const fs::path& path) {
        std::error_code e2;
        if (fs::file_size(path, e2) > 2u * 1024u * 1024u || !secretRefusal(path).empty()) return;
        std::string bytes;
        if (!readWhole(path, bytes) || looksBinary(bytes)) return;
        ++searched;
        const std::string text = textOf(bytes);
        std::vector<std::string> lines;
        std::istringstream in(text);
        for (std::string l; std::getline(in, l);) lines.push_back(l);
        for (std::size_t i = 0; i < lines.size(); ++i) {
            const std::string hay = caseSensitive ? lines[i] : lower(lines[i]);
            if (hay.find(needle) == std::string::npos) continue;
            ++total;
            if (matches.size() >= 50) continue;
            const auto clip = [](const std::string& l) { return l.substr(0, utf8Cut(l, 300)); };
            std::string shown = clip(lines[i]);
            if (context > 0) {
                shown.clear();
                const std::size_t a = i >= static_cast<std::size_t>(context) ? i - static_cast<std::size_t>(context) : 0;
                const std::size_t b = std::min(lines.size() - 1, i + static_cast<std::size_t>(context));
                for (std::size_t k = a; k <= b; ++k) shown += clip(lines[k]) + (k < b ? "\n" : "");
            }
            matches.push_back({{"file", path.u8string()}, {"line", i + 1}, {"content", shown}});
        }
    };
    if (fs::is_regular_file(fs::u8path(dir), ec)) {
        scanFile(fs::u8path(dir));
    } else {
        walkFiles(fs::u8path(dir), 10, 100000, [&](const fs::directory_entry& e) {
            if (!filePattern.empty() && !globMatch(filePattern, e.path().filename().u8string())) return true;
            scanFile(e.path());
            return searched < 20000;
        });
    }
    return {{"status", "success"}, {"pattern", pattern}, {"matches", matches}, {"total_matches", total},
            {"files_searched", searched}, {"directory", dir},
            {"message", total == 0 ? "No matches." : "Found " + std::to_string(total) + " match(es)" +
                                                         (total > matches.size() ? "; showing the first 50." : ".")}};
}

json FlagshipTools::browseDirectory(const json& args) {
    std::string raw = str(args, "directory_path");
    if (trim(raw).empty()) raw = userHomeDir();
    const std::string dir = resolveAllowed(raw);
    if (dir.empty()) return error("'" + raw + "' is outside the folders GAIA may browse.");
    std::error_code ec;
    if (!fs::is_directory(fs::u8path(dir), ec)) return error("Not a folder: " + dir);
    bool showHidden = false;
    try {
        showHidden = flag(args, "show_hidden", false);
    } catch (const std::invalid_argument& e) {
        return error(e.what());
    }
    const std::string sortBy = lower(str(args, "sort_by", "name"));
    if (sortBy != "name" && sortBy != "size" && sortBy != "modified" && sortBy != "type") {
        return error("sort_by must be name, size, modified or type.");
    }
    struct Row { std::string name; bool isDir; std::uintmax_t size; fs::file_time_type mtime; };
    std::vector<Row> rows;
    std::size_t folders = 0, files = 0;
    for (const auto& e : fs::directory_iterator(fs::u8path(dir), fs::directory_options::skip_permission_denied, ec)) {
        const std::string name = e.path().filename().u8string();
        if (!showHidden && !name.empty() && name[0] == '.') continue;
        std::error_code e2;
        const bool isDir = e.is_directory(e2);
        (isDir ? folders : files)++;
        rows.push_back({name, isDir, isDir ? 0 : e.file_size(e2), e.last_write_time(e2)});
    }
    std::sort(rows.begin(), rows.end(), [&](const Row& a, const Row& b) {
        if (a.isDir != b.isDir) return a.isDir;
        if (sortBy == "size") return a.size > b.size;
        if (sortBy == "modified") return a.mtime > b.mtime;
        if (sortBy == "type") return lower(fs::u8path(a.name).extension().u8string()) < lower(fs::u8path(b.name).extension().u8string());
        return lower(a.name) < lower(b.name);
    });
    json entries = json::array();
    for (const auto& r : rows) {
        if (entries.size() >= 200) break;
        json e = {{"name", r.name}, {"type", r.isDir ? "folder" : "file"}, {"modified", isoTime(r.mtime)}};
        if (!r.isDir) e["size"] = humanSize(r.size);
        entries.push_back(e);
    }
    const fs::path parent = fs::u8path(dir).parent_path();
    return {{"status", "success"}, {"current_path", dir},
            {"parent_path", resolveAllowed(parent.u8string()).empty() ? json(nullptr) : json(parent.u8string())},
            {"entries", entries}, {"total_files", files}, {"total_folders", folders},
            {"total_entries", rows.size()}, {"entries_shown", entries.size()}, {"truncated", rows.size() > entries.size()},
            {"display_message", std::to_string(folders) + " folder(s), " + std::to_string(files) + " file(s) in " + dir}};
}

json FlagshipTools::getFileInfo(const json& args) {
    const std::string raw = str(args, "file_path");
    if (trim(raw).empty()) return error("file_path is required");
    const std::string path = resolveAllowed(raw);
    if (path.empty()) return error("'" + raw + "' is outside the folders GAIA may read.");
    const fs::path p = fs::u8path(path);
    std::error_code ec;
    if (!fs::exists(p, ec)) return error("Not found: " + path);
    const bool isDir = fs::is_directory(p, ec);
    json out = {{"status", "success"}, {"file_name", p.filename().u8string()}, {"file_path", path},
                {"is_directory", isDir}, {"modified", isoTime(fs::last_write_time(p, ec))}};
    if (!isDir) {
        const auto size = fs::file_size(p, ec);
        out["file_size_bytes"] = size;
        out["file_size"] = humanSize(size);
        out["extension"] = lower(p.extension().u8string());
        if (secretRefusal(p).empty() && size <= kMaxFileBytes) {
            std::ifstream in(p, std::ios::binary);
            std::string head(512, '\0');
            in.read(&head[0], 512);
            head.resize(static_cast<std::size_t>(in.gcount()));
            if (!looksBinary(head)) out["preview"] = textOf(size > head.size() ? dropPartialTail(head) : head);
        }
    }
    return out;
}

json FlagshipTools::analyzeDataFile(const json& args) {
    const std::string raw = str(args, "file_path");
    if (trim(raw).empty()) return error("file_path is required");
    const std::string path = resolveAllowed(raw);
    if (path.empty()) return error("'" + raw + "' is outside the folders GAIA may read.");
    if (auto why = secretRefusal(fs::u8path(path)); !why.empty()) return error(why);
    std::error_code ec;
    if (!fs::is_regular_file(fs::u8path(path), ec)) return error("File not found: " + path);
    return gaia::analyzeDataFile(path, str(args, "analysis_type", "summary"), str(args, "columns"),
                                 str(args, "group_by"), str(args, "date_range"));
}

// ---------------------------------------------------------------------------
// Shell and Python
// ---------------------------------------------------------------------------

json FlagshipTools::runShellCommand(const json& args) {
    const std::string command = trim(str(args, "command"));
    if (command.empty()) return error("command is required");
    long long timeout = 120;
    try {
        timeout = num(args, "timeout").value_or(120);
    } catch (const std::invalid_argument& e) {
        return error(e.what());
    }
    if (timeout <= 0) timeout = 120;
    if (timeout > 3600) return error("timeout is capped at 3600 seconds; split the work into shorter commands.");
    // Rewriting shared history is never a step GAIA takes on its own.
    static const std::regex forcePush(R"(\bgit\s+push\b.*(\s--force\b|\s-f\b|\s--force-with-lease\b|\s--mirror\b|\s--delete\b|\s--prune\b))");
    if (std::regex_search(command, forcePush)) {
        return error("Not run: GAIA does not force-push, mirror, delete or prune remote branches. Run that "
                     "yourself if it is really wanted.");
    }
    std::lock_guard<std::mutex> lock(shellMutex_);
    if (!shell_) shell_ = std::make_unique<ShellSession>(ctx_.workDir, "");
    const std::string wd = str(args, "working_directory");
    if (!trim(wd).empty()) {
        const std::string dir = resolveAllowed(wd);
        if (dir.empty()) return error("working_directory '" + wd + "' is outside the folders GAIA may use.");
        if (!shell_->setCwd(dir)) return error("Cannot change to " + dir);
    }
    const auto start = std::chrono::steady_clock::now();
    ProcessResult r = shell_->run(command, static_cast<int>(timeout * 1000), 4 * kShellOutputChars, ctx_.cancelled);
    std::string output = r.stdout_output;
    if (!r.stderr_output.empty()) output += (output.empty() ? "" : "\n") + r.stderr_output;
    bool truncated = false;
    if (output.size() > kShellOutputChars) {
        output = "[first " + std::to_string(output.size() - kShellOutputChars) + " chars cut]\n" +
                 output.substr(utf8Cut(output, output.size() - kShellOutputChars));
        truncated = true;
    }
    const double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    json out = {{"status", r.cancelled ? "cancelled" : r.exitCode == 0 && !r.timedOut ? "success" : "error"},
                {"command", command},
                {"output", output},
                {"exit_code", r.exitCode},
                {"cwd", shell_->cwd()},
                {"duration_seconds", std::round(secs * 10) / 10}};
    if (r.cancelled) {
        out["error"] = "Cancelled by the user; the command and everything it started were stopped.";
    } else if (r.timedOut) {
        out["timed_out"] = true;
        out["error"] = "Timed out after " + std::to_string(timeout) + " s; pass a larger timeout if it needs longer.";
    } else if (r.exitCode != 0) {
        out["error"] = "Exited with code " + std::to_string(r.exitCode) + ".";
    }
    if (truncated) out["truncated"] = true;
    return out;
}

json FlagshipTools::runPython(const json& args) {
    if (!args.contains("code") || !args["code"].is_string() || trim(args["code"].get<std::string>()).empty()) {
        return error("code is required");
    }
    const std::string code = args["code"].get<std::string>();
    static const std::regex gaiaImport(R"((^|\n)\s*(from\s+gaia(\.|\s)|import\s+gaia\b))");
    if (std::regex_search(code, gaiaImport)) {
        return error("run_python runs plain Python; GAIA's tools are not importable from it. Call the tool directly.");
    }
    long long timeout = 120;
    try {
        timeout = num(args, "timeout").value_or(120);
    } catch (const std::invalid_argument& e) {
        return error(e.what());
    }
    timeout = std::clamp<long long>(timeout, 1, 1800);
    const std::string python = findPython();
    if (python.empty()) {
        return error("No Python interpreter is installed on PATH (looked for python3, python, py). Install "
                     "Python 3 to use run_python, or compute the answer another way.");
    }
    std::error_code ec;
    fs::create_directories(fs::u8path(ctx_.scratchDir), ec);
    if (ec) return error("Cannot create scratch folder " + ctx_.scratchDir + ": " + ec.message());
    static std::atomic<unsigned> counter{0};
    const fs::path script = fs::u8path(ctx_.scratchDir) /
                            ("run_" + std::to_string(std::time(nullptr)) + "_" + std::to_string(++counter) + ".py");
    {
        std::ofstream f(script, std::ios::binary);
        if (!f) return error("Cannot write " + script.u8string());
        f << code;
    }
    std::string cwd = ctx_.workDir;
    std::string note;
    if (resolveAllowed(cwd).empty()) {
        cwd = ctx_.scratchDir;
        note = "Ran in the scratch folder: the working folder is outside the folders GAIA may use.";
    }
    std::string command = shellQuote(python) + " " + shellQuote(script.u8string());
#ifdef _WIN32
    command = "\"" + command + "\"";  // cmd /c strips one outer pair of quotes
#endif
    const auto start = std::chrono::steady_clock::now();
    ProcessResult r = ProcessRunner::run(command, static_cast<int>(timeout * 1000), cwd,
                                         {{"PYTHONIOENCODING", "utf-8"}}, 256 * 1024, ctx_.cancelled);
    fs::remove(script, ec);
    const double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    json out = {{"status", r.cancelled ? "cancelled" : r.exitCode == 0 && !r.timedOut ? "success" : "error"},
                {"stdout", r.stdout_output.substr(0, utf8Cut(r.stdout_output, 8000))},
                {"stderr", r.stderr_output.substr(0, utf8Cut(r.stderr_output, 2000))},
                {"return_code", r.exitCode},
                {"has_errors", r.exitCode != 0 || r.timedOut},
                {"duration_seconds", std::round(secs * 10) / 10}};
    if (r.cancelled) out["error"] = "Cancelled by the user; the script was stopped.";
    else if (r.timedOut) out["error"] = "Timed out after " + std::to_string(timeout) + " s.";
    else if (r.exitCode != 0) out["error"] = "Python exited with code " + std::to_string(r.exitCode) + "; see stderr.";
    if (r.stdout_output.size() > 8000) out["note"] = "stdout was cut to 8000 characters.";
    if (!note.empty()) out["note"] = note;
    return out;
}

// ---------------------------------------------------------------------------
// Scratchpad
// ---------------------------------------------------------------------------

json FlagshipTools::createTable(const json& args) {
    const std::string name = scratchName(str(args, "table_name"));
    const std::string columns = trim(str(args, "columns"));
    if (name.empty()) return error("table_name is required.");
    if (columns.empty() || columns.find(';') != std::string::npos) {
        return error("columns must be SQLite column definitions, e.g. 'name TEXT, amount REAL'.");
    }
    const std::string table = kScratchPrefix + name;
    std::lock_guard<std::mutex> lock(dbMutex_);
    auto count = scratch_.prepare(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND substr(name, 1, 8) = 'scratch_'");
    count.step();
    if (count.columnInt64(0) >= 100) return error("The scratchpad holds at most 100 tables; drop one first.");
    if (scratch_.tableExists(table)) {
        auto rows = scratch_.prepare("SELECT COUNT(*) FROM \"" + table + "\"");
        rows.step();
        return error("Table '" + name + "' already exists with " + std::to_string(rows.columnInt64(0)) +
                     " rows, possibly from an earlier session. To load this data, drop_table it first or "
                     "pick another name; never answer from it without checking where its rows came from.");
    }
    try {
        scratch_.execute("CREATE TABLE \"" + table + "\" (" + columns + ")");
    } catch (const DatabaseError& e) {
        return error(std::string("Could not create the table: ") + e.what());
    }
    return {{"status", "success"},
            {"message", "Created table '" + name + "'. Query it as " + table + ", e.g. SELECT * FROM " + table + "."}};
}

json FlagshipTools::insertData(const json& args) {
    const std::string name = scratchName(str(args, "table_name"));
    if (name.empty()) return error("table_name is required.");
    const std::string table = kScratchPrefix + name;
    json rows;
    if (args.contains("data") && args["data"].is_array()) {
        rows = args["data"];
    } else {
        const std::string raw = str(args, "data");
        if (raw.size() > 10u * 1024u * 1024u) return error("data is over 10 MB; insert it in parts.");
        try {
            rows = json::parse(raw);
        } catch (const json::parse_error& e) {
            return error(std::string("data must be a JSON array of objects: ") + e.what());
        }
    }
    if (!rows.is_array()) return error("data must be a JSON array of objects.");
    if (rows.size() > 10000) return error("At most 10,000 rows per call; insert the rest in another call.");
    std::lock_guard<std::mutex> lock(dbMutex_);
    if (!scratch_.tableExists(table)) return error("No table '" + name + "'; create it with create_table first.");
    try {
        Transaction tx(scratch_);
        for (const auto& row : rows) {
            if (!row.is_object() || row.empty()) throw std::invalid_argument("every row must be a non-empty object");
            std::string cols, marks;
            for (auto it = row.begin(); it != row.end(); ++it) {
                if (!validIdentifier(it.key())) throw std::invalid_argument("column name '" + it.key() + "' is not valid");
                cols += (cols.empty() ? "\"" : ", \"") + it.key() + "\"";
                marks += marks.empty() ? "?" : ", ?";
            }
            auto st = scratch_.prepare("INSERT INTO \"" + table + "\" (" + cols + ") VALUES (" + marks + ")");
            int i = 1;
            for (auto it = row.begin(); it != row.end(); ++it, ++i) {
                const json& v = it.value();
                if (v.is_null()) st.bindNull(i);
                else if (v.is_boolean()) st.bindInt64(i, v.get<bool>() ? 1 : 0);
                else if (v.is_number_integer()) st.bindInt64(i, v.get<long long>());
                else if (v.is_number()) st.bindDouble(i, v.get<double>());
                else if (v.is_string()) st.bindText(i, v.get<std::string>());
                else st.bindText(i, v.dump());
            }
            st.execute();
        }
        tx.commit();
    } catch (const std::invalid_argument& e) {
        return error(std::string("Nothing inserted: ") + e.what());
    } catch (const DatabaseError& e) {
        return error(std::string("Nothing inserted: ") + e.what());
    }
    return {{"status", "success"}, {"message", "Inserted " + std::to_string(rows.size()) + " row(s) into '" + name + "'."}};
}

json FlagshipTools::queryData(const json& args) {
    const std::string sql = trim(str(args, "sql"));
    // A first screen with clearer errors; st.readOnly() below is the real gate.
    std::string bare;
    bool inQuote = false;
    char q = 0;
    for (char c : sql) {
        if (inQuote) {
            if (c == q) inQuote = false;
            continue;
        }
        if (c == '\'' || c == '"') {
            inQuote = true;
            q = c;
            bare += ' ';
            continue;
        }
        bare += c;
    }
    const std::string upper = [&] {
        std::string u = bare;
        std::transform(u.begin(), u.end(), u.begin(), [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
        return u;
    }();
    if (upper.compare(0, 6, "SELECT") != 0 && upper.compare(0, 4, "WITH") != 0) {
        return error("query_data runs SELECT queries only.");
    }
    // REPLACE is not listed: as a statement readOnly() refuses it, and replace() is a string function.
    static const std::regex forbidden(
        R"(\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX)\b)");
    std::smatch m;
    if (std::regex_search(upper, m, forbidden)) return error("query_data refuses " + m.str(1) + "; it only reads.");
    if (bare.find(';') != std::string::npos && bare.find(';') + 1 < trim(bare).size()) {
        return error("Run one statement at a time.");
    }
    std::lock_guard<std::mutex> lock(dbMutex_);
    try {
        auto st = scratch_.prepare(sql);
        if (!st.readOnly()) return error("query_data runs SELECT queries only; it only reads.");
        const int cols = st.columnCount();
        std::vector<std::string> header;
        for (int c = 0; c < cols; ++c) header.push_back(st.columnName(c));
        std::vector<std::vector<std::string>> rows;
        std::size_t total = 0;
        while (st.step()) {
            ++total;
            if (rows.size() >= 500) continue;
            std::vector<std::string> row;
            for (int c = 0; c < cols; ++c) {
                std::string v = st.isNull(c) ? "NULL" : st.columnText(c);
                if (v.size() > 40) v = v.substr(0, utf8Cut(v, 37)) + "...";
                row.push_back(v);
            }
            rows.push_back(row);
        }
        std::string table = "| ";
        for (const auto& h : header) table += h + " | ";
        table += "\n|";
        for (int c = 0; c < cols; ++c) table += "---|";
        table += "\n";
        for (const auto& r : rows) {
            table += "| ";
            for (const auto& v : r) table += v + " | ";
            table += "\n";
        }
        table += "(" + std::to_string(total) + " rows returned" + (total > rows.size() ? ", first 500 shown" : "") + ")";
        return {{"status", "success"}, {"output", table}, {"row_count", total}};
    } catch (const DatabaseError& e) {
        std::string msg = std::string("Query failed: ") + e.what();
        if (msg.find("no such table") != std::string::npos) {
            msg += ". Scratchpad tables carry the scratch_ prefix: a table created as 'sales' is queried as scratch_sales.";
        }
        return error(msg);
    }
}

json FlagshipTools::listTables(const json&) {
    std::lock_guard<std::mutex> lock(dbMutex_);
    auto st = scratch_.prepare(
        "SELECT name FROM sqlite_master WHERE type='table' AND substr(name, 1, 8) = 'scratch_' ORDER BY name");
    std::string out = "Scratchpad Tables:\n\n";
    std::size_t n = 0;
    while (st.step()) {
        const std::string table = st.columnText(0);
        auto c = scratch_.prepare("SELECT COUNT(*) FROM \"" + table + "\"");
        c.step();
        std::string cols;
        auto info = scratch_.prepare("SELECT name, type FROM pragma_table_info(?)");
        info.bindText(1, table);
        while (info.step()) cols += (cols.empty() ? "" : ", ") + info.columnText(0) + " (" + info.columnText(1) + ")";
        out += "  " + table.substr(kScratchPrefix.size()) + " (" + std::to_string(c.columnInt64(0)) + " rows)\n" +
               "    Columns: " + cols + "\n\n";
        ++n;
    }
    return {{"status", "success"}, {"count", n},
            {"output", n == 0 ? "No scratchpad tables exist yet. Use create_table() to create one."
                              : out + "Query a table as scratch_<name>, e.g. SELECT * FROM scratch_sales."}};
}

json FlagshipTools::dropTable(const json& args) {
    const std::string name = scratchName(str(args, "table_name"));
    if (name.empty()) return error("table_name is required.");
    const std::string table = kScratchPrefix + name;
    std::lock_guard<std::mutex> lock(dbMutex_);
    if (!scratch_.tableExists(table)) return error("No table '" + name + "'.");
    scratch_.execute("DROP TABLE \"" + table + "\"");
    return {{"status", "success"}, {"message", "Dropped table '" + name + "'."}};
}

json FlagshipTools::sleep(const json& args) {
    double seconds = 0;
    try {
        if (args.contains("seconds") && args["seconds"].is_number()) seconds = args["seconds"].get<double>();
        else if (auto n = num(args, "seconds")) seconds = static_cast<double>(*n);
    } catch (const std::invalid_argument& e) {
        return error(e.what());
    }
    if (seconds <= 0 || seconds > 300) return error("seconds must be more than 0 and at most 300.");
    const std::string reason = str(args, "reason");
    const auto start = std::chrono::steady_clock::now();
    const auto until = start + std::chrono::milliseconds(static_cast<long long>(seconds * 1000));
    while (std::chrono::steady_clock::now() < until) {
        if (ctx_.cancelled && ctx_.cancelled()) {
            const double slept = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
            return {{"status", "cancelled"}, {"requested_seconds", seconds}, {"slept_seconds", slept},
                    {"message", "Stopped early: the user cancelled."}};
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    return {{"status", "success"}, {"slept_seconds", seconds}, {"reason", reason}};
}

// ---------------------------------------------------------------------------
// More of the Python full profile's file, web and loop tools
// ---------------------------------------------------------------------------

json FlagshipTools::tree(const json& args) {
    const std::string raw = str(args, "path", ".");
    const std::string root = resolveAllowed(raw.empty() ? "." : raw);
    if (root.empty()) return error("'" + raw + "' is outside the folders GAIA may browse.");
    std::error_code ec;
    if (!fs::is_directory(fs::u8path(root), ec)) return error("Not a folder: " + root);
    long long maxDepth = 3;
    bool showSizes = false, dirsOnly = false;
    try {
        maxDepth = std::clamp<long long>(num(args, "max_depth").value_or(3), 1, 10);
        showSizes = flag(args, "show_sizes", false);
        dirsOnly = flag(args, "dirs_only", false);
    } catch (const std::invalid_argument& e) {
        return error(e.what());
    }
    const std::string include = trim(str(args, "include_pattern"));
    const std::string exclude = trim(str(args, "exclude_pattern"));
    std::string out = fs::u8path(root).filename().u8string() + "/\n";
    std::size_t lines = 0, dirs = 0, files = 0;
    std::function<void(const fs::path&, int, const std::string&)> walk = [&](const fs::path& dir, int depth, const std::string& indent) {
        std::vector<fs::directory_entry> entries;
        std::error_code e2;
        for (const auto& e : fs::directory_iterator(dir, fs::directory_options::skip_permission_denied, e2)) {
            const std::string name = e.path().filename().u8string();
            if (!name.empty() && name[0] == '.') continue;
            entries.push_back(e);
        }
        std::sort(entries.begin(), entries.end(), [](const auto& a, const auto& b) {
            std::error_code x;
            const bool ad = a.is_directory(x), bd = b.is_directory(x);
            if (ad != bd) return ad;
            return lower(a.path().filename().u8string()) < lower(b.path().filename().u8string());
        });
        for (std::size_t i = 0; i < entries.size() && lines < 500; ++i) {
            const auto& e = entries[i];
            const std::string name = e.path().filename().u8string();
            std::error_code x;
            const bool isDir = e.is_directory(x);
            if (!exclude.empty() && globMatch(exclude, name)) continue;
            if (!isDir && (dirsOnly || (!include.empty() && !globMatch(include, name)))) continue;
            const bool lastOne = i + 1 == entries.size();
            out += indent + (lastOne ? "`-- " : "|-- ") + name + (isDir ? "/" : "");
            if (showSizes && !isDir) out += " (" + humanSize(e.file_size(x)) + ")";
            out += "\n";
            ++lines;
            (isDir ? dirs : files)++;
            if (isDir && depth + 1 < maxDepth && !kSkipDirs.count(name)) walk(e.path(), depth + 1, indent + (lastOne ? "    " : "|   "));
        }
    };
    walk(fs::u8path(root), 0, "");
    if (lines >= 500) out += "... (cut at 500 entries; narrow the path or max_depth)\n";
    return {{"status", "success"}, {"path", root}, {"tree", out}, {"directories", dirs}, {"files", files}};
}

json FlagshipTools::fileInfo(const json& args) {
    json a = args.is_object() ? args : json::object();
    if (!a.contains("file_path") && a.contains("path")) a["file_path"] = a["path"];
    return getFileInfo(a);
}

json FlagshipTools::listRecentFiles(const json& args) {
    long long maxResults = 20, days = 30;
    try {
        maxResults = std::clamp<long long>(num(args, "max_results").value_or(20), 1, 200);
        days = std::clamp<long long>(num(args, "days").value_or(30), 1, 3650);
    } catch (const std::invalid_argument& e) {
        return error(e.what());
    }
    std::set<std::string> types;
    {
        std::stringstream ss(str(args, "file_types"));
        for (std::string t; std::getline(ss, t, ',');) {
            t = lower(trim(t));
            if (!t.empty()) types.insert(t[0] == '.' ? t : "." + t);
        }
    }
    const std::string location = lower(trim(str(args, "location", "all")));
    std::vector<std::string> roots;
    const fs::path home = fs::u8path(userHomeDir());
    if (location == "all" || location.empty()) {
        roots = {ctx_.workDir, (home / "Documents").u8string(), (home / "Downloads").u8string(), (home / "Desktop").u8string()};
    } else if (location == "documents" || location == "downloads" || location == "desktop") {
        std::string folder = location;
        folder[0] = static_cast<char>(std::toupper(static_cast<unsigned char>(folder[0])));
        roots = {(home / folder).u8string()};
    } else {
        roots = {str(args, "location")};
    }
    const auto cutoff = fs::file_time_type::clock::now() - std::chrono::hours(24 * days);
    std::vector<std::pair<fs::file_time_type, std::string>> found;
    std::set<std::string> seen;
    for (const auto& r : roots) {
        const std::string dir = resolveAllowed(r);
        std::error_code ec;
        if (dir.empty() || !fs::is_directory(fs::u8path(dir), ec)) continue;
        walkFiles(fs::u8path(dir), 6, 50000, [&](const fs::directory_entry& e) {
            std::error_code x;
            if (!types.empty() && !types.count(lower(e.path().extension().u8string()))) return true;
            const auto t = e.last_write_time(x);
            if (!x && t >= cutoff && seen.insert(e.path().u8string()).second) found.emplace_back(t, e.path().u8string());
            return true;
        });
    }
    std::sort(found.begin(), found.end(), [](const auto& a, const auto& b) { return a.first > b.first; });
    json files = json::array();
    for (std::size_t i = 0; i < found.size() && files.size() < static_cast<std::size_t>(maxResults); ++i) {
        files.push_back({{"path", found[i].second}, {"modified", isoTime(found[i].first)}});
    }
    return {{"status", "success"}, {"files", files}, {"count", files.size()}, {"days", days},
            {"display_message", files.empty() ? "No files changed in the last " + std::to_string(days) + " days."
                                              : std::to_string(files.size()) + " recently changed file(s)."}};
}

json FlagshipTools::searchDirectory(const json& args) {
    const std::string wanted = lower(trim(str(args, "directory_name")));
    if (wanted.empty()) return error("directory_name is required");
    const std::string rootRaw = str(args, "search_root");
    const std::string root = resolveAllowed(rootRaw.empty() ? userHomeDir() : rootRaw);
    if (root.empty()) return error("'" + rootRaw + "' is outside the folders GAIA may search.");
    long long maxDepth = 4;
    try {
        maxDepth = std::clamp<long long>(num(args, "max_depth").value_or(4), 1, 10);
    } catch (const std::invalid_argument& e) {
        return error(e.what());
    }
    json matches = json::array();
    std::error_code ec;
    fs::recursive_directory_iterator it(fs::u8path(root), fs::directory_options::skip_permission_denied, ec), end;
    std::size_t visits = 0;
    for (; !ec && it != end && visits < 100000 && matches.size() < 20; it.increment(ec), ++visits) {
        std::error_code x;
        if (!it->is_directory(x)) continue;
        const std::string name = it->path().filename().u8string();
        if (it.depth() >= maxDepth || kSkipDirs.count(name) || (!name.empty() && name[0] == '.')) {
            it.disable_recursion_pending();
            if (!name.empty() && name[0] == '.') continue;
        }
        if (lower(name).find(wanted) != std::string::npos) matches.push_back(it->path().u8string());
    }
    return {{"status", "success"}, {"directories", matches}, {"count", matches.size()}, {"searched", root}};
}

json FlagshipTools::downloadFile(const json& args) {
    const std::string url = trim(str(args, "url"));
    if (url.empty()) return error("url is required");
    std::string saveTo = str(args, "save_to", "~/Downloads");
    if (trim(saveTo).empty()) saveTo = "~/Downloads";
    const std::string dir = resolveAllowed(saveTo);
    if (dir.empty()) return error("'" + saveTo + "' is outside the folders GAIA may write.");
    HttpFetch r;
    json res = fetchFollowing(url, r);
    if (res.value("status", "") != "success") return res;
    if (r.status >= 400) return error("HTTP " + std::to_string(r.status) + " from " + res.value("url", url));
    std::string name = trim(str(args, "filename"));
    if (name.empty()) {
        std::string u = res.value("url", url);
        u = u.substr(0, u.find_first_of("?#"));
        name = u.substr(u.rfind('/') + 1);
        if (name.empty()) name = "download";
    }
    // ':' would name a drive ("D:run.bat") or an NTFS stream on Windows.
    if (name.find_first_of("/\\:") != std::string::npos || winName(name) == ".." || winName(name) == "." ||
        winName(name).empty() || fs::u8path(name).has_root_path()) {
        return error("filename must be a plain file name, not a path.");
    }
    std::error_code ec;
    fs::create_directories(fs::u8path(dir), ec);
    if (ec) return error("Cannot create folder " + dir + ": " + ec.message());
    fs::path target = fs::u8path(dir) / fs::u8path(name);
    for (int n = 1; fs::exists(target, ec) && n < 1000; ++n) {  // never overwrite
        target = fs::u8path(dir) / fs::u8path(fs::u8path(name).stem().u8string() + " (" + std::to_string(n) + ")" +
                                              fs::u8path(name).extension().u8string());
    }
    if (fs::exists(target, ec)) return error("Too many files named like '" + name + "' in " + dir + "; pass a filename.");
    const std::string checked = resolveAllowed(target.u8string());
    if (checked.empty() || !samePath(fs::u8path(checked).parent_path(), fs::u8path(dir))) {
        return error("'" + target.u8string() + "' is outside the folders GAIA may write.");
    }
    if (auto why = writeRefusal(fs::u8path(checked)); !why.empty()) return error(why);
    target = fs::u8path(checked);
    std::ofstream f(target, std::ios::binary);
    if (!f) return error("Cannot write " + target.u8string());
    f << r.body;
    f.close();
    if (!f) {
        fs::remove(target, ec);
        return error("Writing " + target.u8string() + " failed (disk full or permission denied); nothing was saved.");
    }
    return {{"status", "success"}, {"url", res.value("url", url)}, {"file_path", target.u8string()},
            {"size_bytes", r.body.size()}, {"content_type", r.contentType}};
}

json FlagshipTools::dumpDocument(const json& args) {
    std::string raw = str(args, "file_name");
    if (trim(raw).empty()) raw = str(args, "file_path");
    if (trim(raw).empty()) return error("file_name is required");
    const std::string path = resolveAllowed(raw);
    if (path.empty()) return error("'" + raw + "' is outside the folders GAIA may read.");
    if (auto why = secretRefusal(fs::u8path(path)); !why.empty()) return error(why);
    if (!isDocumentExtension(fs::u8path(path).extension().u8string())) {
        return error("dump_document converts PDF, Word, Excel and PowerPoint files; read text files with read_file.");
    }
    std::string outRaw = trim(str(args, "output_path"));
    // Python's default name: <stem>_extracted.md in GAIA's own folder.
    const std::string out =
        outRaw.empty() ? (fs::u8path(ctx_.scratchDir) / (fs::u8path(path).stem().u8string() + "_extracted.md")).u8string()
                       : resolveAllowed(outRaw);
    if (out.empty()) return error("'" + outRaw + "' is outside the folders GAIA may write.");
    if (lower(fs::u8path(out).extension().u8string()) != ".md" && lower(fs::u8path(out).extension().u8string()) != ".txt") {
        return error("output_path must end in .md or .txt; dump_document writes a text copy of the document.");
    }
    if (auto why = writeRefusal(fs::u8path(out), fs::u8path(ctx_.scratchDir)); !why.empty()) return error(why);
    std::error_code ec;
    // The scratch copy is ours to refresh; a user's file is only replaced through edit/write_file.
    if (!outRaw.empty() && fs::exists(fs::u8path(out), ec)) {
        return error("'" + out + "' already exists; choose a new output_path.");
    }
    DocumentText doc;
    try {
        doc = extractDocumentText(path);
    } catch (const std::runtime_error& e) {
        return error(e.what());
    }
    fs::create_directories(fs::u8path(out).parent_path(), ec);
    if (ec) return error("Cannot create folder " + fs::u8path(out).parent_path().u8string() + ": " + ec.message());
    std::ofstream f(fs::u8path(out), std::ios::binary);
    if (!f) return error("Cannot write " + out);
    f << "# " << fs::u8path(path).filename().u8string() << "\n\n" << doc.text;
    f.close();
    if (!f) {
        fs::remove(fs::u8path(out), ec);
        return error("Writing " + out + " failed (disk full or permission denied); nothing was saved.");
    }
    return {{"status", "success"}, {"source", path}, {"output_path", out}, {"characters", doc.text.size()},
            {"sections", doc.sections}};
}

namespace {

const std::set<std::string> kImageExtensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"};

}  // namespace

static json askAboutImage(FlagshipTools& tools, const FlagshipToolContext& ctx, const std::string& raw,
                          const std::string& prompt) {
    if (!ctx.vision) return error("Image analysis is not available in this session.");
    if (trim(raw).empty()) return error("image_path is required");
    const std::string path = tools.resolveAllowed(raw);
    if (path.empty()) return error("'" + raw + "' is outside the folders GAIA may read.");
    const fs::path p = fs::u8path(path);
    if (auto why = credentialRefusal(path); !why.empty()) return error(why);
    if (!kImageExtensions.count(lower(p.extension().u8string()))) {
        return error("'" + raw + "' is not an image (png, jpg, gif, webp or bmp).");
    }
    std::error_code ec;
    if (!fs::is_regular_file(p, ec)) return error("File not found: " + raw);
    if (fs::file_size(p, ec) > 20u * 1024u * 1024u) return error("'" + raw + "' is over 20 MB.");
    std::string bytes;
    if (!readWhole(p, bytes)) return error("Cannot open " + path);
    try {
        const std::string reply = ctx.vision(prompt, {bytes});
        return {{"status", "success"}, {"image_path", path}, {"description", reply}};
    } catch (const std::exception& e) {
        return error(std::string("The vision model could not read the image: ") + e.what());
    }
}

json FlagshipTools::analyzeImage(const json& args) {
    const std::string focus = trim(str(args, "focus", "all"));
    const std::string prompt =
        "Describe this image in detail" + (focus.empty() || focus == "all" ? std::string(".") : ", focusing on " + focus + ".") +
        " Transcribe any visible text exactly, including numbers.";
    return askAboutImage(*this, ctx_, str(args, "image_path"), prompt);
}

json FlagshipTools::answerQuestionAboutImage(const json& args) {
    const std::string question = trim(str(args, "question"));
    if (question.empty()) return error("question is required");
    json r = askAboutImage(*this, ctx_, str(args, "image_path"),
                           "Answer this question about the image from what is visible in it; say so if it cannot be "
                           "told from the image. Question: " + question);
    if (r.value("status", "") == "success") {
        r["answer"] = r["description"];
        r.erase("description");
        r["question"] = question;
    }
    return r;
}

json FlagshipTools::requestUserInput(const json& args) {
    const std::string message = trim(str(args, "message"));
    if (message.empty()) return error("message is required");
    std::vector<std::string> choices;
    if (args.contains("choices") && args["choices"].is_array()) {
        for (const auto& c : args["choices"]) choices.push_back(c.is_string() ? c.get<std::string>() : c.dump());
    }
    std::optional<std::string> answer;
    if (ctx_.askUser) answer = ctx_.askUser(message, choices);
    if (answer && !trim(*answer).empty()) return {{"status", "success"}, {"response", *answer}};
    const std::string fallback = str(args, "default_if_no_response");
    return {{"status", "no_response"}, {"response", fallback.empty() ? "__NO_RESPONSE__" : fallback},
            {"message", "Nobody answered; carry on with the safest reasonable choice and say which you made."}};
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

void FlagshipTools::registerAll(ToolRegistry& registry, bool includeWeb) {
    using T = ToolParamType;
    auto add = [&](const std::string& name, const std::string& desc, std::vector<ToolParameter> params,
                   json (FlagshipTools::*fn)(const json&), ToolPolicy policy = ToolPolicy::ALLOW) {
        ToolInfo info;
        info.name = name;
        info.description = desc;
        info.parameters = std::move(params);
        info.policy = policy;
        info.callback = [this, fn](const json& args) -> json {
            try {
                return (this->*fn)(args);
            } catch (const std::exception& e) {
                return error(e.what());
            }
        };
        registry.registerTool(std::move(info));
    };

    add("read_file",
        "Read a file: text, or the text of a PDF, Word (.docx), Excel (.xlsx) or PowerPoint (.pptx) "
        "document, with '--- Page N ---' markers to cite. Whole file by default; page long files with "
        "offset/limit (max 8000 characters) or start_line/end_line (max 400 lines). Read a file before "
        "editing or overwriting it.",
        {{"file_path", T::STRING, true, "Path to the file (~ and relative paths allowed)"},
         {"offset", T::INTEGER, false, "Character offset to start at"},
         {"limit", T::INTEGER, false, "Characters to read (max 8000)"},
         {"start_line", T::INTEGER, false, "First line to read (1-based)"},
         {"end_line", T::INTEGER, false, "Last line to read"}},
        &FlagshipTools::readFile);
    add("write_file",
        "Create a file, or replace one you have read with read_file. Creates missing folders.",
        {{"file_path", T::STRING, true, "Where to write"},
         {"content", T::STRING, true, "The full file contents"},
         {"create_dirs", T::BOOLEAN, false, "Create missing folders (default true)"}},
        &FlagshipTools::writeFile, ToolPolicy::CONFIRM);
    add("edit_file",
        "Replace one exact passage in a file you have read. old_content must match exactly one "
        "place; include a few surrounding lines to make it unique. Put only the lines that change "
        "in old_content and new_content.",
        {{"file_path", T::STRING, true, "The file to edit"},
         {"old_content", T::STRING, true, "Exact text to replace"},
         {"new_content", T::STRING, true, "Replacement text"}},
        &FlagshipTools::editFile, ToolPolicy::CONFIRM);
    add("search_file",
        "Find files by name: a substring or a glob such as '*.csv'. Searches the working folder "
        "unless directory is given.",
        {{"file_pattern", T::STRING, true, "Name, part of a name, or glob"},
         {"directory", T::STRING, false, "Folder to search"},
         {"deep_search", T::BOOLEAN, false, "Search deeper folders (slower)"},
         {"file_types", T::UNKNOWN, false, "Extensions to keep, e.g. 'pdf,docx'"},
         {"max_results", T::INTEGER, false, "How many paths to show (default 10)"}},
        &FlagshipTools::searchFile);
    add("find_files",
        "Find files by name (or content with search_type='content'). scope: smart (working folder, "
        "then home), cwd, home, or a folder path.",
        {{"query", T::STRING, true, "Name, glob like '*.pdf', or text to find"},
         {"search_type", T::STRING, false, "auto, name or content"},
         {"scope", T::STRING, false, "smart, cwd, home, or a folder"},
         {"file_types", T::UNKNOWN, false, "Extensions to keep, e.g. 'pdf,docx'"},
         {"size_range", T::STRING, false, "Not supported in this build"},
         {"date_range", T::STRING, false, "Not supported in this build"},
         {"max_results", T::INTEGER, false, "How many (default 25)"},
         {"sort_by", T::STRING, false, "Ignored; results come in folder order"}},
        &FlagshipTools::findFiles);
    add("search_file_content",
        "Find lines containing some text inside files (like grep).",
        {{"pattern", T::STRING, true, "Text to look for"},
         {"directory", T::STRING, false, "Folder or file to search (default: working folder)"},
         {"file_pattern", T::STRING, false, "Only files matching this glob, e.g. '*.py'"},
         {"case_sensitive", T::BOOLEAN, false, "Match case (default false)"},
         {"context_lines", T::INTEGER, false, "Lines of context around each match (0-5)"}},
        &FlagshipTools::searchFileContent);
    add("browse_directory",
        "List a folder's files and subfolders (home folder by default).",
        {{"directory_path", T::STRING, false, "Folder to list"},
         {"show_hidden", T::BOOLEAN, false, "Include hidden entries"},
         {"sort_by", T::STRING, false, "name, size, modified or type"}},
        &FlagshipTools::browseDirectory);
    add("get_file_info", "Size, dates and a short preview of a file or folder.",
        {{"file_path", T::STRING, true, "The file or folder"}}, &FlagshipTools::getFileInfo);
    add("run_shell_command",
#ifdef _WIN32
        "Run a command in cmd.exe and return its output. The session keeps its current folder "
        "between calls. Asks the user before every run.",
#else
        "Run a shell command (/bin/sh) and return its output. The session keeps its current folder "
        "between calls. Asks the user before every run.",
#endif
        {{"command", T::STRING, true, "The command line"},
         {"working_directory", T::STRING, false, "Folder to run in"},
         {"timeout", T::INTEGER, false, "Seconds before it is stopped (default 120, max 3600)"}},
        &FlagshipTools::runShellCommand, ToolPolicy::CONFIRM);
    add("run_python",
        "Run a Python 3 script and return stdout/stderr. Use it to compute numbers instead of "
        "guessing. Asks the user before every run.",
        {{"code", T::STRING, true, "The Python source"},
         {"timeout", T::INTEGER, false, "Seconds before it is stopped (default 120, max 1800)"}},
        &FlagshipTools::runPython, ToolPolicy::CONFIRM);
    if (includeWeb) {
        add("fetch_webpage",
            "Download a web page and return its readable text (or raw HTML with extract_text=false).",
            {{"url", T::STRING, true, "http(s) URL"},
             {"extract_text", T::BOOLEAN, false, "Return text instead of HTML (default true)"}},
            &FlagshipTools::fetchWebpage);
        add("fetch_page",
            "Fetch a web page. extract: text (default), html or links. Long text is paged: pass the "
            "returned next_offset as offset. To pull one detail (a price, a spec) pass find.",
            {{"url", T::STRING, true, "http(s) URL"},
             {"extract", T::STRING, false, "text, html or links"},
             {"max_length", T::INTEGER, false, "Characters to return (512-20000, default 5000)"},
             {"offset", T::INTEGER, false, "Text mode: where to continue a long page"},
             {"find", T::STRING, false, "Text mode: only passages containing this phrase"}},
            &FlagshipTools::fetchPage);
        add("download_file", "Save a file from a URL (never overwrites; a new name is chosen instead).",
            {{"url", T::STRING, true, "http(s) URL"},
             {"save_to", T::STRING, false, "Folder (default ~/Downloads)"},
             {"filename", T::STRING, false, "File name (default: from the URL)"}},
            &FlagshipTools::downloadFile, ToolPolicy::CONFIRM);
        add("search_web",
            "Search the web (DuckDuckGo) and return titles, links and snippets. Read a result with "
            "fetch_webpage.",
            {{"query", T::STRING, true, "What to search for"},
             {"num_results", T::INTEGER, false, "How many results (1-10, default 5)"}},
            &FlagshipTools::searchWeb);
    }
    add("analyze_data_file",
        "Exact numbers from a CSV, TSV or Excel file: per-column totals, averages, min/max and top "
        "values (analysis_type='summary'); group_by=<column> sums every numeric column per group, "
        "largest first, with top_1 and each group's share; date_range filters rows (2025-Q1, "
        "2025-03, 2025, '2025-01 to 2025-06'); analysis_type='trends' adds monthly totals. Handles "
        "semicolon/tab files, UTF-16, currency and European decimals, and says which rows it left out.",
        {{"file_path", T::STRING, true, "The data file"},
         {"analysis_type", T::STRING, false, "summary (default), trends or full"},
         {"columns", T::STRING, false, "Comma-separated columns to focus on"},
         {"group_by", T::STRING, false, "Column to group rows by"},
         {"date_range", T::STRING, false, "Keep only rows dated in this period"}},
        &FlagshipTools::analyzeDataFile);
    add("create_table",
        "Create a scratchpad SQL table for analysing data, e.g. columns='region TEXT, revenue REAL'.",
        {{"table_name", T::STRING, true, "Table name"},
         {"columns", T::STRING, true, "SQLite column definitions"}},
        &FlagshipTools::createTable);
    add("insert_data", "Insert rows into a scratchpad table: data is a JSON array of objects.",
        {{"table_name", T::STRING, true, "Table name"},
         {"data", T::UNKNOWN, true, "JSON array of row objects"}},
        &FlagshipTools::insertData);
    add("query_data",
        "Run a read-only SELECT over the scratchpad tables and return a table. Table names take a "
        "scratch_ prefix: a table created as 'sales' is queried as scratch_sales.",
        {{"sql", T::STRING, true, "One SELECT (or WITH … SELECT) statement"}}, &FlagshipTools::queryData);
    add("list_tables", "List the scratchpad tables, their columns and row counts.", {},
        &FlagshipTools::listTables);
    add("drop_table", "Delete a scratchpad table.", {{"table_name", T::STRING, true, "Table name"}},
        &FlagshipTools::dropTable);
    add("tree", "Show a folder's structure as an indented tree.",
        {{"path", T::STRING, false, "Folder (default: working folder)"},
         {"max_depth", T::INTEGER, false, "Levels to show (default 3)"},
         {"show_sizes", T::BOOLEAN, false, "Show file sizes"},
         {"include_pattern", T::STRING, false, "Only files matching this glob"},
         {"exclude_pattern", T::STRING, false, "Skip entries matching this glob"},
         {"dirs_only", T::BOOLEAN, false, "Folders only"}},
        &FlagshipTools::tree);
    add("file_info", "Size, dates and a short preview of a file or folder.",
        {{"path", T::STRING, true, "The file or folder"}}, &FlagshipTools::fileInfo);
    add("list_recent_files", "Files changed recently in the working folder, Documents, Downloads and Desktop.",
        {{"location", T::STRING, false, "all (default), documents, downloads, desktop, or a folder"},
         {"file_types", T::STRING, false, "Extensions, e.g. 'pdf,docx'"},
         {"max_results", T::INTEGER, false, "How many (default 20)"},
         {"days", T::INTEGER, false, "Changed within this many days (default 30)"}},
        &FlagshipTools::listRecentFiles);
    add("search_directory", "Find folders by name.",
        {{"directory_name", T::STRING, true, "Folder name or part of it"},
         {"search_root", T::STRING, false, "Where to start (default: home)"},
         {"max_depth", T::INTEGER, false, "How deep to look (default 4)"}},
        &FlagshipTools::searchDirectory);
    add("dump_document", "Convert a PDF, Word, Excel or PowerPoint file to a Markdown text file.",
        {{"file_name", T::STRING, true, "The document"},
         {"output_path", T::STRING, false, "Where to write the .md or .txt (default: scratch folder)"}},
        &FlagshipTools::dumpDocument, ToolPolicy::CONFIRM);
    add("request_user_input",
        "Ask the user a question and wait for the answer. Only when you cannot proceed safely without it.",
        {{"message", T::STRING, true, "The question"},
         {"choices", T::ARRAY, false, "Answers to offer"},
         {"default_if_no_response", T::STRING, false, "What to use if nobody answers"},
         {"timeout_seconds", T::INTEGER, false, "How long to wait (max 600)"},
         {"continue_if_no_response", T::BOOLEAN, false, "Carry on without an answer (default true)"}},
        &FlagshipTools::requestUserInput);
    if (ctx_.vision) {
        add("analyze_image", "Describe an image file (png, jpg, gif, webp, bmp), including any text in it.",
            {{"image_path", T::STRING, true, "The image"},
             {"focus", T::STRING, false, "What to focus on (default: all)"}},
            &FlagshipTools::analyzeImage);
        add("answer_question_about_image", "Answer a question about an image file.",
            {{"image_path", T::STRING, true, "The image"}, {"question", T::STRING, true, "The question"}},
            &FlagshipTools::answerQuestionAboutImage);
    }
    add("sleep",
        "Wait up to 300 seconds, e.g. until a rate limit resets. Ends early if the user cancels.",
        {{"seconds", T::NUMBER, true, "How long to wait (max 300)"},
         {"reason", T::STRING, false, "Why, shown to the user"}},
        &FlagshipTools::sleep);
    registerMedia(registry);
}

}  // namespace gaia
