// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/data_file.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>

#include "gaia/documents.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

std::string lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

std::string trim(std::string s) {
    s.erase(0, s.find_first_not_of(" \t\r\n"));
    s.erase(s.find_last_not_of(" \t\r\n") + 1);
    return s;
}

void appendUtf8(std::string& out, unsigned cp) {
    if (cp < 0x80) {
        out.push_back(static_cast<char>(cp));
    } else if (cp < 0x800) {
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

std::string utf16ToUtf8(const std::string& bytes, bool littleEndian) {
    std::string out;
    for (std::size_t i = 0; i + 1 < bytes.size(); i += 2) {
        unsigned u = littleEndian ? (static_cast<unsigned char>(bytes[i]) | (static_cast<unsigned char>(bytes[i + 1]) << 8))
                                  : ((static_cast<unsigned char>(bytes[i]) << 8) | static_cast<unsigned char>(bytes[i + 1]));
        if (u >= 0xD800 && u < 0xDC00 && i + 3 < bytes.size()) {
            const unsigned lo = littleEndian ? (static_cast<unsigned char>(bytes[i + 2]) | (static_cast<unsigned char>(bytes[i + 3]) << 8))
                                             : ((static_cast<unsigned char>(bytes[i + 2]) << 8) | static_cast<unsigned char>(bytes[i + 3]));
            if (lo >= 0xDC00 && lo < 0xE000) {
                u = 0x10000 + ((u - 0xD800) << 10) + (lo - 0xDC00);
                i += 2;
            }
        }
        if (u >= 0xD800 && u < 0xE000) u = 0xFFFD;  // a surrogate with no partner
        appendUtf8(out, u);
    }
    return out;
}

bool validUtf8(const std::string& s) {
    std::size_t i = 0;
    while (i < s.size()) {
        const unsigned char c = static_cast<unsigned char>(s[i]);
        std::size_t extra = c < 0x80 ? 0 : (c >> 5) == 0x6 ? 1 : (c >> 4) == 0xE ? 2 : (c >> 3) == 0x1E ? 3 : 4;
        if (extra == 4 || i + extra >= s.size() + (extra == 0 ? 1 : 0)) return false;
        for (std::size_t k = 1; k <= extra; ++k) {
            if ((static_cast<unsigned char>(s[i + k]) & 0xC0) != 0x80) return false;
        }
        i += extra + 1;
    }
    return true;
}

std::string cp1252(const std::string& s) {
    static const unsigned short high[32] = {
        0x20AC, 0xFFFD, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160,
        0x2039, 0x0152, 0xFFFD, 0x017D, 0xFFFD, 0xFFFD, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022,
        0x2013, 0x2014, 0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0xFFFD, 0x017E, 0x0178};
    std::string out;
    for (unsigned char c : s) appendUtf8(out, c < 0x80 ? c : c < 0xA0 ? high[c - 0x80] : c);
    return out;
}

std::vector<std::vector<std::string>> parseDelimited(const std::string& text, char delim) {
    std::vector<std::vector<std::string>> rows;
    std::vector<std::string> row;
    std::string cell;
    bool quoted = false, any = false;
    for (std::size_t i = 0; i < text.size(); ++i) {
        const char c = text[i];
        if (quoted) {
            if (c == '"') {
                if (i + 1 < text.size() && text[i + 1] == '"') {
                    cell.push_back('"');
                    ++i;
                } else {
                    quoted = false;
                }
            } else {
                cell.push_back(c);
            }
            continue;
        }
        if (c == '"' && trim(cell).empty()) {
            quoted = true;
            cell.clear();
            any = true;
        } else if (c == delim) {
            row.push_back(cell);
            cell.clear();
            any = true;
        } else if (c == '\n' || c == '\r') {
            if (c == '\r' && i + 1 < text.size() && text[i + 1] == '\n') ++i;
            if (any || !cell.empty()) {
                row.push_back(cell);
                rows.push_back(row);
            }
            row.clear();
            cell.clear();
            any = false;
        } else {
            cell.push_back(c);
            any = true;
        }
    }
    if (any || !cell.empty()) {
        row.push_back(cell);
        rows.push_back(row);
    }
    return rows;
}

char sniffDelimiter(const std::string& text) {
    // The candidate that splits the first lines into the same, largest field count.
    std::vector<std::string> lines;
    std::istringstream in(text);
    for (std::string l; std::getline(in, l) && lines.size() < 20;) {
        if (!trim(l).empty()) lines.push_back(l);
    }
    char best = ',';
    double bestScore = -1;
    for (char d : {',', ';', '\t', '|'}) {
        std::map<std::size_t, int> counts;
        for (const auto& l : lines) {
            std::size_t n = 1;
            bool q = false;
            for (char c : l) {
                if (c == '"') q = !q;
                else if (c == d && !q) ++n;
            }
            counts[n]++;
        }
        auto mode = std::max_element(counts.begin(), counts.end(),
                                     [](const auto& a, const auto& b) { return a.second < b.second; });
        if (mode == counts.end() || mode->first < 2) continue;
        const double score = static_cast<double>(mode->second) * 100 + static_cast<double>(mode->first);
        if (score > bestScore) {
            bestScore = score;
            best = d;
        }
    }
    return best;
}

// Date as YYYYMMDD (day 0 for month-only values), or 0.
long dateKey(const std::string& raw) {
    const std::string s = trim(raw);
    int y = 0, m = 0, d = 0;
    char a = 0, b = 0;
    static const char* months[] = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"};
    auto ok = [&] { return y >= 1900 && y <= 2200 && m >= 1 && m <= 12 && d >= 0 && d <= 31; };
    if (std::sscanf(s.c_str(), "%4d%c%2d%c%2d", &y, &a, &m, &b, &d) == 5 && (a == '-' || a == '/' || a == '.') && a == b) {
        if (ok()) return y * 10000L + m * 100 + d;
    }
    y = m = d = 0;
    if (std::sscanf(s.c_str(), "%2d%c%2d%c%4d", &m, &a, &d, &b, &y) == 5 && a == b && (a == '/' || a == '-' || a == '.')) {
        if (a == '.') std::swap(m, d);  // DD.MM.YYYY
        if (m > 12 && d <= 12) std::swap(m, d);
        if (ok()) return y * 10000L + m * 100 + d;
    }
    y = m = d = 0;
    if (s.size() >= 7 && std::sscanf(s.c_str(), "%4d-%2d", &y, &m) == 2 && s.size() <= 8) {
        d = 0;
        if (ok()) return y * 10000L + m * 100;
    }
    // "Jan 2025", "January 2025", "2025 Jan"
    const std::string l = lower(s);
    for (int i = 0; i < 12; ++i) {
        if (l.find(months[i]) != std::string::npos) {
            for (std::size_t p = 0; p + 4 <= l.size(); ++p) {
                if (std::isdigit(static_cast<unsigned char>(l[p])) && std::sscanf(l.c_str() + p, "%4d", &y) == 1 && y >= 1900) {
                    m = i + 1;
                    d = 0;
                    if (ok()) return y * 10000L + m * 100;
                }
            }
        }
    }
    return 0;
}

// date_range -> [start, end] inclusive, as YYYYMMDD.
bool parseRange(const std::string& raw, long& start, long& end) {
    std::string s = trim(raw);
    for (const char* sep : {" to ", " - ", ":", ".."}) {
        const auto at = s.find(sep);
        if (at != std::string::npos && at > 0) {
            long a0, a1, b0, b1;
            if (parseRange(s.substr(0, at), a0, a1) && parseRange(s.substr(at + std::strlen(sep)), b0, b1)) {
                start = a0;
                end = b1;
                return start <= end;
            }
        }
    }
    int y = 0, q = 0, m = 0, d = 0;
    const std::string l = lower(s);
    if (std::sscanf(l.c_str(), "%4d-q%1d", &y, &q) == 2 || std::sscanf(l.c_str(), "q%1d %4d", &q, &y) == 2 ||
        std::sscanf(l.c_str(), "q%1d-%4d", &q, &y) == 2) {
        if (q < 1 || q > 4) return false;
    } else if (std::sscanf(l.c_str(), "q%1d'%2d", &q, &y) == 2) {
        y += 2000;
    } else if (l.size() == 4 && std::sscanf(l.c_str(), "%4d", &y) == 1) {
        start = y * 10000L;
        end = y * 10000L + 1231;
        return true;
    } else if (const long k = dateKey(s)) {
        if (k % 100 == 0) {
            start = k;
            end = k + 31;
        } else {
            start = end = k;
        }
        return true;
    } else {
        return false;
    }
    m = (q - 1) * 3 + 1;
    start = y * 10000L + m * 100;
    end = y * 10000L + (m + 2) * 100 + 31;
    (void)d;
    return true;
}

// The row that names the columns, below any title or note rows: Python's
// _header_row_index, where a text cell is one that is neither a number nor a date.
std::size_t headerRowIndex(const std::vector<std::vector<std::string>>& grid) {
    constexpr std::size_t kHeaderSearchRows = 20;
    auto isText = [](const std::string& c) {
        double v;
        return !parseNumber(c, v) && dateKey(c) == 0;
    };
    std::vector<std::vector<std::string>> filled;
    for (std::size_t i = 0; i < std::min(grid.size(), kHeaderSearchRows); ++i) {
        filled.emplace_back();
        for (const auto& c : grid[i]) {
            if (!trim(c).empty()) filled.back().push_back(c);
        }
    }
    auto allText = [&](const std::vector<std::string>& cells) { return std::all_of(cells.begin(), cells.end(), isText); };
    if (filled[0].size() >= 2 && allText(filled[0])) return 0;
    std::size_t width = 0;
    for (const auto& cells : filled) width = std::max(width, cells.size());
    if (width < 2) return 0;
    for (std::size_t i = 0; i < filled.size(); ++i) {
        if (filled[i].size() == width && allText(filled[i])) return i;
    }
    return 0;
}

json roundNum(double v) {
    if (std::isfinite(v) && std::fabs(v - std::round(v)) < 1e-9 && std::fabs(v) < 9e15) return static_cast<long long>(std::llround(v));
    return std::round(v * 10000.0) / 10000.0;
}

}  // namespace

bool parseNumber(const std::string& raw, double& out) {
    std::string s = trim(raw);
    if (s.empty()) return false;
    bool negative = false;
    if (s.front() == '(' && s.back() == ')') {
        negative = true;
        s = trim(s.substr(1, s.size() - 2));
    }
    std::string clean;
    for (std::size_t i = 0; i < s.size(); ++i) {
        const unsigned char c = static_cast<unsigned char>(s[i]);
        if (std::isdigit(c) || c == '.' || c == ',' || c == '-' || c == '+' || c == 'e' || c == 'E') {
            clean.push_back(static_cast<char>(c));
        } else if (c == '$' || c == ' ' || c == '%' || c == '\'') {
            continue;
        } else if (c == 0xE2 && i + 2 < s.size() && static_cast<unsigned char>(s[i + 1]) == 0x88 &&
                   static_cast<unsigned char>(s[i + 2]) == 0x92) {  // U+2212 minus sign
            clean.push_back('-');
            i += 2;
        } else if (c == 0xE2 && i + 2 < s.size()) {  // euro sign, thin spaces
            i += 2;
        } else if (c == 0xC2 && i + 1 < s.size()) {  // pound, yen, nbsp
            ++i;
        } else {
            return false;
        }
    }
    if (clean.empty() || clean.find_first_of("0123456789") == std::string::npos) return false;
    const auto lastComma = clean.rfind(','), lastDot = clean.rfind('.');
    if (lastComma != std::string::npos && lastDot != std::string::npos) {
        // Whichever comes last is the decimal mark.
        if (lastComma > lastDot) {
            clean.erase(std::remove(clean.begin(), clean.end(), '.'), clean.end());
            std::replace(clean.begin(), clean.end(), ',', '.');
        } else {
            clean.erase(std::remove(clean.begin(), clean.end(), ','), clean.end());
        }
    } else if (lastComma != std::string::npos) {
        // "1,234" is a thousands group; "12,5" and "1234,56" are decimals.
        const std::size_t after = clean.size() - lastComma - 1;
        if (after == 3 && std::count(clean.begin(), clean.end(), ',') >= 1 && clean.find(',') > 0 && clean.find(',') <= 3) {
            clean.erase(std::remove(clean.begin(), clean.end(), ','), clean.end());
        } else if (std::count(clean.begin(), clean.end(), ',') == 1) {
            std::replace(clean.begin(), clean.end(), ',', '.');
        } else {
            return false;
        }
    }
    char* end = nullptr;
    const double v = std::strtod(clean.c_str(), &end);
    if (end == clean.c_str() || *end != '\0') return false;
    out = negative ? -v : v;
    return true;
}

DataTable readDataTable(const std::string& path) {
    DataTable t;
    const std::string ext = lower(fs::u8path(path).extension().u8string());
    std::vector<std::vector<std::string>> grid;
    if (ext == ".xlsx") {
        const auto sheets = readWorkbook(path);
        for (const auto& s : sheets) {
            if (s.rows.size() >= 2) {
                grid = s.rows;
                if (sheets.size() > 1) t.warnings.push_back("read sheet '" + s.name + "' (the first with data) of " + std::to_string(sheets.size()));
                break;
            }
        }
        if (grid.empty()) throw std::runtime_error(path + " has no sheet with a header and data rows.");
    } else if (ext == ".xls") {
        throw std::runtime_error(path + " is a legacy .xls workbook, which this build cannot read; save it as .xlsx or .csv.");
    } else {
        std::ifstream in(fs::u8path(path), std::ios::binary);
        if (!in) throw std::runtime_error("cannot open " + path);
        std::ostringstream ss;
        ss << in.rdbuf();
        std::string bytes = ss.str();
        if (bytes.size() >= 2 && static_cast<unsigned char>(bytes[0]) == 0xFF && static_cast<unsigned char>(bytes[1]) == 0xFE) {
            bytes = utf16ToUtf8(bytes.substr(2), true);
            t.encoding = "utf-16le";
        } else if (bytes.size() >= 2 && static_cast<unsigned char>(bytes[0]) == 0xFE && static_cast<unsigned char>(bytes[1]) == 0xFF) {
            bytes = utf16ToUtf8(bytes.substr(2), false);
            t.encoding = "utf-16be";
        } else if (bytes.size() >= 4 && bytes[1] == '\0' && bytes[3] == '\0' && bytes[0] != '\0') {
            bytes = utf16ToUtf8(bytes, true);
            t.encoding = "utf-16le";
        } else {
            if (bytes.size() >= 3 && bytes.compare(0, 3, "\xEF\xBB\xBF") == 0) bytes.erase(0, 3);
            if (validUtf8(bytes)) {
                t.encoding = "utf-8";
            } else {
                bytes = cp1252(bytes);
                t.encoding = "cp1252";
            }
        }
        const char delim = ext == ".tsv" ? '\t' : sniffDelimiter(bytes);
        t.delimiter = std::string(1, delim);
        grid = parseDelimited(bytes, delim);
    }
    if (grid.empty()) return t;
    const std::size_t headerRow = headerRowIndex(grid);
    if (headerRow > 0) t.warnings.push_back("skipped " + std::to_string(headerRow) + " line(s) above the header");
    for (auto& c : grid[headerRow]) t.columns.push_back(trim(c));
    for (std::size_t i = 0; i < t.columns.size(); ++i) {
        if (t.columns[i].empty()) t.columns[i] = "column_" + std::to_string(i + 1);
    }
    // Repeated names get pandas' suffixes (amount, amount.1) so no column is hidden behind another.
    std::set<std::string> taken(t.columns.begin(), t.columns.end()), seen;
    std::vector<std::string> renamed;
    for (auto& name : t.columns) {
        if (seen.insert(name).second) continue;
        std::size_t k = 1;
        while (taken.count(name + "." + std::to_string(k))) ++k;
        const std::string fresh = name + "." + std::to_string(k);
        renamed.push_back(name + " -> " + fresh);
        name = fresh;
        taken.insert(fresh);
        seen.insert(fresh);
    }
    if (!renamed.empty()) {
        std::string list;
        for (const auto& r : renamed) list += (list.empty() ? "" : ", ") + r;
        t.warnings.push_back("renamed repeated column name(s): " + list);
    }
    std::size_t ragged = 0, repeatedHeaders = 0;
    for (std::size_t r = headerRow + 1; r < grid.size(); ++r) {
        auto row = grid[r];
        bool blank = true;
        for (const auto& c : row) blank = blank && trim(c).empty();
        if (blank) continue;
        if (row.size() != t.columns.size()) ++ragged;
        row.resize(t.columns.size());
        bool isHeader = true;
        for (std::size_t c = 0; c < row.size(); ++c) isHeader = isHeader && trim(row[c]) == t.columns[c];
        if (isHeader) {
            ++repeatedHeaders;
            continue;
        }
        t.rows.push_back(std::move(row));
    }
    if (ragged) t.warnings.push_back(std::to_string(ragged) + " row(s) had a different number of fields than the header");
    if (repeatedHeaders) t.warnings.push_back("skipped " + std::to_string(repeatedHeaders) + " repeated header row(s)");
    return t;
}

json analyzeDataFile(const std::string& path, const std::string& analysisTypeRaw, const std::string& columnsArg,
                     const std::string& groupByArg, const std::string& dateRange) {
    const std::string analysisType = lower(trim(analysisTypeRaw.empty() ? "summary" : analysisTypeRaw));
    if (analysisType != "summary" && analysisType != "trends" && analysisType != "full") {
        return {{"status", "error"},
                {"error", "analysis_type must be summary, trends or full" +
                              std::string(analysisType == "spending" ? " (for spending by category, pass group_by=<category column>)" : "")},
                {"has_errors", true}};
    }
    DataTable t;
    try {
        t = readDataTable(path);
    } catch (const std::runtime_error& e) {
        return {{"status", "error"}, {"error", e.what()}, {"has_errors", true}, {"operation", "analyze_data_file"}};
    }
    const std::string name = fs::u8path(path).filename().u8string();
    if (t.rows.empty()) {
        return {{"status", "success"}, {"file", name}, {"row_count", 0}, {"columns", t.columns},
                {"message", "File is empty or contains only headers."}};
    }
    auto findColumn = [&t](const std::string& wanted) -> long {
        const std::string w = lower(trim(wanted));
        for (std::size_t i = 0; i < t.columns.size(); ++i) {
            if (lower(t.columns[i]) == w) return static_cast<long>(i);
        }
        return -1;
    };

    // Column types: numeric when nearly every filled cell parses as a number.
    const std::size_t ncol = t.columns.size();
    std::vector<std::string> types(ncol, "text");
    for (std::size_t c = 0; c < ncol; ++c) {
        std::size_t filled = 0, numeric = 0, dates = 0;
        static const std::set<std::string> kMissingMarkers = {"n/a", "na", "#n/a", "null", "none", "-", "--", "?", "tbd", "nan"};
        for (const auto& r : t.rows) {
            if (trim(r[c]).empty() || kMissingMarkers.count(lower(trim(r[c])))) continue;
            ++filled;
            double v;
            if (dateKey(r[c])) ++dates;
            else if (parseNumber(r[c], v)) ++numeric;
        }
        if (filled == 0) types[c] = "empty";
        else if (dates * 10 >= filled * 8) types[c] = "date";
        else if (numeric * 10 >= filled * 8) types[c] = "numeric";
    }

    // Total rows would be counted twice; they are left out, and named.
    std::vector<std::vector<std::string>> rows;
    std::vector<std::size_t> totalRows;
    for (std::size_t i = 0; i < t.rows.size(); ++i) {
        bool isTotal = false;
        for (std::size_t c = 0; c < ncol; ++c) {
            if (types[c] != "text") continue;
            const std::string v = lower(trim(t.rows[i][c]));
            const bool endsTotal = v.size() > 6 && v.compare(v.size() - 6, 6, " total") == 0;
            if (v == "total" || v == "subtotal" || v == "sum" || v.rfind("total ", 0) == 0 || endsTotal ||
                v.rfind("subtotal ", 0) == 0) {
                isTotal = true;
            }
        }
        if (isTotal) totalRows.push_back(i + 2);
        else rows.push_back(t.rows[i]);
    }
    if (!totalRows.empty()) {
        std::string list;
        for (auto r : totalRows) list += (list.empty() ? "" : ", ") + std::to_string(r);
        t.warnings.push_back("left out " + std::to_string(totalRows.size()) + " total/subtotal row(s) (data rows " + list + ")");
    }

    // Date filter.
    long dateCol = -1;
    for (std::size_t c = 0; c < ncol && dateCol < 0; ++c) {
        if (types[c] == "date") dateCol = static_cast<long>(c);
    }
    json result = {{"status", "success"}, {"file", name}, {"file_path", fs::u8path(path).u8string()}};
    if (!trim(dateRange).empty()) {
        long start = 0, end = 0;
        if (!parseRange(dateRange, start, end)) {
            return {{"status", "error"},
                    {"error", "date_range '" + dateRange + "' is not a quarter (2025-Q1, Q1'25), year, month (2025-03), day, or range (2025-01 to 2025-06)."},
                    {"has_errors", true}};
        }
        if (dateCol < 0) {
            return {{"status", "error"}, {"error", "date_range was given but no column holds dates."}, {"has_errors", true}};
        }
        std::vector<std::vector<std::string>> kept;
        for (const auto& r : rows) {
            const long k = dateKey(r[static_cast<std::size_t>(dateCol)]);
            const long day = k % 100 == 0 ? k + 1 : k;
            if (k && day >= start && day <= end) kept.push_back(r);
        }
        rows = kept;
        result["date_filter_applied"] = dateRange;
        result["date_filter_parsed"] = {{"start", start}, {"end", end}};
        result["date_column"] = t.columns[static_cast<std::size_t>(dateCol)];
    }

    std::vector<std::size_t> focus;
    if (!trim(columnsArg).empty()) {
        std::stringstream ss(columnsArg);
        for (std::string c; std::getline(ss, c, ',');) {
            const long i = findColumn(c);
            if (i < 0) {
                return {{"status", "error"}, {"error", "No column named '" + trim(c) + "'. Columns: " + json(t.columns).dump()},
                        {"has_errors", true}};
            }
            focus.push_back(static_cast<std::size_t>(i));
        }
    } else {
        for (std::size_t c = 0; c < ncol; ++c) focus.push_back(c);
    }

    result["row_count"] = rows.size();
    result["columns"] = t.columns;
    result["column_count"] = ncol;
    json columnTypes = json::object();
    for (std::size_t c = 0; c < ncol; ++c) columnTypes[t.columns[c]] = types[c];
    result["column_types"] = columnTypes;
    if (!t.delimiter.empty()) result["delimiter"] = t.delimiter == "\t" ? "tab" : t.delimiter;
    if (!t.encoding.empty() && t.encoding != "utf-8") result["encoding"] = t.encoding;

    json summary = json::object();
    for (std::size_t c : focus) {
        json col = {{"type", types[c]}};
        if (types[c] == "numeric") {
            double sum = 0, mn = 0, mx = 0;
            std::size_t n = 0, missing = 0;
            std::vector<std::string> bad;
            for (const auto& r : rows) {
                double v;
                if (trim(r[c]).empty()) {
                    ++missing;
                } else if (parseNumber(r[c], v)) {
                    sum += v;
                    mn = n ? std::min(mn, v) : v;
                    mx = n ? std::max(mx, v) : v;
                    ++n;
                } else if (bad.size() < 5) {
                    bad.push_back(r[c]);
                }
            }
            col.update({{"count", n}, {"sum", roundNum(sum)}, {"mean", n ? roundNum(sum / static_cast<double>(n)) : json(nullptr)},
                        {"min", n ? roundNum(mn) : json(nullptr)}, {"max", n ? roundNum(mx) : json(nullptr)}, {"missing", missing}});
            if (!bad.empty()) col["unparsed_values"] = bad;
        } else {
            std::map<std::string, std::size_t> counts;
            for (const auto& r : rows) {
                if (!trim(r[c]).empty()) counts[trim(r[c])]++;
            }
            std::vector<std::pair<std::string, std::size_t>> top(counts.begin(), counts.end());
            std::sort(top.begin(), top.end(), [](const auto& a, const auto& b) { return a.second > b.second; });
            json tv = json::array();
            for (std::size_t i = 0; i < top.size() && i < 8; ++i) tv.push_back({{"value", top[i].first}, {"count", top[i].second}});
            col.update({{"unique", counts.size()}, {"top_values", tv}});
            // Values that differ only by case or spacing are usually one thing typed two ways.
            std::map<std::string, std::vector<std::string>> folded;
            for (const auto& [v, n] : counts) folded[lower(v)].push_back(v);
            json similar = json::array();
            for (const auto& [k, vs] : folded) {
                if (vs.size() > 1) similar.push_back(vs);
            }
            if (!similar.empty()) col["similar_values"] = similar;
        }
        summary[t.columns[c]] = col;
    }
    result["summary"] = summary;

    std::vector<std::size_t> numericFocus;
    for (std::size_t c : focus) {
        if (types[c] == "numeric") numericFocus.push_back(c);
    }

    if (!trim(groupByArg).empty()) {
        const long g = findColumn(groupByArg);
        if (g < 0) {
            return {{"status", "error"}, {"error", "No column named '" + trim(groupByArg) + "' to group by. Columns: " + json(t.columns).dump()},
                    {"has_errors", true}};
        }
        struct Group {
            std::size_t rows = 0;
            std::map<std::size_t, double> sums;
        };
        std::map<std::string, Group> groups;
        for (const auto& r : rows) {
            Group& grp = groups[trim(r[static_cast<std::size_t>(g)])];
            ++grp.rows;
            for (std::size_t c : numericFocus) {
                double v;
                if (static_cast<long>(c) != g && parseNumber(r[c], v)) grp.sums[c] += v;
            }
        }
        std::size_t key = numericFocus.empty() ? ncol : numericFocus.front();
        for (std::size_t c : numericFocus) {
            if (static_cast<long>(c) != g) {
                key = c;
                break;
            }
        }
        std::vector<std::pair<std::string, Group>> sorted(groups.begin(), groups.end());
        std::sort(sorted.begin(), sorted.end(), [key](const auto& a, const auto& b) {
            const double av = a.second.sums.count(key) ? a.second.sums.at(key) : 0;
            const double bv = b.second.sums.count(key) ? b.second.sums.at(key) : 0;
            // NaN (inf minus inf) sorts last so the order stays a strict weak ordering.
            if (std::isnan(av) != std::isnan(bv)) return std::isnan(bv);
            if (!std::isnan(av) && av != bv) return av > bv;
            return a.second.rows > b.second.rows;
        });
        double grand = 0;
        for (const auto& [k, grp] : sorted) grand += grp.sums.count(key) ? grp.sums.at(key) : 0;
        json out = json::array();
        for (const auto& [k, grp] : sorted) {
            json totals = json::object();
            for (const auto& [c, v] : grp.sums) totals[t.columns[c]] = roundNum(v);
            json entry = {{"group", k.empty() ? "(blank)" : k}, {"row_count", grp.rows}, {"totals", totals}};
            if (key < ncol && grand != 0) entry["share_of_total"] = std::round((grp.sums.count(key) ? grp.sums.at(key) : 0) / grand * 10000.0) / 100.0;
            out.push_back(entry);
        }
        result["group_by"] = t.columns[static_cast<std::size_t>(g)];
        result["group_by_results"] = out;
        if (!out.empty() && key < ncol) {
            result["sorted_by"] = t.columns[key];
            result["top_1"] = {{"group", out[0]["group"]}, {"value", out[0]["totals"].value(t.columns[key], json(0))}, {"column", t.columns[key]}};
        }
    }

    if ((analysisType == "trends" || analysisType == "full") && dateCol >= 0) {
        std::map<long, std::map<std::size_t, double>> byMonth;
        for (const auto& r : rows) {
            const long k = dateKey(r[static_cast<std::size_t>(dateCol)]);
            if (!k) continue;
            auto& m = byMonth[k / 100];
            for (std::size_t c : numericFocus) {
                double v;
                if (parseNumber(r[c], v)) m[c] += v;
            }
        }
        json months = json::array();
        for (const auto& [ym, sums] : byMonth) {
            char label[16];
            std::snprintf(label, sizeof(label), "%04ld-%02ld", ym / 100, ym % 100);
            json totals = json::object();
            for (const auto& [c, v] : sums) totals[t.columns[c]] = roundNum(v);
            months.push_back({{"month", label}, {"totals", totals}});
        }
        json trends = {{"date_column", t.columns[static_cast<std::size_t>(dateCol)]}, {"by_month", months}};
        if (months.size() >= 2 && !numericFocus.empty()) {
            const std::string col = t.columns[numericFocus.front()];
            const double first = months.front()["totals"].value(col, 0.0), last = months.back()["totals"].value(col, 0.0);
            trends["direction"] = last > first ? "up" : last < first ? "down" : "flat";
            trends["direction_column"] = col;
        }
        result["trends"] = trends;
    } else if (analysisType == "trends") {
        t.warnings.push_back("no column holds dates, so there is no trend to compute");
    }

    json sample = json::array();
    for (std::size_t i = 0; i < rows.size() && i < 5; ++i) {
        json r = json::object();
        for (std::size_t c = 0; c < ncol; ++c) r[t.columns[c]] = rows[i][c];
        sample.push_back(r);
    }
    result["sample_rows"] = sample;
    if (!t.warnings.empty()) result["warnings"] = t.warnings;
    return result;
}

}  // namespace gaia
