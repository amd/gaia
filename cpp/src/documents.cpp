// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/documents.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>

#include "miniz.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

constexpr std::size_t kMaxColumns = 16384;
// Larger than any document part or page stream GAIA reads; past it the file is a bomb.
constexpr std::size_t kMaxInflated = 256u * 1024u * 1024u;
// All parts or streams of one file together; repeating one part must not multiply the work.
constexpr std::size_t kMaxDocumentInflated = 512u * 1024u * 1024u;
constexpr std::size_t kMaxPdfPages = 5000;
// Form XObjects drawn while reading one page; nesting multiplies them.
constexpr std::size_t kMaxFormRuns = 2000;
// Cells readWorkbook() materializes; a cell far to the right widens every row.
constexpr std::size_t kMaxGridCells = 10u * 1000u * 1000u;

std::string lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
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
    } else if (cp < 0x110000) {
        out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    }
}

unsigned winAnsi(unsigned char c) {
    static const unsigned short high[32] = {
        0x20AC, 0xFFFD, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160,
        0x2039, 0x0152, 0xFFFD, 0x017D, 0xFFFD, 0xFFFD, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022,
        0x2013, 0x2014, 0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0xFFFD, 0x017E, 0x0178};
    return (c >= 0x80 && c < 0xA0) ? high[c - 0x80] : c;
}

std::string readFileBytes(const std::string& path) {
    std::ifstream in(fs::u8path(path), std::ios::binary);
    if (!in) throw std::runtime_error("cannot open " + path);
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

// ---------------------------------------------------------------------------
// XML helpers for the Office formats
// ---------------------------------------------------------------------------

std::string xmlUnescape(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (std::size_t i = 0; i < s.size(); ++i) {
        if (s[i] != '&') {
            out.push_back(s[i]);
            continue;
        }
        const auto semi = s.find(';', i);
        if (semi == std::string::npos || semi - i > 10) {
            out.push_back('&');
            continue;
        }
        const std::string e = s.substr(i + 1, semi - i - 1);
        if (e == "amp") out += '&';
        else if (e == "lt") out += '<';
        else if (e == "gt") out += '>';
        else if (e == "quot") out += '"';
        else if (e == "apos") out += '\'';
        else if (!e.empty() && e[0] == '#') {
            const unsigned cp = static_cast<unsigned>(
                (e.size() > 1 && (e[1] == 'x' || e[1] == 'X')) ? std::strtoul(e.c_str() + 2, nullptr, 16)
                                                               : std::strtoul(e.c_str() + 1, nullptr, 10));
            appendUtf8(out, cp);
        } else {
            out += s.substr(i, semi - i + 1);
        }
        i = semi;
    }
    return out;
}

std::string attr(const std::string& tag, const std::string& name) {
    const std::string key = " " + name + "=\"";
    auto at = tag.find(key);
    if (at == std::string::npos) return "";
    at += key.size();
    return xmlUnescape(tag.substr(at, tag.find('"', at) - at));
}

class Zip {
public:
    explicit Zip(const std::string& path) {
        std::memset(&zip_, 0, sizeof(zip_));
        data_ = readFileBytes(path);
        if (!mz_zip_reader_init_mem(&zip_, data_.data(), data_.size(), 0)) {
            throw std::runtime_error(path + " is not a readable Office file (not a zip archive, or damaged).");
        }
    }
    ~Zip() { mz_zip_reader_end(&zip_); }
    Zip(const Zip&) = delete;
    Zip& operator=(const Zip&) = delete;

    bool has(const std::string& name) { return mz_zip_reader_locate_file(&zip_, name.c_str(), nullptr, 0) >= 0; }
    std::string read(const std::string& name) {
        const int index = mz_zip_reader_locate_file(&zip_, name.c_str(), nullptr, 0);
        mz_zip_archive_file_stat st;
        if (index < 0 || !mz_zip_reader_file_stat(&zip_, static_cast<mz_uint>(index), &st)) {
            throw std::runtime_error("the archive has no readable " + name);
        }
        if (st.m_uncomp_size > kMaxInflated) {
            throw std::runtime_error(name + " unpacks to " + std::to_string(st.m_uncomp_size / (1024 * 1024)) +
                                     " MB, more than GAIA reads from one document part");
        }
        if (inflated_ + st.m_uncomp_size > kMaxDocumentInflated) {
            throw std::runtime_error("the file's parts unpack to more than " +
                                     std::to_string(kMaxDocumentInflated / (1024 * 1024)) +
                                     " MB in total, more than GAIA reads from one document");
        }
        std::size_t size = 0;
        void* p = mz_zip_reader_extract_file_to_heap(&zip_, name.c_str(), &size, 0);
        if (!p) throw std::runtime_error("the archive has no readable " + name);
        std::string out(static_cast<const char*>(p), size);
        mz_free(p);
        inflated_ += out.size();
        return out;
    }
    std::vector<std::string> names() {
        std::vector<std::string> out;
        const mz_uint n = mz_zip_reader_get_num_files(&zip_);
        for (mz_uint i = 0; i < n; ++i) {
            char buf[512];
            mz_zip_reader_get_filename(&zip_, i, buf, sizeof(buf));
            out.push_back(buf);
        }
        return out;
    }

private:
    std::string data_;
    mz_zip_archive zip_;
    std::size_t inflated_ = 0;
};

// Walk tags in document order, calling fn(tagText) for tags and text(chunk) for text.
template <typename TagFn, typename TextFn>
void walkXml(const std::string& xml, TagFn&& onTag, TextFn&& onText) {
    std::size_t i = 0;
    while (i < xml.size()) {
        if (xml[i] == '<') {
            const auto end = xml.find('>', i);
            if (end == std::string::npos) return;
            onTag(xml.substr(i, end - i + 1));
            i = end + 1;
        } else {
            const auto next = xml.find('<', i);
            onText(xml.substr(i, (next == std::string::npos ? xml.size() : next) - i));
            i = next == std::string::npos ? xml.size() : next;
        }
    }
}

std::string tagName(const std::string& tag) {
    std::size_t b = 1;
    if (b < tag.size() && tag[b] == '/') ++b;
    std::size_t e = b;
    while (e < tag.size() && !std::isspace(static_cast<unsigned char>(tag[e])) && tag[e] != '>' && tag[e] != '/') ++e;
    return tag.substr(b, e - b);
}

DocumentText readDocx(const std::string& path) {
    Zip zip(path);
    DocumentText d;
    d.kind = "docx";
    d.sections = 1;
    const std::string xml = zip.read("word/document.xml");
    bool inText = false;
    int fallback = 0;  // inside mc:Fallback, a second copy of the mc:Choice content
    std::string out;
    walkXml(
        xml,
        [&](const std::string& tag) {
            const std::string name = tagName(tag);
            const bool closing = tag.size() > 1 && tag[1] == '/';
            if (name == "mc:Fallback") {
                if (closing) fallback = std::max(0, fallback - 1);
                else if (tag[tag.size() - 2] != '/') ++fallback;
                return;
            }
            if (fallback) return;
            if (name == "w:t") inText = !closing && tag[tag.size() - 2] != '/';
            else if (name == "w:p" && closing) out += "\n";
            else if (name == "w:tab") out += "\t";
            else if (name == "w:br" || name == "w:cr") out += "\n";
            else if (name == "w:tc" && closing) out += "\t";
            else if (name == "w:tr" && closing) out += "\n";
        },
        [&](const std::string& text) {
            if (inText && !fallback) out += xmlUnescape(text);
        });
    d.text = out;
    if (zip.has("word/footnotes.xml")) d.warnings.push_back("footnotes are not included");
    return d;
}

/// "BC12" -> 54 (0-based column of the cell reference).
std::size_t columnIndex(const std::string& ref) {
    std::size_t col = 0;
    for (char c : ref) {
        if (c < 'A' || c > 'Z') break;
        col = col * 26 + static_cast<std::size_t>(c - 'A' + 1);
        if (col > kMaxColumns) return kMaxColumns - 1;  // Excel's last column is XFD
    }
    return col == 0 ? 0 : col - 1;
}

// 54 -> "BC"
std::string columnName(std::size_t col) {
    std::string out;
    for (++col; col > 0; col = (col - 1) / 26) out.insert(out.begin(), static_cast<char>('A' + (col - 1) % 26));
    return out;
}

// A number format shows a date or time when it uses d, m, y, h or s outside quotes,
// escapes and [colour]/[locale] blocks. Elapsed-time formats ([h]:mm) stay numbers.
bool isDateFormatCode(const std::string& code) {
    for (const char* elapsed : {"[h", "[H", "[m", "[M", "[s", "[S"}) {
        if (code.find(elapsed) != std::string::npos) return false;
    }
    for (std::size_t i = 0; i < code.size(); ++i) {
        const char c = code[i];
        if (c == ';') break;  // the first section decides
        if (c == '"' || c == '[') {
            const auto q = code.find(c == '"' ? '"' : ']', i + 1);
            if (q == std::string::npos) break;
            i = q;
        } else if (c == '\\' || c == '_' || c == '*') {
            ++i;
        } else if (std::strchr("dmyhsDMYHS", c)) {
            return true;
        }
    }
    return false;
}

// For each cell style index, whether its number is a date, from xl/styles.xml.
std::vector<bool> readDateStyles(Zip& zip) {
    std::vector<bool> out;
    if (!zip.has("xl/styles.xml")) return out;
    std::map<unsigned long, bool> custom;
    bool inCellXfs = false;
    walkXml(
        zip.read("xl/styles.xml"),
        [&](const std::string& tag) {
            const std::string name = tagName(tag);
            const bool closing = tag.size() > 1 && tag[1] == '/';
            if (name == "numFmt" && !closing) {
                custom[std::strtoul(attr(tag, "numFmtId").c_str(), nullptr, 10)] = isDateFormatCode(attr(tag, "formatCode"));
            } else if (name == "cellXfs") {
                inCellXfs = !closing && tag[tag.size() - 2] != '/';
            } else if (name == "xf" && !closing && inCellXfs) {
                const unsigned long id = std::strtoul(attr(tag, "numFmtId").c_str(), nullptr, 10);
                // Excel's built-in date and time formats, including the East Asian ones.
                bool date = (id >= 14 && id <= 22) || (id >= 27 && id <= 36) || (id >= 45 && id <= 47) ||
                            (id >= 50 && id <= 58);
                if (auto it = custom.find(id); it != custom.end()) date = it->second;
                out.push_back(date);
            }
        },
        [](const std::string&) {});
    return out;
}

// A date serial as openpyxl's str() of it: "2025-01-05 00:00:00", or "13:30:00" for a
// time of day alone. "" when the value is not a usable serial.
std::string excelSerialToText(const std::string& raw, bool date1904) {
    char* end = nullptr;
    const double v = std::strtod(raw.c_str(), &end);
    if (end == raw.c_str() || !std::isfinite(v) || v < 0 || v > 2958465) return "";
    long long day = static_cast<long long>(std::floor(v));
    long long secs = std::llround((v - static_cast<double>(day)) * 86400.0);
    if (secs >= 86400) {
        ++day;
        secs -= 86400;
    }
    char buf[48];
    if (day == 0) {
        std::snprintf(buf, sizeof(buf), "%02lld:%02lld:%02lld", secs / 3600, secs / 60 % 60, secs % 60);
        return buf;
    }
    // Excel counts 1900-02-29, which never was, so serials before it are a day short.
    if (!date1904 && day < 60) ++day;
    // Days since 1970-01-01, then civil-from-days (H. Hinnant).
    const long long z = day + (date1904 ? -24107 : -25569) + 719468;
    const long long era = (z >= 0 ? z : z - 146096) / 146097;
    const long long doe = z - era * 146097;
    const long long yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    const long long doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    const long long mp = (5 * doy + 2) / 153;
    const long long dd = doy - (153 * mp + 2) / 5 + 1;
    const long long mm = mp < 10 ? mp + 3 : mp - 9;
    const long long yy = yoe + era * 400 + (mm <= 2 ? 1 : 0);
    std::snprintf(buf, sizeof(buf), "%04lld-%02lld-%02lld %02lld:%02lld:%02lld", yy, mm, dd, secs / 3600,
                  secs / 60 % 60, secs % 60);
    return buf;
}

DocumentText readXlsx(const std::string& path) {
    Zip zip(path);
    DocumentText d;
    d.kind = "xlsx";
    std::vector<std::string> shared;
    if (zip.has("xl/sharedStrings.xml")) {
        const std::string xml = zip.read("xl/sharedStrings.xml");
        bool inT = false;
        std::string cur;
        walkXml(
            xml,
            [&](const std::string& tag) {
                const std::string name = tagName(tag);
                const bool closing = tag.size() > 1 && tag[1] == '/';
                if (name == "t") inT = !closing && tag[tag.size() - 2] != '/';
                if (name == "si" && closing) {
                    shared.push_back(cur);
                    cur.clear();
                }
            },
            [&](const std::string& t) {
                if (inT) cur += xmlUnescape(t);
            });
    }
    const std::vector<bool> dateStyles = readDateStyles(zip);
    // Sheet names and their files, in workbook order.
    std::vector<std::pair<std::string, std::string>> sheets;
    std::map<std::string, std::string> rels;
    if (zip.has("xl/_rels/workbook.xml.rels")) {
        walkXml(
            zip.read("xl/_rels/workbook.xml.rels"),
            [&](const std::string& tag) {
                if (tagName(tag) == "Relationship") {
                    std::string target = attr(tag, "Target");
                    if (!target.empty() && target[0] == '/') target = target.substr(1);
                    else target = "xl/" + target;
                    rels[attr(tag, "Id")] = target;
                }
            },
            [](const std::string&) {});
    }
    bool date1904 = false;
    walkXml(
        zip.read("xl/workbook.xml"),
        [&](const std::string& tag) {
            const std::string n = tagName(tag);
            if (n == "sheet") sheets.emplace_back(attr(tag, "name"), rels[attr(tag, "r:id")]);
            else if (n == "workbookPr") date1904 = attr(tag, "date1904") == "1" || attr(tag, "date1904") == "true";
        },
        [](const std::string&) {});
    std::set<std::string> partsRead;
    for (const auto& [name, file] : sheets) {
        if (!file.empty() && !partsRead.insert(file).second) {
            d.warnings.push_back("sheet '" + name + "' points at the same data as an earlier sheet and was skipped");
            continue;
        }
        ++d.sections;
        d.text += "--- Sheet: " + name + " ---\n";
        if (file.empty() || !zip.has(file)) {
            d.warnings.push_back("sheet '" + name + "' could not be located in the workbook");
            continue;
        }
        std::string type, value, ref, row;
        std::size_t style = 0, rowNum = 0, nextCol = 0;
        bool inV = false, inIs = false;
        walkXml(
            zip.read(file),
            [&](const std::string& tag) {
                const std::string n = tagName(tag);
                const bool closing = tag.size() > 1 && tag[1] == '/';
                if (n == "row" && !closing) {
                    const std::string r = attr(tag, "r");
                    rowNum = r.empty() ? rowNum + 1 : std::strtoul(r.c_str(), nullptr, 10);
                    nextCol = 0;
                } else if (n == "c" && !closing) {
                    type = attr(tag, "t");
                    style = std::strtoul(attr(tag, "s").c_str(), nullptr, 10);
                    // A cell without r sits right of the one before it.
                    ref = attr(tag, "r");
                    const std::size_t col = ref.empty() ? std::min(nextCol, kMaxColumns - 1) : columnIndex(ref);
                    if (ref.empty()) ref = columnName(col) + std::to_string(rowNum);
                    nextCol = col + 1;
                    value.clear();
                } else if (n == "c" && closing) {
                    std::string shown = value;
                    if (type == "s") {
                        const std::size_t idx = static_cast<std::size_t>(std::strtoul(value.c_str(), nullptr, 10));
                        shown = idx < shared.size() ? shared[idx] : "";
                    } else if (type == "b") {
                        shown = value == "1" ? "TRUE" : "FALSE";
                    } else if ((type.empty() || type == "n") && !value.empty() && style < dateStyles.size() &&
                               dateStyles[style]) {
                        const std::string when = excelSerialToText(value, date1904);
                        if (!when.empty()) shown = when;
                    }
                    // One cell stays on one line of the text view.
                    for (char& ch : shown) {
                        if (ch == '\t' || ch == '\n' || ch == '\r') ch = ' ';
                    }
                    if (!row.empty()) row += "\t";
                    row += ref + "=" + shown;
                } else if (n == "v") {
                    inV = !closing;
                } else if (n == "is") {
                    inIs = !closing;
                } else if (n == "row" && closing) {
                    if (!row.empty()) d.text += row + "\n";
                    row.clear();
                }
            },
            [&](const std::string& t) {
                if (inV || inIs) value += xmlUnescape(t);
            });
        d.text += "\n";
    }
    return d;
}

DocumentText readPptx(const std::string& path) {
    Zip zip(path);
    DocumentText d;
    d.kind = "pptx";
    std::vector<std::pair<int, std::string>> slides;
    for (const auto& n : zip.names()) {
        if (n.rfind("ppt/slides/slide", 0) == 0 && n.size() > 20 && n.compare(n.size() - 4, 4, ".xml") == 0) {
            slides.emplace_back(std::atoi(n.c_str() + 16), n);
        }
    }
    std::sort(slides.begin(), slides.end());
    for (const auto& [num, file] : slides) {
        ++d.sections;
        std::string text;
        bool inT = false;
        walkXml(
            zip.read(file),
            [&](const std::string& tag) {
                const std::string n = tagName(tag);
                const bool closing = tag.size() > 1 && tag[1] == '/';
                if (n == "a:t") inT = !closing && tag[tag.size() - 2] != '/';
                else if (n == "a:p" && closing) text += "\n";
            },
            [&](const std::string& t) {
                if (inT) text += xmlUnescape(t);
            });
        d.text += "--- Slide " + std::to_string(num) + " ---\n" + text + "\n";
        if (text.find_first_not_of(" \n\t") == std::string::npos) d.emptySections.push_back(num);
    }
    return d;
}

// ---------------------------------------------------------------------------
// PDF
// ---------------------------------------------------------------------------

bool isWs(char c) { return c == ' ' || c == '\n' || c == '\r' || c == '\t' || c == '\f' || c == '\0'; }
bool isDelim(char c) {
    return c == '(' || c == ')' || c == '<' || c == '>' || c == '[' || c == ']' || c == '{' || c == '}' || c == '/' ||
           c == '%';
}

struct PdfObject {
    std::string dict;    ///< the object's text up to `stream` (or all of it)
    std::string stream;  ///< raw stream bytes, still encoded
    bool hasStream = false;
    std::size_t at = 0;  ///< file offset of its definition (or of its object stream)
};

class Pdf {
public:
    explicit Pdf(std::string bytes) : data_(std::move(bytes)) {
        if (data_.compare(0, 5, "%PDF-") != 0) throw std::runtime_error("not a PDF (no %PDF- header)");
        scanObjects();
        expandObjectStreams();
    }

    const PdfObject* get(int num) const {
        auto it = objects_.find(num);
        return it == objects_.end() ? nullptr : &it->second;
    }

    // Value of /Key in a dictionary text: the raw token, a ref "N G R", or a nested << >> / [ ].
    static std::string value(const std::string& dict, const std::string& key) {
        std::size_t pos = 0;
        int depth = 0;
        while (pos < dict.size()) {
            if (dict.compare(pos, 2, "<<") == 0) {
                ++depth;
                pos += 2;
                continue;
            }
            if (dict.compare(pos, 2, ">>") == 0) {
                --depth;
                pos += 2;
                continue;
            }
            if (dict[pos] == '(') {
                pos = skipString(dict, pos);
                continue;
            }
            if (dict[pos] == '/' && depth == 1 && dict.compare(pos + 1, key.size(), key) == 0) {
                const std::size_t after = pos + 1 + key.size();
                if (after < dict.size() && !isWs(dict[after]) && !isDelim(dict[after])) {
                    ++pos;
                    continue;
                }
                std::size_t v = after;
                while (v < dict.size() && isWs(dict[v])) ++v;
                return readValue(dict, v);
            }
            ++pos;
        }
        return "";
    }

    static int refNum(const std::string& v) {
        // "12 0 R" → 12
        char* end = nullptr;
        const long n = std::strtol(v.c_str(), &end, 10);
        if (end == v.c_str()) return -1;
        return (v.find('R') != std::string::npos) ? static_cast<int>(n) : -1;
    }

    static std::vector<int> refList(const std::string& v) {
        std::vector<int> out;
        if (v.empty()) return out;
        std::istringstream in(v[0] == '[' ? v.substr(1, v.size() - 2) : v);
        std::vector<std::string> tok;
        for (std::string t; in >> t;) tok.push_back(t);
        for (std::size_t i = 0; i + 2 < tok.size(); ++i) {
            if (tok[i + 2] == "R") {
                out.push_back(std::atoi(tok[i].c_str()));
                i += 2;
            }
        }
        return out;
    }

    // A dictionary value that may be inline or an indirect reference.
    std::string resolve(const std::string& v) const {
        const int r = refNum(v);
        if (r < 0) return v;
        const PdfObject* o = get(r);
        return o ? o->dict : "";
    }

    // The references in an array value, which may itself be a reference to the array.
    std::vector<int> refArray(const std::string& v) const {
        if (!v.empty() && v[0] != '[') {
            const int r = refNum(v);
            const PdfObject* o = r >= 0 ? get(r) : nullptr;
            if (o && !o->hasStream) {
                const auto b = o->dict.find_first_not_of(" \t\r\n\f");
                if (b != std::string::npos && o->dict[b] == '[') return refList(readValue(o->dict, b));
            }
        }
        return refList(v);
    }

    std::string decoded(const PdfObject& o, std::string* why) const {
        std::string filter = value(o.dict, "Filter");
        if (filter.empty()) {
            charge(o.stream.size());
            return o.stream;
        }
        // A single name or an array of them, applied in order.
        std::vector<std::string> names;
        for (std::size_t i = 0; i < filter.size(); ++i) {
            if (filter[i] != '/') continue;
            std::size_t e = i + 1;
            while (e < filter.size() && !isWs(filter[e]) && !isDelim(filter[e])) ++e;
            names.push_back(filter.substr(i + 1, e - i - 1));
            i = e - 1;
        }
        std::string data = o.stream;
        for (const auto& n : names) {
            if (n == "FlateDecode" || n == "Fl") data = inflate(data, why);
            else if (n == "ASCII85Decode" || n == "A85") data = ascii85(data);
            else if (n == "ASCIIHexDecode" || n == "AHx") data = asciiHex(data);
            else {
                if (why) *why = "stream filter /" + n + " is not supported";
                return "";
            }
            charge(data.size());
        }
        return data;
    }

    // Counts bytes handed out by decoded(); a file that repeats one stream stops here.
    void charge(std::size_t bytes) const {
        inflated_ += bytes;
        if (inflated_ > kMaxDocumentInflated) throw budgetSpent();
    }

    static std::runtime_error budgetSpent() {
        return std::runtime_error("the PDF's streams unpack to more than " +
                                  std::to_string(kMaxDocumentInflated / (1024 * 1024)) +
                                  " MB in total, more than GAIA reads from one document");
    }

    static std::string ascii85(const std::string& in) {
        std::string out;
        unsigned long tuple = 0;
        int count = 0;
        std::size_t i = 0;
        if (in.compare(0, 2, "<~") == 0) i = 2;
        for (; i < in.size(); ++i) {
            const char c = in[i];
            if (c == '~') break;
            if (isWs(c)) continue;
            if (c == 'z' && count == 0) {
                out.append(4, '\0');
                continue;
            }
            if (c < '!' || c > 'u') continue;
            tuple = tuple * 85 + static_cast<unsigned long>(c - '!');
            if (++count == 5) {
                for (int k = 3; k >= 0; --k) out.push_back(static_cast<char>((tuple >> (8 * k)) & 0xFF));
                tuple = 0;
                count = 0;
            }
        }
        if (count > 1) {
            for (int k = count; k < 5; ++k) tuple = tuple * 85 + 84;
            for (int k = 3; k > 3 - (count - 1); --k) out.push_back(static_cast<char>((tuple >> (8 * k)) & 0xFF));
        }
        return out;
    }

    static std::string asciiHex(const std::string& in) {
        std::string hex, out;
        for (char c : in) {
            if (c == '>') break;
            if (std::isxdigit(static_cast<unsigned char>(c))) hex.push_back(c);
        }
        if (hex.size() % 2) hex.push_back('0');
        for (std::size_t k = 0; k < hex.size(); k += 2) {
            out.push_back(static_cast<char>(std::strtoul(hex.substr(k, 2).c_str(), nullptr, 16)));
        }
        return out;
    }

    bool encrypted() const { return trailerHas("Encrypt"); }

    int rootPages() const {
        for (const auto& [num, o] : objects_) {
            if (typeOf(o.dict) == "Catalog") {
                const int pages = refNum(value(o.dict, "Pages"));
                if (pages >= 0) return pages;
            }
        }
        return -1;
    }

    static std::string typeOf(const std::string& dict) {
        std::string t = value(dict, "Type");
        if (!t.empty() && t[0] == '/') t = t.substr(1);
        return t;
    }

    const std::map<int, PdfObject>& objects() const { return objects_; }

private:
    static std::size_t skipString(const std::string& s, std::size_t pos) {
        int depth = 0;
        for (; pos < s.size(); ++pos) {
            if (s[pos] == '\\') {
                ++pos;
                continue;
            }
            if (s[pos] == '(') ++depth;
            if (s[pos] == ')' && --depth == 0) return pos + 1;
        }
        return pos;
    }

    static std::string readValue(const std::string& s, std::size_t v) {
        if (v >= s.size()) return "";
        if (s.compare(v, 2, "<<") == 0) {
            int depth = 0;
            std::size_t e = v;
            for (; e < s.size(); ++e) {
                if (s.compare(e, 2, "<<") == 0) {
                    ++depth;
                    ++e;
                } else if (s.compare(e, 2, ">>") == 0) {
                    --depth;
                    ++e;
                    if (depth == 0) break;
                }
            }
            return s.substr(v, e - v + 1);
        }
        if (s[v] == '[') {
            int depth = 0;
            std::size_t e = v;
            for (; e < s.size(); ++e) {
                if (s[e] == '[') ++depth;
                if (s[e] == ']' && --depth == 0) break;
            }
            return s.substr(v, e - v + 1);
        }
        if (s[v] == '(') return s.substr(v, skipString(s, v) - v);
        // A name, a number, or "N G R".
        std::size_t e = v + 1;
        while (e < s.size() && !isWs(s[e]) && !isDelim(s[e])) ++e;
        std::string tok = s.substr(v, e - v);
        if (std::isdigit(static_cast<unsigned char>(tok[0]))) {
            std::size_t p = e;
            while (p < s.size() && isWs(s[p])) ++p;
            std::size_t g = p;
            while (g < s.size() && std::isdigit(static_cast<unsigned char>(s[g]))) ++g;
            std::size_t q = g;
            while (q < s.size() && isWs(s[q])) ++q;
            if (g > p && q < s.size() && s[q] == 'R') return s.substr(v, q - v + 1);
        }
        return tok;
    }

    std::string inflate(const std::string& in, std::string* why) const {
        struct Sink {
            std::string out;
            std::size_t limit;
        } sink{"", std::min(kMaxInflated, kMaxDocumentInflated - std::min(inflated_, kMaxDocumentInflated))};
        size_t inLen = in.size();
        const int ok = tinfl_decompress_mem_to_callback(
            in.data(), &inLen,
            [](const void* buf, int len, void* user) -> int {
                auto* s = static_cast<Sink*>(user);
                if (s->out.size() + static_cast<std::size_t>(len) > s->limit) return 0;
                s->out.append(static_cast<const char*>(buf), static_cast<std::size_t>(len));
                return 1;
            },
            &sink, TINFL_FLAG_PARSE_ZLIB_HEADER);
        if (!ok) {
            const bool full = sink.out.size() + 65536 > sink.limit;
            if (full && sink.limit < kMaxInflated) throw budgetSpent();
            if (why) *why = full ? "a compressed stream unpacks past 256 MB and was skipped" : "a compressed stream is damaged";
            return "";
        }
        return std::move(sink.out);
    }

    bool trailerHas(const std::string& key) const {
        const auto t = data_.rfind("trailer");
        if (t != std::string::npos && data_.find("/" + key, t) != std::string::npos) return true;
        for (const auto& [n, o] : objects_) {
            if (typeOf(o.dict) == "XRef" && !value(o.dict, key).empty()) return true;
        }
        return false;
    }

    // Later definitions (incremental updates) win, whether direct or in an object stream.
    void define(int num, PdfObject o) {
        auto it = objects_.find(num);
        if (it == objects_.end()) objects_.emplace(num, std::move(o));
        else if (it->second.at <= o.at) it->second = std::move(o);
    }

    // End of the dictionary that opens data_[from, limit) -- just past its ">>" --
    // or npos when the object is not a dictionary or it does not close before limit.
    std::size_t dictEnd(std::size_t from, std::size_t limit) const {
        while (from < limit && isWs(data_[from])) ++from;
        if (data_.compare(from, 2, "<<") != 0) return std::string::npos;
        int depth = 0;
        for (std::size_t i = from; i < limit;) {
            const char c = data_[i];
            if (c == '(') {
                int nest = 0;
                for (; i < limit; ++i) {
                    if (data_[i] == '\\') ++i;
                    else if (data_[i] == '(') ++nest;
                    else if (data_[i] == ')' && --nest == 0) break;
                }
                ++i;
            } else if (c == '%') {
                while (i < limit && data_[i] != '\n' && data_[i] != '\r') ++i;
            } else if (c == '<' && i + 1 < limit && data_[i + 1] == '<') {
                ++depth;
                i += 2;
            } else if (c == '<') {
                while (i < limit && data_[i] != '>') ++i;
                ++i;
            } else if (c == '>' && i + 1 < limit && data_[i + 1] == '>') {
                i += 2;
                if (--depth == 0) return i;
            } else {
                ++i;
            }
        }
        return std::string::npos;
    }

    void scanObjects() {
        std::size_t pos = 0;
        std::size_t noEndstreamFrom = std::string::npos;  // a search that found none need not run again
        while ((pos = data_.find(" obj", pos)) != std::string::npos) {
            // Walk back over "N G".
            std::size_t g = pos;
            while (g > 0 && std::isdigit(static_cast<unsigned char>(data_[g - 1]))) --g;
            std::size_t sp = g;
            while (sp > 0 && data_[sp - 1] == ' ') --sp;
            std::size_t n = sp;
            while (n > 0 && std::isdigit(static_cast<unsigned char>(data_[n - 1]))) --n;
            if (g == pos || n == sp || sp == g) {
                pos += 4;
                continue;
            }
            const int num = std::atoi(data_.c_str() + n);
            const std::size_t bodyStart = pos + 4;
            const std::size_t nextEnd = data_.find("endobj", bodyStart);
            if (nextEnd == std::string::npos) break;
            PdfObject o;
            o.at = n;
            std::size_t end = nextEnd;
            // Only a "stream" keyword right after the object's dictionary starts its data.
            std::size_t s = dictEnd(bodyStart, nextEnd);
            if (s != std::string::npos) {
                while (s < nextEnd && isWs(data_[s])) ++s;
                if (data_.compare(s, 6, "stream") != 0) s = std::string::npos;
            }
            if (s == std::string::npos) {
                o.dict = data_.substr(bodyStart, nextEnd - bodyStart);
            } else {
                o.dict = data_.substr(bodyStart, s - bodyStart);
                o.hasStream = true;
                std::size_t start = s + 6;
                if (start < data_.size() && data_[start] == '\r') ++start;
                if (start < data_.size() && data_[start] == '\n') ++start;
                std::size_t dataEnd = std::string::npos, keyword = std::string::npos;
                // The declared length, when it is direct and endstream follows it.
                const std::string len = value(o.dict, "Length");
                if (!len.empty() && refNum(len) < 0) {
                    const long l = std::strtol(len.c_str(), nullptr, 10);
                    if (l >= 0 && static_cast<std::size_t>(l) <= data_.size() - start) {
                        std::size_t k = start + static_cast<std::size_t>(l);
                        while (k < data_.size() && isWs(data_[k])) ++k;
                        if (data_.compare(k, 9, "endstream") == 0) {
                            dataEnd = start + static_cast<std::size_t>(l);
                            keyword = k;
                        }
                    }
                }
                if (keyword == std::string::npos && start < noEndstreamFrom) {
                    keyword = data_.find("endstream", start);
                    if (keyword == std::string::npos) noEndstreamFrom = start;
                    dataEnd = keyword;
                }
                if (keyword == std::string::npos) {
                    o.stream = data_.substr(start, nextEnd > start ? nextEnd - start : 0);
                } else {
                    o.stream = data_.substr(start, dataEnd - start);
                    end = data_.find("endobj", keyword + 9);
                    if (end == std::string::npos) end = data_.size();
                }
            }
            define(num, std::move(o));
            pos = end + 6;
        }
    }

    void expandObjectStreams() {
        std::vector<std::pair<int, PdfObject>> extra;
        for (const auto& [num, o] : objects_) {
            if (!o.hasStream || typeOf(o.dict) != "ObjStm") continue;
            const std::string data = decoded(o, nullptr);
            const int count = std::atoi(value(o.dict, "N").c_str());
            const std::size_t first = static_cast<std::size_t>(std::atoi(value(o.dict, "First").c_str()));
            std::istringstream header(data.substr(0, std::min(first, data.size())));
            std::vector<std::pair<int, std::size_t>> entries;
            for (int i = 0; i < count; ++i) {
                int objNum = 0;
                std::size_t off = 0;
                if (!(header >> objNum >> off)) break;
                entries.emplace_back(objNum, off);
            }
            for (std::size_t i = 0; i < entries.size(); ++i) {
                const std::size_t a = first + entries[i].second;
                const std::size_t b = i + 1 < entries.size() ? first + entries[i + 1].second : data.size();
                if (a < data.size() && b >= a) {
                    extra.emplace_back(entries[i].first, PdfObject{data.substr(a, std::min(b, data.size()) - a), "", false, o.at});
                }
            }
        }
        for (auto& [num, o] : extra) define(num, std::move(o));
    }

    std::string data_;
    std::map<int, PdfObject> objects_;
    mutable std::size_t inflated_ = 0;
};

// A font's code → Unicode map, from its ToUnicode CMap.
struct FontMap {
    int codeBytes = 1;
    std::map<unsigned, std::string> map;
    bool hasMap = false;
    std::map<unsigned, double> widths;  // glyph space, 1/1000 of the font size
    double defaultWidth = 500;
};

// A ToUnicode destination: UTF-16BE code units, left-padded to whole units.
std::vector<unsigned> utf16Units(std::string hex) {
    if (hex.size() % 4) hex.insert(0, 4 - hex.size() % 4, '0');
    std::vector<unsigned> units;
    for (std::size_t i = 0; i + 4 <= hex.size(); i += 4) {
        units.push_back(static_cast<unsigned>(std::strtoul(hex.substr(i, 4).c_str(), nullptr, 16)));
    }
    return units;
}

std::string utf16UnitsToUtf8(const std::vector<unsigned>& units) {
    std::string out;
    for (std::size_t i = 0; i < units.size(); ++i) {
        unsigned u = units[i];
        if (u >= 0xD800 && u < 0xDC00 && i + 1 < units.size() && units[i + 1] >= 0xDC00 && units[i + 1] < 0xE000) {
            u = 0x10000 + ((u - 0xD800) << 10) + (units[++i] - 0xDC00);
        } else if (u >= 0xD800 && u < 0xE000) {
            u = 0xFFFD;
        }
        appendUtf8(out, u);
    }
    return out;
}

std::string utf16HexToUtf8(const std::string& hex) { return utf16UnitsToUtf8(utf16Units(hex)); }

FontMap parseCMap(const std::string& cmap) {
    FontMap f;
    f.hasMap = true;
    auto hexTokens = [](const std::string& s) {
        std::vector<std::string> out;
        std::size_t p = 0;
        while ((p = s.find('<', p)) != std::string::npos) {
            const auto e = s.find('>', p);
            if (e == std::string::npos) break;
            std::string h;
            for (std::size_t i = p + 1; i < e; ++i) {
                if (std::isxdigit(static_cast<unsigned char>(s[i]))) h.push_back(s[i]);
            }
            out.push_back(h);
            p = e + 1;
        }
        return out;
    };
    const auto cs = cmap.find("begincodespacerange");
    if (cs != std::string::npos) {
        const auto toks = hexTokens(cmap.substr(cs, cmap.find("endcodespacerange", cs) - cs));
        if (!toks.empty()) f.codeBytes = std::max<int>(1, static_cast<int>(toks[0].size() / 2));
    }
    std::size_t p = 0;
    while ((p = cmap.find("beginbfchar", p)) != std::string::npos) {
        const auto e = cmap.find("endbfchar", p);
        const auto toks = hexTokens(cmap.substr(p, e - p));
        for (std::size_t i = 0; i + 1 < toks.size(); i += 2) {
            f.map[static_cast<unsigned>(std::strtoul(toks[i].c_str(), nullptr, 16))] = utf16HexToUtf8(toks[i + 1]);
        }
        p = e;
    }
    p = 0;
    while ((p = cmap.find("beginbfrange", p)) != std::string::npos) {
        const auto e = cmap.find("endbfrange", p);
        const std::string block = cmap.substr(p + 12, e - p - 12);
        // Each line: <lo> <hi> <dst>  or  <lo> <hi> [<d1> <d2> ...]
        std::istringstream lines(block);
        for (std::string line; std::getline(lines, line);) {
            const auto toks = hexTokens(line);
            if (toks.size() < 3) continue;
            const unsigned lo = static_cast<unsigned>(std::strtoul(toks[0].c_str(), nullptr, 16));
            const unsigned hi = static_cast<unsigned>(std::strtoul(toks[1].c_str(), nullptr, 16));
            if (hi < lo || hi - lo > 65535) continue;
            if (line.find('[') != std::string::npos) {
                for (unsigned c = lo; c <= hi && 2 + (c - lo) < toks.size(); ++c) f.map[c] = utf16HexToUtf8(toks[2 + (c - lo)]);
            } else {
                // The last code unit counts up through the range; a surrogate pair stays a pair.
                std::vector<unsigned> dst = utf16Units(toks[2]);
                if (dst.empty()) continue;
                const unsigned base = dst.back();
                for (unsigned c = lo; c <= hi; ++c) {
                    dst.back() = base + (c - lo);
                    f.map[c] = utf16UnitsToUtf8(dst);
                }
            }
        }
        p = e;
    }
    return f;
}

// Form XObjects drawn while reading one page: which are open, and how many ran.
struct FormBudget {
    std::vector<int> open;
    std::size_t runs = 0;
    bool exhausted = false;  // forms past kMaxFormRuns were skipped
    bool cycle = false;      // a form drew itself, directly or through others
};

class PageText {
public:
    PageText(const Pdf& pdf, std::map<std::string, FontMap>& cache, FormBudget& forms)
        : pdf_(pdf), cache_(cache), forms_(forms) {}

    std::string run(const std::string& content, const std::string& resources, int depth = 0) {
        resources_ = pdf_.resolve(resources);
        fonts_ = pdf_.resolve(Pdf::value(resources_, "Font"));
        xobjects_ = pdf_.resolve(Pdf::value(resources_, "XObject"));
        depth_ = depth;
        tokenize(content);
        return out_;
    }

private:
    static std::vector<double> numbers(const std::string& arr) {
        std::vector<double> out;
        std::string cleaned = arr;
        for (char& c : cleaned) {
            if (c == '[' || c == ']') c = ' ';
        }
        std::istringstream in(cleaned);
        for (std::string t; in >> t;) out.push_back(std::atof(t.c_str()));
        return out;
    }

    void loadWidths(FontMap& f, const std::string& dict) {
        if (Pdf::value(dict, "Subtype") == "/Type0") {
            const auto kids = Pdf::refList(Pdf::value(dict, "DescendantFonts"));
            std::string desc = kids.empty() ? pdf_.resolve(Pdf::value(dict, "DescendantFonts")) : pdf_.resolve(std::to_string(kids[0]) + " 0 R");
            const std::string dw = Pdf::value(desc, "DW");
            f.defaultWidth = dw.empty() ? 1000 : std::atof(dw.c_str());
            // W: c [w1 w2 ...]  or  c1 c2 w
            const std::string w = pdf_.resolve(Pdf::value(desc, "W"));
            std::size_t i = w.find('[');
            if (i == std::string::npos) return;
            ++i;
            std::vector<double> pending;
            while (i < w.size()) {
                while (i < w.size() && isWs(w[i])) ++i;
                if (i >= w.size() || w[i] == ']') break;
                if (w[i] == '[') {
                    const auto e = w.find(']', i);
                    if (e == std::string::npos) break;
                    const auto ws = numbers(w.substr(i, e - i + 1));
                    if (!pending.empty()) {
                        unsigned c = static_cast<unsigned>(pending.back());
                        for (double x : ws) f.widths[c++] = x;
                    }
                    pending.clear();
                    i = e + 1;
                    continue;
                }
                std::size_t e = i;
                while (e < w.size() && !isWs(w[e]) && w[e] != '[' && w[e] != ']') ++e;
                pending.push_back(std::atof(w.substr(i, e - i).c_str()));
                i = e;
                if (pending.size() == 3) {
                    for (unsigned c = static_cast<unsigned>(pending[0]); c <= static_cast<unsigned>(pending[1]) && c - static_cast<unsigned>(pending[0]) < 65536; ++c) {
                        f.widths[c] = pending[2];
                    }
                    pending.clear();
                }
            }
            return;
        }
        const std::string first = Pdf::value(dict, "FirstChar");
        const auto ws = numbers(pdf_.resolve(Pdf::value(dict, "Widths")));
        unsigned c = static_cast<unsigned>(std::atoi(first.c_str()));
        for (double x : ws) f.widths[c++] = x;
        const std::string missing = Pdf::value(pdf_.resolve(Pdf::value(dict, "FontDescriptor")), "MissingWidth");
        if (!missing.empty()) f.defaultWidth = std::atof(missing.c_str());
    }

    const FontMap& font(const std::string& name) {
        static const FontMap none;
        const std::string ref = Pdf::value(fonts_, name);
        if (ref.empty()) return none;
        auto it = cache_.find(ref);
        if (it != cache_.end()) return it->second;
        FontMap f;
        const std::string dict = pdf_.resolve(ref);
        const int tu = Pdf::refNum(Pdf::value(dict, "ToUnicode"));
        if (const PdfObject* o = tu >= 0 ? pdf_.get(tu) : nullptr) {
            f = parseCMap(pdf_.decoded(*o, nullptr));
        } else if (Pdf::value(dict, "Subtype") == "/Type0") {
            f.codeBytes = 2;  // composite font with no map: codes are glyph ids
        }
        if (Pdf::value(dict, "Subtype") == "/Type0") f.codeBytes = std::max(f.codeBytes, 2);
        loadWidths(f, dict);
        return cache_[ref] = f;
    }

    double scale() const { return fontSize_ * (tmScale_ == 0 ? 1 : tmScale_); }

    // Before drawing at a new position: a space or a line break, if the move earns one.
    void settle() {
        if (!moved_) return;
        moved_ = false;
        if (out_.empty()) return;
        if (std::abs(y_ - drawnY_) > 0.5 * std::max(1.0, scale())) {
            newline();
        } else {
            const double gap = x_ - penX_;
            if (gap > 0.2 * std::max(1.0, scale()) || gap < -2.0 * std::max(1.0, scale())) space();
        }
    }

    void show(const std::string& bytes) {
        settle();
        const FontMap& f = font(currentFont_);
        const std::size_t step = static_cast<std::size_t>(std::max(1, f.codeBytes));
        for (std::size_t i = 0; i + step <= bytes.size(); i += step) {
            unsigned code = 0;
            for (std::size_t b = 0; b < step; ++b) code = (code << 8) | static_cast<unsigned char>(bytes[i + b]);
            if (f.hasMap) {
                auto it = f.map.find(code);
                if (it != f.map.end()) out_ += it->second;
            } else if (step == 1 && (code >= 0x20 || code == '\t')) {
                appendUtf8(out_, winAnsi(static_cast<unsigned char>(code)));
            }
            auto w = f.widths.find(code);
            x_ += (w == f.widths.end() ? f.defaultWidth : w->second) / 1000.0 * scale();
        }
        penX_ = x_;
        drawnY_ = y_;
    }

    void moveTo(double x, double y) {
        x_ = x;
        y_ = y;
        moved_ = true;
    }

    void newline() {
        if (!out_.empty() && out_.back() != '\n') out_ += "\n";
    }
    void space() {
        if (!out_.empty() && out_.back() != ' ' && out_.back() != '\n') out_ += " ";
    }

    static std::string literal(const std::string& s, std::size_t& i) {
        std::string out;
        int depth = 1;
        for (++i; i < s.size(); ++i) {
            char c = s[i];
            if (c == '\\' && i + 1 < s.size()) {
                char n = s[++i];
                switch (n) {
                    case 'n': out += '\n'; break;
                    case 'r': out += '\r'; break;
                    case 't': out += '\t'; break;
                    case 'b': out += '\b'; break;
                    case 'f': out += '\f'; break;
                    case '\r': if (i + 1 < s.size() && s[i + 1] == '\n') ++i; break;
                    case '\n': break;
                    default:
                        if (n >= '0' && n <= '7') {
                            int v = n - '0';
                            for (int k = 0; k < 2 && i + 1 < s.size() && s[i + 1] >= '0' && s[i + 1] <= '7'; ++k) v = v * 8 + (s[++i] - '0');
                            out.push_back(static_cast<char>(v));
                        } else {
                            out.push_back(n);
                        }
                }
                continue;
            }
            if (c == '(') ++depth;
            if (c == ')' && --depth == 0) {
                ++i;
                break;
            }
            out.push_back(c);
        }
        return out;
    }

    static std::string hexString(const std::string& s, std::size_t& i) {
        std::string hex;
        for (++i; i < s.size() && s[i] != '>'; ++i) {
            if (std::isxdigit(static_cast<unsigned char>(s[i]))) hex.push_back(s[i]);
        }
        ++i;
        if (hex.size() % 2) hex.push_back('0');
        std::string out;
        for (std::size_t k = 0; k < hex.size(); k += 2) out.push_back(static_cast<char>(std::strtoul(hex.substr(k, 2).c_str(), nullptr, 16)));
        return out;
    }

    void tokenize(const std::string& s) {
        std::vector<std::string> operands;  // numbers and names
        std::vector<std::string> strings;   // the string operands, decoded
        std::vector<std::pair<bool, std::string>> array;  // TJ: (isString, value)
        bool inArray = false;
        std::size_t i = 0;
        auto num = [&operands](std::size_t fromEnd) {
            return operands.size() > fromEnd ? std::atof(operands[operands.size() - 1 - fromEnd].c_str()) : 0.0;
        };
        while (i < s.size()) {
            const char c = s[i];
            if (isWs(c)) {
                ++i;
                continue;
            }
            if (c == '%') {
                while (i < s.size() && s[i] != '\n' && s[i] != '\r') ++i;
                continue;
            }
            if (c == '(') {
                std::string str = literal(s, i);
                if (inArray) array.emplace_back(true, str);
                else strings.push_back(str);
                continue;
            }
            if (c == '<' && i + 1 < s.size() && s[i + 1] != '<') {
                std::string str = hexString(s, i);
                if (inArray) array.emplace_back(true, str);
                else strings.push_back(str);
                continue;
            }
            if (c == '<' || c == '>') {  // inline dict in BDC/BI: skip
                i += 2;
                continue;
            }
            if (c == '[') {
                inArray = true;
                array.clear();
                ++i;
                continue;
            }
            if (c == ']') {
                inArray = false;
                ++i;
                continue;
            }
            std::size_t e = i + (c == '/' ? 1 : 0);
            while (e < s.size() && !isWs(s[e]) && !isDelim(s[e])) ++e;
            if (e == i) {
                ++i;
                continue;
            }
            const std::string tok = s.substr(i, e - i);
            i = e;
            const char t0 = tok[0];
            if (t0 == '/' || std::isdigit(static_cast<unsigned char>(t0)) || t0 == '-' || t0 == '+' || t0 == '.') {
                if (inArray) array.emplace_back(false, tok);
                else operands.push_back(tok);
                continue;
            }
            // An operator.
            if (tok == "BI") {  // inline image: skip to EI
                const auto ei = s.find("EI", i);
                i = ei == std::string::npos ? s.size() : ei + 2;
            } else if (tok == "BT") {
                lineX_ = lineY_ = 0;
                tmScale_ = 1;
                moveTo(0, 0);
            } else if (tok == "Tf" && !operands.empty()) {
                fontSize_ = num(0);
                for (auto it = operands.rbegin(); it != operands.rend(); ++it) {
                    if ((*it)[0] == '/') {
                        currentFont_ = it->substr(1);
                        break;
                    }
                }
            } else if (tok == "TL") {
                leading_ = num(0);
            } else if (tok == "Tj" && !strings.empty()) {
                show(strings.back());
            } else if ((tok == "'" || tok == "\"") && !strings.empty()) {
                lineY_ -= leading_ * tmScale_;
                moveTo(lineX_, lineY_);
                show(strings.back());
            } else if (tok == "TJ") {
                for (const auto& [isStr, v] : array) {
                    if (isStr) {
                        show(v);
                    } else {
                        const double adj = std::atof(v.c_str());
                        x_ -= adj / 1000.0 * scale();
                        // A large negative kern is how many writers space words.
                        if (adj < -180) space();
                        penX_ = x_;
                    }
                }
                array.clear();
            } else if (tok == "Td" || tok == "TD") {
                if (tok == "TD") leading_ = -num(0);
                lineX_ += num(1) * tmScale_;
                lineY_ += num(0) * tmScale_;
                moveTo(lineX_, lineY_);
            } else if (tok == "Tm" && operands.size() >= 6) {
                tmScale_ = std::abs(num(5)) > 0 ? std::abs(num(5)) : std::abs(num(2));
                lineX_ = num(1);
                lineY_ = num(0);
                moveTo(lineX_, lineY_);
            } else if (tok == "T*") {
                lineY_ -= leading_ * tmScale_;
                moveTo(lineX_, lineY_);
                newline();
            } else if (tok == "Do" && !operands.empty() && depth_ < 4) {
                const std::string name = operands.back().substr(1);
                const int ref = Pdf::refNum(Pdf::value(xobjects_, name));
                const PdfObject* o = ref >= 0 ? pdf_.get(ref) : nullptr;
                if (o && Pdf::value(o->dict, "Subtype") == "/Form" && o->hasStream) {
                    if (std::find(forms_.open.begin(), forms_.open.end(), ref) != forms_.open.end()) {
                        forms_.cycle = true;
                    } else if (forms_.runs >= kMaxFormRuns) {
                        forms_.exhausted = true;
                    } else {
                        ++forms_.runs;
                        forms_.open.push_back(ref);
                        PageText inner(pdf_, cache_, forms_);
                        std::string res = Pdf::value(o->dict, "Resources");
                        if (res.empty()) res = resources_;
                        const std::string text = inner.run(pdf_.decoded(*o, nullptr), res, depth_ + 1);
                        forms_.open.pop_back();
                        if (!text.empty()) {
                            newline();
                            out_ += text;
                            newline();
                        }
                    }
                }
            }
            operands.clear();
            strings.clear();
        }
    }

    const Pdf& pdf_;
    std::map<std::string, FontMap>& cache_;
    FormBudget& forms_;
    std::string resources_, fonts_, xobjects_, currentFont_;
    std::string out_;
    double fontSize_ = 12, tmScale_ = 1, leading_ = 0;
    double lineX_ = 0, lineY_ = 0, x_ = 0, y_ = 0, penX_ = 0, drawnY_ = 0;
    bool moved_ = false;
    int depth_ = 0;
};

// Leaf pages in reading order with their inherited resources. Iterative, so a deep
// or cyclic page tree cannot exhaust the stack. False when pages past kMaxPdfPages were left out.
bool collectPages(const Pdf& pdf, int root, std::vector<std::pair<int, std::string>>& pages) {
    std::vector<std::string> resources{""};
    std::vector<std::pair<int, std::size_t>> stack{{root, 0}};  // node, index into resources
    std::set<int> seen;
    while (!stack.empty()) {
        const auto [node, inherited] = stack.back();
        stack.pop_back();
        if (node < 0 || !seen.insert(node).second) continue;
        const PdfObject* o = pdf.get(node);
        if (!o) continue;
        std::string res = Pdf::value(o->dict, "Resources");
        const std::string type = Pdf::typeOf(o->dict);
        const std::string kids = Pdf::value(o->dict, "Kids");
        if (type == "Pages" || (!kids.empty() && type != "Page")) {
            std::size_t r = inherited;
            if (!res.empty()) {
                resources.push_back(std::move(res));
                r = resources.size() - 1;
            }
            const std::vector<int> refs = pdf.refArray(kids);
            for (auto it = refs.rbegin(); it != refs.rend(); ++it) stack.emplace_back(*it, r);
        } else {
            if (pages.size() >= kMaxPdfPages) return false;
            pages.emplace_back(node, res.empty() ? resources[inherited] : res);
        }
    }
    return true;
}

DocumentText readPdf(const std::string& path) {
    Pdf pdf(readFileBytes(path));
    if (pdf.encrypted()) {
        throw std::runtime_error(path + " is an encrypted PDF; its text cannot be read without the password.");
    }
    DocumentText d;
    d.kind = "pdf";
    std::vector<std::pair<int, std::string>> pages;
    if (!collectPages(pdf, pdf.rootPages(), pages)) {
        d.warnings.push_back("the PDF has more than " + std::to_string(kMaxPdfPages) + " pages; only the first " +
                             std::to_string(kMaxPdfPages) + " were read");
    }
    if (pages.empty()) {
        // No usable page tree (damaged file): read every page object in file order, and say so.
        for (const auto& [num, o] : pdf.objects()) {
            if (Pdf::typeOf(o.dict) == "Page") pages.emplace_back(num, Pdf::value(o.dict, "Resources"));
        }
        if (pages.empty()) throw std::runtime_error(path + " has no pages GAIA can read (the PDF structure is damaged).");
        d.warnings.push_back("the page tree is damaged; pages are listed in file order, which may not be reading order");
    }
    std::map<std::string, FontMap> fonts;
    std::string why;
    for (std::size_t p = 0; p < pages.size(); ++p) {
        const PdfObject* page = pdf.get(pages[p].first);
        std::string content;
        for (int ref : pdf.refArray(Pdf::value(page->dict, "Contents"))) {
            if (const PdfObject* c = pdf.get(ref)) content += pdf.decoded(*c, &why) + "\n";
        }
        FormBudget forms;
        PageText extractor(pdf, fonts, forms);
        std::string text = extractor.run(content, pages[p].second);
        const int number = static_cast<int>(p + 1);
        if (forms.exhausted) {
            d.warnings.push_back("page " + std::to_string(number) + " draws more than " + std::to_string(kMaxFormRuns) +
                                 " form XObjects; the rest of its text was not read");
        }
        if (forms.cycle) {
            d.warnings.push_back("page " + std::to_string(number) + " has a form XObject that draws itself; the repeat was skipped");
        }
        d.text += "--- Page " + std::to_string(number) + " ---\n" + text + "\n\n";
        if (text.find_first_not_of(" \n\t\r") == std::string::npos) d.emptySections.push_back(number);
    }
    d.sections = static_cast<int>(pages.size());
    if (!why.empty()) d.warnings.push_back(why);
    if (!d.emptySections.empty()) {
        d.warnings.push_back(std::to_string(d.emptySections.size()) +
                             " page(s) have no text layer (scanned images); their content cannot be read here");
    }
    return d;
}

}  // namespace

std::vector<Worksheet> readWorkbook(const std::string& path) {
    // Same parse as the text view, kept as a grid.
    const DocumentText d = readXlsx(path);
    std::vector<Worksheet> sheets;
    std::size_t gridCells = 0;
    std::istringstream in(d.text);
    for (std::string line; std::getline(in, line);) {
        if (line.rfind("--- Sheet: ", 0) == 0) {
            sheets.push_back({line.substr(11, line.size() - 15), {}});
            continue;
        }
        if (sheets.empty() || line.empty()) continue;
        std::vector<std::string> row;
        std::istringstream cells(line);
        for (std::string cell; std::getline(cells, cell, '\t');) {
            const auto eq = cell.find('=');
            if (eq == std::string::npos) continue;
            const std::size_t col = columnIndex(cell.substr(0, eq));
            if (row.size() <= col) {
                gridCells += col + 1 - row.size();
                if (gridCells > kMaxGridCells) {
                    throw std::runtime_error(path + " spans more than " + std::to_string(kMaxGridCells) +
                                             " cells (rows times their last column), more than GAIA reads as a table; "
                                             "save the range you need as CSV");
                }
                row.resize(col + 1);
            }
            row[col] = cell.substr(eq + 1);
        }
        sheets.back().rows.push_back(std::move(row));
    }
    return sheets;
}

namespace {

// One image XObject as bytes a vision model accepts: JPEG passed through, 8-bit
// gray/RGB pixels re-encoded as PNG. Anything else (predictors, CMYK, JBIG2,
// indexed colour) is skipped.
std::string pageImageBytes(const Pdf& pdf, const PdfObject& o) {
    std::string filter = Pdf::value(o.dict, "Filter");
    const bool dct = filter.find("DCTDecode") != std::string::npos || filter.find("/DCT") != std::string::npos;
    if (dct) {
        // Undo any ASCII wrappers before the JPEG itself.
        PdfObject outer = o;
        const auto at = filter.find("/DCT");
        outer.dict = "<< /Filter " + filter.substr(0, at) + (filter.front() == '[' ? "]" : "") + " >>";
        std::string why;
        std::string bytes = at <= 1 ? o.stream : pdf.decoded(outer, &why);
        if (bytes.size() < 4 || static_cast<unsigned char>(bytes[0]) != 0xFF ||
            static_cast<unsigned char>(bytes[1]) != 0xD8) {
            return "";
        }
        return bytes;
    }
    if (!Pdf::value(o.dict, "DecodeParms").empty() || Pdf::value(o.dict, "BitsPerComponent") != "8") return "";
    const std::string cs = pdf.resolve(Pdf::value(o.dict, "ColorSpace"));
    const int channels = cs == "/DeviceGray" ? 1 : cs == "/DeviceRGB" ? 3 : 0;
    const int w = std::atoi(Pdf::value(o.dict, "Width").c_str());
    const int h = std::atoi(Pdf::value(o.dict, "Height").c_str());
    if (!channels || w <= 0 || h <= 0 || w > 20000 || h > 20000) return "";
    std::string why;
    const std::string pixels = pdf.decoded(o, &why);
    if (pixels.size() < static_cast<std::size_t>(w) * h * channels) return "";
    std::size_t len = 0;
    void* png = tdefl_write_image_to_png_file_in_memory(pixels.data(), w, h, channels, &len);
    if (!png) return "";
    std::string out(static_cast<const char*>(png), len);
    mz_free(png);
    return out;
}

void collectPageImages(const Pdf& pdf, const std::string& resources, int depth, std::set<int>& visited, std::string& best) {
    if (depth > 4) return;
    const std::string xobjects = pdf.resolve(Pdf::value(pdf.resolve(resources), "XObject"));
    for (std::size_t i = xobjects.find('/'); i != std::string::npos; i = xobjects.find('/', i + 1)) {
        std::size_t e = i + 1;
        while (e < xobjects.size() && !isWs(xobjects[e]) && !isDelim(xobjects[e])) ++e;
        const int ref = Pdf::refNum(Pdf::value(xobjects, xobjects.substr(i + 1, e - i - 1)));
        const PdfObject* o = ref >= 0 ? pdf.get(ref) : nullptr;
        if (!o || !o->hasStream || !visited.insert(ref).second) continue;
        const std::string subtype = Pdf::value(o->dict, "Subtype");
        if (subtype == "/Form") {
            collectPageImages(pdf, Pdf::value(o->dict, "Resources"), depth + 1, visited, best);
        } else if (subtype == "/Image") {
            std::string bytes = pageImageBytes(pdf, *o);
            if (bytes.size() > best.size()) best = std::move(bytes);
        }
    }
}

}  // namespace

std::map<int, std::string> extractPageImages(const std::string& path, const std::vector<int>& wanted) {
    std::map<int, std::string> out;
    Pdf pdf(readFileBytes(path));
    std::vector<std::pair<int, std::string>> pages;
    collectPages(pdf, pdf.rootPages(), pages);
    for (int n : wanted) {
        if (n < 1 || static_cast<std::size_t>(n) > pages.size()) continue;
        std::string best;
        std::set<int> visited;
        collectPageImages(pdf, pages[static_cast<std::size_t>(n - 1)].second, 0, visited, best);
        if (!best.empty()) out[n] = std::move(best);
    }
    return out;
}

bool isDocumentExtension(const std::string& extension) {
    const std::string e = lower(extension);
    return e == ".pdf" || e == ".docx" || e == ".xlsx" || e == ".pptx";
}

DocumentText extractDocumentText(const std::string& path) {
    const std::string ext = lower(fs::u8path(path).extension().u8string());
    try {
        if (ext == ".pdf") return readPdf(path);
        if (ext == ".docx") return readDocx(path);
        if (ext == ".xlsx") return readXlsx(path);
        if (ext == ".pptx") return readPptx(path);
    } catch (const std::runtime_error& e) {
        const std::string msg = e.what();
        throw std::runtime_error(msg.find(path) == std::string::npos ? path + ": " + msg : msg);
    }
    throw std::runtime_error(path + " is not a document type GAIA extracts text from (pdf, docx, xlsx, pptx).");
}

}  // namespace gaia
