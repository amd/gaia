// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/document_index.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <random>
#include <regex>
#include <set>
#include <sstream>

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

std::string decodeXmlEntities(std::string s) {
    const std::pair<const char*, const char*> ents[] = {
        {"&lt;", "<"}, {"&gt;", ">"}, {"&quot;", "\""}, {"&apos;", "'"}, {"&amp;", "&"}};
    for (const auto& [from, to] : ents) {
        size_t pos = 0;
        while ((pos = s.find(from, pos)) != std::string::npos) {
            s.replace(pos, std::strlen(from), to);
            pos += std::strlen(to);
        }
    }
    return s;
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
    const std::string extract = "unzip -p " + detail::shellQuote(safe.str()) + " word/document.xml";
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
            parts.push_back(std::to_string(tooLarge) + " too large (text over 1 MB, PDF or Word over " +
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
    const bool doc = withDocuments && (ext == ".pdf" || ext == ".docx");
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

}  // namespace

std::string MissingDocumentReader::remedy(const std::string& file, const std::string& program) {
    return "Can't read PDF " + file + ": this agent reads PDFs with " + program +
           ", which is not installed on this PC. Install poppler (it provides " + program +
           "; on Debian or Ubuntu: sudo apt install poppler-utils) and attach the file again, or switch to "
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
    ExtractedText e = isSupportedExtension(ext) ? extractFile(path) : extractPlainTextFile(path);
    pages.emplace_back(0, e.text);
    return pages;
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

void DocumentIndex::removeFile(const std::string& file) {
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
    removeFile(file);
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
        "Index a document (PDF, Word .docx, text, Markdown, code) or every supported file in a "
        "folder so query_documents can find passages in it, with page numbers for PDFs.";
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
