// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/chunking.h"

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <map>
#include <mutex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <vector>

namespace gaia {
namespace {

// --------------------------------------------------------------------------
// String helpers. Semantics follow Python's str methods so that the port
// produces identical chunk boundaries.
// --------------------------------------------------------------------------

struct CodePoint {
    unsigned value = 0;
    std::size_t next = 0;
};

/// Decode the UTF-8 code point starting at byte `i`. Malformed bytes decode as
/// themselves so that arbitrary input still advances instead of hanging.
CodePoint codePointAt(const std::string& s, std::size_t i) {
    const unsigned char lead = static_cast<unsigned char>(s[i]);
    std::size_t extra = 0;
    unsigned value = lead;
    if (lead < 0x80) {
        extra = 0;
    } else if ((lead & 0xE0) == 0xC0) {
        extra = 1;
        value = lead & 0x1Fu;
    } else if ((lead & 0xF0) == 0xE0) {
        extra = 2;
        value = lead & 0x0Fu;
    } else if ((lead & 0xF8) == 0xF0) {
        extra = 3;
        value = lead & 0x07u;
    } else {
        return {lead, i + 1};
    }
    if (i + extra >= s.size()) return {lead, i + 1};
    for (std::size_t k = 1; k <= extra; ++k) {
        const unsigned char cont = static_cast<unsigned char>(s[i + k]);
        if ((cont & 0xC0) != 0x80) return {lead, i + 1};
        value = (value << 6) | (cont & 0x3Fu);
    }
    return {value, i + extra + 1};
}

/// Python's str.isspace() / regex `\s` set. Includes NBSP and the Unicode
/// separators, which appear in text pasted out of browsers and word processors.
bool isPythonSpace(unsigned cp) {
    switch (cp) {
    case 0x09: case 0x0A: case 0x0B: case 0x0C: case 0x0D:
    case 0x1C: case 0x1D: case 0x1E: case 0x1F: case 0x20:
    case 0x85: case 0xA0: case 0x1680:
    case 0x2000: case 0x2001: case 0x2002: case 0x2003: case 0x2004: case 0x2005:
    case 0x2006: case 0x2007: case 0x2008: case 0x2009: case 0x200A:
    case 0x2028: case 0x2029: case 0x202F: case 0x205F: case 0x3000:
        return true;
    default:
        return false;
    }
}

/// Python's str.isupper() for a single code point.
bool isPythonUpper(unsigned cp) {
    struct Range {
        unsigned first;
        unsigned last;
    };
    static const Range kUpperRanges[] = {
#include "unicode_upper_ranges.inc"
    };
    const auto* end = kUpperRanges + (sizeof(kUpperRanges) / sizeof(kUpperRanges[0]));
    const auto* it = std::upper_bound(
        kUpperRanges, end, cp,
        [](unsigned value, const Range& range) { return value < range.first; });
    return it != kUpperRanges && cp <= (it - 1)->last;
}

std::string strip(const std::string& s) {
    std::size_t begin = 0;
    while (begin < s.size()) {
        const CodePoint cp = codePointAt(s, begin);
        if (!isPythonSpace(cp.value)) break;
        begin = cp.next;
    }
    std::size_t end = begin;
    for (std::size_t i = begin; i < s.size();) {
        const CodePoint cp = codePointAt(s, i);
        if (!isPythonSpace(cp.value)) end = cp.next;
        i = cp.next;
    }
    return s.substr(begin, end - begin);
}

/// Unicode code-point count — Python's len() on a str, not a byte count.
std::size_t utf8Length(const std::string& s) {
    std::size_t count = 0;
    for (unsigned char c : s) {
        if ((c & 0xC0) != 0x80) ++count;
    }
    return count;
}

/// Python's `len(text) // 4` token estimate.
std::size_t estimateTokens(const std::string& s) {
    return utf8Length(s) / 4;
}

/// Byte offset of the code point `n` positions back from the end.
std::size_t offsetOfLastCodePoints(const std::string& s, std::size_t n) {
    std::size_t seen = 0;
    std::size_t i = s.size();
    while (i > 0) {
        --i;
        if ((static_cast<unsigned char>(s[i]) & 0xC0) != 0x80) {
            ++seen;
            if (seen == n) return i;
        }
    }
    return 0;
}

/// Python's str.split() with no argument: split on whitespace runs, drop empties.
std::vector<std::string> splitWhitespace(const std::string& s) {
    std::vector<std::string> out;
    std::size_t i = 0;
    while (i < s.size()) {
        while (i < s.size()) {
            const CodePoint cp = codePointAt(s, i);
            if (!isPythonSpace(cp.value)) break;
            i = cp.next;
        }
        const std::size_t start = i;
        while (i < s.size()) {
            const CodePoint cp = codePointAt(s, i);
            if (isPythonSpace(cp.value)) break;
            i = cp.next;
        }
        if (i > start) out.push_back(s.substr(start, i - start));
    }
    return out;
}

/// Python's str.split("\n"): keeps empty fields.
std::vector<std::string> splitLines(const std::string& s) {
    std::vector<std::string> lines;
    std::size_t start = 0;
    for (std::size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '\n') {
            lines.push_back(s.substr(start, i - start));
            start = i + 1;
        }
    }
    lines.push_back(s.substr(start));
    return lines;
}

std::string join(const std::vector<std::string>& parts, const std::string& sep) {
    std::string out;
    for (std::size_t i = 0; i < parts.size(); ++i) {
        if (i) out += sep;
        out += parts[i];
    }
    return out;
}

void replaceAll(std::string& s, const std::string& from, const std::string& to) {
    if (from.empty()) return;
    std::size_t pos = 0;
    while ((pos = s.find(from, pos)) != std::string::npos) {
        s.replace(pos, from.size(), to);
        pos += to.size();
    }
}

/// ASCII-only lower-casing: locale-aware tolower() would corrupt UTF-8 bytes.
std::string toLower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](char c) {
        return (c >= 'A' && c <= 'Z') ? static_cast<char>(c - 'A' + 'a') : c;
    });
    return s;
}

// --------------------------------------------------------------------------
// Chunking internals
// --------------------------------------------------------------------------

/// RAGSDK._get_last_n_tokens: roughly the last n tokens, cut at a word boundary.
std::string lastNTokens(const std::string& text, std::size_t nTokens) {
    const std::size_t targetChars = nTokens * 4;
    if (utf8Length(text) <= targetChars) return text;

    const std::string trimmed = text.substr(offsetOfLastCodePoints(text, targetChars));
    const std::size_t firstSpace = trimmed.find(' ');
    if (firstSpace != std::string::npos && firstSpace > 0) {
        return trimmed.substr(firstSpace + 1);
    }
    return trimmed;
}

/// Python regex `^[\-=_]{3,}$` against an already-stripped line.
bool isHorizontalRule(const std::string& stripped) {
    if (stripped.size() < 3) return false;
    return std::all_of(stripped.begin(), stripped.end(), [](char c) {
        return c == '-' || c == '=' || c == '_';
    });
}

bool isAsciiUpper(char c) {
    return c >= 'A' && c <= 'Z';
}

/// Split on blank-line runs: Python's re.split(r"\n\s*\n", text).
/// A whitespace run containing two or more newlines is a paragraph break.
std::vector<std::string> splitParagraphs(const std::string& text) {
    std::vector<std::string> paragraphs;
    std::size_t segmentStart = 0;
    std::size_t i = 0;
    while (i < text.size()) {
        CodePoint cp = codePointAt(text, i);
        if (!isPythonSpace(cp.value)) {
            i = cp.next;
            continue;
        }
        std::size_t runEnd = i;
        std::size_t firstNewline = std::string::npos;
        std::size_t lastNewline = std::string::npos;
        int newlines = 0;
        while (runEnd < text.size()) {
            cp = codePointAt(text, runEnd);
            if (!isPythonSpace(cp.value)) break;
            if (cp.value == '\n') {
                if (firstNewline == std::string::npos) firstNewline = runEnd;
                lastNewline = runEnd;
                ++newlines;
            }
            runEnd = cp.next;
        }
        if (newlines >= 2) {
            paragraphs.push_back(text.substr(segmentStart, firstNewline - segmentStart));
            segmentStart = lastNewline + 1;
        }
        i = runEnd;
    }
    paragraphs.push_back(text.substr(segmentStart));

    std::vector<std::string> out;
    for (const auto& p : paragraphs) {
        std::string s = strip(p);
        if (!s.empty()) out.push_back(s);
    }
    return out;
}

/// STEP 2 of _split_text_into_chunks: cut the document at markdown headers,
/// horizontal rules, and title-shaped lines surrounded by whitespace.
std::vector<std::string> splitSections(const std::string& text) {
    const std::vector<std::string> lines = splitLines(text);
    std::vector<std::string> sections;
    std::vector<std::string> current;

    for (std::size_t i = 0; i < lines.size(); ++i) {
        const std::string stripped = strip(lines[i]);
        bool isBoundary = false;

        if (!stripped.empty() && stripped[0] == '#') {
            isBoundary = true;
        } else if (isHorizontalRule(stripped)) {
            isBoundary = true;
        } else if (!stripped.empty() && utf8Length(stripped) < 100 && i > 0) {
            const bool prevEmpty = strip(lines[i - 1]).empty();
            const bool nextExists = i + 1 < lines.size();
            const bool nextNotEmpty = nextExists && !strip(lines[i + 1]).empty();
            if (prevEmpty && nextNotEmpty) {
                const char last = stripped.back();
                const bool endsWithPunctuation =
                    last == '.' || last == '!' || last == '?' || last == ',' || last == ';';
                if (isPythonUpper(codePointAt(stripped, 0).value) && !endsWithPunctuation) {
                    isBoundary = true;
                }
            }
        }

        if (isBoundary && !current.empty()) {
            sections.push_back(join(current, "\n"));
            current.assign(1, lines[i]);
        } else {
            current.push_back(lines[i]);
        }
    }

    if (!current.empty()) sections.push_back(join(current, "\n"));
    return sections;
}

// --------------------------------------------------------------------------
// Extraction internals
// --------------------------------------------------------------------------

const std::set<std::string>& markdownExtensions() {
    static const std::set<std::string> exts = {".md", ".markdown"};
    return exts;
}

const std::set<std::string>& plainTextExtensions() {
    // Mirrors the text branch of RAGSDK._extract_text_from_file.
    static const std::set<std::string> exts = {".txt", ".log", ".rst"};
    return exts;
}

const std::set<std::string>& sourceExtensions() {
    // Mirrors the code/web branch of RAGSDK._extract_text_from_file.
    static const std::set<std::string> exts = {
        ".py",    ".pyw",   ".java",  ".cpp",  ".cc",     ".cxx",   ".hpp",  ".h",
        ".c",     ".cs",    ".go",    ".rs",   ".rb",     ".php",   ".swift", ".kt",
        ".kts",   ".scala", ".js",    ".jsx",  ".ts",     ".tsx",   ".mjs",  ".cjs",
        ".vue",   ".svelte",".astro", ".css",  ".scss",   ".sass",  ".less", ".styl",
        ".stylus",".html",  ".htm",   ".svg",  ".sh",     ".bash",  ".ps1",  ".r",
        ".sql",   ".yaml",  ".yml",   ".xml",  ".toml",   ".ini",   ".cfg",  ".conf",
        ".env",   ".properties",      ".gradle",          ".cmake", ".mk",   ".make",
    };
    return exts;
}

/// Formats whose Python extractor needs a document-parsing backend.
/// Deliberately out of scope for the native runtime — see chunking.h.
const std::map<std::string, std::string>& documentFormats() {
    static const std::map<std::string, std::string> formats = {
        {".pdf", "PDF"},         {".docx", "Word"},       {".doc", "Word"},
        {".xlsx", "Excel"},      {".xls", "Excel"},       {".pptx", "PowerPoint"},
        {".ppt", "PowerPoint"},  {".odt", "OpenDocument"},{".ods", "OpenDocument"},
        {".odp", "OpenDocument"},{".rtf", "Rich Text"},   {".epub", "EPUB"},
    };
    return formats;
}

/// Formats the Python SDK renders structurally (CSV rows, pretty-printed JSON).
/// Reading them as raw text here would silently diverge from Python's output,
/// so they are refused with the escape hatch named instead.
const std::map<std::string, std::string>& structuredFormats() {
    static const std::map<std::string, std::string> formats = {
        {".csv", "CSV"},
        {".json", "JSON"},
    };
    return formats;
}

std::mutex& extractorMutex() {
    static std::mutex m;
    return m;
}

std::map<std::string, TextExtractor>& customExtractors() {
    static std::map<std::string, TextExtractor> extractors;
    return extractors;
}

std::string extensionOf(const std::string& filePath) {
    const std::size_t slash = filePath.find_last_of("/\\");
    const std::string name =
        slash == std::string::npos ? filePath : filePath.substr(slash + 1);
    const std::size_t dot = name.find_last_of('.');
    if (dot == std::string::npos || dot == 0) return "";
    return toLower(name.substr(dot));
}

std::string supportedSummary() {
    return "supported: .txt, .md, .markdown, .rst, .log plus common source, "
           "markup and config types (.py, .cpp, .ts, .yaml, ...) - call "
           "gaia::supportedExtensions() for the full list";
}

/// Decode file bytes the way RAGSDK._extract_text_from_text_file does: UTF-8
/// first, latin-1 as the fallback. Binary content is refused, not mangled.
std::string decodeText(const std::string& bytes, const std::string& filePath) {
    if (bytes.find('\0') != std::string::npos) {
        throw std::runtime_error(
            "Cannot extract text from binary file: " + filePath +
            "\nThe file contains NUL bytes, so it is not a text document."
            "\nIf it is a supported document format, register a handler with "
            "gaia::registerExtractor().");
    }

    // Valid UTF-8 passes through untouched.
    bool valid = true;
    for (std::size_t i = 0; i < bytes.size() && valid;) {
        const unsigned char c = static_cast<unsigned char>(bytes[i]);
        std::size_t extra = 0;
        if (c < 0x80) {
            extra = 0;
        } else if ((c & 0xE0) == 0xC0) {
            extra = 1;
        } else if ((c & 0xF0) == 0xE0) {
            extra = 2;
        } else if ((c & 0xF8) == 0xF0) {
            extra = 3;
        } else {
            valid = false;
            break;
        }
        if (i + extra >= bytes.size()) {
            valid = false;
            break;
        }
        for (std::size_t k = 1; k <= extra; ++k) {
            if ((static_cast<unsigned char>(bytes[i + k]) & 0xC0) != 0x80) {
                valid = false;
                break;
            }
        }
        i += extra + 1;
    }
    if (valid) return bytes;

    // latin-1 fallback: every byte is a code point, re-encoded as UTF-8 so the
    // code-point count matches what Python's latin-1 decode would produce.
    std::string out;
    out.reserve(bytes.size() + bytes.size() / 4);
    for (unsigned char c : bytes) {
        if (c < 0x80) {
            out.push_back(static_cast<char>(c));
        } else {
            out.push_back(static_cast<char>(0xC0 | (c >> 6)));
            out.push_back(static_cast<char>(0x80 | (c & 0x3F)));
        }
    }
    return out;
}

std::string readFileBytes(const std::string& filePath) {
    std::error_code ec;
    // Reading a directory yields an empty stream on some platforms; refuse it
    // here so extraction can never return silently-empty text.
    if (!std::filesystem::is_regular_file(filePath, ec) || ec) {
        throw std::runtime_error("Not a readable file: " + filePath +
                                 "\nCheck that the path exists and is a regular file.");
    }
    std::ifstream in(filePath, std::ios::binary);
    if (!in) {
        throw std::runtime_error("Cannot read file: " + filePath +
                                 "\nCheck that the path exists and is readable.");
    }
    std::ostringstream buffer;
    buffer << in.rdbuf();
    if (in.bad()) {
        throw std::runtime_error("I/O error while reading file: " + filePath);
    }
    return buffer.str();
}

TextFormat classify(const std::string& extension) {
    if (markdownExtensions().count(extension)) return TextFormat::Markdown;
    if (sourceExtensions().count(extension)) return TextFormat::SourceCode;
    return TextFormat::PlainText;
}

} // namespace

// --------------------------------------------------------------------------
// Public API
// --------------------------------------------------------------------------

std::vector<std::string> splitIntoSentences(const std::string& text) {
    std::string protectedText = text;
    replaceAll(protectedText, "Dr.", "Dr<DOT>");
    replaceAll(protectedText, "Mr.", "Mr<DOT>");
    replaceAll(protectedText, "Mrs.", "Mrs<DOT>");
    replaceAll(protectedText, "Ms.", "Ms<DOT>");
    replaceAll(protectedText, "Prof.", "Prof<DOT>");
    replaceAll(protectedText, "Sr.", "Sr<DOT>");
    replaceAll(protectedText, "Jr.", "Jr<DOT>");
    replaceAll(protectedText, "vs.", "vs<DOT>");
    replaceAll(protectedText, "e.g.", "e<DOT>g<DOT>");
    replaceAll(protectedText, "i.e.", "i<DOT>e<DOT>");
    replaceAll(protectedText, "etc.", "etc<DOT>");

    // Equivalent to re.split(r"(?<=[.!?])\s+(?=[A-Z])", text): break on a
    // whitespace run preceded by sentence punctuation and followed by a capital.
    std::vector<std::string> raw;
    std::size_t segmentStart = 0;
    std::size_t i = 0;
    while (i < protectedText.size()) {
        CodePoint cp = codePointAt(protectedText, i);
        if (!isPythonSpace(cp.value)) {
            i = cp.next;
            continue;
        }
        std::size_t runEnd = i;
        while (runEnd < protectedText.size()) {
            cp = codePointAt(protectedText, runEnd);
            if (!isPythonSpace(cp.value)) break;
            runEnd = cp.next;
        }

        const char prev = i > 0 ? protectedText[i - 1] : '\0';
        const bool afterSentenceEnd = prev == '.' || prev == '!' || prev == '?';
        const bool beforeCapital =
            runEnd < protectedText.size() && isAsciiUpper(protectedText[runEnd]);
        if (afterSentenceEnd && beforeCapital) {
            raw.push_back(protectedText.substr(segmentStart, i - segmentStart));
            segmentStart = runEnd;
        }
        i = runEnd;
    }
    raw.push_back(protectedText.substr(segmentStart));

    std::vector<std::string> sentences;
    for (auto& s : raw) {
        replaceAll(s, "<DOT>", ".");
        std::string trimmed = strip(s);
        if (!trimmed.empty()) sentences.push_back(trimmed);
    }
    return sentences;
}

std::vector<std::string> splitTextIntoChunks(const std::string& text,
                                             const ChunkingConfig& config) {
    if (config.chunkSize == 0) {
        throw std::invalid_argument("ChunkingConfig.chunkSize must be greater than 0");
    }
    if (config.chunkOverlap >= config.chunkSize) {
        throw std::invalid_argument(
            "ChunkingConfig.chunkOverlap (" + std::to_string(config.chunkOverlap) +
            ") must be smaller than chunkSize (" + std::to_string(config.chunkSize) +
            "); an overlap at or above the chunk size never advances the window.");
    }

    const std::size_t chunkSizeTokens = config.chunkSize;
    const std::size_t overlapTokens = config.chunkOverlap;

    std::vector<std::string> sections = splitSections(text);
    std::vector<std::string> paragraphs =
        sections.size() <= 3 ? splitParagraphs(text) : sections;

    std::vector<std::string> chunks;
    std::vector<std::string> currentChunk;
    std::size_t currentSize = 0;

    for (const auto& rawPara : paragraphs) {
        const std::string para = strip(rawPara);
        if (para.empty()) continue;

        const std::size_t paraTokens = estimateTokens(para);

        if (paraTokens > chunkSizeTokens) {
            for (const auto& sentence : splitIntoSentences(para)) {
                const std::size_t sentenceTokens = estimateTokens(sentence);

                if (currentSize + sentenceTokens > chunkSizeTokens && !currentChunk.empty()) {
                    const std::string overlapText = join(currentChunk, " ");
                    chunks.push_back(overlapText);

                    if (estimateTokens(overlapText) > overlapTokens) {
                        currentChunk = splitWhitespace(lastNTokens(overlapText, overlapTokens));
                        currentSize = overlapTokens;
                    } else {
                        currentChunk.clear();
                        currentSize = 0;
                    }
                }

                currentChunk.push_back(sentence);
                currentSize += sentenceTokens;
            }
        } else if (currentSize + paraTokens > chunkSizeTokens && !currentChunk.empty()) {
            const std::string overlapText = join(currentChunk, " ");
            chunks.push_back(overlapText);

            currentChunk = splitWhitespace(lastNTokens(overlapText, overlapTokens));
            currentSize = estimateTokens(join(currentChunk, " "));

            currentChunk.push_back(para);
            currentSize += paraTokens;
        } else {
            currentChunk.push_back(para);
            currentSize += paraTokens;
        }
    }

    if (!currentChunk.empty()) chunks.push_back(join(currentChunk, " "));
    return chunks;
}

ExtractedText extractPlainTextFile(const std::string& filePath) {
    ExtractedText result;
    result.extension = extensionOf(filePath);
    result.format = classify(result.extension);
    result.text = strip(decodeText(readFileBytes(filePath), filePath));
    return result;
}

ExtractedText extractFile(const std::string& filePath) {
    const std::string ext = extensionOf(filePath);

    TextExtractor custom;
    {
        std::lock_guard<std::mutex> lock(extractorMutex());
        auto it = customExtractors().find(ext);
        if (it != customExtractors().end()) custom = it->second;
    }
    if (custom) {
        ExtractedText result;
        result.extension = ext;
        result.format = classify(ext);
        result.text = strip(custom(filePath));
        return result;
    }

    auto doc = documentFormats().find(ext);
    if (doc != documentFormats().end()) {
        throw std::runtime_error(
            "Unsupported file type '" + ext + "' (" + doc->second + " document): " + filePath +
            "\nThe C++ runtime deliberately does not link a document-parsing backend, "
            "so " + doc->second + " files cannot be extracted natively."
            "\nTo index it: convert the file to text or Markdown first, or link your own "
            "parser with gaia::registerExtractor(\"" + ext + "\", ...)."
            "\n" + supportedSummary());
    }

    auto structured = structuredFormats().find(ext);
    if (structured != structuredFormats().end()) {
        throw std::runtime_error(
            "Unsupported file type '" + ext + "' (" + structured->second + "): " + filePath +
            "\nThe Python RAG SDK renders " + structured->second +
            " structurally, and reading it as raw text here would produce different chunks."
            "\nTo index the raw text anyway, call gaia::extractPlainTextFile(); to match the "
            "Python rendering, register a handler with gaia::registerExtractor(\"" + ext +
            "\", ...)."
            "\n" + supportedSummary());
    }

    if (!isSupportedExtension(ext)) {
        const std::string shown = ext.empty() ? "(no extension)" : ext;
        throw std::runtime_error(
            "Unsupported file type '" + shown + "': " + filePath +
            "\nTo read it as plain text anyway, call gaia::extractPlainTextFile(); to add "
            "native support, register a handler with gaia::registerExtractor()."
            "\n" + supportedSummary());
    }

    return extractPlainTextFile(filePath);
}

std::vector<std::string> chunkFile(const std::string& filePath,
                                   const ChunkingConfig& config) {
    return splitTextIntoChunks(extractFile(filePath).text, config);
}

void registerExtractor(const std::string& extension, TextExtractor extractor) {
    if (extension.empty() || extension[0] != '.') {
        throw std::invalid_argument(
            "registerExtractor: extension must start with a dot (got '" + extension + "')");
    }
    if (!extractor) {
        throw std::invalid_argument("registerExtractor: extractor callback must not be empty");
    }
    std::lock_guard<std::mutex> lock(extractorMutex());
    customExtractors()[toLower(extension)] = std::move(extractor);
}

bool isSupportedExtension(const std::string& extension) {
    const std::string ext = toLower(extension);
    {
        std::lock_guard<std::mutex> lock(extractorMutex());
        if (customExtractors().count(ext)) return true;
    }
    return plainTextExtensions().count(ext) || markdownExtensions().count(ext) ||
           sourceExtensions().count(ext);
}

std::vector<std::string> supportedExtensions() {
    std::set<std::string> all;
    all.insert(plainTextExtensions().begin(), plainTextExtensions().end());
    all.insert(markdownExtensions().begin(), markdownExtensions().end());
    all.insert(sourceExtensions().begin(), sourceExtensions().end());
    {
        std::lock_guard<std::mutex> lock(extractorMutex());
        for (const auto& entry : customExtractors()) all.insert(entry.first);
    }
    return std::vector<std::string>(all.begin(), all.end());
}

} // namespace gaia
