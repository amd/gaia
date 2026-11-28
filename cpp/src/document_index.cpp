// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/document_index.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <random>
#include <regex>
#include <set>
#include <sstream>
#include <string_view>

#include "gaia/ignore.h"
#include "gaia/process.h"
#include "lower_ascii.h"
#include "shell_quote.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

constexpr double kRrfK = 60.0;
constexpr double kBm25K1 = 1.2;
constexpr double kBm25B = 0.75;
constexpr size_t kMaxExtractBytes = 64u * 1024u * 1024u;

std::string lowerExt(const std::string& path) { return detail::lowerAscii(fs::path(path).extension().string()); }

#ifdef _WIN32
constexpr bool kShellExpandsPercent = true;
#else
constexpr bool kShellExpandsPercent = false;
#endif

// cmd /c expands %NAME% even inside quotes, and a Windows file name may hold a %.
// Such a file is read through a temporary copy whose name has none.
class ShellSafePath {
public:
    explicit ShellSafePath(const std::string& path) : path_(path) {
        if (!kShellExpandsPercent || path.find('%') == std::string::npos) return;
        std::error_code ec;
        const fs::path dir = fs::temp_directory_path(ec);
        if (ec || dir.string().find('%') != std::string::npos) {
            throw std::runtime_error("Can't read " + path +
                                     ": its name contains %, and there is no temporary folder without one "
                                     "to copy it to. Rename the file without % and try again.");
        }
        std::mt19937_64 rng(std::random_device{}());
        copy_ = dir / ("gaia-doc-" + std::to_string(rng()) + lowerExt(path));
        fs::copy_file(fs::path(path), copy_, fs::copy_options::overwrite_existing, ec);
        if (ec) {
            copy_.clear();
            throw std::runtime_error("Can't read " + path + ": its name contains %, and copying it to " +
                                     dir.string() + " failed (" + ec.message() +
                                     "). Rename the file without % and try again.");
        }
        path_ = copy_.string();
    }
    ~ShellSafePath() {
        if (copy_.empty()) return;
        std::error_code ec;
        fs::remove(copy_, ec);
    }
    ShellSafePath(const ShellSafePath&) = delete;
    ShellSafePath& operator=(const ShellSafePath&) = delete;
    const std::string& str() const { return path_; }

private:
    std::string path_;
    fs::path copy_;
};

#ifdef __APPLE__
constexpr const char* kPdfKitScript = R"JS(ObjC.import('PDFKit');
function run(argv) {
  var doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));
  if (doc.isNil()) throw new Error('not a readable PDF');
  if (doc.isLocked) throw new Error('the PDF is password-protected');
  var out = $.NSFileHandle.fileHandleWithStandardOutput;
  for (var i = 0; i < doc.pageCount; i++) {
    var s = doc.pageAtIndex(i).string;
    out.writeData($((s.isNil() ? '' : s.js) + '\f').dataUsingEncoding($.NSUTF8StringEncoding));
  }
})JS";
#endif

void appendUtf8(std::string& out, unsigned cp) {
    if (cp == 0 || cp > 0x10FFFFu || (cp >= 0xD800u && cp <= 0xDFFFu)) return;
    if (cp < 0x80u) {
        out.push_back(static_cast<char>(cp));
    } else if (cp < 0x800u) {
        out.push_back(static_cast<char>(0xC0u | (cp >> 6)));
        out.push_back(static_cast<char>(0x80u | (cp & 0x3Fu)));
    } else if (cp < 0x10000u) {
        out.push_back(static_cast<char>(0xE0u | (cp >> 12)));
        out.push_back(static_cast<char>(0x80u | ((cp >> 6) & 0x3Fu)));
        out.push_back(static_cast<char>(0x80u | (cp & 0x3Fu)));
    } else {
        out.push_back(static_cast<char>(0xF0u | (cp >> 18)));
        out.push_back(static_cast<char>(0x80u | ((cp >> 12) & 0x3Fu)));
        out.push_back(static_cast<char>(0x80u | ((cp >> 6) & 0x3Fu)));
        out.push_back(static_cast<char>(0x80u | (cp & 0x3Fu)));
    }
}

std::string decodeXmlEntities(std::string_view s) {
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size();) {
        if (s[i] != '&') {
            out.push_back(s[i++]);
            continue;
        }
        const size_t limit = std::min(s.size(), i + 16);
        const size_t semi = s.find(';', i + 1);
        if (semi == std::string_view::npos || semi >= limit) {
            out.push_back(s[i++]);
            continue;
        }
        const std::string_view ent = s.substr(i + 1, semi - i - 1);
        if (ent == "lt") {
            out.push_back('<');
        } else if (ent == "gt") {
            out.push_back('>');
        } else if (ent == "quot") {
            out.push_back('"');
        } else if (ent == "apos") {
            out.push_back('\'');
        } else if (ent == "amp") {
            out.push_back('&');
        } else if (ent.size() >= 2 && ent[0] == '#') {
            unsigned cp = 0;
            bool ok = false;
            if (ent.size() >= 3 && (ent[1] == 'x' || ent[1] == 'X')) {
                ok = true;
                for (size_t k = 2; k < ent.size(); ++k) {
                    const char ch = ent[k];
                    unsigned d = 0;
                    if (ch >= '0' && ch <= '9') d = static_cast<unsigned>(ch - '0');
                    else if (ch >= 'a' && ch <= 'f') d = static_cast<unsigned>(10 + ch - 'a');
                    else if (ch >= 'A' && ch <= 'F') d = static_cast<unsigned>(10 + ch - 'A');
                    else { ok = false; break; }
                    if (cp > 0x10FFFFu) { ok = false; break; }
                    cp = (cp << 4) | d;
                }
            } else {
                ok = true;
                for (size_t k = 1; k < ent.size(); ++k) {
                    const char ch = ent[k];
                    if (ch < '0' || ch > '9') { ok = false; break; }
                    if (cp > 0x10FFFFu) { ok = false; break; }
                    cp = cp * 10u + static_cast<unsigned>(ch - '0');
                }
            }
            if (ok && cp > 0 && cp <= 0x10FFFFu && !(cp >= 0xD800u && cp <= 0xDFFFu)) {
                appendUtf8(out, cp);
            } else {
                out.push_back(s[i++]);
                continue;
            }
        } else {
            out.push_back(s[i++]);
            continue;
        }
        i = semi + 1;
    }
    return out;
}

constexpr size_t kMaxZipEntries = 4096;
constexpr size_t kMaxSpreadsheetColumns = 256;

std::string trimAscii(std::string_view s) {
    size_t start = 0;
    while (start < s.size() && (s[start] == ' ' || s[start] == '\t' || s[start] == '\r' || s[start] == '\n')) {
        ++start;
    }
    size_t end = s.size();
    while (end > start && (s[end - 1] == ' ' || s[end - 1] == '\t' || s[end - 1] == '\r' || s[end - 1] == '\n')) {
        --end;
    }
    return std::string(s.substr(start, end - start));
}

bool looksEncryptedError(const std::string& stderrText) {
    const std::string low = detail::lowerAscii(stderrText);
    return low.find("password") != std::string::npos || low.find("encrypted") != std::string::npos;
}

void checkOfficeZipHeader(const std::string& path, const std::string& format, const std::string& ext) {
    std::ifstream in(fs::u8path(path), std::ios::binary);
    char hdr[8] = {};
    in.read(hdr, sizeof(hdr));
    const std::streamsize n = in.gcount();
    static constexpr unsigned char kOle2[8] = {0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1};
    const std::string name = fs::path(path).filename().u8string();
    if (n == 8 && std::memcmp(hdr, kOle2, 8) == 0) {
        throw std::runtime_error("Can't read " + format + " file " + name +
                                 ": it is password-protected or in legacy binary format. Save an unprotected copy as " +
                                 ext + " and try again.");
    }
    const bool zipMagic = n >= 4 && hdr[0] == 'P' && hdr[1] == 'K' &&
                          ((hdr[2] == 0x03 && hdr[3] == 0x04) || (hdr[2] == 0x05 && hdr[3] == 0x06));
    if (!zipMagic) {
        throw std::runtime_error("Can't read " + format + " file " + name + ": it is not a valid " + ext + " file.");
    }
}

class ZipArchive {
public:
    ZipArchive(const std::string& path, std::string format, std::string ext)
        : name_(fs::path(path).filename().u8string()),
          safe_(path),
          format_(std::move(format)),
          ext_(std::move(ext)) {
        checkOfficeZipHeader(path, format_, ext_);
#ifdef _WIN32
        const std::string tar = systemProgramPath("tar.exe");
        const std::string listCmd = "\"\"" + tar + "\" -tf " + detail::shellQuote(safe_.str()) + "\"";
        const char* tool = "tar";
#else
        const std::string listCmd = "unzip -Z1 " + detail::shellQuote(safe_.str());
        const char* tool = "unzip";
#endif
        ProcessResult r = ProcessRunner::run(listCmd, 60000, "", {}, 4u * 1024u * 1024u);
        if (r.exitCode != 0) {
            const bool missing = r.exitCode == 127 || r.exitCode == 9009;
            if (missing) {
                throw std::runtime_error("Can't read " + format_ + " file " + name_ + ": " + std::string(tool) +
                                         " is not installed");
            }
            if (looksEncryptedError(r.stderr_output)) {
                throw std::runtime_error("Can't read " + format_ + " file " + name_ +
                                         ": it is password-protected. Save an unprotected copy as " + ext_ +
                                         " and try again.");
            }
            throw std::runtime_error("Can't read " + format_ + " file " + name_ + ": it is not a valid " + ext_ +
                                     " (" + trimAscii(r.stderr_output.substr(0, 200)) + ")");
        }
        std::istringstream in(r.stdout_output);
        for (std::string line; std::getline(in, line);) {
            if (!line.empty() && line.back() == '\r') line.pop_back();
            if (line.empty()) continue;
            if (entries_.size() >= kMaxZipEntries) {
                throw std::runtime_error("Can't read " + format_ + " file " + name_ +
                                         ": archive has too many entries (limit " + std::to_string(kMaxZipEntries) +
                                         ").");
            }
            entries_.push_back(line);
            entryLookup_.emplace(detail::lowerAscii(line), line);
        }
    }

    const std::vector<std::string>& entries() const { return entries_; }

    std::string resolve(const std::string& target) const {
        if (std::find(entries_.begin(), entries_.end(), target) != entries_.end()) return target;
        auto it = entryLookup_.find(detail::lowerAscii(target));
        return it == entryLookup_.end() ? "" : it->second;
    }

    std::string readEntry(const std::string& entry) {
        if (entry.empty() || entry[0] == '-' || entry.find('\0') != std::string::npos ||
            entry.find('\n') != std::string::npos || entry.find('\r') != std::string::npos ||
            entry.find("..") != std::string::npos) {
            throw std::runtime_error("Can't read " + format_ + " file " + name_ + ": invalid archive entry path.");
        }
        if (totalExtracted_ >= kMaxExtractBytes) {
            throw std::runtime_error("Can't read " + format_ + " file " + name_ + ": decompressed content exceeds " +
                                     std::to_string(kMaxExtractBytes >> 20) + " MB.");
        }
        const size_t remaining = kMaxExtractBytes - totalExtracted_;
#ifdef _WIN32
        const std::string tar = systemProgramPath("tar.exe");
        const std::string cmd = "\"\"" + tar + "\" -xOf " + detail::shellQuote(safe_.str()) + " " +
                                detail::shellQuote(entry) + "\"";
        const char* tool = "tar";
#else
        // -P '' prevents unzip from blocking on a password prompt if an entry is encrypted.
        const std::string cmd = "unzip -P '' -p " + detail::shellQuote(safe_.str()) + " " + detail::shellQuote(entry);
        const char* tool = "unzip";
#endif
        ProcessResult r = ProcessRunner::run(cmd, 60000, "", {}, remaining);
        if (r.stdout_output.size() >= remaining) {
            throw std::runtime_error("Can't read " + format_ + " file " + name_ + ": decompressed content exceeds " +
                                     std::to_string(kMaxExtractBytes >> 20) + " MB.");
        }
        if (r.exitCode != 0) {
            const bool missing = r.exitCode == 127 || r.exitCode == 9009;
            if (missing) {
                throw std::runtime_error("Can't read " + format_ + " file " + name_ + ": " + std::string(tool) +
                                         " is not installed");
            }
            if (looksEncryptedError(r.stderr_output)) {
                throw std::runtime_error("Can't read " + format_ + " file " + name_ +
                                         ": it is password-protected. Save an unprotected copy as " + ext_ +
                                         " and try again.");
            }
            throw std::runtime_error("Can't read " + format_ + " file " + name_ + ": it is not a valid " + ext_ +
                                     " (" + trimAscii(r.stderr_output.substr(0, 200)) + ")");
        }
        totalExtracted_ += r.stdout_output.size();
        return r.stdout_output;
    }

private:
    std::string name_;
    ShellSafePath safe_;
    std::string format_;
    std::string ext_;
    std::vector<std::string> entries_;
    std::map<std::string, std::string> entryLookup_;
    size_t totalExtracted_ = 0;
};

std::string_view xmlLocalName(std::string_view name) {
    const size_t colon = name.find(':');
    return colon == std::string_view::npos ? name : name.substr(colon + 1);
}

bool isXmlSpace(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }

bool isXmlNameChar(char c) {
    const unsigned char u = static_cast<unsigned char>(c);
    return std::isalnum(u) || c == '_' || c == '-' || c == ':' || c == '.';
}

struct XmlTag {
    bool closing = false;
    bool selfClosing = false;
    std::string_view fullName;
    std::string_view local;
    std::string_view attrs;
    size_t start = 0;
    size_t end = 0;  // index after '>'
};

bool nextXmlTag(std::string_view xml, size_t pos, XmlTag& out) {
    while (pos < xml.size()) {
        const size_t lt = xml.find('<', pos);
        if (lt == std::string_view::npos) return false;
        if (lt + 1 >= xml.size()) return false;
        if (xml.compare(lt, 4, "<!--") == 0) {
            const size_t end = xml.find("-->", lt + 4);
            if (end == std::string_view::npos) return false;
            pos = end + 3;
            continue;
        }
        if (xml.compare(lt, 9, "<![CDATA[") == 0) {
            const size_t end = xml.find("]]>", lt + 9);
            if (end == std::string_view::npos) return false;
            pos = end + 3;
            continue;
        }
        if (xml[lt + 1] == '?' || xml[lt + 1] == '!') {
            const size_t gt = xml.find('>', lt + 2);
            if (gt == std::string_view::npos) return false;
            pos = gt + 1;
            continue;
        }
        size_t gt = lt + 1;
        char quote = 0;
        while (gt < xml.size()) {
            const char c = xml[gt];
            if (quote != 0) {
                if (c == quote) quote = 0;
            } else if (c == '"' || c == '\'') {
                quote = c;
            } else if (c == '>') {
                break;
            }
            ++gt;
        }
        if (gt >= xml.size()) return false;
        size_t p = lt + 1;
        const bool closing = xml[p] == '/';
        if (closing) ++p;
        while (p < gt && isXmlSpace(xml[p])) ++p;
        const size_t nameStart = p;
        while (p < gt && isXmlNameChar(xml[p])) ++p;
        const std::string_view fullName = xml.substr(nameStart, p - nameStart);
        size_t attrEnd = gt;
        while (attrEnd > p && isXmlSpace(xml[attrEnd - 1])) --attrEnd;
        const bool selfClosing = !closing && attrEnd > p && xml[attrEnd - 1] == '/';
        if (selfClosing) --attrEnd;

        out.closing = closing;
        out.selfClosing = selfClosing;
        out.fullName = fullName;
        out.local = xmlLocalName(fullName);
        out.attrs = xml.substr(p, attrEnd - p);
        out.start = lt;
        out.end = gt + 1;
        return true;
    }
    return false;
}

std::string extractXmlAttr(std::string_view attrs, std::string_view attrName) {
    size_t i = 0;
    while (i < attrs.size()) {
        while (i < attrs.size() && isXmlSpace(attrs[i])) ++i;
        const size_t nameStart = i;
        while (i < attrs.size() && isXmlNameChar(attrs[i])) ++i;
        const std::string_view name = attrs.substr(nameStart, i - nameStart);
        while (i < attrs.size() && isXmlSpace(attrs[i])) ++i;
        if (i >= attrs.size() || attrs[i] != '=') {
            if (i == nameStart) ++i;
            continue;
        }
        ++i;
        while (i < attrs.size() && isXmlSpace(attrs[i])) ++i;
        if (i >= attrs.size() || (attrs[i] != '"' && attrs[i] != '\'')) continue;
        const char q = attrs[i++];
        const size_t valStart = i;
        const size_t valEnd = attrs.find(q, valStart);
        if (valEnd == std::string_view::npos) break;
        i = valEnd + 1;
        if (name == attrName) {
            return decodeXmlEntities(attrs.substr(valStart, valEnd - valStart));
        }
    }
    return "";
}

// Finds the closing tag matching an already-opened element with local name `localName`.
// Returns the byte offset of the matching closing tag's `<`, and sets `afterClose` past `>`.
size_t findMatchingClose(std::string_view xml, size_t bodyStart, std::string_view localName, size_t& afterClose) {
    int depth = 1;
    size_t pos = bodyStart;
    XmlTag tag;
    while (nextXmlTag(xml, pos, tag)) {
        pos = tag.end;
        if (tag.local != localName) continue;
        if (tag.closing) {
            if (--depth == 0) {
                afterClose = tag.end;
                return tag.start;
            }
        } else if (!tag.selfClosing) {
            ++depth;
        }
    }
    afterClose = xml.size();
    return std::string_view::npos;
}

std::string normalizeZipTarget(const std::string& baseDir, std::string target) {
    for (char& c : target) {
        if (c == '\\') c = '/';
    }
    std::string combined;
    if (!target.empty() && target[0] == '/') {
        combined = target.substr(1);
    } else {
        combined = baseDir + target;
    }
    std::vector<std::string> segs;
    size_t i = 0;
    while (i <= combined.size()) {
        size_t slash = combined.find('/', i);
        std::string part = combined.substr(i, slash == std::string::npos ? std::string::npos : slash - i);
        if (part == "..") {
            if (!segs.empty()) segs.pop_back();
        } else if (!part.empty() && part != ".") {
            segs.push_back(std::move(part));
        }
        if (slash == std::string::npos) break;
        i = slash + 1;
    }
    std::string out;
    for (size_t k = 0; k < segs.size(); ++k) {
        if (k > 0) out += '/';
        out += segs[k];
    }
    return out;
}

struct OoxmlRel {
    std::string id;
    std::string type;
    std::string target;
};

std::vector<OoxmlRel> parseRelationships(std::string_view xml, const std::string& baseDir) {
    std::vector<OoxmlRel> rels;
    XmlTag tag;
    size_t pos = 0;
    while (nextXmlTag(xml, pos, tag)) {
        pos = tag.end;
        if (tag.closing || tag.local != "Relationship") continue;
        if (detail::lowerAscii(extractXmlAttr(tag.attrs, "TargetMode")) == "external") continue;
        OoxmlRel r;
        r.id = extractXmlAttr(tag.attrs, "Id");
        r.type = extractXmlAttr(tag.attrs, "Type");
        const std::string rawTarget = extractXmlAttr(tag.attrs, "Target");
        if (!rawTarget.empty()) r.target = normalizeZipTarget(baseDir, rawTarget);
        if (!r.id.empty() && !r.target.empty()) rels.push_back(std::move(r));
    }
    return rels;
}

bool parseUnsignedDecimal(std::string_view digits, size_t maxVal, size_t& out) {
    if (digits.empty()) return false;
    size_t val = 0;
    for (char c : digits) {
        if (c < '0' || c > '9') return false;
        const size_t d = static_cast<size_t>(c - '0');
        if (val > (maxVal - d) / 10) return false;
        val = val * 10 + d;
    }
    out = val;
    return true;
}

bool parseNumberedEntry(const std::string& entry, const std::string& prefix, const std::string& suffix, int& number) {
    const std::string low = detail::lowerAscii(entry);
    if (low.size() <= prefix.size() + suffix.size()) return false;
    if (low.compare(0, prefix.size(), prefix) != 0) return false;
    if (low.compare(low.size() - suffix.size(), suffix.size(), suffix) != 0) return false;
    const std::string_view digits =
        std::string_view(low).substr(prefix.size(), low.size() - prefix.size() - suffix.size());
    size_t parsed = 0;
    if (!parseUnsignedDecimal(digits, 1000000u, parsed)) return false;
    number = static_cast<int>(parsed);
    return true;
}

std::string collectTElements(std::string_view xml, bool skipPhonetic, bool skipSlideNumField, bool handleBreaks) {
    std::string out;
    XmlTag tag;
    size_t pos = 0;
    while (nextXmlTag(xml, pos, tag)) {
        pos = tag.end;
        if (tag.closing) continue;
        if (skipPhonetic && tag.local == "rPh" && !tag.selfClosing) {
            size_t after = pos;
            findMatchingClose(xml, pos, "rPh", after);
            pos = after;
            continue;
        }
        if (skipSlideNumField && tag.local == "fld" && !tag.selfClosing) {
            if (detail::lowerAscii(extractXmlAttr(tag.attrs, "type")) == "slidenum") {
                size_t after = pos;
                findMatchingClose(xml, pos, "fld", after);
                pos = after;
                continue;
            }
        }
        if (handleBreaks && tag.local == "tab") {
            out.push_back('\t');
            continue;
        }
        if (handleBreaks && tag.local == "br") {
            out.push_back('\n');
            continue;
        }
        if (tag.local == "t" && !tag.selfClosing) {
            size_t after = pos;
            const size_t closeStart = findMatchingClose(xml, pos, "t", after);
            if (closeStart == std::string_view::npos) break;
            out += decodeXmlEntities(xml.substr(pos, closeStart - pos));
            pos = after;
        }
    }
    return out;
}

std::vector<std::string> parseSharedStrings(std::string_view xml) {
    std::vector<std::string> strings;
    XmlTag tag;
    size_t pos = 0;
    while (nextXmlTag(xml, pos, tag)) {
        pos = tag.end;
        if (tag.closing || tag.local != "si") continue;
        if (tag.selfClosing) {
            strings.emplace_back();
            continue;
        }
        size_t after = pos;
        const size_t closeStart = findMatchingClose(xml, pos, "si", after);
        if (closeStart == std::string_view::npos) break;
        strings.push_back(collectTElements(xml.substr(pos, closeStart - pos), true, false, false));
        pos = after;
    }
    return strings;
}

int parseCellColumn(const std::string& cellRef) {
    int col = 0;
    bool any = false;
    for (unsigned char c : cellRef) {
        if (c >= 'A' && c <= 'Z') {
            col = col * 26 + (c - 'A' + 1);
            any = true;
        } else if (c >= 'a' && c <= 'z') {
            col = col * 26 + (c - 'a' + 1);
            any = true;
        } else {
            break;
        }
        if (col > 16384) return -1;
    }
    return any ? (col - 1) : -1;
}

std::string extractFirstV(std::string_view cellBody) {
    XmlTag tag;
    size_t pos = 0;
    while (nextXmlTag(cellBody, pos, tag)) {
        pos = tag.end;
        if (tag.closing || tag.local != "v" || tag.selfClosing) continue;
        size_t after = pos;
        const size_t closeStart = findMatchingClose(cellBody, pos, "v", after);
        if (closeStart == std::string_view::npos) return "";
        return decodeXmlEntities(cellBody.substr(pos, closeStart - pos));
    }
    return "";
}

std::vector<std::vector<std::string>> parseWorksheetRows(std::string_view xml,
                                                         const std::vector<std::string>& sharedStrings) {
    std::vector<std::vector<std::string>> rows;
    XmlTag tag;
    size_t pos = 0;
    while (nextXmlTag(xml, pos, tag)) {
        pos = tag.end;
        if (tag.closing || tag.local != "row" || tag.selfClosing) continue;
        size_t afterRow = pos;
        const size_t closeRow = findMatchingClose(xml, pos, "row", afterRow);
        if (closeRow == std::string_view::npos) break;
        const std::string_view rowBody = xml.substr(pos, closeRow - pos);
        pos = afterRow;

        std::vector<std::string> cells;
        bool hasAnyText = false;
        size_t cPos = 0;
        XmlTag cTag;
        while (nextXmlTag(rowBody, cPos, cTag)) {
            cPos = cTag.end;
            if (cTag.closing || cTag.local != "c") continue;
            const int parsedCol = parseCellColumn(extractXmlAttr(cTag.attrs, "r"));
            const std::string cellType = extractXmlAttr(cTag.attrs, "t");
            std::string_view cellBody;
            if (!cTag.selfClosing) {
                size_t afterCell = cPos;
                const size_t closeCell = findMatchingClose(rowBody, cPos, "c", afterCell);
                if (closeCell == std::string_view::npos) break;
                cellBody = rowBody.substr(cPos, closeCell - cPos);
                cPos = afterCell;
            }

            std::string value;
            if (cellType == "inlineStr") {
                value = collectTElements(cellBody, true, false, false);
            } else if (cellType == "s") {
                const std::string idxText = trimAscii(extractFirstV(cellBody));
                size_t idx = 0;
                if (parseUnsignedDecimal(idxText, sharedStrings.size(), idx) && idx < sharedStrings.size()) {
                    value = sharedStrings[idx];
                }
            } else if (cellType == "b") {
                const std::string raw = trimAscii(extractFirstV(cellBody));
                if (raw == "1") value = "TRUE";
                else if (raw == "0") value = "FALSE";
                else value = raw;
            } else {
                value = extractFirstV(cellBody);
            }

            if (!trimAscii(value).empty()) hasAnyText = true;
            if (parsedCol >= 0 && static_cast<size_t>(parsedCol) < kMaxSpreadsheetColumns) {
                if (cells.size() <= static_cast<size_t>(parsedCol)) {
                    cells.resize(static_cast<size_t>(parsedCol) + 1);
                }
                cells[static_cast<size_t>(parsedCol)] = std::move(value);
            } else {
                cells.push_back(std::move(value));
            }
        }
        if (hasAnyText) rows.push_back(std::move(cells));
    }
    return rows;
}

bool isNumericCell(const std::string& cell) {
    const std::string t = trimAscii(cell);
    if (t.empty()) return false;
    bool digit = false;
    for (unsigned char c : t) {
        if (std::isdigit(c)) {
            digit = true;
        } else if (c != '.' && c != '-' && c != '+' && c != ',' && c != '%' && c != '$') {
            return false;
        }
    }
    return digit;
}

std::string formatSheetText(const std::string& sheetName, std::vector<std::vector<std::string>> rows) {
    if (rows.empty()) return "";
    std::vector<std::string> lines;
    lines.push_back("Sheet: " + sheetName);

    auto nonEmptyCount = [](const std::vector<std::string>& row) {
        size_t n = 0;
        for (const auto& c : row) {
            if (!trimAscii(c).empty()) ++n;
        }
        return n;
    };

    size_t firstWide = 0;
    while (firstWide < rows.size() && nonEmptyCount(rows[firstWide]) <= 1) ++firstWide;
    if (firstWide > 0 && firstWide + 1 < rows.size()) {
        bool headerHasNum = false;
        for (const auto& c : rows[firstWide]) {
            if (isNumericCell(c)) headerHasNum = true;
        }
        bool nextHasNum = false;
        for (const auto& c : rows[firstWide + 1]) {
            if (isNumericCell(c)) nextHasNum = true;
        }
        if (!headerHasNum && nextHasNum) {
            for (size_t i = 0; i < firstWide; ++i) {
                std::string title;
                for (const auto& c : rows[i]) {
                    const std::string t = trimAscii(c);
                    if (!t.empty()) title += (title.empty() ? "" : " ") + t;
                }
                if (!title.empty()) lines.push_back(std::move(title));
            }
            rows.erase(rows.begin(), rows.begin() + static_cast<std::ptrdiff_t>(firstWide));
        }
    }

    const auto& header = rows[0];
    bool hasHeader = false;
    for (const auto& c : header) {
        if (!trimAscii(c).empty() && !isNumericCell(c)) {
            hasHeader = true;
            break;
        }
    }

    if (hasHeader && rows.size() > 1) {
        std::string cols;
        for (const auto& c : header) {
            const std::string t = trimAscii(c);
            if (!t.empty()) cols += (cols.empty() ? "" : ", ") + t;
        }
        if (!cols.empty()) lines.push_back("Columns: " + cols);
        for (size_t r = 1; r < rows.size(); ++r) {
            std::string rowLine;
            for (size_t c = 0; c < rows[r].size(); ++c) {
                const std::string val = trimAscii(rows[r][c]);
                if (val.empty()) continue;
                std::string colName = c < header.size() ? trimAscii(header[c]) : "";
                if (colName.empty()) colName = "Col" + std::to_string(c + 1);
                rowLine += (rowLine.empty() ? "" : " | ") + colName + ": " + val;
            }
            if (!rowLine.empty()) lines.push_back(std::move(rowLine));
        }
    } else {
        for (const auto& row : rows) {
            std::string rowLine;
            for (const auto& c : row) {
                const std::string val = trimAscii(c);
                if (!val.empty()) rowLine += (rowLine.empty() ? "" : " | ") + val;
            }
            if (!rowLine.empty()) lines.push_back(std::move(rowLine));
        }
    }

    std::string out;
    for (size_t i = 0; i < lines.size(); ++i) {
        if (i > 0) out += '\n';
        out += lines[i];
    }
    return out;
}

std::vector<std::pair<int, std::string>> extractXlsxPages(const std::string& path) {
    ZipArchive zip(path, "Excel", ".xlsx");
    std::vector<std::string> sharedStrings;
    const std::string ssEntry = zip.resolve("xl/sharedStrings.xml");
    if (!ssEntry.empty()) sharedStrings = parseSharedStrings(zip.readEntry(ssEntry));

    struct SheetSpec {
        std::string name;
        std::string entry;
    };
    std::vector<SheetSpec> sheets;

    const std::string wbEntry = zip.resolve("xl/workbook.xml");
    const std::string wbRelsEntry = zip.resolve("xl/_rels/workbook.xml.rels");
    if (!wbEntry.empty() && !wbRelsEntry.empty()) {
        std::map<std::string, std::string> relMap;
        for (const auto& r : parseRelationships(zip.readEntry(wbRelsEntry), "xl/")) {
            relMap[r.id] = r.target;
        }
        const std::string wbXml = zip.readEntry(wbEntry);
        XmlTag tag;
        size_t pos = 0;
        while (nextXmlTag(wbXml, pos, tag)) {
            pos = tag.end;
            if (tag.closing || tag.local != "sheet") continue;
            const std::string name = extractXmlAttr(tag.attrs, "name");
            std::string rId = extractXmlAttr(tag.attrs, "r:id");
            if (rId.empty()) rId = extractXmlAttr(tag.attrs, "id");
            auto it = relMap.find(rId);
            if (it == relMap.end()) continue;
            const std::string resolved = zip.resolve(it->second);
            if (!resolved.empty()) {
                sheets.push_back({name.empty() ? ("Sheet" + std::to_string(sheets.size() + 1)) : name, resolved});
            }
        }
    }

    if (sheets.empty()) {
        std::vector<std::pair<int, std::string>> numbered;
        for (const auto& entry : zip.entries()) {
            int num = 0;
            if (parseNumberedEntry(entry, "xl/worksheets/sheet", ".xml", num)) {
                numbered.emplace_back(num, entry);
            }
        }
        std::sort(numbered.begin(), numbered.end());
        for (const auto& [num, entry] : numbered) {
            sheets.push_back({"Sheet" + std::to_string(num), entry});
        }
    }

    const std::string fileName = fs::path(path).filename().u8string();
    if (sheets.empty()) {
        throw std::runtime_error("Can't read Excel file " + fileName + ": it is not a valid .xlsx workbook.");
    }

    std::vector<std::pair<int, std::string>> pages;
    for (const auto& sheet : sheets) {
        auto rows = parseWorksheetRows(zip.readEntry(sheet.entry), sharedStrings);
        const std::string text = formatSheetText(sheet.name, std::move(rows));
        if (!text.empty()) pages.emplace_back(0, text);
    }
    if (pages.empty()) {
        throw std::runtime_error("Can't index " + fileName + ": Excel workbook has no cell values.");
    }
    return pages;
}

std::string extractPptxXmlText(std::string_view xml, bool skipSlideNumField) {
    std::vector<std::string> lines;
    XmlTag tag;
    size_t pos = 0;
    while (nextXmlTag(xml, pos, tag)) {
        pos = tag.end;
        if (tag.closing || tag.selfClosing) continue;
        if (tag.local == "tbl") {
            size_t afterTbl = pos;
            const size_t closeTbl = findMatchingClose(xml, pos, "tbl", afterTbl);
            if (closeTbl == std::string_view::npos) break;
            const std::string_view tblBody = xml.substr(pos, closeTbl - pos);
            pos = afterTbl;

            size_t trPos = 0;
            XmlTag trTag;
            while (nextXmlTag(tblBody, trPos, trTag)) {
                trPos = trTag.end;
                if (trTag.closing || trTag.local != "tr" || trTag.selfClosing) continue;
                size_t afterTr = trPos;
                const size_t closeTr = findMatchingClose(tblBody, trPos, "tr", afterTr);
                if (closeTr == std::string_view::npos) break;
                const std::string_view trBody = tblBody.substr(trPos, closeTr - trPos);
                trPos = afterTr;

                std::string rowLine;
                size_t tcPos = 0;
                XmlTag tcTag;
                while (nextXmlTag(trBody, tcPos, tcTag)) {
                    tcPos = tcTag.end;
                    if (tcTag.closing || tcTag.local != "tc" || tcTag.selfClosing) continue;
                    size_t afterTc = tcPos;
                    const size_t closeTc = findMatchingClose(trBody, tcPos, "tc", afterTc);
                    if (closeTc == std::string_view::npos) break;
                    const std::string_view tcBody = trBody.substr(tcPos, closeTc - tcPos);
                    tcPos = afterTc;

                    std::string cellText;
                    size_t pPos = 0;
                    XmlTag pTag;
                    while (nextXmlTag(tcBody, pPos, pTag)) {
                        pPos = pTag.end;
                        if (pTag.closing || pTag.local != "p" || pTag.selfClosing) continue;
                        size_t afterP = pPos;
                        const size_t closeP = findMatchingClose(tcBody, pPos, "p", afterP);
                        if (closeP == std::string_view::npos) break;
                        const std::string para =
                            trimAscii(collectTElements(tcBody.substr(pPos, closeP - pPos), false, skipSlideNumField, true));
                        if (!para.empty()) cellText += (cellText.empty() ? "" : " ") + para;
                        pPos = afterP;
                    }
                    if (!cellText.empty()) rowLine += (rowLine.empty() ? "" : " | ") + cellText;
                }
                if (!rowLine.empty()) lines.push_back(std::move(rowLine));
            }
            continue;
        }
        if (tag.local == "p") {
            size_t afterP = pos;
            const size_t closeP = findMatchingClose(xml, pos, "p", afterP);
            if (closeP == std::string_view::npos) break;
            const std::string para =
                trimAscii(collectTElements(xml.substr(pos, closeP - pos), false, skipSlideNumField, true));
            if (!para.empty()) lines.push_back(para);
            pos = afterP;
        }
    }
    std::string out;
    for (size_t i = 0; i < lines.size(); ++i) {
        if (i > 0) out += '\n';
        out += lines[i];
    }
    return out;
}

std::vector<std::pair<int, std::string>> extractPptxPages(const std::string& path) {
    ZipArchive zip(path, "PowerPoint", ".pptx");
    std::vector<std::string> slides;

    const std::string presEntry = zip.resolve("ppt/presentation.xml");
    const std::string presRelsEntry = zip.resolve("ppt/_rels/presentation.xml.rels");
    if (!presEntry.empty() && !presRelsEntry.empty()) {
        std::map<std::string, std::string> relMap;
        for (const auto& r : parseRelationships(zip.readEntry(presRelsEntry), "ppt/")) {
            relMap[r.id] = r.target;
        }
        const std::string presXml = zip.readEntry(presEntry);
        XmlTag tag;
        size_t pos = 0;
        while (nextXmlTag(presXml, pos, tag)) {
            pos = tag.end;
            if (tag.closing || tag.local != "sldId") continue;
            std::string rId = extractXmlAttr(tag.attrs, "r:id");
            if (rId.empty()) rId = extractXmlAttr(tag.attrs, "id");
            auto it = relMap.find(rId);
            if (it == relMap.end()) continue;
            const std::string resolved = zip.resolve(it->second);
            if (!resolved.empty()) slides.push_back(resolved);
        }
    }

    if (slides.empty()) {
        std::vector<std::pair<int, std::string>> numbered;
        for (const auto& entry : zip.entries()) {
            int num = 0;
            if (parseNumberedEntry(entry, "ppt/slides/slide", ".xml", num)) {
                numbered.emplace_back(num, entry);
            }
        }
        std::sort(numbered.begin(), numbered.end());
        for (const auto& [_, entry] : numbered) slides.push_back(entry);
    }

    const std::string fileName = fs::path(path).filename().u8string();
    if (slides.empty()) {
        throw std::runtime_error("Can't read PowerPoint file " + fileName +
                                 ": it is not a valid .pptx presentation.");
    }

    std::vector<std::pair<int, std::string>> pages;
    for (size_t i = 0; i < slides.size(); ++i) {
        const int slideNum = static_cast<int>(i + 1);
        const std::string& slideEntry = slides[i];
        std::string body = extractPptxXmlText(zip.readEntry(slideEntry), false);

        std::string notesEntry;
        const size_t slash = slideEntry.rfind('/');
        const std::string slideDir = slash == std::string::npos ? "" : slideEntry.substr(0, slash + 1);
        const std::string slideFile = slash == std::string::npos ? slideEntry : slideEntry.substr(slash + 1);
        const std::string slideRels = zip.resolve(slideDir + "_rels/" + slideFile + ".rels");
        if (!slideRels.empty()) {
            for (const auto& r : parseRelationships(zip.readEntry(slideRels), slideDir)) {
                if (detail::lowerAscii(r.type).find("notesslide") != std::string::npos ||
                    detail::lowerAscii(r.target).find("notesslide") != std::string::npos) {
                    notesEntry = zip.resolve(r.target);
                    if (!notesEntry.empty()) break;
                }
            }
        } else {
            // Only fall back to filename numbering when the slide has no .rels file at all.
            int fileNum = 0;
            if (parseNumberedEntry(slideEntry, "ppt/slides/slide", ".xml", fileNum)) {
                notesEntry = zip.resolve("ppt/notesSlides/notesSlide" + std::to_string(fileNum) + ".xml");
            }
        }
        if (!notesEntry.empty()) {
            const std::string notes = extractPptxXmlText(zip.readEntry(notesEntry), true);
            if (!notes.empty()) {
                body += (body.empty() ? "" : "\n\n") + std::string("Speaker Notes:\n") + notes;
            }
        }
        if (!body.empty()) {
            pages.emplace_back(slideNum, "Slide " + std::to_string(slideNum) + "\n" + body);
        }
    }

    if (pages.empty()) {
        throw std::runtime_error("Can't index " + fileName +
                                 ": PowerPoint presentation has no extractable text.");
    }
    return pages;
}

// Paragraph text of a .docx: <w:t> runs, <w:p> ends become newlines.
std::string docxText(const std::string& path) {
    const ShellSafePath safe(path);
#ifdef _WIN32
    // Windows ships no unzip; its own tar reads zip archives. Named in full: a GNU tar earlier on
    // PATH (Git's) does not read them, and takes C: for a remote host.
    const std::string tar = systemProgramPath("tar.exe");
    // cmd /c drops the first and last quote of a line that starts with one, so the line gets an extra pair.
    const std::string extract = "\"\"" + tar + "\" -xOf " + detail::shellQuote(safe.str()) + " word/document.xml\"";
    const char* tool = "tar";
#else
    const std::string extract = "unzip -P '' -p " + detail::shellQuote(safe.str()) + " word/document.xml";
    const char* tool = "unzip";
#endif
    ProcessResult r = ProcessRunner::run(extract, 60000, "", {}, kMaxExtractBytes);
    if (r.exitCode != 0) {
        // 127 from a POSIX shell and 9009 from cmd.exe both mean the program was not found.
        const bool missing = r.exitCode == 127 || r.exitCode == 9009;
        throw std::runtime_error("Can't read Word file " + path + ": " +
                                 (missing ? std::string(tool) + " is not installed"
                                                    : "it is not a valid .docx (" +
                                                          r.stderr_output.substr(0, 200) + ")"));
    }
    const std::string& xml = r.stdout_output;
    std::string out;
    size_t i = 0;
    while (i < xml.size()) {
        if (xml.compare(i, 6, "</w:p>") == 0) {
            out += '\n';
            i += 6;
        } else if (xml.compare(i, 4, "<w:t") == 0 &&
                   (xml[i + 4] == '>' || xml[i + 4] == ' ')) {
            const size_t open = xml.find('>', i);
            const size_t close = xml.find("</w:t>", open);
            if (open == std::string::npos || close == std::string::npos) break;
            out += xml.substr(open + 1, close - open - 1);
            i = close + 6;
        } else if (xml.compare(i, 7, "<w:tab/") == 0) {
            out += '\t';
            i += 7;
        } else {
            ++i;
        }
    }
    return decodeXmlEntities(out);
}

constexpr std::uintmax_t kMaxTextBytes = std::uintmax_t{1} << 20;

struct SkippedFiles {
    size_t ignored = 0;
    size_t inSkippedFolder = 0;
    size_t tooLarge = 0;
    std::set<std::string> folders;

    size_t total() const { return ignored + inSkippedFolder + tooLarge; }

    std::string summary() const {
        std::vector<std::string> parts;
        if (ignored) parts.push_back(std::to_string(ignored) + " ignored by .gitignore");
        if (inSkippedFolder) {
            std::string names;
            for (const auto& f : folders) names += (names.empty() ? "" : ", ") + f;
            parts.push_back(std::to_string(inSkippedFolder) + " in a skipped folder (" + names + ")");
        }
        if (tooLarge) {
            parts.push_back(std::to_string(tooLarge) + " too large (text over 1 MB, documents over " +
                            std::to_string(kMaxExtractBytes >> 20) + " MB)");
        }
        std::string out;
        for (const auto& part : parts) out += (out.empty() ? "" : ", ") + part;
        return out;
    }

    json toJson() const {
        json folderNames = json::array();
        for (const auto& f : folders) folderNames.push_back(f);
        return {{"ignored_by_gitignore", ignored}, {"in_skipped_folder", inSkippedFolder},
                {"too_large", tooLarge}, {"skipped_folders", folderNames}};
    }
};

bool indexableExtension(const fs::path& p, bool withDocuments, bool* document = nullptr) {
    const std::string ext = lowerExt(p.string());
    const bool doc = withDocuments && (ext == ".pdf" || ext == ".docx" || ext == ".xlsx" || ext == ".pptx");
    if (document) *document = doc;
    return doc || isSupportedExtension(ext);
}

size_t countIndexableUnder(const fs::path& dir, bool withDocuments) {
    std::error_code ec;
    size_t n = 0;
    fs::recursive_directory_iterator it(dir, fs::directory_options::skip_permission_denied, ec), end;
    for (; it != end && !ec; it.increment(ec)) {
        if (it->is_regular_file(ec) && indexableExtension(it->path(), withDocuments)) ++n;
    }
    return n;
}

// A folder link (a symlink, or a Windows junction) is never walked into, wherever it leads;
// any other folder only when it resolves under @p rootReal.
bool walkInto(const fs::directory_entry& entry, const fs::path& rootReal) {
    std::error_code ec;
    const fs::file_status st = entry.symlink_status(ec);
    if (ec || fs::is_symlink(st)) return false;
#ifdef _MSC_VER
    if (st.type() == fs::file_type::junction) return false;
#endif
    if (rootReal.empty()) return false;
    const fs::path real = fs::canonical(entry.path(), ec);
    if (ec) return false;
    const fs::path rel = real.lexically_relative(rootReal);
    return !rel.empty() && *rel.begin() != fs::path("..");
}

// Files worth indexing under root: skips VCS/build folders, ignored paths, unreadable folders and
// folder links.
std::vector<std::string> indexableFiles(const std::string& root, bool withDocuments,
                                        SkippedFiles* skipped = nullptr) {
    static const std::set<std::string> skipDirs = {
        ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", "build", "dist",
        "target", ".tox", ".mypy_cache", ".pytest_cache", ".next", ".gradle", "out"};
    const GitignoreMatcher ignore = GitignoreMatcher::forDirectory(root);
    std::error_code ec;
    std::vector<std::string> files;
    std::error_code rootEc;
    fs::path rootReal = fs::canonical(root, rootEc);
    if (rootEc) rootReal.clear();
    fs::recursive_directory_iterator it(root, fs::directory_options::skip_permission_denied, ec), end;
    for (; it != end && !ec; it.increment(ec)) {
        const fs::path& p = it->path();
        const bool dir = it->is_directory(ec);
        if (dir && !walkInto(*it, rootReal)) {
            it.disable_recursion_pending();
            continue;
        }
        const bool skipDir = dir && skipDirs.count(p.filename().string()) != 0;
        if (skipDir || (dir && ignore.isIgnored(p.u8string(), true))) {
            it.disable_recursion_pending();
            if (skipped) {
                const size_t n = countIndexableUnder(p, withDocuments);
                (skipDir ? skipped->inSkippedFolder : skipped->ignored) += n;
                if (skipDir && n != 0) skipped->folders.insert(p.filename().string());
            }
            continue;
        }
        if (dir || !it->is_regular_file(ec)) continue;
        // A link can lead out of the folder the user granted.
        if (it->is_symlink(ec)) continue;
        bool document = false;
        if (!indexableExtension(p, withDocuments, &document)) continue;
        if (ignore.isIgnored(p.u8string(), false)) {
            if (skipped) ++skipped->ignored;
            continue;
        }
        if (it->file_size(ec) > (document ? std::uintmax_t{kMaxExtractBytes} : kMaxTextBytes)) {
            if (skipped) ++skipped->tooLarge;
            continue;
        }
        files.push_back(p.string());
    }
    return files;
}

const char* unsupportedDocumentFormat(const std::string& ext) {
    static const std::map<std::string, const char*> kFormats = {
        {".xlsm", "Excel"},          {".xlsb", "Excel"},
        {".xls", "Excel"},           {".pptm", "PowerPoint"},
        {".ppt", "PowerPoint"},      {".doc", "legacy Word"},
        {".ods", "OpenDocument spreadsheet"},
        {".odp", "OpenDocument presentation"},
        {".odt", "OpenDocument text"},
        {".epub", "EPUB"},           {".pages", "Pages"},
        {".numbers", "Numbers"},     {".key", "Keynote"},
    };
    auto it = kFormats.find(ext);
    return it == kFormats.end() ? nullptr : it->second;
}

}  // namespace

std::string MissingDocumentReader::remedy(const std::string& file, const std::string& program) {
    return "Can't read PDF " + file + ": this agent reads PDFs with " + program +
           ", which is not installed on this PC. Install poppler (it provides " + program +
#ifdef _WIN32
           ") and attach the file again, or switch to "
#else
           "; on Debian or Ubuntu: sudo apt install poppler-utils) and attach the file again, or switch to "
#endif
           "Full-featured (gaiapy), which reads PDFs itself (Settings > Agent & model in the GAIA app, "
           "--agent-program gaiapy in the terminal).";
}

MissingDocumentReader::MissingDocumentReader(const std::string& path, std::string program_)
    : std::runtime_error(remedy(path, program_) +
                         " Tell the user exactly this; other ways of reading the PDF will not work here."),
      program(std::move(program_)) {}

DocumentIndex::DocumentIndex(EmbedFunction embed, std::string embeddingModel,
                             ChunkingConfig chunking)
    : embed_(std::move(embed)), embeddingModel_(std::move(embeddingModel)), chunking_(chunking) {
    if (embed_) {
        VectorIndexOptions o;
        o.metric = Metric::InnerProduct;
        o.normalizeOnAdd = true;
        o.embeddingModel = embeddingModel_;
        vectors_ = std::make_unique<VectorIndex>(o);
    }
}

std::vector<std::pair<int, std::string>> DocumentIndex::extractPages(const std::string& path) {
    std::error_code ec;
    if (!fs::is_regular_file(path, ec)) throw std::runtime_error("No such file: " + path);
    const std::string ext = lowerExt(path);
    std::vector<std::pair<int, std::string>> pages;
    if (ext == ".pdf") {
        const ShellSafePath safe(path);
#ifdef _WIN32
        // By full path: cmd.exe would run a pdftotext.bat in the working folder first.
        const std::string pdftotext = findProgramOnPath("pdftotext");
        if (pdftotext.empty()) throw MissingDocumentReader(path, "pdftotext");
        // cmd /c drops the first and last quote of a line that starts with one, so the line gets an extra pair.
        const std::string extract = "\"\"" + pdftotext + "\" -layout " + detail::shellQuote(safe.str()) + " -\"";
#else
        const std::string extract = "pdftotext -layout " + detail::shellQuote(safe.str()) + " -";
#endif
        ProcessResult r = ProcessRunner::run(extract, 120000, "", {}, kMaxExtractBytes);
        const bool noPdftotext = r.exitCode == 127;
#ifdef __APPLE__
        // No poppler on a stock Mac: the system's own PDF reader, one form feed after each page.
        if (noPdftotext) {
            r = ProcessRunner::run("/usr/bin/osascript -l JavaScript -e " + detail::shellQuote(kPdfKitScript) + " " +
                                       detail::shellQuote(path),
                                   120000, "", {}, kMaxExtractBytes);
        }
#else
        if (noPdftotext) throw MissingDocumentReader(path, "pdftotext");
#endif
        if (r.stdout_output.size() >= kMaxExtractBytes) {
            throw std::runtime_error("PDF " + path + " has more than " +
                                     std::to_string(kMaxExtractBytes >> 20) +
                                     " MB of text; split it before indexing.");
        }
        if (r.exitCode != 0) {
            throw std::runtime_error("Can't read PDF " + path + ": " + (noPdftotext ? "macOS" : "pdftotext") +
                                     " failed: " + r.stderr_output.substr(0, 300));
        }
        size_t start = 0;
        int page = 1;
        const std::string t = tidyPdfText(r.stdout_output);
        while (start <= t.size()) {
            size_t ff = t.find('\f', start);
            const std::string body = t.substr(start, ff == std::string::npos ? std::string::npos : ff - start);
            if (body.find_first_not_of(" \t\r\n") != std::string::npos) pages.emplace_back(page, body);
            if (ff == std::string::npos) break;
            start = ff + 1;
            ++page;
        }
        if (pages.empty()) {
            throw std::runtime_error("PDF " + path +
                                     " has no text layer (scanned pages?). Read it with an OCR "
                                     "or vision tool instead.");
        }
        return pages;
    }
    if (ext == ".docx") {
        pages.emplace_back(0, docxText(path));
        return pages;
    }
    if (ext == ".xlsx") {
        return extractXlsxPages(path);
    }
    if (ext == ".pptx") {
        return extractPptxPages(path);
    }
    // A reader the app registered for one of these formats still wins.
    if (const char* format = isSupportedExtension(ext) ? nullptr : unsupportedDocumentFormat(ext)) {
        const std::string name = fs::path(path).filename().u8string();
        throw std::runtime_error(
            "Can't index " + name + ": " + format + " (" + ext +
            ") files aren't supported for automatic indexing. Supported formats are PDF, Word (.docx), "
            "Excel (.xlsx), PowerPoint (.pptx), and plain text or code files.");
    }
    try {
        ExtractedText e = isSupportedExtension(ext) ? extractFile(path) : extractPlainTextFile(path);
        pages.emplace_back(0, e.text);
        return pages;
    } catch (const std::runtime_error& e) {
        // Chunking's generic binary error names the C++ SDK hook; keep user-facing tool output plain.
        if (std::string(e.what()).find("registerExtractor") != std::string::npos) {
            const std::string name = fs::path(path).filename().u8string();
            throw std::runtime_error(
                "Can't index " + name + ": binary files aren't supported for automatic indexing. "
                "Supported formats are PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), "
                "and plain text or code files.");
        }
        throw;
    }
}

namespace {

// Dashes, curly quotes, no-break spaces and the like split words, as their
// ASCII look-alikes do: "Moody’s" and "B–42" must match a typed "Moody's", "B-42".
bool unicodePunctuation(const std::string& s, size_t i) {
    const auto b = [&](size_t k) { return i + k < s.size() ? static_cast<unsigned char>(s[i + k]) : 0u; };
    const unsigned c0 = b(0);
    if (c0 == 0xC2) return b(1) >= 0xA0 && b(1) <= 0xBF;               // U+00A0-00BF
    if (c0 == 0xE2) return b(1) == 0x80 || b(1) == 0x81 ||              // U+2000-207F
                           (b(1) == 0x88 && b(2) == 0x92);              // U+2212 minus
    if (c0 == 0xE3) return b(1) == 0x80;                                // U+3000-303F
    if (c0 == 0xEF) return b(1) == 0xBF && b(2) == 0xBD;                // U+FFFD
    return false;
}

bool numericWord(const std::string& w) {
    bool digit = false;
    for (unsigned char c : w) {
        if (std::isdigit(c)) {
            digit = true;
        } else if (!std::strchr(".,-%()$", c) && c < 0x80) {
            return false;
        }
    }
    return digit;
}

std::vector<std::string> wordsIn(const std::string& line) {
    std::vector<std::string> words;
    std::istringstream in(line);
    for (std::string w; in >> w;) words.push_back(w);
    return words;
}

constexpr size_t kMaxTableHeading = 800;

// A table's title and column names: the lines above its first row of numbers,
// on a page that is mostly numbers. Empty for prose.
std::string tableHeading(const std::string& page) {
    size_t words = 0, numbers = 0;
    for (const auto& w : wordsIn(page)) {
        ++words;
        if (numericWord(w)) ++numbers;
    }
    if (words == 0 || numbers * 2 < words) return "";
    std::istringstream in(page);
    std::string heading;
    for (std::string line; std::getline(in, line);) {
        const auto lineWords = wordsIn(line);
        size_t n = 0;
        for (const auto& w : lineWords) n += numericWord(w) ? 1 : 0;
        if (lineWords.size() >= 3 && n * 5 >= lineWords.size() * 3) break;
        for (const auto& w : lineWords) heading += (heading.empty() ? "" : " ") + w;
        if (heading.size() > kMaxTableHeading) return "";
    }
    return heading;
}

}  // namespace

std::string DocumentIndex::tidyPdfText(const std::string& text) {
    std::string out;
    out.reserve(text.size());
    bool junk = false;
    for (size_t i = 0; i < text.size(); ++i) {
        const unsigned char c = static_cast<unsigned char>(text[i]);
        const bool replacement = text.compare(i, 3, "\xEF\xBF\xBD") == 0;
        const bool control = c < 0x20 && c != '\n' && c != '\t' && c != '\f' && c != '\r';
        if (replacement || control) {
            if (replacement) i += 2;
            if (!junk && !out.empty() && out.back() != ' ' && out.back() != '\n') out.push_back(' ');
            junk = true;
            continue;
        }
        if (junk && c == ' ' && !out.empty() && out.back() == ' ') continue;
        junk = false;
        out.push_back(static_cast<char>(c));
    }
    return out;
}

std::vector<std::string> DocumentIndex::tokens(const std::string& text) const {
    std::vector<std::string> out;
    std::string cur;
    for (size_t i = 0; i < text.size(); ++i) {
        const unsigned char c = static_cast<unsigned char>(text[i]);
        const size_t len = c < 0x80 ? 1 : c >= 0xF0 ? 4 : c >= 0xE0 ? 3 : c >= 0xC0 ? 2 : 1;
        if (std::isalnum(c) || (c >= 0x80 && !unicodePunctuation(text, i))) {
            if (c < 0x80) {
                cur.push_back(static_cast<char>(std::tolower(c)));
            } else {
                cur.append(text, i, len);
                i += len - 1;
            }
            continue;
        }
        if (c >= 0x80) i += len - 1;
        if (!cur.empty()) {
            out.push_back(std::move(cur));
            cur.clear();
        }
    }
    if (!cur.empty()) out.push_back(std::move(cur));
    return out;
}

void DocumentIndex::removeFile(const std::string& path) {
    const std::string file = fs::absolute(path).lexically_normal().string();
    std::lock_guard<std::mutex> lock(mutex_);
    removeFileLocked(file);
}

void DocumentIndex::removeFileLocked(const std::string& file) {
    std::vector<DocumentChunk> chunks;
    std::vector<std::map<std::string, int>> tf;
    std::vector<size_t> lens;
    for (size_t i = 0; i < chunks_.size(); ++i) {
        if (chunks_[i].file == file) {
            for (const auto& [term, _] : termFreq_[i]) {
                if (--docFreq_[term] <= 0) docFreq_.erase(term);
            }
            if (vectors_) vectors_->remove(std::to_string(i));
            continue;
        }
        chunks.push_back(std::move(chunks_[i]));
        tf.push_back(std::move(termFreq_[i]));
        lens.push_back(lengths_[i]);
    }
    if (vectors_ && chunks.size() != chunks_.size()) {
        // Ids are positions; rebuild them after removal.
        std::vector<std::vector<float>> keep;
        for (size_t i = 0; i < chunks_.size(); ++i) {
            if (vectors_->contains(std::to_string(i))) keep.push_back(vectors_->get(std::to_string(i)));
        }
        vectors_->clear();
        for (size_t i = 0; i < keep.size(); ++i) vectors_->add(std::to_string(i), keep[i]);
    }
    chunks_ = std::move(chunks);
    termFreq_ = std::move(tf);
    lengths_ = std::move(lens);
}

size_t DocumentIndex::indexFile(const std::string& path) {
    const std::string file = fs::absolute(path).lexically_normal().string();
    std::vector<DocumentChunk> fresh;
    for (const auto& [page, text] : extractPages(file)) {
        auto pieces = splitTextIntoChunks(text, chunking_);
        // A row cut off from its column names can't be found by them.
        const std::string heading = pieces.size() > 1 ? tableHeading(text) : "";
        for (size_t j = 0; j < pieces.size(); ++j) {
            std::string c = j == 0 || heading.empty() ? std::move(pieces[j]) : heading + "\n" + pieces[j];
            fresh.push_back({file, page, std::move(c)});
        }
    }
    std::vector<std::vector<float>> vecs;
    if (embed_ && !fresh.empty()) {
        std::vector<std::string> texts;
        for (const auto& c : fresh) texts.push_back(c.text);
        vecs = embed_(texts);
        if (vecs.size() != fresh.size()) {
            throw std::runtime_error("Embedder returned " + std::to_string(vecs.size()) +
                                     " vectors for " + std::to_string(fresh.size()) + " chunks");
        }
    }
    std::lock_guard<std::mutex> lock(mutex_);
    // Refuse a bad embedding while the file's previous chunks are still indexed.
    const size_t dimension =
        vectors_ && vectors_->dimension() != 0 ? vectors_->dimension()
                                               : (vecs.empty() ? 0 : vecs[0].size());
    for (size_t j = 0; j < vecs.size(); ++j) {
        const auto& v = vecs[j];
        const bool finite = std::all_of(v.begin(), v.end(), [](float x) { return std::isfinite(x); });
        const bool zero = std::all_of(v.begin(), v.end(), [](float x) { return x == 0.0f; });
        if (v.empty() || v.size() != dimension || !finite || zero) {
            throw std::runtime_error(
                "Embedder returned an unusable vector for chunk " + std::to_string(j + 1) + " of " +
                file + " (" + std::to_string(v.size()) + " values, expected " +
                std::to_string(dimension) +
                " finite values that are not all zero); the file was not re-indexed. Check that "
                "the embedding model" +
                (embeddingModel_.empty() ? std::string() : " " + embeddingModel_) +
                " is loaded and is the one this index was built with.");
        }
    }
    removeFileLocked(file);
    for (size_t j = 0; j < fresh.size(); ++j) {
        std::map<std::string, int> tf;
        const auto toks = tokens(fresh[j].text);
        for (const auto& t : toks) ++tf[t];
        for (const auto& [t, _] : tf) ++docFreq_[t];
        if (vectors_) vectors_->add(std::to_string(chunks_.size()), vecs[j]);
        termFreq_.push_back(std::move(tf));
        lengths_.push_back(toks.size());
        chunks_.push_back(std::move(fresh[j]));
    }
    return fresh.size();
}

size_t DocumentIndex::indexDirectory(const std::string& root, std::vector<std::string>* failures) {
    size_t total = 0;
    for (const auto& file : indexableFiles(root, false)) {
        try {
            total += indexFile(file);
        } catch (const std::exception& e) {
            if (failures) failures->push_back(file + ": " + e.what());
        }
    }
    return total;
}

std::vector<DocumentHit> DocumentIndex::query(const std::string& q, size_t k,
                                              const std::string& fileFilter) const {
    std::vector<float> qvec;
    if (embed_) qvec = embed_({q}).at(0);  // outside the lock: a network call

    std::lock_guard<std::mutex> lock(mutex_);
    if (chunks_.empty()) return {};
    auto allowed = [&](size_t i) {
        return fileFilter.empty() || chunks_[i].file.find(fileFilter) != std::string::npos;
    };

    // BM25 ranking.
    const auto qt = tokens(q);
    double avgLen = 0;
    for (auto l : lengths_) avgLen += static_cast<double>(l);
    avgLen = std::max(1.0, avgLen / static_cast<double>(lengths_.size()));
    const double n = static_cast<double>(chunks_.size());
    std::vector<std::pair<double, size_t>> bm25;
    for (size_t i = 0; i < chunks_.size(); ++i) {
        if (!allowed(i)) continue;
        double s = 0;
        for (const auto& t : std::set<std::string>(qt.begin(), qt.end())) {
            auto tf = termFreq_[i].find(t);
            if (tf == termFreq_[i].end()) continue;
            const double df = static_cast<double>(docFreq_.at(t));
            const double idf = std::log(1.0 + (n - df + 0.5) / (df + 0.5));
            const double f = tf->second;
            s += idf * f * (kBm25K1 + 1) /
                 (f + kBm25K1 * (1 - kBm25B + kBm25B * static_cast<double>(lengths_[i]) / avgLen));
        }
        if (s > 0) bm25.emplace_back(s, i);
    }
    std::sort(bm25.rbegin(), bm25.rend());

    std::map<size_t, double> fused;
    const size_t depth = std::max<size_t>(k * 4, 20);
    for (size_t r = 0; r < bm25.size() && r < depth; ++r) fused[bm25[r].second] += 1.0 / (kRrfK + r + 1);
    if (vectors_ && !qvec.empty()) {
        size_t rank = 0;
        for (const auto& [id, _] : vectors_->search(qvec, std::min(chunks_.size(), depth * 3))) {
            const size_t i = std::stoul(id);
            if (!allowed(i)) continue;
            fused[i] += 1.0 / (kRrfK + rank + 1);
            if (++rank >= depth) break;
        }
    }
    std::vector<DocumentHit> hits;
    for (const auto& [i, s] : fused) hits.push_back({chunks_[i], s});
    std::sort(hits.begin(), hits.end(),
              [](const DocumentHit& a, const DocumentHit& b) { return a.score > b.score; });
    if (hits.size() > k) hits.resize(k);
    return hits;
}

std::map<std::string, size_t> DocumentIndex::files() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::map<std::string, size_t> out;
    for (const auto& c : chunks_) ++out[c.file];
    return out;
}

void DocumentIndex::registerTools(ToolRegistry& registry,
                                  std::function<std::string(const std::string&)> checkPath) {
    const std::string mode = usesEmbeddings() ? "hybrid (keyword + " + embeddingModel_ + ")"
                                              : "keyword only (no embedding model configured)";

    ToolInfo index;
    index.name = "index_document";
    index.description =
        "Index a document (PDF, Word .docx, Excel .xlsx, PowerPoint .pptx, text, Markdown, code) "
        "or every supported file in a folder so query_documents can find passages in it, with page "
        "numbers for PDFs and slide numbers for PowerPoint.";
    index.parameters = {{"path", ToolParamType::STRING, true, "File or folder to index."}};
    index.policy = ToolPolicy::ALLOW;
    index.callback = [this, checkPath, mode](const json& args) -> json {
        const std::string path = args.value("path", "");
        if (checkPath) {
            const std::string why = checkPath(path);
            if (!why.empty()) return {{"status", "error"}, {"error", why}};
        }
        std::error_code ec;
        std::vector<std::string> targets;
        SkippedFiles skipped;
        if (fs::is_directory(path, ec)) {
            targets = indexableFiles(path, true, &skipped);
        } else {
            targets.push_back(path);
        }
        json done = json::array(), failed = json::array();
        size_t total = 0;
        for (const auto& t : targets) {
            // Each file too: a folder inside the granted one can be a junction to elsewhere.
            if (checkPath && t != path) {
                const std::string why = checkPath(t);
                if (!why.empty()) {
                    failed.push_back({{"file", t}, {"error", why}});
                    continue;
                }
            }
            try {
                const size_t n = indexFile(t);
                total += n;
                done.push_back({{"file", t}, {"chunks", n}});
            } catch (const MissingDocumentReader& e) {
                failed.push_back({{"file", t}, {"error", e.what()}, {"missing_program", e.program}});
            } catch (const std::exception& e) {
                failed.push_back({{"file", t}, {"error", e.what()}});
            }
        }
        json out = {{"status", done.empty() ? "error" : "success"}, {"indexed", done},
                    {"chunks", total}, {"retrieval", mode}};
        if (!failed.empty()) out["failed"] = failed;
        if (skipped.total() != 0) {
            out["skipped_files"] = skipped.total();
            out["skipped"] = skipped.toJson();
        }
        if (done.empty()) {
            // One file: its own reason, which says what the user can do.
            std::string error = targets.empty()      ? "No supported files were indexed at " + path
                                : failed.size() == 1 ? failed[0].value("error", "")
                                                     : "Nothing could be indexed (see failed)";
            while (!error.empty() && error.back() == '.') error.pop_back();
            if (skipped.total() != 0) {
                error += ": " + std::to_string(skipped.total()) + " skipped - " + skipped.summary() +
                         ". To index one of them, call index_document with that file's own path";
            }
            out["error"] = error + ".";
        }
        return out;
    };
    registry.registerTool(std::move(index));

    ToolInfo query;
    query.name = "query_documents";
    query.description =
        "Find the passages in indexed documents that answer a question. Returns text with file "
        "and page. Index the documents first. Quote and cite what it returns; if nothing "
        "relevant comes back, say the documents don't contain it.";
    query.parameters = {
        {"query", ToolParamType::STRING, true, "The question or key terms."},
        {"top_k", ToolParamType::INTEGER, false, "Passages to return (default 5, max 20)."},
        {"file", ToolParamType::STRING, false, "Only search files whose path contains this."},
    };
    query.policy = ToolPolicy::ALLOW;
    query.callback = [this, mode](const json& args) -> json {
        if (files().empty()) {
            return {{"status", "error"}, {"error", "No documents indexed yet. Call index_document first."}};
        }
        const size_t k = static_cast<size_t>(std::clamp<long long>(args.value("top_k", 5LL), 1, 20));
        std::vector<DocumentHit> hits;
        try {
            hits = this->query(args.value("query", ""), k, args.value("file", ""));
        } catch (const std::exception& e) {
            return {{"status", "error"}, {"error", std::string("Retrieval failed: ") + e.what()}};
        }
        json results = json::array();
        for (const auto& h : hits) {
            json r = {{"file", h.chunk.file}, {"text", h.chunk.text},
                      {"score", std::round(h.score * 10000) / 10000}};
            if (h.chunk.page > 0) r["page"] = h.chunk.page;
            results.push_back(std::move(r));
        }
        return {{"status", "success"}, {"retrieval", mode}, {"results", results}};
    };
    registry.registerTool(std::move(query));

    ToolInfo list;
    list.name = "list_indexed_documents";
    list.description = "List the documents indexed so far and how many passages each has.";
    list.policy = ToolPolicy::ALLOW;
    list.callback = [this, mode](const json&) -> json {
        json items = json::array();
        for (const auto& [f, n] : files()) items.push_back({{"file", f}, {"chunks", n}});
        return {{"status", "success"}, {"documents", items}, {"retrieval", mode}};
    };
    registry.registerTool(std::move(list));
}

}  // namespace gaia
