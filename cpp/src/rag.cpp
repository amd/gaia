// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/rag.h"

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>

#include "gaia/documents.h"
#include "gaia/ignore.h"
#include "gaia/tool_registry.h"
#include "gaia/vector_index.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

constexpr std::size_t kEmbedBatch = 32;
constexpr std::size_t kMaxDirectoryFiles = 500;
constexpr std::size_t kMaxCodeFiles = 5000;
// Fewer words per section would turn one document into thousands of model calls.
constexpr long long kMinWordsPerSection = 500;

std::string lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

std::size_t utf8Cut(const std::string& s, std::size_t pos) {
    if (pos >= s.size()) return s.size();
    while (pos > 0 && (static_cast<unsigned char>(s[pos]) & 0xC0) == 0x80) --pos;
    return pos;
}

json error(const std::string& m) { return {{"status", "error"}, {"error", m}}; }

// Embeddings for every text, checked against what the index already holds before any is added.
std::vector<std::vector<float>> embedAll(const Embedder& embed, const std::vector<std::string>& texts, std::size_t dimension) {
    std::vector<std::vector<float>> vectors;
    for (std::size_t i = 0; i < texts.size(); i += kEmbedBatch) {
        const std::vector<std::string> batch(texts.begin() + static_cast<std::ptrdiff_t>(i),
                                             texts.begin() + static_cast<std::ptrdiff_t>(std::min(texts.size(), i + kEmbedBatch)));
        auto v = embed(batch);
        if (v.size() != batch.size()) {
            throw std::runtime_error("the embedder returned " + std::to_string(v.size()) + " vectors for " +
                                     std::to_string(batch.size()) + " texts");
        }
        vectors.insert(vectors.end(), std::make_move_iterator(v.begin()), std::make_move_iterator(v.end()));
    }
    for (const auto& v : vectors) {
        if (dimension == 0) dimension = v.size();
        if (v.size() != dimension) {
            throw std::runtime_error("the embedder returned vectors of " + std::to_string(v.size()) + " values where " +
                                     std::to_string(dimension) + " were expected");
        }
    }
    return vectors;
}

const char* const kNoDocuments =
    "No documents are indexed yet. Find the file (search_file / find_files), call index_document on it, "
    "then query_specific_file or query_documents. For general knowledge questions, answer directly.";

}  // namespace

void fillScannedPages(const std::string& path, DocumentText& doc, const VisionReader& vision, std::size_t maxPages) {
    std::vector<int> wanted(doc.emptySections.begin(),
                            doc.emptySections.begin() + static_cast<std::ptrdiff_t>(std::min(maxPages, doc.emptySections.size())));
    std::map<int, std::string> jpegs;
    try {
        jpegs = extractPageImages(path, wanted);
    } catch (const std::runtime_error& e) {
        doc.warnings.push_back(std::string("could not read the page images: ") + e.what());
        return;
    }
    // Markers come in page order; walk them in turn so text that looks like a marker cannot be taken for one.
    std::map<int, std::size_t> markerEnd;
    for (std::size_t n = 1, from = 0; n <= static_cast<std::size_t>(std::max(doc.sections, 0)); ++n) {
        const std::string marker = "--- Page " + std::to_string(n) + " ---\n";
        const auto at = doc.text.find(marker, from);
        if (at == std::string::npos) continue;
        from = at + marker.size();
        markerEnd[static_cast<int>(n)] = from;
    }
    std::map<std::size_t, std::string> inserts;  // position -> transcribed text
    std::vector<int> stillEmpty;
    std::size_t read = 0;
    for (int page : doc.emptySections) {
        auto it = jpegs.find(page);
        if (it == jpegs.end()) {
            stillEmpty.push_back(page);
            continue;
        }
        std::string text;
        try {
            text = vision(kTranscribePagePrompt, {it->second});
        } catch (const std::exception& e) {
            doc.warnings.push_back("page " + std::to_string(page) + " could not be read by the vision model: " + e.what());
            stillEmpty.push_back(page);
            continue;
        }
        auto at = markerEnd.find(page);
        if (at == markerEnd.end()) {
            doc.warnings.push_back("page " + std::to_string(page) + " was read by the vision model but has no page marker to put it under");
            stillEmpty.push_back(page);
            continue;
        }
        inserts[at->second] = text + "\n";
        ++read;
    }
    // Last first, so earlier positions stay valid.
    for (auto it = inserts.rbegin(); it != inserts.rend(); ++it) doc.text.insert(it->first, it->second);
    doc.emptySections = stillEmpty;
    // Replace the generic scanned-page warning with what happened.
    doc.warnings.erase(std::remove_if(doc.warnings.begin(), doc.warnings.end(),
                                      [](const std::string& w) { return w.find("no text layer") != std::string::npos; }),
                       doc.warnings.end());
    if (read) doc.warnings.push_back(std::to_string(read) + " scanned page(s) were read with the vision model; check their numbers");
    if (!stillEmpty.empty()) {
        doc.warnings.push_back(std::to_string(stillEmpty.size()) + " page(s) still have no readable text");
    }
}

std::vector<std::string> chunkText(const std::string& text, std::size_t chunkChars, std::size_t overlapChars) {
    std::vector<std::string> out;
    if (text.find_first_not_of(" \t\r\n") == std::string::npos) return out;
    std::size_t start = 0;
    while (start < text.size()) {
        std::size_t end = std::min(text.size(), start + chunkChars);
        if (end < text.size()) {
            // Prefer a paragraph break, then a sentence end, then a space, in the back half.
            const std::size_t floor = start + chunkChars / 2;
            std::size_t cut = text.rfind("\n\n", end);
            if (cut == std::string::npos || cut < floor) {
                cut = std::string::npos;
                for (std::size_t i = end; i > floor; --i) {
                    if ((text[i - 1] == '.' || text[i - 1] == '!' || text[i - 1] == '?') && std::isspace(static_cast<unsigned char>(text[i]))) {
                        cut = i;
                        break;
                    }
                }
            }
            if (cut == std::string::npos) {
                const auto sp = text.rfind(' ', end);
                cut = (sp != std::string::npos && sp > floor) ? sp : utf8Cut(text, end);
            }
            // Bytes that are not UTF-8 can walk the cut back to the start; cut hard instead.
            if (cut > start) end = cut;
        }
        std::string piece = text.substr(start, end - start);
        const auto b = piece.find_first_not_of(" \t\r\n");
        if (b != std::string::npos) out.push_back(piece.substr(b, piece.find_last_not_of(" \t\r\n") - b + 1));
        if (end >= text.size()) break;
        std::size_t next = end > overlapChars ? end - overlapChars : end;
        if (next <= start) next = end;
        // Start the overlap on a word boundary.
        const auto sp = text.find(' ', next);
        if (sp != std::string::npos && sp < end) next = sp + 1;
        start = next;
    }
    return out;
}

DocumentIndex::DocumentIndex(Embedder embedder) : embed_(std::move(embedder)) {
    VectorIndexOptions o;
    o.metric = Metric::InnerProduct;
    o.normalizeOnAdd = true;
    index_ = std::make_unique<VectorIndex>(o);
}

DocumentIndex::~DocumentIndex() = default;

bool DocumentIndex::empty() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return chunks_.empty();
}

json DocumentIndex::indexDocument(const std::string& raw) {
    const std::string path = resolve_ ? resolve_(raw) : raw;
    if (path.empty()) return error("'" + raw + "' is outside the folders GAIA may read.");
    if (const std::string why = refuse_ ? refuse_(path) : ""; !why.empty()) return error(why);
    std::error_code ec;
    if (!fs::is_regular_file(fs::u8path(path), ec)) return error("File not found: " + raw);
    {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = files_.find(path);
        if (it != files_.end()) {
            return {{"status", "success"}, {"file_path", path}, {"already_indexed", true},
                    {"num_chunks", it->second.chunks}, {"message", "Already indexed; query it with query_specific_file."}};
        }
    }
    // Pages stay separate so every chunk can be cited by page.
    std::vector<std::pair<int, std::string>> sections;
    std::string kind = "text";
    int pages = 0;
    std::vector<std::string> warnings;
    const std::string ext = lower(fs::u8path(path).extension().u8string());
    if (isDocumentExtension(ext)) {
        DocumentText doc;
        try {
            doc = extractDocumentText(path);
        } catch (const std::runtime_error& e) {
            return error(e.what());
        }
        kind = doc.kind;
        pages = doc.sections;
        warnings = doc.warnings;
        if (doc.kind == "pdf" && !doc.emptySections.empty() && vision_) {
            fillScannedPages(path, doc, vision_);
            warnings = doc.warnings;
        }
        if (doc.kind == "pdf" || doc.kind == "pptx") {
            // Markers are written for pages 1..N in order; look for each in turn so a
            // marker-like line inside the document's own text cannot reorder pages.
            const std::string word = doc.kind == "pdf" ? "Page" : "Slide";
            std::vector<std::pair<int, std::size_t>> marks;  // page, start of its marker
            std::size_t from = 0;
            for (int n = 1; n <= doc.sections; ++n) {
                const std::string m = "--- " + word + " " + std::to_string(n) + " ---\n";
                const auto at = doc.text.find(m, from);
                if (at == std::string::npos) continue;
                marks.emplace_back(n, at);
                from = at + m.size();
            }
            for (std::size_t i = 0; i < marks.size(); ++i) {
                const std::string m = "--- " + word + " " + std::to_string(marks[i].first) + " ---\n";
                const std::size_t begin = marks[i].second + m.size();
                const std::size_t end = i + 1 < marks.size() ? marks[i + 1].second : doc.text.size();
                sections.emplace_back(marks[i].first, doc.text.substr(begin, end - begin));
            }
        } else {
            sections.emplace_back(0, doc.text);
        }
    } else {
        std::ifstream in(fs::u8path(path), std::ios::binary);
        std::ostringstream ss;
        ss << in.rdbuf();
        const std::string text = ss.str();
        if (text.find('\0') != std::string::npos) return error(raw + " is a binary file; there is no text to index.");
        sections.emplace_back(0, text);
    }
    std::vector<Chunk> fresh;
    for (const auto& [page, text] : sections) {
        for (auto& piece : chunkText(text, kChunkChars, kOverlapChars)) fresh.push_back({path, page, std::move(piece)});
    }
    if (fresh.empty()) {
        return error(raw + " has no text to index" +
                     std::string(kind == "pdf" ? " (it looks like a scanned document: its pages are images)." : "."));
    }
    std::vector<std::string> texts;
    for (const auto& c : fresh) texts.push_back(c.text);
    std::size_t dimension = 0;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        dimension = index_->dimension();
    }
    std::vector<std::vector<float>> vectors;
    try {
        vectors = embedAll(embed_, texts, dimension);
    } catch (const std::exception& e) {
        return {{"status", "error"}, {"error", std::string("Could not index ") + raw + ": " + e.what()},
                {"hint", "read_file can still read the document directly."}};
    }
    std::lock_guard<std::mutex> lock(mutex_);
    std::size_t added = 0;
    try {
        for (; added < fresh.size(); ++added) index_->add(std::to_string(chunks_.size() + added), vectors[added]);
    } catch (const std::exception& e) {
        // All or nothing: a half-added file would leave ids with no chunk behind them.
        for (std::size_t i = 0; i < added; ++i) index_->remove(std::to_string(chunks_.size() + i));
        return error(std::string("Could not index ") + raw + ": " + e.what());
    }
    chunks_.insert(chunks_.end(), fresh.begin(), fresh.end());
    files_[path] = {fresh.size(), pages, kind};
    json out = {{"status", "success"}, {"file_path", path}, {"file_type", kind}, {"num_chunks", fresh.size()},
                {"total_indexed_files", files_.size()},
                {"message", "Indexed " + fs::u8path(path).filename().u8string() + " (" + std::to_string(fresh.size()) +
                                " chunks). Next: query_specific_file with the user's question."}};
    if (pages) out["num_pages"] = pages;
    if (!warnings.empty()) out["warnings"] = warnings;
    return out;
}

json DocumentIndex::queryDocuments(const std::string& query, const std::string& onlyFileRaw) {
    if (query.find_first_not_of(" \t\r\n") == std::string::npos) return error("query is required");
    std::string onlyFile;
    if (!onlyFileRaw.empty()) {
        std::lock_guard<std::mutex> lock(mutex_);
        const std::string resolved = resolve_ ? resolve_(onlyFileRaw) : onlyFileRaw;
        if (files_.count(resolved)) {
            onlyFile = resolved;
        } else {
            // A bare name matches an indexed file with that name.
            const std::string want = lower(fs::u8path(onlyFileRaw).filename().u8string());
            for (const auto& [f, info] : files_) {
                if (lower(fs::u8path(f).filename().u8string()) == want) onlyFile = f;
            }
        }
        if (onlyFile.empty()) {
            return {{"status", "error"}, {"error", "'" + onlyFileRaw + "' is not indexed."},
                    {"hint", "Call index_document on it first, then query it."}};
        }
    }
    if (empty()) return {{"status", "no_documents"}, {"message", kNoDocuments}};
    std::vector<float> q;
    try {
        q = embed_({query}).at(0);
    } catch (const std::exception& e) {
        return error(std::string("Document search failed: ") + e.what());
    }
    std::lock_guard<std::mutex> lock(mutex_);
    const auto hits = index_->search(q, onlyFile.empty() ? kTopK : chunks_.size());
    json chunks = json::array();
    std::vector<std::string> sources;
    bool allPaged = true;
    for (const auto& [id, score] : hits) {
        const Chunk& c = chunks_.at(std::stoul(id));
        if (!onlyFile.empty() && c.file != onlyFile) continue;
        json entry = {{"chunk_id", chunks.size() + 1}, {"source_file", c.file}, {"content", c.text},
                      {"relevance_score", score}};
        if (c.page > 0) entry["page"] = c.page;
        else allPaged = false;
        chunks.push_back(entry);
        if (std::find(sources.begin(), sources.end(), c.file) == sources.end()) sources.push_back(c.file);
        if (chunks.size() >= kTopK) break;
    }
    json out = {{"status", "success"}, {"message", "Found " + std::to_string(chunks.size()) + " relevant document chunks"},
                {"chunks", chunks}, {"num_chunks", chunks.size()}, {"source_files", sources},
                {"instruction", std::string("Answer from these chunks only; quote numbers and names exactly. If the answer "
                                            "is not in them, say it is not in the document.") +
                                    (allPaged && !chunks.empty() ? " Cite the page of each fact." : "")}};
    if (!onlyFile.empty()) out["file"] = onlyFile;
    return out;
}

json DocumentIndex::indexDirectory(const std::string& raw, bool recursive) {
    const std::string dir = resolve_ ? resolve_(raw) : raw;
    if (dir.empty()) return error("'" + raw + "' is outside the folders GAIA may read.");
    std::error_code ec;
    if (!fs::is_directory(fs::u8path(dir), ec)) return error("Not a folder: " + raw);
    static const std::set<std::string> exts = {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md", ".markdown", ".csv",
                                               ".json", ".html", ".htm", ".rst", ".log", ".xml", ".yaml", ".yml"};
    std::vector<std::string> files;
    bool capped = false;
    auto consider = [&](const fs::directory_entry& e) {
        std::error_code x;
        if (e.is_symlink(x) || !e.is_regular_file(x) || !exts.count(lower(e.path().extension().u8string()))) return;
        if (files.size() >= kMaxDirectoryFiles) capped = true;
        else files.push_back(e.path().u8string());
    };
    if (recursive) {
        for (auto it = fs::recursive_directory_iterator(fs::u8path(dir), fs::directory_options::skip_permission_denied, ec);
             !ec && it != fs::recursive_directory_iterator() && !capped; it.increment(ec)) {
            consider(*it);
        }
    } else {
        for (const auto& e : fs::directory_iterator(fs::u8path(dir), fs::directory_options::skip_permission_denied, ec)) {
            consider(e);
            if (capped) break;
        }
    }
    std::sort(files.begin(), files.end());
    json indexed = json::array(), failed = json::array();
    std::size_t chunks = 0;
    for (const auto& f : files) {
        const json r = indexDocument(f);
        if (r.value("status", "") == "success") {
            indexed.push_back(f);
            chunks += r.value("num_chunks", 0u);
        } else {
            failed.push_back({{"file", f}, {"error", r.value("error", "")}});
        }
    }
    json out = {{"status", indexed.empty() && !failed.empty() ? "error" : "success"}, {"directory", dir},
                {"indexed_files", indexed}, {"failed", failed}, {"total_chunks", chunks},
                {"message", "Indexed " + std::to_string(indexed.size()) + " of " + std::to_string(files.size()) + " supported file(s)."}};
    if (capped) {
        out["warnings"] = json::array({"the folder holds more than " + std::to_string(kMaxDirectoryFiles) +
                                       " supported files; only the first " + std::to_string(kMaxDirectoryFiles) +
                                       " found were indexed. Index a subfolder to reach the rest."});
    }
    return out;
}

json DocumentIndex::searchChunks(const std::string& pattern) const {
    if (pattern.empty()) return error("pattern is required");
    // Exact, case-insensitive text: a model-written regex could hang every document tool.
    const std::string needle = lower(pattern);
    std::lock_guard<std::mutex> lock(mutex_);
    if (chunks_.empty()) return {{"status", "no_documents"}, {"message", kNoDocuments}};
    json matches = json::array();
    std::size_t total = 0;
    for (const auto& c : chunks_) {
        const std::size_t at = lower(c.text).find(needle);
        if (at == std::string::npos) continue;
        ++total;
        if (matches.size() >= 50) continue;
        const std::size_t from = utf8Cut(c.text, at > 150 ? at - 150 : 0);
        json e = {{"source_file", c.file}, {"match", c.text.substr(at, needle.size())}, {"context", c.text.substr(from, utf8Cut(c.text, std::min(c.text.size(), at + 250)) - from)}};
        if (c.page) e["page"] = c.page;
        matches.push_back(e);
    }
    return {{"status", "success"}, {"pattern", pattern}, {"matches", matches}, {"total_matches", total}};
}

json DocumentIndex::summarizeDocument(const std::string& raw, const std::string& summaryType, long long maxWords) {
    if (!summarize_) return error("summarize_document is not available in this session.");
    const std::string path = resolve_ ? resolve_(raw) : raw;
    if (path.empty()) return error("'" + raw + "' is outside the folders GAIA may read.");
    if (const std::string why = refuse_ ? refuse_(path) : ""; !why.empty()) return error(why);
    std::string text;
    const std::string ext = lower(fs::u8path(path).extension().u8string());
    try {
        if (isDocumentExtension(ext)) {
            text = extractDocumentText(path).text;
        } else {
            std::ifstream in(fs::u8path(path), std::ios::binary);
            if (!in) return error("File not found: " + raw);
            std::ostringstream ss;
            ss << in.rdbuf();
            text = ss.str();
        }
    } catch (const std::runtime_error& e) {
        return error(e.what());
    }
    if (text.find_first_not_of(" \t\r\n-Page0123456789") == std::string::npos) return error(raw + " has no text to summarize.");
    const std::string style = summaryType.empty() ? "detailed" : summaryType;
    if (maxWords <= 0) maxWords = 20000;
    if (maxWords < kMinWordsPerSection) {
        return error("max_words_per_section must be at least " + std::to_string(kMinWordsPerSection) + " (got " +
                     std::to_string(maxWords) + "); leave it out for the default of 20000.");
    }
    // Sections of at most maxWords words, cut at whitespace.
    std::vector<std::string> sections;
    std::size_t words = 0, start = 0;
    bool inWord = false;
    for (std::size_t i = 0; i < text.size(); ++i) {
        const bool ws = std::isspace(static_cast<unsigned char>(text[i])) != 0;
        if (!ws && !inWord && ++words > static_cast<std::size_t>(maxWords)) {
            sections.push_back(text.substr(start, i - start));
            start = i;
            words = 1;
        }
        inWord = !ws;
    }
    sections.push_back(text.substr(start));
    const std::string instruction =
        "Summarize the document text below (" + style + " summary). Keep every number, name, date and section or "
        "page reference exactly as written; do not add anything the text does not say.";
    try {
        std::vector<std::string> parts;
        for (std::size_t i = 0; i < sections.size(); ++i) {
            parts.push_back(summarize_(sections.size() > 1 ? instruction + " This is part " + std::to_string(i + 1) +
                                                                 " of " + std::to_string(sections.size()) + "."
                                                           : instruction,
                                       sections[i]));
        }
        std::string summary = parts.front();
        if (parts.size() > 1) {
            std::string joined;
            for (std::size_t i = 0; i < parts.size(); ++i) joined += "Part " + std::to_string(i + 1) + ":\n" + parts[i] + "\n\n";
            summary = summarize_("Combine these part summaries of one document into a single " + style +
                                     " summary, keeping every specific fact.",
                                 joined);
        }
        return {{"status", "success"}, {"file", path}, {"summary_type", style}, {"sections", sections.size()}, {"summary", summary}};
    } catch (const std::exception& e) {
        return error(std::string("Could not summarize ") + raw + ": " + e.what());
    }
}

json DocumentIndex::listIndexed() const {
    std::lock_guard<std::mutex> lock(mutex_);
    json docs = json::array();
    for (const auto& [f, info] : files_) {
        json d = {{"file_path", f}, {"name", fs::u8path(f).filename().u8string()}, {"num_chunks", info.chunks}, {"type", info.kind}};
        if (info.pages) d["num_pages"] = info.pages;
        docs.push_back(d);
    }
    return {{"status", "success"}, {"documents", docs}, {"count", docs.size()},
            {"message", docs.empty() ? "No documents indexed." : "These are file names only; query them for content."}};
}

json DocumentIndex::status() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return {{"status", "success"}, {"indexed_files", files_.size()}, {"total_chunks", chunks_.size()},
            {"chunk_size_chars", kChunkChars}, {"chunk_overlap_chars", kOverlapChars}, {"retrieval", "embedding similarity"}};
}

void DocumentIndex::registerTools(ToolRegistry& registry) {
    using T = ToolParamType;
    auto add = [&registry](const std::string& name, const std::string& desc, std::vector<ToolParameter> params,
                           std::function<json(const json&)> fn) {
        ToolInfo info;
        info.name = name;
        info.description = desc;
        info.parameters = std::move(params);
        info.callback = [fn](const json& args) -> json {
            try {
                return fn(args);
            } catch (const std::exception& e) {
                return {{"status", "error"}, {"error", e.what()}};
            }
        };
        registry.registerTool(std::move(info));
    };
    add("index_document",
        "Index a document (PDF, Word, PowerPoint, Excel, text, Markdown) so its content can be searched. "
        "Then call query_specific_file with the question.",
        {{"file_path", T::STRING, true, "The document to index"}},
        [this](const json& a) { return indexDocument(a.value("file_path", "")); });
    add("query_documents",
        "Search every indexed document for passages relevant to a question; returns the passages with "
        "their file and page. Use one query per fact.",
        {{"query", T::STRING, true, "What to look for"}},
        [this](const json& a) { return queryDocuments(a.value("query", "")); });
    add("query_specific_file",
        "Search one indexed document for passages relevant to a question; returns the passages with their page.",
        {{"file_path", T::STRING, true, "The indexed document (path or file name)"},
         {"query", T::STRING, true, "What to look for"}},
        [this](const json& a) { return queryDocuments(a.value("query", ""), a.value("file_path", "")); });
    add("index_directory", "Index every supported document in a folder (recursive=true for subfolders).",
        {{"directory_path", T::STRING, true, "The folder"}, {"recursive", T::BOOLEAN, false, "Include subfolders"}},
        [this](const json& a) {
            return indexDirectory(a.value("directory_path", ""), a.contains("recursive") && a["recursive"].is_boolean() && a["recursive"].get<bool>());
        });
    add("search_indexed_chunks", "Find exact text or a regular expression in the indexed documents.",
        {{"pattern", T::STRING, true, "Text or regex"}}, [this](const json& a) { return searchChunks(a.value("pattern", "")); });
    add("summarize_document",
        "Summarize a whole document, section by section for long ones. summary_type: detailed (default), brief or bullets.",
        {{"file_path", T::STRING, true, "The document"},
         {"summary_type", T::STRING, false, "detailed, brief or bullets"},
         {"max_words_per_section", T::INTEGER, false, "Words per section (default 20000)"}},
        [this](const json& a) {
            return summarizeDocument(a.value("file_path", ""), a.value("summary_type", "detailed"),
                                     a.contains("max_words_per_section") && a["max_words_per_section"].is_number()
                                         ? a["max_words_per_section"].get<long long>()
                                         : 20000);
        });
    add("list_indexed_documents", "List the documents indexed in this session (names only, not content).", {},
        [this](const json&) { return listIndexed(); });
    add("rag_status", "How many documents and chunks are indexed.", {}, [this](const json&) { return status(); });
}

// ---------------------------------------------------------------------------
// CodeIndex
// ---------------------------------------------------------------------------

CodeIndex::CodeIndex(Embedder embedder, std::string defaultRoot)
    : embed_(std::move(embedder)), defaultRoot_(std::move(defaultRoot)) {
    VectorIndexOptions o;
    o.metric = Metric::InnerProduct;
    o.normalizeOnAdd = true;
    index_ = std::make_unique<VectorIndex>(o);
}

CodeIndex::~CodeIndex() = default;

json CodeIndex::indexCodebase(const std::string& raw) {
    const std::string wanted = raw.empty() ? defaultRoot_ : raw;
    const std::string root = resolve_ ? resolve_(wanted) : wanted;
    if (root.empty()) return error("'" + wanted + "' is outside the folders GAIA may read.");
    std::error_code ec;
    if (!fs::is_directory(fs::u8path(root), ec)) return error("Not a folder: " + wanted);
    static const std::set<std::string> code = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".py", ".js", ".jsx", ".ts", ".tsx",
                                               ".go", ".rs", ".java", ".kt", ".cs", ".rb", ".php", ".swift", ".m", ".sh",
                                               ".ps1", ".lua", ".sql", ".md", ".toml", ".yaml", ".yml", ".json", ".cmake",
                                               ".txt", ".mdx", ".vue", ".svelte", ".scala"};
    const GitignoreMatcher ignore = GitignoreMatcher::forDirectory(root);
    std::vector<Chunk> fresh;
    std::size_t files = 0;
    bool capped = false;
    for (auto it = fs::recursive_directory_iterator(fs::u8path(root), fs::directory_options::skip_permission_denied, ec);
         !ec && it != fs::recursive_directory_iterator(); it.increment(ec)) {
        std::error_code x;
        const std::string name = it->path().filename().u8string();
        const bool isDir = it->is_directory(x);
        const std::string rel = fs::relative(it->path(), fs::u8path(root), x).generic_u8string();
        if ((!name.empty() && name[0] == '.') || name == "node_modules" || name == "build" || name == "dist" ||
            ignore.isIgnored(it->path().u8string(), isDir)) {
            if (isDir) it.disable_recursion_pending();
            continue;
        }
        if (isDir || it->is_symlink(x) || !code.count(lower(it->path().extension().u8string())) ||
            it->file_size(x) > 1024 * 1024 || (refuse_ && !refuse_(it->path().u8string()).empty())) {
            continue;
        }
        if (files >= kMaxCodeFiles) {
            capped = true;
            break;
        }
        std::ifstream in(it->path(), std::ios::binary);
        std::vector<std::string> lines;
        for (std::string l; std::getline(in, l);) lines.push_back(l);
        ++files;
        // 60-line windows with 10 lines of overlap.
        for (std::size_t s = 0; s < lines.size(); s += 50) {
            const std::size_t e = std::min(lines.size(), s + 60);
            std::string text = rel + "\n";
            for (std::size_t k = s; k < e; ++k) text += lines[k] + "\n";
            if (text.size() > DocumentIndex::kChunkChars) text = text.substr(0, utf8Cut(text, DocumentIndex::kChunkChars));
            fresh.push_back({rel, static_cast<int>(s + 1), static_cast<int>(e), text});
            if (e == lines.size()) break;
        }
    }
    // Built aside and swapped in whole, so a failure keeps the previous index and its chunks together.
    VectorIndexOptions o;
    o.metric = Metric::InnerProduct;
    o.normalizeOnAdd = true;
    auto next = std::make_unique<VectorIndex>(o);
    try {
        std::vector<std::string> texts;
        for (const auto& c : fresh) texts.push_back(c.text);
        const auto vectors = embedAll(embed_, texts, 0);
        for (std::size_t i = 0; i < fresh.size(); ++i) next->add(std::to_string(i), vectors[i]);
    } catch (const std::exception& e) {
        return error(std::string("Could not index the code: ") + e.what() + ". search_file_content still finds exact text.");
    }
    std::lock_guard<std::mutex> lock(mutex_);
    index_ = std::move(next);
    chunks_ = std::move(fresh);
    files_ = files;
    root_ = root;
    json out = {{"status", "success"}, {"repo_path", root}, {"files_indexed", files}, {"chunks", chunks_.size()},
                {"message", "Indexed " + std::to_string(files) + " files. Use search_code_index to find code by what it does."}};
    if (capped) {
        out["warnings"] = json::array({"the repository holds more than " + std::to_string(kMaxCodeFiles) +
                                       " source files; only the first " + std::to_string(kMaxCodeFiles) +
                                       " found were indexed. Pass a subfolder as repo_path to reach the rest."});
    }
    return out;
}

json CodeIndex::search(const std::string& query, const std::string& scope, long long topK) {
    if (query.find_first_not_of(" \t\r\n") == std::string::npos) return error("query is required");
    // The first search builds the index, so a task that never searches never pays for it.
    if (status()["indexed"] == false) {
        const json built = indexCodebase("");
        if (built.value("status", "") != "success") return built;
    }
    std::vector<float> q;
    try {
        q = embed_({query}).at(0);
    } catch (const std::exception& e) {
        return error(std::string("Code search failed: ") + e.what());
    }
    std::lock_guard<std::mutex> lock(mutex_);
    topK = std::clamp<long long>(topK <= 0 ? 10 : topK, 1, 50);
    const std::string filter = (scope.empty() || scope == "all") ? "" : lower(scope);
    json results = json::array();
    for (const auto& [id, score] : index_->search(q, filter.empty() ? static_cast<std::size_t>(topK) : chunks_.size())) {
        const Chunk& c = chunks_.at(std::stoul(id));
        if (!filter.empty() && lower(c.file).find(filter) == std::string::npos) continue;
        results.push_back({{"file", c.file}, {"lines", std::to_string(c.startLine) + "-" + std::to_string(c.endLine)},
                           {"score", score}, {"snippet", c.text.substr(c.text.find('\n') + 1)}});
        if (results.size() >= static_cast<std::size_t>(topK)) break;
    }
    return {{"status", "success"}, {"query", query}, {"repo_path", root_}, {"results", results}, {"count", results.size()}};
}

json CodeIndex::status() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return {{"status", "success"}, {"indexed", !chunks_.empty()}, {"repo_path", root_.empty() ? defaultRoot_ : root_},
            {"files", files_}, {"chunks", chunks_.size()}};
}

json CodeIndex::clear() {
    std::lock_guard<std::mutex> lock(mutex_);
    chunks_.clear();
    files_ = 0;
    index_->clear();
    return {{"status", "success"}, {"message", "Code index cleared."}};
}

void CodeIndex::registerTools(ToolRegistry& registry) {
    using T = ToolParamType;
    auto add = [&registry](const std::string& name, const std::string& desc, std::vector<ToolParameter> params,
                           std::function<json(const json&)> fn) {
        ToolInfo info;
        info.name = name;
        info.description = desc;
        info.parameters = std::move(params);
        info.callback = [fn](const json& args) -> json {
            try {
                return fn(args);
            } catch (const std::exception& e) {
                return {{"status", "error"}, {"error", e.what()}};
            }
        };
        registry.registerTool(std::move(info));
    };
    add("index_codebase", "Build the semantic index of a code repository (default: the working folder).",
        {{"repo_path", T::STRING, false, "Repository folder"}}, [this](const json& a) { return indexCodebase(a.value("repo_path", "")); });
    add("search_code_index",
        "Find code by what it does (\"where are retries handled\"), not by exact text; returns file and line ranges. "
        "Builds the index on first use.",
        {{"query", T::STRING, true, "What the code does"},
         {"scope", T::STRING, false, "all (default) or a path fragment to stay within"},
         {"top_k", T::INTEGER, false, "How many results (default 10)"}},
        [this](const json& a) {
            return search(a.value("query", ""), a.value("scope", "all"),
                          a.contains("top_k") && a["top_k"].is_number() ? a["top_k"].get<long long>() : 10);
        });
    add("get_index_status", "Whether the code index is built, and how big it is.", {}, [this](const json&) { return status(); });
    add("clear_code_index", "Drop the code index.", {}, [this](const json&) { return clear(); });
}

}  // namespace gaia
