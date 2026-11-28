// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Ported from src/gaia/agents/tools/_calendar/model.py. Python reads named
// zones through zoneinfo; here a POSIX system's TZif files are read directly
// and Windows asks the system for its own zone rules.

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <map>
#include <mutex>

#include "calendar_internal.h"
#include "gaia/calendar_tools.h"
#include "gaia/types.h"
#include "mail_text.h"

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace fs = std::filesystem;

namespace gaia::calendar {

namespace text = mail::text;

namespace {

const std::map<std::string, std::string>& windowsZones() {
    static const std::map<std::string, std::string> table{
#include "calendar_win_zones.inc"
    };
    return table;
}

int64_t floorDiv(int64_t a, int64_t b) { return a / b - ((a % b != 0) && ((a < 0) != (b < 0))); }

Date civilFromDays(int64_t z) {
    z += 719468;
    const int64_t era = floorDiv(z, 146097);
    const int64_t doe = z - era * 146097;
    const int64_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    const int64_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    const int64_t mp = (5 * doy + 2) / 153;
    const int d = static_cast<int>(doy - (153 * mp + 2) / 5 + 1);
    const int m = static_cast<int>(mp < 10 ? mp + 3 : mp - 9);
    return {static_cast<int>(yoe + era * 400 + (m <= 2)), m, d};
}

bool isLeap(int y) { return (y % 4 == 0 && y % 100 != 0) || y % 400 == 0; }

int daysInMonth(int y, int m) {
    static const int kDays[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    return m == 2 && isLeap(y) ? 29 : kDays[m - 1];
}

std::string pad(int64_t value, int width) {
    std::string s = std::to_string(value < 0 ? -value : value);
    while (static_cast<int>(s.size()) < width) s = "0" + s;
    return s;
}

std::string offsetText(int offset) {
    const char sign = offset < 0 ? '-' : '+';
    const int a = offset < 0 ? -offset : offset;
    std::string out = std::string(1, sign) + pad(a / 3600, 2) + ":" + pad(a / 60 % 60, 2);
    if (a % 60) out += ":" + pad(a % 60, 2);
    return out;
}

std::string repr(const std::string& s) { return text::pyRepr(json(s)); }

// ---- zones ----

class FixedZone : public Zone {
public:
    explicit FixedZone(int offset) : offset_(offset) {}
    int offsetAt(int64_t) const override { return offset_; }

private:
    int offset_;
};

std::shared_ptr<const Zone> zoneByName(const std::string& name);

class SystemZone : public Zone {
public:
    int offsetAt(int64_t utc) const override {
        const std::time_t t = static_cast<std::time_t>(utc);
        std::tm tm{};
#ifdef _WIN32
        // The CRT reads TZ only in its "EST5EDT" form, so an IANA name like America/New_York needs the zone table.
        std::string tz = getEnvVar("TZ");
        if (!tz.empty() && tz[0] == ':') tz = tz.substr(1);
        if (!tz.empty()) {
            std::shared_ptr<const Zone> named;
            {
                std::lock_guard<std::mutex> lock(mu_);
                if (tz != cachedName_) {
                    cachedName_ = tz;
                    cachedZone_ = zoneByName(tz);
                }
                named = cachedZone_;
            }
            if (named) return named->offsetAt(utc);
        }
        if (_localtime64_s(&tm, &t) != 0) throw CalendarError("This computer's clock can't read that date.");
        return static_cast<int>(_mkgmtime64(&tm) - t);
#else
        if (!localtime_r(&t, &tm)) throw CalendarError("This computer's clock can't read that date.");
        return static_cast<int>(tm.tm_gmtoff);
#endif
    }

#ifdef _WIN32
private:
    mutable std::mutex mu_;
    mutable std::string cachedName_;
    mutable std::shared_ptr<const Zone> cachedZone_;
#endif
};

// A POSIX TZ rule, as a TZif footer carries it: "EST5EDT,M3.2.0,M11.1.0".
struct PosixRule {
    int stdOffset = 0;
    int dstOffset = 0;
    bool hasDst = false;
    struct When {
        char kind = 'M';  // 'J' (1-365, no Feb 29), 'D' (0-365), 'M' (month.week.day)
        int a = 0, b = 0, c = 0;
        int time = 7200;
    } start, end;

    static bool parse(const std::string& s, PosixRule& out) {
        size_t i = 0;
        auto name = [&]() {
            if (i < s.size() && s[i] == '<') {
                const size_t close = s.find('>', i);
                if (close == std::string::npos) return false;
                i = close + 1;
                return true;
            }
            const size_t from = i;
            while (i < s.size() && std::isalpha(static_cast<unsigned char>(s[i]))) ++i;
            return i - from >= 3;
        };
        auto number = [&](int& v) {
            if (i >= s.size() || !std::isdigit(static_cast<unsigned char>(s[i]))) return false;
            v = 0;
            while (i < s.size() && std::isdigit(static_cast<unsigned char>(s[i]))) v = v * 10 + (s[i++] - '0');
            return true;
        };
        auto hms = [&](int& seconds) {
            int sign = 1;
            if (i < s.size() && (s[i] == '+' || s[i] == '-')) sign = s[i++] == '-' ? -1 : 1;
            int h = 0, m = 0, sec = 0;
            if (!number(h)) return false;
            if (i < s.size() && s[i] == ':') {
                ++i;
                if (!number(m)) return false;
                if (i < s.size() && s[i] == ':') {
                    ++i;
                    if (!number(sec)) return false;
                }
            }
            seconds = sign * (h * 3600 + m * 60 + sec);
            return true;
        };
        auto when = [&](When& w) {
            if (i < s.size() && s[i] == 'M') {
                ++i;
                w.kind = 'M';
                if (!number(w.a) || i >= s.size() || s[i++] != '.' || !number(w.b) || i >= s.size() ||
                    s[i++] != '.' || !number(w.c)) {
                    return false;
                }
            } else if (i < s.size() && s[i] == 'J') {
                ++i;
                w.kind = 'J';
                if (!number(w.a)) return false;
            } else {
                w.kind = 'D';
                if (!number(w.a)) return false;
            }
            w.time = 7200;
            if (i < s.size() && s[i] == '/') {
                ++i;
                if (!hms(w.time)) return false;
            }
            return true;
        };
        int west = 0;
        if (!name() || !hms(west)) return false;
        out.stdOffset = -west;
        if (i == s.size()) return true;
        if (!name()) return false;
        out.hasDst = true;
        out.dstOffset = out.stdOffset + 3600;
        if (i < s.size() && s[i] != ',') {
            if (!hms(west)) return false;
            out.dstOffset = -west;
        }
        if (i == s.size()) {
            out.start = {'M', 3, 2, 0, 7200};
            out.end = {'M', 11, 1, 0, 7200};
            return true;
        }
        if (s[i++] != ',' || !when(out.start) || i >= s.size() || s[i++] != ',' || !when(out.end)) return false;
        return i == s.size();
    }

    // Seconds from the year's start, local time, of `w` in year `y`.
    static int64_t dayOf(const When& w, int y) {
        if (w.kind == 'J') return w.a - 1 + (isLeap(y) && w.a >= 60 ? 1 : 0);
        if (w.kind == 'D') return w.a;
        const int64_t first = daysFromCivil({y, w.a, 1});
        const int firstWeekday = static_cast<int>(((first % 7) + 11) % 7);  // 0 = Sunday
        int day = 1 + (w.c - firstWeekday + 7) % 7 + (w.b - 1) * 7;
        while (day > daysInMonth(y, w.a)) day -= 7;
        return daysFromCivil({y, w.a, day}) - daysFromCivil({y, 1, 1});
    }

    int offsetAt(int64_t utc) const {
        if (!hasDst) return stdOffset;
        const int y = civilFromDays(floorDiv(utc + stdOffset, 86400)).year;
        const int64_t yearStart = daysFromCivil({y, 1, 1}) * 86400;
        const int64_t begins = yearStart + dayOf(start, y) * 86400 + start.time - stdOffset;
        const int64_t ends = yearStart + dayOf(end, y) * 86400 + end.time - dstOffset;
        const bool dst = begins < ends ? (utc >= begins && utc < ends) : (utc < ends || utc >= begins);
        return dst ? dstOffset : stdOffset;
    }
};

class TzifZone : public Zone {
public:
    std::vector<int64_t> transitions;
    std::vector<int> transitionType;
    std::vector<int> offsets;
    int beforeFirst = 0;
    std::optional<PosixRule> rule;

    int offsetAt(int64_t utc) const override {
        if (transitions.empty() || utc < transitions.front()) {
            if (transitions.empty() && rule) return rule->offsetAt(utc);
            return beforeFirst;
        }
        if (utc >= transitions.back() && rule) return rule->offsetAt(utc);
        const auto it = std::upper_bound(transitions.begin(), transitions.end(), utc);
        return offsets[static_cast<size_t>(transitionType[static_cast<size_t>(it - transitions.begin() - 1)])];
    }

    static std::shared_ptr<TzifZone> read(const fs::path& path) {
        std::ifstream in(path, std::ios::binary);
        if (!in) return nullptr;
        const std::string data((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
        auto be = [&data](size_t at, int bytes) -> int64_t {
            uint64_t v = 0;
            for (int k = 0; k < bytes; ++k) v = (v << 8) | static_cast<unsigned char>(data[at + static_cast<size_t>(k)]);
            if (bytes == 4) return static_cast<int32_t>(static_cast<uint32_t>(v));
            return static_cast<int64_t>(v);
        };
        if (data.size() < 44 || data.compare(0, 4, "TZif") != 0) return nullptr;
        struct Header {
            int64_t isut, isstd, leap, time, type, chars;
        };
        auto header = [&](size_t at) {
            return Header{be(at + 20, 4), be(at + 24, 4), be(at + 28, 4), be(at + 32, 4), be(at + 36, 4),
                          be(at + 40, 4)};
        };
        Header h = header(0);
        size_t at = 44;
        int timeBytes = 4;
        auto blockSize = [](const Header& hd, int tb) {
            return static_cast<size_t>(hd.time * tb + hd.time + hd.type * 6 + hd.chars + hd.leap * (tb + 4) +
                                       hd.isstd + hd.isut);
        };
        if (data[4] >= '2') {
            at += blockSize(h, 4);
            if (data.size() < at + 44 || data.compare(at, 4, "TZif") != 0) return nullptr;
            h = header(at);
            at += 44;
            timeBytes = 8;
        }
        if (data.size() < at + blockSize(h, timeBytes)) return nullptr;
        auto zone = std::make_shared<TzifZone>();
        for (int64_t k = 0; k < h.time; ++k) zone->transitions.push_back(be(at + static_cast<size_t>(k * timeBytes), timeBytes));
        at += static_cast<size_t>(h.time * timeBytes);
        for (int64_t k = 0; k < h.time; ++k) zone->transitionType.push_back(static_cast<unsigned char>(data[at + static_cast<size_t>(k)]));
        at += static_cast<size_t>(h.time);
        std::vector<bool> isDst;
        for (int64_t k = 0; k < h.type; ++k) {
            zone->offsets.push_back(static_cast<int>(be(at + static_cast<size_t>(k * 6), 4)));
            isDst.push_back(data[at + static_cast<size_t>(k * 6 + 4)] != 0);
        }
        if (zone->offsets.empty()) return nullptr;
        for (const int t : zone->transitionType) {
            if (t < 0 || t >= static_cast<int>(zone->offsets.size())) return nullptr;
        }
        zone->beforeFirst = zone->offsets[0];
        for (size_t k = 0; k < isDst.size(); ++k) {
            if (!isDst[k]) {
                zone->beforeFirst = zone->offsets[k];
                break;
            }
        }
        at += static_cast<size_t>(h.type * 6) + static_cast<size_t>(h.chars) +
              static_cast<size_t>(h.leap * (timeBytes + 4)) + static_cast<size_t>(h.isstd + h.isut);
        if (timeBytes == 8 && at < data.size() && data[at] == '\n') {
            const size_t close = data.find('\n', at + 1);
            if (close != std::string::npos && close > at + 1) {
                PosixRule r;
                if (PosixRule::parse(data.substr(at + 1, close - at - 1), r)) zone->rule = r;
            }
        }
        return zone;
    }
};

#ifdef _WIN32
class WindowsZone : public Zone {
public:
    explicit WindowsZone(const DYNAMIC_TIME_ZONE_INFORMATION& info) : info_(info) {}
    int offsetAt(int64_t utc) const override {
        const int64_t ticks = (utc + 11644473600LL) * 10000000LL;
        FILETIME ft{static_cast<DWORD>(ticks & 0xFFFFFFFF), static_cast<DWORD>(ticks >> 32)};
        SYSTEMTIME st{}, local{};
        if (!FileTimeToSystemTime(&ft, &st) || !SystemTimeToTzSpecificLocalTimeEx(&info_, &st, &local)) {
            throw CalendarError("Windows couldn't convert a time to the event's time zone.");
        }
        FILETIME lft{};
        SystemTimeToFileTime(&local, &lft);
        const int64_t lticks = (static_cast<int64_t>(lft.dwHighDateTime) << 32) | lft.dwLowDateTime;
        return static_cast<int>(lticks / 10000000LL - 11644473600LL - utc);
    }

private:
    DYNAMIC_TIME_ZONE_INFORMATION info_;
};

std::string narrow(const wchar_t* w) {
    std::string out;
    for (; *w; ++w) out += static_cast<char>(*w < 128 ? *w : '?');
    return out;
}

std::shared_ptr<const Zone> windowsZoneByKey(const std::string& key) {
    DYNAMIC_TIME_ZONE_INFORMATION info{};
    for (DWORD i = 0; EnumDynamicTimeZoneInformation(i, &info) == ERROR_SUCCESS; ++i) {
        if (narrow(info.TimeZoneKeyName) == key) return std::make_shared<WindowsZone>(info);
    }
    return nullptr;
}
#endif

bool safeZoneName(const std::string& name) {
    if (name.empty() || name.front() == '/' || name.front() == '\\' || name.find("..") != std::string::npos) {
        return false;
    }
    return name.find('\0') == std::string::npos;
}

std::vector<fs::path> zoneDirs() {
    std::vector<fs::path> dirs;
    const std::string env = getEnvVar("TZDIR");
    if (!env.empty()) dirs.emplace_back(env);
    for (const char* d : {"/usr/share/zoneinfo", "/usr/lib/zoneinfo", "/usr/share/lib/zoneinfo", "/etc/zoneinfo"}) {
        dirs.emplace_back(d);
    }
    return dirs;
}

std::shared_ptr<const Zone> zoneFile(const std::string& name) {
    if (!safeZoneName(name)) return nullptr;
    static std::mutex mu;
    static std::map<std::string, std::shared_ptr<const Zone>> cache;
    std::lock_guard<std::mutex> lock(mu);
    const auto hit = cache.find(name);
    if (hit != cache.end()) return hit->second;
    std::shared_ptr<const Zone> found;
    for (const auto& dir : zoneDirs()) {
        std::error_code ec;
        const fs::path p = dir / fs::u8path(name);
        if (!fs::is_regular_file(p, ec)) continue;
        found = TzifZone::read(p);
        if (found) break;
    }
    cache[name] = found;
    return found;
}

std::shared_ptr<const Zone> zoneByName(const std::string& name) {
#ifdef _WIN32
    for (const auto& [windows, iana] : windowsZones()) {
        if (windows == name || iana == name) return windowsZoneByKey(windows);
    }
    return nullptr;
#else
    return zoneFile(name);
#endif
}

std::mutex clockMu;
std::function<int64_t()> clockOverride;

int64_t parseDigits(const std::string& s, size_t at, size_t n, bool& ok) {
    int64_t v = 0;
    for (size_t k = 0; k < n; ++k) {
        if (at + k >= s.size() || s[at + k] < '0' || s[at + k] > '9') {
            ok = false;
            return 0;
        }
        v = v * 10 + (s[at + k] - '0');
    }
    return v;
}

struct Parsed {
    Date date;
    int hour = 0, minute = 0, second = 0, micro = 0;
    std::optional<int> offset;
};

// The date part of datetime.fromisoformat: YYYY-MM-DD or YYYYMMDD. Returns its length, 0 when invalid.
size_t parseIsoDate(const std::string& s, Date& d) {
    bool ok = true;
    if (s.size() >= 10 && s[4] == '-' && s[7] == '-') {
        d = {static_cast<int>(parseDigits(s, 0, 4, ok)), static_cast<int>(parseDigits(s, 5, 2, ok)),
             static_cast<int>(parseDigits(s, 8, 2, ok))};
        return ok ? 10 : 0;
    }
    if (s.size() >= 8) {
        d = {static_cast<int>(parseDigits(s, 0, 4, ok)), static_cast<int>(parseDigits(s, 4, 2, ok)),
             static_cast<int>(parseDigits(s, 6, 2, ok))};
        if (ok && (s.size() == 8 || !std::isdigit(static_cast<unsigned char>(s[8])))) return 8;
    }
    return 0;
}

bool validDate(const Date& d) {
    return d.year >= 1 && d.year <= 9999 && d.month >= 1 && d.month <= 12 && d.day >= 1 &&
           d.day <= daysInMonth(d.year, d.month);
}

// HH[:MM[:SS]] or HH[MM[SS]], then a fraction after any of them, as fromisoformat
// reads a time or an offset. An empty fraction passes only before an offset.
bool parseClock(const std::string& s, int& h, int& m, int& sec, int& micro, bool emptyFraction = false) {
    bool ok = true;
    h = m = sec = micro = 0;
    if (s.size() < 2) return false;
    h = static_cast<int>(parseDigits(s, 0, 2, ok));
    size_t at = 2;
    const bool extended = s.size() > 2 && s[2] == ':';
    for (int* part : {&m, &sec}) {
        if (at >= s.size() || s[at] == '.' || s[at] == ',') break;
        if (extended) {
            if (s[at] != ':') return false;
            ++at;
        }
        *part = static_cast<int>(parseDigits(s, at, 2, ok));
        at += 2;
    }
    if (!ok) return false;
    if (at < s.size()) {
        if (s[at] != '.' && s[at] != ',') return false;
        const std::string digits = s.substr(at + 1);
        if (digits.empty() && !emptyFraction) return false;
        for (const char c : digits) {
            if (c < '0' || c > '9') return false;
        }
        const std::string kept = digits.substr(0, 6);
        micro = kept.empty() ? 0 : std::stoi(kept);
        for (size_t k = kept.size(); k < 6; ++k) micro *= 10;
    }
    return h <= 23 && m <= 59 && sec <= 59;
}

std::optional<Parsed> fromIsoformat(const std::string& s) {
    Parsed p;
    const size_t dateLen = parseIsoDate(s, p.date);
    if (dateLen == 0 || !validDate(p.date)) return std::nullopt;
    if (dateLen == s.size()) return p;
    // Any one character may separate the date from the time.
    const text::Cps rest = text::decode(s.substr(dateLen));
    if (rest.size() < 2) return std::nullopt;
    std::string clock = text::encode(text::Cps(rest.begin() + 1, rest.end()));
    size_t tz = clock.find_first_of("+-Z");
    std::string zone;
    if (tz != std::string::npos) {
        zone = clock.substr(tz);
        clock = clock.substr(0, tz);
    }
    if (!parseClock(clock, p.hour, p.minute, p.second, p.micro, !zone.empty())) return std::nullopt;
    if (!zone.empty()) {
        if (zone == "Z") {
            p.offset = 0;
            return p;
        }
        const int sign = zone[0] == '-' ? -1 : 1;
        int h = 0, m = 0, sec = 0, micro = 0;
        if (!parseClock(zone.substr(1), h, m, sec, micro)) return std::nullopt;
        p.offset = sign * (h * 3600 + m * 60 + sec);
    }
    return p;
}

}  // namespace

// ---- public ----

int64_t daysFromCivil(const Date& d) {
    const int64_t y = d.year - (d.month <= 2);
    const int64_t era = floorDiv(y, 400);
    const int64_t yoe = y - era * 400;
    const int64_t doy = (153 * (d.month + (d.month > 2 ? -3 : 9)) + 2) / 5 + d.day - 1;
    const int64_t doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + doe - 719468;
}

Date addDays(const Date& d, int days) { return civilFromDays(daysFromCivil(d) + days); }

int weekday(const Date& d) { return static_cast<int>(((daysFromCivil(d) % 7) + 10) % 7); }

Date dateOf(const Moment& m) { return civilFromDays(floorDiv(m.utc + m.offset, 86400)); }

std::string isoformat(const Date& d) { return pad(d.year, 4) + "-" + pad(d.month, 2) + "-" + pad(d.day, 2); }

std::string isoformat(const Moment& m, bool secondsOnly) {
    const int64_t wall = m.utc + m.offset;
    const int64_t secs = wall - floorDiv(wall, 86400) * 86400;
    std::string out = isoformat(dateOf(m)) + "T" + pad(secs / 3600, 2) + ":" + pad(secs / 60 % 60, 2) + ":" +
                      pad(secs % 60, 2);
    if (!secondsOnly && m.micro) out += "." + pad(m.micro, 6);
    return out + offsetText(m.offset);
}

Moment withOffset(Moment m, int offset) {
    m.offset = offset;
    return m;
}

Moment inZone(const Moment& m, const Zone& zone) { return withOffset(m, zone.offsetAt(m.utc)); }

Moment fromWall(int64_t wall, int micro, const Zone& zone) {
    // zoneinfo's fold=0: a repeated hour reads the offset before the change, and so does a skipped one.
    const int before = zone.offsetAt(wall - 86400);
    const int after = zone.offsetAt(wall + 86400);
    const bool beforeFits = zone.offsetAt(wall - before) == before;
    const bool afterFits = zone.offsetAt(wall - after) == after;
    const int offset = afterFits && !beforeFits ? after : before;
    return {wall - offset, micro, offset};
}

std::shared_ptr<const Zone> fixedZone(int offset) { return std::make_shared<FixedZone>(offset); }

std::shared_ptr<const Zone> localZone() {
    static const std::shared_ptr<const Zone> zone = std::make_shared<SystemZone>();
    return zone;
}

std::shared_ptr<const Zone> namedZone(const std::string& name) {
    if (name.empty()) return nullptr;
    if (auto zone = zoneByName(name)) return zone;
    const auto& table = windowsZones();
    const auto it = table.find(name);
    return it == table.end() ? nullptr : zoneByName(it->second);
}

std::string localZoneName() {
    std::string tz = getEnvVar("TZ");
    if (!tz.empty() && tz[0] == ':') tz = tz.substr(1);
    if (!tz.empty()) {
        const std::string marker = "zoneinfo/";
        const size_t at = tz.rfind(marker);
        if (tz[0] == '/' && at != std::string::npos) tz = tz.substr(at + marker.size());
        return zoneByName(tz) ? tz : "";
    }
#ifdef _WIN32
    DYNAMIC_TIME_ZONE_INFORMATION info{};
    if (GetDynamicTimeZoneInformation(&info) == TIME_ZONE_ID_INVALID) return "";
    const auto& table = windowsZones();
    const auto it = table.find(narrow(info.TimeZoneKeyName));
    return it == table.end() ? "" : it->second;
#else
    std::error_code ec;
    const fs::path link = fs::read_symlink("/etc/localtime", ec);
    if (ec) return "";
    const std::string target = link.generic_string();
    const std::string marker = "zoneinfo/";
    const size_t at = target.rfind(marker);
    if (at == std::string::npos) return "";
    const std::string name = target.substr(at + marker.size());
    return zoneByName(name) ? name : "";
#endif
}

Moment systemNow() {
    {
        std::lock_guard<std::mutex> lock(clockMu);
        if (clockOverride) {
            const int64_t us = clockOverride();
            return {floorDiv(us, 1000000), static_cast<int>(us - floorDiv(us, 1000000) * 1000000), 0};
        }
    }
    const auto us = std::chrono::duration_cast<std::chrono::microseconds>(
                        std::chrono::system_clock::now().time_since_epoch())
                        .count();
    return {floorDiv(us, 1000000), static_cast<int>(us - floorDiv(us, 1000000) * 1000000), 0};
}

bool isBareDate(const std::string& value) {
    const std::string s = text::strip(value);
    if (s.size() != 10 || s[4] != '-' || s[7] != '-') return false;
    for (size_t k : {0, 1, 2, 3, 5, 6, 8, 9}) {
        if (s[k] < '0' || s[k] > '9') return false;
    }
    return true;
}

namespace {

CalendarError noOffsetError(const std::string& name, const std::string& value) {
    const Moment now = inZone(systemNow(), *localZone());
    return CalendarError(name + "=" + repr(value) +
                         " has no UTC offset, so GAIA can't tell which moment it means. Retry with an offset, "
                         "for example '2026-11-03T09:00:00-05:00'. This computer's time is " +
                         isoformat(now, true) + ".");
}

}  // namespace

Moment parseInstant(const std::string& value, const std::string& name) {
    const std::string stripped = text::strip(value);
    if (isBareDate(stripped)) throw noOffsetError(name, value);
    std::string normalized = stripped;
    if (!normalized.empty() && (normalized.back() == 'Z' || normalized.back() == 'z')) {
        normalized = normalized.substr(0, normalized.size() - 1) + "+00:00";
    }
    // Python 3.10 parses only 3 or 6 fractional digits; Graph sends 7.
    const size_t dot = normalized.find('.');
    if (dot != std::string::npos) {
        size_t end = dot + 1;
        while (end < normalized.size() && normalized[end] >= '0' && normalized[end] <= '9') ++end;
        if (end > dot + 1) {
            std::string digits = normalized.substr(dot + 1, end - dot - 1).substr(0, 6);
            while (digits.size() < 6) digits += '0';
            normalized = normalized.substr(0, dot + 1) + digits + normalized.substr(end);
        }
    }
    const std::optional<Parsed> parsed = fromIsoformat(normalized);
    if (!parsed) {
        throw CalendarError(name + "=" + repr(value) +
                            " is not a valid timestamp. Retry with one like '2026-11-03T09:00:00-05:00'.");
    }
    if (!parsed->offset) throw noOffsetError(name, value);
    if (*parsed->offset <= -86400 || *parsed->offset >= 86400) {
        throw CalendarError(name + "=" + repr(value) +
                            " is not a valid timestamp. Retry with one like '2026-11-03T09:00:00-05:00'.");
    }
    const int64_t wall =
        daysFromCivil(parsed->date) * 86400 + parsed->hour * 3600 + parsed->minute * 60 + parsed->second;
    return {wall - *parsed->offset, parsed->micro, *parsed->offset};
}

Date parseDate(const std::string& value, const std::string& name) {
    const std::string s = text::strip(value);
    Date d;
    if (parseIsoDate(s, d) != s.size() || s.empty() || !validDate(d)) {
        throw CalendarError(name + "=" + repr(value) + " is not a valid date. Retry with YYYY-MM-DD.");
    }
    return d;
}

EventSlot parseSlot(const std::string& rawStart, const std::string& rawEnd) {
    const std::string start = text::strip(rawStart);
    const std::string end = text::strip(rawEnd);
    if (isBareDate(start) != isBareDate(end)) {
        throw CalendarError("start=" + repr(start) + " and end=" + repr(end) +
                            " must match: two YYYY-MM-DD dates for an all-day event, or two timestamps with UTC "
                            "offsets for a timed one.");
    }
    if (isBareDate(start)) {
        const Date first = parseDate(start, "start");
        const Date last = parseDate(end, "end");
        if (last < first) {
            throw CalendarError("end=" + repr(end) + " is before start=" + repr(start) +
                                ". For an all-day event, end is the last day it covers.");
        }
        return {isoformat(first), isoformat(addDays(last, 1)), true};
    }
    const Moment begins = parseInstant(start, "start");
    const Moment ends = parseInstant(end, "end");
    if (ends <= begins) throw CalendarError("end=" + repr(end) + " must be after start=" + repr(start) + ".");
    return {isoformat(begins), isoformat(ends), false};
}

namespace {

// local_zone(): this computer's zone when it can be named, else none.
std::shared_ptr<const Zone> namedLocalZone() { return localZoneName().empty() ? nullptr : localZone(); }

}  // namespace

std::shared_ptr<const Zone> wallClockZone(const Moment& anchor) {
    auto local = namedLocalZone();
    if (local && local->offsetAt(anchor.utc) == anchor.offset) return local;
    return fixedZone(anchor.offset);
}

void checkDaylightOffset(const std::string& value, const std::string& name, const Moment& now) {
    const Moment moment = parseInstant(value, name);
    auto local = namedLocalZone();
    const std::string stripped = text::strip(value);
    // A Z time is the stated way to name another zone's time, so it stands.
    if (!local || (!stripped.empty() && (stripped.back() == 'Z' || stripped.back() == 'z'))) return;
    const int offset = moment.offset;
    const int offsetThen = local->offsetAt(moment.utc);
    if (offset == offsetThen || offset != local->offsetAt(now.utc)) return;
    const Moment meant = fromWall(moment.utc + moment.offset, moment.micro, *local);
    const std::string shown = isoformat(moment);
    const std::string meantText = isoformat(meant);
    throw CalendarError(
        name + "=" + repr(value) + " uses UTC offset " + shown.substr(shown.size() - 6) +
        ", this computer's offset today, but on " + isoformat(dateOf(moment)) + " its time zone is at " +
        meantText.substr(meantText.size() - 6) +
        " because daylight saving time changes in between, so this names a time an hour off. Nothing was asked or "
        "changed. If the person meant their own local time, retry with " + meantText +
        "; if they meant another time zone, give the time in UTC with a Z suffix.");
}

namespace {

std::string utcZ(const Moment& m) { return isoformat(withOffset({m.utc, 0, 0}, 0), true).substr(0, 19) + "Z"; }

}  // namespace

Window::Window(Moment s, Moment e) : start(s), end(e) {
    if (end <= start) {
        throw CalendarError("The window end (" + isoformat(end) + ") must be after its start (" + isoformat(start) +
                            "). Retry with a later end.");
    }
}

std::string Window::startUtc() const { return utcZ(start); }
std::string Window::endUtc() const { return utcZ(end); }

}  // namespace gaia::calendar

namespace gaia {

void CalendarTools::setClockForTesting(std::function<int64_t()> utcMicros) {
    std::lock_guard<std::mutex> lock(calendar::clockMu);
    calendar::clockOverride = std::move(utcMicros);
}

}  // namespace gaia
