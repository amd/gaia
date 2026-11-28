// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/automation_store.h"

#include <algorithm>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <random>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <thread>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#include <sys/locking.h>
#include <sys/stat.h>
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>
#endif

namespace fs = std::filesystem;

namespace gaia {

namespace {

constexpr const char* kKindReminder = "reminder";
constexpr const char* kKindSummary = "summary";
constexpr const char* kKindTrigger = "trigger";
constexpr const char* kKindAlert = "alert";
constexpr int kLockTimeoutMs = 10000;
constexpr int kLockPollMs = 20;

double nowSeconds() {
    using namespace std::chrono;
    return duration<double>(system_clock::now().time_since_epoch()).count();
}

std::string trim(const std::string& s) {
    const size_t first = s.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return "";
    return s.substr(first, s.find_last_not_of(" \t\r\n") - first + 1);
}

std::string toLower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

fs::path userHomeDir() {
#ifdef _WIN32
    const wchar_t* profile = _wgetenv(L"USERPROFILE");
    if (profile && *profile) return fs::path(profile);
    const std::string fallback = getEnvVar("USERPROFILE");
    return fallback.empty() ? fs::path() : fs::u8path(fallback);
#else
    const std::string home = getEnvVar("HOME");
    return home.empty() ? fs::path() : fs::u8path(home);
#endif
}

fs::path expandUserPath(const std::string& raw) {
    if (raw == "~" || raw.rfind("~/", 0) == 0 || raw.rfind("~\\", 0) == 0) {
        const fs::path home = userHomeDir();
        if (!home.empty()) return raw.size() <= 2 ? home : home / fs::u8path(raw.substr(2));
    }
    return fs::u8path(raw);
}

std::string randomHex(size_t chars) {
    static thread_local std::mt19937_64 rng(std::random_device{}());
    static constexpr char kHex[] = "0123456789abcdef";
    std::uniform_int_distribution<int> dist(0, 15);
    std::string out;
    out.reserve(chars);
    for (size_t i = 0; i < chars; ++i) out.push_back(kHex[dist(rng)]);
    return out;
}

std::tm toLocalTm(std::time_t t) {
    std::tm out{};
#ifdef _WIN32
    localtime_s(&out, &t);
#else
    localtime_r(&t, &out);
#endif
    return out;
}

std::time_t utcTmToTimeT(std::tm* tm) {
#ifdef _WIN32
    return _mkgmtime(tm);
#else
    return timegm(tm);
#endif
}

// Cross-process advisory lock matching gaia.utils.file_lock (<target>.lock).
class AdvisoryFileLock {
public:
    explicit AdvisoryFileLock(const fs::path& target) {
        const fs::path lockPath = fs::u8path(target.u8string() + ".lock");
        std::error_code ec;
        if (!lockPath.parent_path().empty()) {
            fs::create_directories(lockPath.parent_path(), ec);
        }
#ifdef _WIN32
        fd_ = _wopen(lockPath.c_str(), _O_RDWR | _O_CREAT, _S_IREAD | _S_IWRITE);
#else
        fd_ = ::open(lockPath.c_str(), O_RDWR | O_CREAT, 0600);
#endif
        if (fd_ < 0) {
            throw std::runtime_error("Cannot open lock file " + lockPath.u8string() + ": " + std::strerror(errno));
        }

        const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(kLockTimeoutMs);
        while (true) {
            if (tryLock()) return;
            if (std::chrono::steady_clock::now() >= deadline) {
                closeFd();
                throw std::runtime_error("Timed out waiting for lock on " + target.u8string() +
                                         ". Retry once other GAIA operations finish.");
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(kLockPollMs));
        }
    }

    ~AdvisoryFileLock() {
        if (fd_ >= 0) {
#ifdef _WIN32
            _lseek(fd_, 0, SEEK_SET);
            _locking(fd_, _LK_UNLCK, 1);
#else
            ::flock(fd_, LOCK_UN);
#endif
            closeFd();
        }
    }

    AdvisoryFileLock(const AdvisoryFileLock&) = delete;
    AdvisoryFileLock& operator=(const AdvisoryFileLock&) = delete;

private:
    bool tryLock() {
#ifdef _WIN32
        _lseek(fd_, 0, SEEK_SET);
        return _locking(fd_, _LK_NBLCK, 1) == 0;
#else
        return ::flock(fd_, LOCK_EX | LOCK_NB) == 0;
#endif
    }

    void closeFd() {
        if (fd_ >= 0) {
#ifdef _WIN32
            _close(fd_);
#else
            ::close(fd_);
#endif
            fd_ = -1;
        }
    }

    int fd_ = -1;
};

void writeJsonAtomic(const fs::path& target, const json& data) {
    std::error_code ec;
    if (!target.parent_path().empty()) {
        fs::create_directories(target.parent_path(), ec);
        if (ec) {
            throw std::runtime_error("Cannot create directory " + target.parent_path().u8string() + ": " +
                                     ec.message());
        }
    }
    const fs::path tmp = target.parent_path() / ("." + target.filename().u8string() + "." + randomHex(12));
    const std::string bytes = data.dump(2) + "\n";

#ifdef _WIN32
    const int fd = _wopen(tmp.c_str(), _O_WRONLY | _O_CREAT | _O_EXCL | _O_BINARY, _S_IREAD | _S_IWRITE);
#else
    const int fd = ::open(tmp.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0600);
#endif
    if (fd < 0) {
        throw std::runtime_error("Cannot write " + target.u8string() + ": " + std::strerror(errno));
    }

    bool ok = true;
    size_t written = 0;
    while (written < bytes.size()) {
#ifdef _WIN32
        const int n = _write(fd, bytes.data() + written,
                             static_cast<unsigned int>(std::min<size_t>(bytes.size() - written, 1u << 20)));
#else
        const ssize_t n = ::write(fd, bytes.data() + written, bytes.size() - written);
#endif
        if (n <= 0) {
            ok = false;
            break;
        }
        written += static_cast<size_t>(n);
    }
    if (ok) {
#ifdef _WIN32
        ok = _commit(fd) == 0;
#else
        ok = ::fsync(fd) == 0;
#endif
    }
    const int savedErr = errno;
#ifdef _WIN32
    _close(fd);
#else
    ::close(fd);
#endif
    if (!ok) {
        fs::remove(tmp, ec);
        throw std::runtime_error("Failed writing " + target.u8string() + ": " + std::strerror(savedErr));
    }

#ifdef _WIN32
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
    while (!MoveFileExW(tmp.c_str(), target.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        const DWORD err = GetLastError();
        if ((err == ERROR_ACCESS_DENIED || err == ERROR_SHARING_VIOLATION) &&
            std::chrono::steady_clock::now() < deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
            continue;
        }
        fs::remove(tmp, ec);
        throw std::runtime_error("Cannot replace " + target.u8string() + " (Windows error " + std::to_string(err) +
                                 ").");
    }
#else
    if (::rename(tmp.c_str(), target.c_str()) != 0) {
        const int renameErr = errno;
        fs::remove(tmp, ec);
        throw std::runtime_error("Cannot replace " + target.u8string() + ": " + std::strerror(renameErr));
    }
#endif
}

json automationToJson(const Automation& a) {
    return json{
        {"id", a.id},
        {"name", a.name},
        {"kind", a.kind},
        {"prompt", a.prompt},
        {"say", a.say},
        {"every_seconds", a.everySeconds},
        {"next_at", a.nextAt},
        {"check", a.check},
        {"quiet", a.quiet},
        {"days", a.days},
        {"paused", a.paused},
        {"created_at", a.createdAt},
        {"last_run_at", a.lastRunAt},
        {"runs", a.runs},
        {"last_output", a.lastOutput},
        {"checked", a.checked},
        {"failures", a.failures},
        {"last_alert", a.lastAlert},
    };
}

Automation automationFromJson(const json& j, const fs::path& path) {
    if (!j.is_object() || !j.contains("id") || !j.contains("name") || !j.contains("kind") || !j["id"].is_string() ||
        !j["name"].is_string() || !j["kind"].is_string()) {
        throw std::runtime_error(path.u8string() + " is not usable: automation entry is missing id, name, or kind. "
                                                   "Delete it to clear your automations.");
    }
    Automation a;
    a.id = j["id"].get<std::string>();
    a.name = j["name"].get<std::string>();
    a.kind = j["kind"].get<std::string>();
    a.prompt = j.value("prompt", std::string());
    a.say = j.value("say", std::string());
    a.everySeconds = j.value("every_seconds", static_cast<std::int64_t>(0));
    a.nextAt = j.value("next_at", 0.0);
    a.check = j.value("check", std::string());
    a.quiet = j.value("quiet", std::string());
    a.days = j.value("days", std::string());
    a.paused = j.value("paused", false);
    a.createdAt = j.value("created_at", 0.0);
    a.lastRunAt = j.value("last_run_at", 0.0);
    a.runs = j.value("runs", static_cast<std::int64_t>(0));
    a.lastOutput = j.value("last_output", std::string());
    a.checked = j.value("checked", false);
    a.failures = j.value("failures", static_cast<std::int64_t>(0));
    a.lastAlert = j.value("last_alert", std::string());
    return a;
}

std::vector<Automation> loadUnlocked(const fs::path& path) {
    std::error_code ec;
    if (!fs::exists(path, ec)) return {};
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("Cannot read " + path.u8string() + ". Check its permissions.");
    }
    json root;
    try {
        in >> root;
    } catch (const std::exception& e) {
        throw std::runtime_error(path.u8string() + " is not valid JSON: " + e.what() +
                                 ". Delete it to clear your automations.");
    }
    if (!root.is_object()) {
        throw std::runtime_error(path.u8string() +
                                 " must hold a JSON object. Delete it to clear your automations.");
    }
    if (!root.contains("automations") || root["automations"].is_null()) return {};
    if (!root["automations"].is_array()) {
        throw std::runtime_error(path.u8string() +
                                 " is not usable: 'automations' must be a list. Delete it to clear your automations.");
    }
    std::vector<Automation> out;
    for (const auto& entry : root["automations"]) out.push_back(automationFromJson(entry, path));
    return out;
}

void saveUnlocked(const fs::path& path, const std::vector<Automation>& items) {
    json arr = json::array();
    for (const auto& item : items) arr.push_back(automationToJson(item));
    writeJsonAtomic(path, json{{"automations", arr}});
}

int findIndex(const std::vector<Automation>& items, const std::string& rawRef) {
    const std::string ref = trim(rawRef);
    if (ref.empty()) return -1;
    // Exact id wins over name, matching Python's store.find.
    for (size_t i = 0; i < items.size(); ++i) {
        if (items[i].id == ref) return static_cast<int>(i);
    }
    const std::string lowered = toLower(ref);
    for (size_t i = 0; i < items.size(); ++i) {
        if (toLower(items[i].name) == lowered) return static_cast<int>(i);
    }
    return -1;
}

std::invalid_argument missingError(const std::string& ref, const std::vector<Automation>& items) {
    std::string names;
    for (const auto& a : items) {
        if (!names.empty()) names += ", ";
        names += "'" + a.name + "'";
    }
    if (names.empty()) names = "none yet";
    return std::invalid_argument("No automation is called or numbered '" + ref +
                                 "'. Its exact name, or its id from the list, is needed. Names: " + names + ".");
}

std::optional<std::int64_t> parseDurationSeconds(const std::string& raw) {
    static const std::regex kDurationRe(
        R"(^(?:in\s+|\+)?((?:\s*\d+\s*(?:m|mins?|minutes?|h|hrs?|hours?|d|days?|w|wks?|weeks?)\s*(?:and\s*)?)+)$)",
        std::regex::icase);
    static const std::regex kPartRe(
        R"((\d+)\s*(m|mins?|minutes?|h|hrs?|hours?|d|days?|w|wks?|weeks?))", std::regex::icase);

    const std::string text = trim(raw);
    std::smatch m;
    if (!std::regex_match(text, m, kDurationRe)) return std::nullopt;
    const std::string body = m[1].str();

    std::int64_t total = 0;
    for (std::sregex_iterator it(body.begin(), body.end(), kPartRe), end; it != end; ++it) {
        const std::int64_t n = std::stoll((*it)[1].str());
        const char unit = static_cast<char>(std::tolower(static_cast<unsigned char>((*it)[2].str()[0])));
        std::int64_t mul = 60;
        if (unit == 'h') mul = 3600;
        else if (unit == 'd') mul = 86400;
        else if (unit == 'w') mul = 7 * 86400;
        if (n > AutomationStore::kMaxDelaySeconds / mul + 1) {
            return AutomationStore::kMaxDelaySeconds + 1;
        }
        total += n * mul;
    }
    return total;
}

bool tokenizeCommand(const std::string& command, std::vector<std::string>& out) {
    out.clear();
    std::string cur;
    bool inSingle = false;
    bool inDouble = false;
    bool tokenStarted = false;
    for (size_t i = 0; i < command.size(); ++i) {
        const char c = command[i];
        if (inSingle) {
            if (c == '\'') inSingle = false;
            else cur.push_back(c);
        } else if (inDouble) {
            if (c == '"') inDouble = false;
            else if (c == '\\' && i + 1 < command.size() && (command[i + 1] == '"' || command[i + 1] == '\\')) {
                cur.push_back(command[++i]);
            } else {
                cur.push_back(c);
            }
        } else if (c == '\'') {
            inSingle = true;
            tokenStarted = true;
        } else if (c == '"') {
            inDouble = true;
            tokenStarted = true;
        } else if (std::isspace(static_cast<unsigned char>(c))) {
            if (tokenStarted) {
                out.push_back(cur);
                cur.clear();
                tokenStarted = false;
            }
        } else {
            cur.push_back(c);
            tokenStarted = true;
        }
    }
    if (inSingle || inDouble) return false;
    if (tokenStarted) out.push_back(cur);
    return !out.empty();
}

std::string deliveryNote(bool paired) {
    if (paired) return " It arrives in your Discord DM.";
    return " Nothing delivers it yet: automations reach you through Discord, so connect it under "
           "Settings > Messaging in the GAIA app.";
}

std::string creationMessage(const Automation& item, double nowEpoch, bool paired) {
    const std::string quiet = item.quiet.empty() ? "" : ", quiet " + item.quiet;
    std::string msg;
    if (item.kind == kKindReminder) {
        msg = "Reminder '" + item.name + "' (id " + item.id + ") set for " +
              AutomationStore::formatShortWhen(item.nextAt, nowEpoch) + ".";
    } else if (item.kind == kKindTrigger) {
        msg = "Trigger '" + item.name + "' (id " + item.id + "): every " +
              AutomationStore::formatInterval(item.everySeconds) + quiet + " GAIA runs `" + item.check +
              "` and messages you only when its output changes.";
    } else if (item.kind == kKindAlert) {
        msg = "Alert '" + item.name + "' (id " + item.id + "): every " +
              AutomationStore::formatInterval(item.everySeconds) + quiet +
              " GAIA checks and messages you only when something needs your attention.";
    } else {
        msg = "Summary '" + item.name + "' (id " + item.id + "): every " +
              AutomationStore::formatInterval(item.everySeconds) + quiet + ", first at " +
              AutomationStore::formatShortWhen(item.nextAt, nowEpoch) + ".";
    }
    return msg + deliveryNote(paired);
}

}  // namespace

AutomationStore::AutomationStore(std::string path) : path_(std::move(path)) {}

std::string AutomationStore::defaultPath() {
    for (const char* var : {"GAIA_HOME", "GAIA_CONFIG_DIR"}) {
        const std::string raw = trim(getEnvVar(var));
        if (!raw.empty()) return (expandUserPath(raw) / "automations" / "automations.json").u8string();
    }
    const fs::path home = userHomeDir();
    if (home.empty()) {
        throw std::runtime_error("Can't locate automations store: neither GAIA_HOME nor HOME is set.");
    }
    return (home / ".gaia" / "automations" / "automations.json").u8string();
}

std::string AutomationStore::resolvePath() const { return path_.empty() ? defaultPath() : path_; }

std::int64_t AutomationStore::parseInterval(const std::string& text) {
    static const std::regex kEveryPrefix(R"(^every\s+)", std::regex::icase);
    const std::string stripped = std::regex_replace(trim(text), kEveryPrefix, "");
    const auto secs = parseDurationSeconds(stripped);
    if (!secs.has_value()) {
        throw std::invalid_argument("'" + text +
                                    "' is not an interval. Use a number and a unit, like 30m, 6h or 1d.");
    }
    if (*secs < kMinIntervalSeconds) {
        throw std::invalid_argument("The shortest interval is 5 minutes.");
    }
    return *secs;
}

double AutomationStore::parseWhen(const std::string& raw, double nowEpoch) {
    static const char* kExamples = "like 10m, in 2 hours, 17:30, 5pm, tomorrow 9am or 2026-12-01T09:00";
    const std::string text = trim(raw);
    if (text.empty()) {
        throw std::invalid_argument(std::string("'' is not a time GAIA can read; say it ") + kExamples + ".");
    }

    if (const auto secs = parseDurationSeconds(text); secs.has_value()) {
        if (*secs <= 0 || *secs > kMaxDelaySeconds) {
            throw std::invalid_argument("'" + text + "' is not within the next year; say it " + kExamples + ".");
        }
        return nowEpoch + static_cast<double>(*secs);
    }

    static const std::regex kClockRe(
        R"(^(?:at\s+)?(today|tomorrow)?\s*(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$)", std::regex::icase);
    std::smatch clock;
    if (std::regex_match(text, clock, kClockRe)) {
        const std::string day = toLower(clock[1].str());
        int hour = std::stoi(clock[2].str());
        const int minute = clock[3].matched ? std::stoi(clock[3].str()) : 0;
        const std::string ampm = toLower(clock[4].str());
        if (!ampm.empty()) {
            if (hour < 1 || hour > 12) {
                throw std::invalid_argument("'" + text + "' is not a time; say it " + kExamples + ".");
            }
            hour = (hour % 12) + (ampm == "pm" ? 12 : 0);
        }
        if (hour > 23 || minute > 59) {
            throw std::invalid_argument("'" + text + "' is not a time; say it " + kExamples + ".");
        }

        std::tm wall = toLocalTm(static_cast<std::time_t>(std::floor(nowEpoch)));
        wall.tm_hour = hour;
        wall.tm_min = minute;
        wall.tm_sec = 0;
        wall.tm_isdst = -1;
        if (day == "tomorrow") {
            wall.tm_mday += 1;
            const std::time_t target = std::mktime(&wall);
            return static_cast<double>(target);
        }
        std::time_t target = std::mktime(&wall);
        if (static_cast<double>(target) <= nowEpoch) {
            if (day == "today") {
                throw std::invalid_argument("'" + text + "' has already passed today.");
            }
            wall = toLocalTm(static_cast<std::time_t>(std::floor(nowEpoch)));
            wall.tm_mday += 1;
            wall.tm_hour = hour;
            wall.tm_min = minute;
            wall.tm_sec = 0;
            wall.tm_isdst = -1;
            target = std::mktime(&wall);
        }
        return static_cast<double>(target);
    }

    static const std::regex kIsoRe(
        R"(^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?$)",
        std::regex::icase);
    std::smatch iso;
    if (std::regex_match(text, iso, kIsoRe)) {
        const int year = std::stoi(iso[1].str());
        const int month = std::stoi(iso[2].str());
        const int day = std::stoi(iso[3].str());
        const int hour = std::stoi(iso[4].str());
        const int minute = std::stoi(iso[5].str());
        const int second = iso[6].matched ? std::stoi(iso[6].str()) : 0;
        if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59 || second > 59) {
            throw std::invalid_argument("'" + text + "' is not a time GAIA can read; say it " + kExamples + ".");
        }

        std::tm tm{};
        tm.tm_year = year - 1900;
        tm.tm_mon = month - 1;
        tm.tm_mday = day;
        tm.tm_hour = hour;
        tm.tm_min = minute;
        tm.tm_sec = second;

        double moment = 0.0;
        if (!iso[7].matched) {
            tm.tm_isdst = -1;
            std::tm check = tm;
            const std::time_t t = std::mktime(&check);
            if (t == static_cast<std::time_t>(-1) || check.tm_year != tm.tm_year || check.tm_mon != tm.tm_mon ||
                check.tm_mday != tm.tm_mday) {
                throw std::invalid_argument("'" + text + "' is not a time GAIA can read; say it " + kExamples + ".");
            }
            moment = static_cast<double>(t);
        } else {
            std::tm check = tm;
            const std::time_t utc = utcTmToTimeT(&check);
            if (utc == static_cast<std::time_t>(-1) || check.tm_year != tm.tm_year || check.tm_mon != tm.tm_mon ||
                check.tm_mday != tm.tm_mday) {
                throw std::invalid_argument("'" + text + "' is not a time GAIA can read; say it " + kExamples + ".");
            }
            const std::string tz = iso[7].str();
            int offsetSeconds = 0;
            if (tz != "Z" && tz != "z") {
                const int sign = tz[0] == '-' ? -1 : 1;
                const int tzH = std::stoi(tz.substr(1, 2));
                const int tzM = std::stoi(tz.substr(4, 2));
                if (tzH > 23 || tzM > 59) {
                    throw std::invalid_argument("'" + text + "' is not a time GAIA can read; say it " + kExamples +
                                                ".");
                }
                offsetSeconds = sign * (tzH * 3600 + tzM * 60);
            }
            moment = static_cast<double>(utc - offsetSeconds);
        }

        if (moment <= nowEpoch) {
            throw std::invalid_argument("'" + text + "' is in the past.");
        }
        if (moment - nowEpoch > static_cast<double>(kMaxDelaySeconds)) {
            throw std::invalid_argument("'" + text + "' is more than a year away.");
        }
        return moment;
    }

    throw std::invalid_argument("'" + text + "' is not a time GAIA can read; say it " + kExamples + ".");
}

std::string AutomationStore::parseQuiet(const std::string& raw) {
    static const std::regex kQuietRe(R"(^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$)");
    const std::string text = trim(raw);
    std::smatch m;
    if (!std::regex_match(text, m, kQuietRe)) {
        throw std::invalid_argument("'" + raw + "' is not quiet hours; write it like 22:00-07:00.");
    }
    const int h1 = std::stoi(m[1].str());
    const int m1 = std::stoi(m[2].str());
    const int h2 = std::stoi(m[3].str());
    const int m2 = std::stoi(m[4].str());
    if (h1 > 23 || h2 > 23 || m1 > 59 || m2 > 59) {
        throw std::invalid_argument("'" + raw + "' is not quiet hours; write it like 22:00-07:00.");
    }
    return text;
}

std::string AutomationStore::parseDays(const std::string& raw) {
    static const std::vector<std::string> kDayNames = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"};
    auto dayIndex = [&](const std::string& token) -> int {
        const std::string key = toLower(trim(token)).substr(0, 3);
        for (size_t i = 0; i < kDayNames.size(); ++i) {
            if (kDayNames[i] == key) return static_cast<int>(i);
        }
        return -1;
    };

    std::string words = toLower(trim(raw));
    if (words == "weekdays") words = "mon-fri";
    else if (words == "weekends") words = "sat,sun";
    else if (words == "daily") words = "mon-sun";

    std::set<int> days;
    std::stringstream ss(words);
    std::string part;
    while (std::getline(ss, part, ',')) {
        part = trim(part);
        if (part.empty()) continue;
        const size_t dash = part.find('-');
        if (dash == std::string::npos) {
            const int idx = dayIndex(part);
            if (idx < 0) {
                throw std::invalid_argument("'" + raw +
                                            "' is not a set of days; write it like mon-fri, weekends or mon,wed,fri.");
            }
            days.insert(idx);
        } else {
            if (part.find('-', dash + 1) != std::string::npos) {
                throw std::invalid_argument("'" + raw +
                                            "' is not a set of days; write it like mon-fri, weekends or mon,wed,fri.");
            }
            const int first = dayIndex(part.substr(0, dash));
            const int last = dayIndex(part.substr(dash + 1));
            if (first < 0 || last < 0) {
                throw std::invalid_argument("'" + raw +
                                            "' is not a set of days; write it like mon-fri, weekends or mon,wed,fri.");
            }
            if (first <= last) {
                for (int d = first; d <= last; ++d) days.insert(d);
            } else {
                for (int d = first; d < 7; ++d) days.insert(d);
                for (int d = 0; d <= last; ++d) days.insert(d);
            }
        }
    }
    if (days.empty()) {
        throw std::invalid_argument("'" + raw + "' names no days.");
    }
    std::string out;
    for (int d : days) {
        if (!out.empty()) out.push_back(',');
        out += kDayNames[static_cast<size_t>(d)];
    }
    return out;
}

void AutomationStore::validateCheckCommand(const std::string& raw) {
    static const std::string kRule =
        "A check runs on its own every few minutes, so it must be one GitHub read: "
        "`gaia automation ci-status --repo OWNER/REPO --branch main`, or a gh read naming the repo, "
        "like `gh pr view 12 --repo OWNER/REPO --json reviewDecision --jq .reviewDecision`.";

    const std::string command = trim(raw);
    if (command.empty()) throw std::invalid_argument("check is empty. " + kRule);

    if (command.find_first_of(";|&`$()><\n\r") != std::string::npos) {
        throw std::invalid_argument("`" + command + "` is not a single command. " + kRule);
    }

    std::vector<std::string> argv;
    if (!tokenizeCommand(command, argv) || argv.empty()) {
        throw std::invalid_argument("`" + command + "` is not a single command. " + kRule);
    }

    const std::string& bin = argv[0];
    if (bin == "gaia") {
        if (argv.size() < 3 || argv[1] != "automation" || argv[2] != "ci-status") {
            throw std::invalid_argument("`" + command + "` is not a read. " + kRule);
        }
        return;
    }

    if (bin != "gh") {
        throw std::invalid_argument("`" + command + "` is not a GitHub read. " + kRule);
    }
    if (argv.size() < 2) {
        throw std::invalid_argument("`" + command + "` is not a read. " + kRule);
    }
    for (const auto& arg : argv) {
        if (arg == "--paginate") {
            throw std::invalid_argument("`" + command + "` pages through everything. " + kRule);
        }
    }

    const std::string& sub = argv[1];
    bool readOk = false;
    bool namesRepo = false;
    if (sub == "api") {
        if (argv.size() >= 3 && argv[2].rfind("repos/", 0) == 0) {
            namesRepo = true;
            readOk = true;
            for (size_t i = 2; i < argv.size(); ++i) {
                const std::string& a = argv[i];
                if (a == "-X" || a == "--method") {
                    if (i + 1 >= argv.size() || toLower(argv[i + 1]) != "get") readOk = false;
                } else if (a.rfind("--method=", 0) == 0 && toLower(a.substr(9)) != "get") {
                    readOk = false;
                } else if (a == "-f" || a == "-F" || a == "--field" || a == "--raw-field" || a == "--input" ||
                           a.rfind("--field=", 0) == 0 || a.rfind("--raw-field=", 0) == 0 ||
                           a.rfind("--input=", 0) == 0) {
                    readOk = false;
                }
            }
        }
    } else if (sub == "search") {
        readOk = true;
    } else if (argv.size() >= 3) {
        const std::string& verb = argv[2];
        static const std::set<std::string> kReadGroups = {"pr", "issue", "run", "release", "workflow", "repo"};
        static const std::set<std::string> kReadVerbs = {"view", "list", "checks", "diff", "status"};
        readOk = kReadGroups.count(sub) > 0 && kReadVerbs.count(verb) > 0;
    }

    if (!readOk) {
        throw std::invalid_argument("`" + command + "` is not a read. " + kRule);
    }
    for (const auto& a : argv) {
        if (a == "-R" || a == "--repo" || a.rfind("--repo=", 0) == 0) namesRepo = true;
    }
    if (!namesRepo) {
        throw std::invalid_argument("`" + command +
                                    "` does not name the repository, so it would depend on the folder "
                                    "the bridge runs in. " +
                                    kRule);
    }
}

std::string AutomationStore::formatInterval(std::int64_t seconds) {
    struct Unit {
        const char* word;
        std::int64_t size;
    };
    static constexpr Unit kUnits[] = {{"week", 7 * 86400}, {"day", 86400}, {"hour", 3600}};
    for (const auto& u : kUnits) {
        if (seconds % u.size == 0) {
            const std::int64_t n = seconds / u.size;
            return std::to_string(n) + " " + u.word + (n != 1 ? "s" : "");
        }
    }
    const std::int64_t n = std::max<std::int64_t>(1, seconds / 60);
    return std::to_string(n) + " minute" + (n != 1 ? "s" : "");
}

std::string AutomationStore::formatLocalTime(double epoch) {
    const std::tm tm = toLocalTm(static_cast<std::time_t>(std::floor(epoch)));
    char buf[64];
    std::strftime(buf, sizeof(buf), "%a %d %b %H:%M", &tm);
    return buf;
}

std::string AutomationStore::formatShortWhen(double epoch, double nowEpoch) {
    const std::tm target = toLocalTm(static_cast<std::time_t>(std::floor(epoch)));
    const std::tm today = toLocalTm(static_cast<std::time_t>(std::floor(nowEpoch)));
    char hm[16];
    std::strftime(hm, sizeof(hm), "%H:%M", &target);
    if (target.tm_year == today.tm_year && target.tm_yday == today.tm_yday) return hm;
    char day[32];
    std::strftime(day, sizeof(day), "%a %d %b ", &target);
    return std::string(day) + hm;
}

std::string AutomationStore::confirmSummary(const std::string& tool, const json& args) {
    if (!args.is_object()) return "";
    if (tool == "create_automation") {
        const std::string name = trim(args.value("name", std::string()));
        const std::string at = trim(args.value("at", std::string()));
        const std::string every = trim(args.value("every", std::string()));
        const std::string say = trim(args.value("say", std::string()));
        const std::string check = trim(args.value("check", std::string()));
        const bool alert = args.value("alert", false);
        std::string kind = "automation";
        if (every.empty() && !at.empty()) kind = "reminder";
        else if (!check.empty()) kind = "trigger";
        else if (alert) kind = "alert";
        else if (!every.empty()) kind = "summary";

        std::string out = "Create " + kind;
        if (!name.empty()) out += " \"" + name + "\"";
        if (!every.empty() && !at.empty()) out += " (every " + every + ", at " + at + ")";
        else if (!every.empty()) out += " (every " + every + ")";
        else if (!at.empty()) out += " (at " + at + ")";
        if (!say.empty()) out += ": " + say;
        return out;
    }
    if (tool == "edit_automation") {
        const std::string ref = trim(args.value("ref", std::string()));
        return ref.empty() ? "Edit automation" : "Edit automation \"" + ref + "\"";
    }
    if (tool == "pause_automation") {
        const std::string ref = trim(args.value("ref", std::string()));
        return ref.empty() ? "Pause automation" : "Pause automation \"" + ref + "\"";
    }
    if (tool == "resume_automation") {
        const std::string ref = trim(args.value("ref", std::string()));
        return ref.empty() ? "Resume automation" : "Resume automation \"" + ref + "\"";
    }
    if (tool == "remove_automation") {
        const std::string ref = trim(args.value("ref", std::string()));
        return ref.empty() ? "Remove automation" : "Remove automation \"" + ref + "\"";
    }
    return "";
}

bool AutomationStore::discordPaired(const std::string& storePath) const {
    const fs::path bridge = fs::u8path(storePath).parent_path().parent_path() / "discord" / "bridge.json";
    std::error_code ec;
    if (!fs::is_regular_file(bridge, ec)) return false;
    std::ifstream in(bridge, std::ios::binary);
    if (!in) return false;
    const json j = json::parse(in, nullptr, false);
    if (!j.is_object() || !j.contains("owner_id") || !j["owner_id"].is_string()) return false;
    return !trim(j["owner_id"].get<std::string>()).empty();
}

void AutomationStore::dropWaitingFor(const std::string& storePath, const std::string& automationId) {
    if (automationId.empty()) return;
    const fs::path outboxPath = fs::u8path(storePath).parent_path() / "outbox.json";
    std::error_code ec;
    if (!fs::exists(outboxPath, ec)) return;

    AdvisoryFileLock lock(outboxPath);
    if (!fs::exists(outboxPath, ec)) return;
    std::ifstream in(outboxPath, std::ios::binary);
    if (!in) {
        throw std::runtime_error("Cannot read " + outboxPath.u8string() + ". Check its permissions.");
    }
    json root;
    try {
        in >> root;
    } catch (const std::exception& e) {
        throw std::runtime_error(outboxPath.u8string() + " is not valid JSON: " + e.what() +
                                 ". Delete it to drop waiting automation deliveries.");
    }
    if (!root.is_object() || !root.contains("deliveries") || !root["deliveries"].is_array()) {
        throw std::runtime_error(outboxPath.u8string() +
                                 " is not usable: expected a JSON object with a 'deliveries' list.");
    }

    json kept = json::array();
    bool dropped = false;
    for (const auto& entry : root["deliveries"]) {
        if (entry.is_object() && entry.value("automation_id", std::string()) == automationId) {
            dropped = true;
            continue;
        }
        kept.push_back(entry);
    }
    if (dropped) {
        writeJsonAtomic(outboxPath, json{{"deliveries", kept}});
    }
}

std::vector<Automation> AutomationStore::list() const {
    const fs::path target = fs::u8path(resolvePath());
    AdvisoryFileLock lock(target);
    return loadUnlocked(target);
}

Automation AutomationStore::create(const CreateRequest& req, double nowEpoch) const {
    const double now = nowEpoch > 0.0 ? nowEpoch : nowSeconds();
    const std::string name = trim(req.name);
    const std::string prompt = trim(req.prompt);
    const std::string say = trim(req.say);
    const std::string every = trim(req.every);
    const std::string at = trim(req.at);
    const std::string check = trim(req.check);
    const std::string quiet = trim(req.quiet);
    const std::string days = trim(req.days);

    if (name.empty()) {
        throw std::invalid_argument("name is empty; give it a short name.");
    }
    if (every.empty() && at.empty()) {
        throw std::invalid_argument(
            "Say when: pass 'at' for once (10m, 17:30, 5pm, tomorrow 9am), 'every' to repeat, "
            "or both for a repeat that starts at a set time (every='1d', at='8am').");
    }
    if (!quiet.empty()) parseQuiet(quiet);

    Automation item;
    item.name = name;
    item.createdAt = now;
    if (!at.empty() && every.empty()) {
        if (!check.empty() || req.alert) {
            throw std::invalid_argument("A trigger or alert repeats; pass 'every'.");
        }
        if (prompt.empty() && say.empty()) {
            throw std::invalid_argument("A reminder needs 'say' or 'prompt'.");
        }
        if (!days.empty()) {
            throw std::invalid_argument("'days' is for a repeating automation.");
        }
        item.kind = kKindReminder;
        item.prompt = prompt;
        item.say = say;
        item.everySeconds = 0;
        item.nextAt = parseWhen(at, now);
    } else {
        const std::int64_t seconds = parseInterval(every);
        if (!say.empty()) {
            throw std::invalid_argument("'say' is for a one-time reminder (pass 'at' without 'every').");
        }
        if (prompt.empty()) {
            throw std::invalid_argument("prompt is empty; say what GAIA should do.");
        }
        if (!check.empty() && req.alert) {
            throw std::invalid_argument("Use 'check' or 'alert', not both.");
        }
        if (!check.empty()) {
            validateCheckCommand(check);
            item.kind = kKindTrigger;
        } else {
            item.kind = req.alert ? kKindAlert : kKindSummary;
        }
        item.prompt = prompt;
        item.everySeconds = seconds;
        item.check = check;
        item.quiet = quiet;
        if (!days.empty()) item.days = parseDays(days);
        if (!at.empty()) {
            item.nextAt = parseWhen(at, now);
        } else {
            item.nextAt = now + static_cast<double>(item.kind == kKindAlert ? 60 : seconds);
        }
    }

    const fs::path target = fs::u8path(resolvePath());
    AdvisoryFileLock lock(target);
    auto items = loadUnlocked(target);
    if (static_cast<int>(items.size()) >= kMaxAutomations) {
        throw std::invalid_argument("You already have " + std::to_string(kMaxAutomations) +
                                    " automations. List them and remove one first.");
    }
    const std::string lowered = toLower(item.name);
    for (const auto& existing : items) {
        if (toLower(existing.name) == lowered) {
            throw std::invalid_argument("An automation is already called '" + item.name +
                                        "'. Pick another name, or edit that one.");
        }
    }
    item.id = randomHex(8);
    items.push_back(item);
    saveUnlocked(target, items);
    return item;
}

Automation AutomationStore::edit(const EditRequest& req, double nowEpoch) const {
    const double now = nowEpoch > 0.0 ? nowEpoch : nowSeconds();
    const fs::path target = fs::u8path(resolvePath());
    AdvisoryFileLock lock(target);
    auto items = loadUnlocked(target);
    const int idx = findIndex(items, req.ref);
    if (idx < 0) throw missingError(req.ref, items);

    Automation updated = items[static_cast<size_t>(idx)];
    bool changed = false;

    if (req.name.has_value()) {
        const std::string newName = trim(*req.name);
        if (newName.empty()) throw std::invalid_argument("name is empty; give the new name.");
        const std::string lowered = toLower(newName);
        for (size_t i = 0; i < items.size(); ++i) {
            if (static_cast<int>(i) != idx && toLower(items[i].name) == lowered) {
                throw std::invalid_argument("An automation is already called '" + newName + "'.");
            }
        }
        updated.name = newName;
        changed = true;
    }
    if (req.prompt.has_value()) {
        const std::string p = trim(*req.prompt);
        if (p.empty() && updated.say.empty()) {
            throw std::invalid_argument("prompt is empty; say what GAIA should do.");
        }
        updated.prompt = p;
        changed = true;
    }
    if (req.say.has_value()) {
        const std::string s = trim(*req.say);
        if (updated.repeats() || (s.empty() && updated.prompt.empty())) {
            throw std::invalid_argument("'say' is a one-time reminder's text.");
        }
        updated.say = s;
        changed = true;
    }
    if (req.every.has_value() && !trim(*req.every).empty()) {
        if (!updated.repeats()) {
            throw std::invalid_argument("A one-time reminder takes 'at', not 'every'.");
        }
        updated.everySeconds = parseInterval(*req.every);
        if (updated.nextAt <= now) updated.nextAt = now + static_cast<double>(updated.everySeconds);
        changed = true;
    }
    if (req.at.has_value() && !trim(*req.at).empty()) {
        updated.nextAt = parseWhen(*req.at, now);
        changed = true;
    }
    if (req.check.has_value()) {
        if (updated.kind != kKindTrigger) {
            throw std::invalid_argument("Only a trigger has a 'check'.");
        }
        const std::string c = trim(*req.check);
        validateCheckCommand(c);
        updated.check = c;
        updated.lastOutput.clear();
        updated.checked = false;
        updated.failures = 0;
        changed = true;
    }
    if (req.quiet.has_value()) {
        const std::string q = trim(*req.quiet);
        if (!q.empty()) parseQuiet(q);
        updated.quiet = q;
        changed = true;
    }
    if (req.days.has_value()) {
        if (!updated.repeats()) {
            throw std::invalid_argument("'days' is for a repeating automation.");
        }
        const std::string d = trim(*req.days);
        updated.days = d.empty() ? "" : parseDays(d);
        changed = true;
    }
    if (!changed) {
        throw std::invalid_argument("Nothing to change; pass at least one field to update.");
    }

    items[static_cast<size_t>(idx)] = updated;
    saveUnlocked(target, items);
    return updated;
}

Automation AutomationStore::setPaused(const std::string& ref, bool paused) const {
    const std::string storePath = resolvePath();
    const fs::path target = fs::u8path(storePath);
    Automation updated;
    {
        AdvisoryFileLock lock(target);
        auto items = loadUnlocked(target);
        const int idx = findIndex(items, ref);
        if (idx < 0) throw missingError(ref, items);
        items[static_cast<size_t>(idx)].paused = paused;
        updated = items[static_cast<size_t>(idx)];
        saveUnlocked(target, items);
    }
    if (paused) dropWaitingFor(storePath, updated.id);
    return updated;
}

Automation AutomationStore::remove(const std::string& ref) const {
    const std::string storePath = resolvePath();
    const fs::path target = fs::u8path(storePath);
    Automation removed;
    {
        AdvisoryFileLock lock(target);
        auto items = loadUnlocked(target);
        const int idx = findIndex(items, ref);
        if (idx < 0) throw missingError(ref, items);
        removed = items[static_cast<size_t>(idx)];
        items.erase(items.begin() + idx);
        saveUnlocked(target, items);
    }
    dropWaitingFor(storePath, removed.id);
    return removed;
}

json AutomationStore::describe(const Automation& item) {
    json out = {
        {"id", item.id},
        {"name", item.name},
        {"kind", item.kind},
        {"status", item.paused ? "paused" : "active"},
        {"next", formatLocalTime(item.nextAt)},
    };
    if (item.repeats()) out["every"] = formatInterval(item.everySeconds);
    if (!item.quiet.empty()) out["quiet_hours"] = item.quiet;
    if (!item.days.empty()) out["days"] = item.days;
    if (!item.say.empty()) out["says"] = item.say;
    if (!item.prompt.empty()) out["prompt"] = item.prompt;
    if (!item.check.empty()) out["check"] = item.check;
    if (item.failures > 0) out["failing_checks"] = item.failures;
    return out;
}

void AutomationStore::registerTools(ToolRegistry& registry) {
    ToolInfo listTool;
    listTool.name = "list_automations";
    listTool.description =
        "List all scheduled reminders, summaries, alerts and triggers currently configured in GAIA.";
    listTool.policy = ToolPolicy::ALLOW;
    listTool.callback = [this](const json&) -> json {
        try {
            const auto items = this->list();
            json arr = json::array();
            for (const auto& item : items) arr.push_back(describe(item));
            return {{"status", "success"}, {"automations", arr}, {"count", arr.size()}};
        } catch (const std::exception& e) {
            return {{"status", "error"}, {"error", e.what()}};
        }
    };
    registry.registerTool(std::move(listTool));

    ToolInfo createTool;
    createTool.name = "create_automation";
    createTool.description =
        "Create a scheduled reminder, summary, alert, or trigger that GAIA runs on its own and shows in "
        "Settings > Automations (delivered via Discord). For a one-time reminder (e.g. 'remind me at 5pm to call Sam'), "
        "pass 'name', 'at' (like '10m', '5pm', '17:30', 'tomorrow 9am', or ISO datetime), and 'say' (exact message) or "
        "'prompt'. For repeating automations, pass 'every' (min '5m', e.g. '30m', '6h', '1d') and 'prompt', plus "
        "optional 'at' (time of day), 'alert'=true (stay silent unless something needs attention), 'check' (read-only "
        "GitHub/CI command for triggers), 'quiet' ('22:00-07:00'), and 'days' ('mon-fri', 'weekends').";
    createTool.parameters = {
        {"name", ToolParamType::STRING, true, "Short name the user recognizes, e.g. 'Call Sam' or 'Morning inbox'."},
        {"at", ToolParamType::STRING, false,
         "When to fire once (or the time of day for a repeating run), e.g. '10m', '5pm', '17:30', 'tomorrow 9am'."},
        {"every", ToolParamType::STRING, false,
         "Repeat interval (minimum '5m'), e.g. '30m', '6h', '1d'. Omit for a one-time reminder."},
        {"say", ToolParamType::STRING, false,
         "Exact reminder text sent without the model (one-time reminders only)."},
        {"prompt", ToolParamType::STRING, false,
         "Self-contained instructions GAIA executes when the automation runs."},
        {"alert", ToolParamType::BOOLEAN, false,
         "true for an alert that speaks only when something needs attention (requires 'every' and 'prompt')."},
        {"check", ToolParamType::STRING, false,
         "Read-only GitHub/CI command for a trigger that wakes GAIA only when its output changes."},
        {"quiet", ToolParamType::STRING, false, "Quiet hours window like '22:00-07:00' when repeating runs wait."},
        {"days", ToolParamType::STRING, false,
         "Days a repeating automation runs on, e.g. 'mon-fri', 'weekdays', 'weekends', 'mon,wed,fri'."},
    };
    createTool.policy = ToolPolicy::CONFIRM;
    createTool.callback = [this](const json& a) -> json {
        try {
            CreateRequest req;
            req.name = a.value("name", std::string());
            req.at = a.value("at", std::string());
            req.every = a.value("every", std::string());
            req.say = a.value("say", std::string());
            req.prompt = a.value("prompt", std::string());
            req.alert = a.value("alert", false);
            req.check = a.value("check", std::string());
            req.quiet = a.value("quiet", std::string());
            req.days = a.value("days", std::string());
            const double now = nowSeconds();
            const Automation created = this->create(req, now);
            const bool paired = this->discordPaired(this->resolvePath());
            return {
                {"status", "success"},
                {"automation", describe(created)},
                {"message", creationMessage(created, now, paired)},
                {"delivers", paired},
            };
        } catch (const std::exception& e) {
            return {{"status", "error"}, {"error", e.what()}};
        }
    };
    registry.registerTool(std::move(createTool));

    ToolInfo editTool;
    editTool.name = "edit_automation";
    editTool.description =
        "Edit an existing automation or reminder by its id or exact name. Pass only the fields to change.";
    editTool.parameters = {
        {"ref", ToolParamType::STRING, true, "The automation's id or exact name."},
        {"name", ToolParamType::STRING, false, "New name."},
        {"at", ToolParamType::STRING, false, "New fire time (or next time of day for a repeating automation)."},
        {"every", ToolParamType::STRING, false, "New repeat interval (repeating automations only, min '5m')."},
        {"say", ToolParamType::STRING, false, "New exact reminder text (one-time reminders only)."},
        {"prompt", ToolParamType::STRING, false, "New self-contained prompt."},
        {"check", ToolParamType::STRING, false, "New read-only check command (triggers only)."},
        {"quiet", ToolParamType::STRING, false, "New quiet hours like '22:00-07:00', or '' to clear."},
        {"days", ToolParamType::STRING, false, "New days like 'mon-fri', or '' to clear (repeating only)."},
    };
    editTool.policy = ToolPolicy::CONFIRM;
    editTool.callback = [this](const json& a) -> json {
        try {
            EditRequest req;
            req.ref = a.value("ref", std::string());
            auto optStr = [&](const char* key) -> std::optional<std::string> {
                if (a.contains(key) && a[key].is_string()) return a[key].get<std::string>();
                return std::nullopt;
            };
            req.name = optStr("name");
            req.at = optStr("at");
            req.every = optStr("every");
            req.say = optStr("say");
            req.prompt = optStr("prompt");
            req.check = optStr("check");
            req.quiet = optStr("quiet");
            req.days = optStr("days");
            const Automation updated = this->edit(req);
            return {
                {"status", "success"},
                {"automation", describe(updated)},
                {"message", "Updated '" + updated.name + "'."},
            };
        } catch (const std::exception& e) {
            return {{"status", "error"}, {"error", e.what()}};
        }
    };
    registry.registerTool(std::move(editTool));

    ToolInfo pauseTool;
    pauseTool.name = "pause_automation";
    pauseTool.description = "Pause an active automation or reminder by its id or exact name.";
    pauseTool.parameters = {{"ref", ToolParamType::STRING, true, "The automation's id or exact name."}};
    pauseTool.policy = ToolPolicy::CONFIRM;
    pauseTool.callback = [this](const json& a) -> json {
        try {
            const Automation updated = this->setPaused(a.value("ref", std::string()), true);
            return {
                {"status", "success"},
                {"automation", describe(updated)},
                {"message", "Paused '" + updated.name + "'."},
            };
        } catch (const std::exception& e) {
            return {{"status", "error"}, {"error", e.what()}};
        }
    };
    registry.registerTool(std::move(pauseTool));

    ToolInfo resumeTool;
    resumeTool.name = "resume_automation";
    resumeTool.description = "Resume a paused automation or reminder by its id or exact name.";
    resumeTool.parameters = {{"ref", ToolParamType::STRING, true, "The automation's id or exact name."}};
    resumeTool.policy = ToolPolicy::CONFIRM;
    resumeTool.callback = [this](const json& a) -> json {
        try {
            const Automation updated = this->setPaused(a.value("ref", std::string()), false);
            return {
                {"status", "success"},
                {"automation", describe(updated)},
                {"message", "Resumed '" + updated.name + "'."},
            };
        } catch (const std::exception& e) {
            return {{"status", "error"}, {"error", e.what()}};
        }
    };
    registry.registerTool(std::move(resumeTool));

    ToolInfo removeTool;
    removeTool.name = "remove_automation";
    removeTool.description = "Delete an automation or reminder permanently by its id or exact name.";
    removeTool.parameters = {{"ref", ToolParamType::STRING, true, "The automation's id or exact name."}};
    removeTool.policy = ToolPolicy::CONFIRM;
    removeTool.callback = [this](const json& a) -> json {
        try {
            const Automation removed = this->remove(a.value("ref", std::string()));
            return {
                {"status", "success"},
                {"automation", describe(removed)},
                {"message", "Removed '" + removed.name + "'."},
            };
        } catch (const std::exception& e) {
            return {{"status", "error"}, {"error", e.what()}};
        }
    };
    registry.registerTool(std::move(removeTool));
}

}  // namespace gaia
