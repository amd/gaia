// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/document_index.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <regex>
#include <set>

#include "gaia/ignore.h"
#include "gaia/process.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

constexpr double kRrfK = 60.0;
constexpr double kBm25K1 = 1.2;
constexpr double kBm25B = 0.75;
constexpr size_t kMaxExtractBytes = 64u * 1024u * 1024u;

std::string lowerExt(const std::string& path) {
    std::string e = fs::path(path).extension().string();
    std::transform(e.begin(), e.end(), e.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return e;
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
    ProcessResult r = ProcessRunner::run("unzip -p " + shellQuote(path) + " word/document.xml",
                                         60000, "", {}, kMaxExtractBytes);
    if (r.exitCode != 0) {
        throw std::runtime_error("Can't read Word file " + path + ": " +
                                 (r.exitCode == 127 ? std::string("unzip is not installed")
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

}  // namespace

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
        ProcessResult r = ProcessRunner::run("pdftotext -layout " + shellQuote(path) + " -", 120000,
                                             "", {}, kMaxExtractBytes);
        if (r.stdout_output.size() >= kMaxExtractBytes) {
            throw std::runtime_error("PDF " + path + " has more than " +
                                     std::to_string(kMaxExtractBytes >> 20) +
                                     " MB of text; split it before indexing.");
        }
        if (r.exitCode != 0) {
            throw std::runtime_error(
                "Can't read PDF " + path + ": pdftotext " +
                (r.exitCode == 127 ? std::string("is not installed (install poppler-utils)")
                                   : "failed: " + r.stderr_output.substr(0, 300)));
        }
        size_t start = 0;
        int page = 1;
        const std::string& t = r.stdout_output;
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

std::vector<std::string> DocumentIndex::tokens(const std::string& text) const {
    std::vector<std::string> out;
    std::string cur;
    for (unsigned char c : text) {
        if (std::isalnum(c) || c >= 0x80) {
            cur.push_back(static_cast<char>(std::tolower(c)));
        } else if (!cur.empty()) {
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
        for (auto& c : splitTextIntoChunks(text, chunking_)) fresh.push_back({file, page, std::move(c)});
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
    static const std::set<std::string> skipDirs = {
        ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", "build", "dist",
        "target", ".tox", ".mypy_cache", ".pytest_cache", ".next", ".gradle", "out"};
    const GitignoreMatcher ignore = GitignoreMatcher::forDirectory(root);
    std::error_code ec;
    size_t total = 0;
    fs::recursive_directory_iterator it(root, fs::directory_options::skip_permission_denied, ec), end;
    for (; it != end && !ec; it.increment(ec)) {
        const fs::path& p = it->path();
        const bool dir = it->is_directory(ec);
        if (dir && (skipDirs.count(p.filename().string()) || ignore.isIgnored(p.string(), true))) {
            it.disable_recursion_pending();
            continue;
        }
        if (dir || !it->is_regular_file(ec)) continue;
        if (ignore.isIgnored(p.string(), false)) continue;
        if (it->file_size(ec) > (1u << 20)) continue;
        if (!isSupportedExtension(lowerExt(p.string()))) continue;
        try {
            total += indexFile(p.string());
        } catch (const std::exception& e) {
            if (failures) failures->push_back(p.string() + ": " + e.what());
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
        if (fs::is_directory(path, ec)) {
            for (const auto& e : fs::recursive_directory_iterator(path, ec)) {
                if (!e.is_regular_file(ec)) continue;
                const std::string ext = lowerExt(e.path().string());
                if (ext == ".pdf" || ext == ".docx" || isSupportedExtension(ext)) {
                    targets.push_back(e.path().string());
                }
            }
        } else {
            targets.push_back(path);
        }
        json done = json::array(), failed = json::array();
        size_t total = 0;
        for (const auto& t : targets) {
            try {
                const size_t n = indexFile(t);
                total += n;
                done.push_back({{"file", t}, {"chunks", n}});
            } catch (const std::exception& e) {
                failed.push_back({{"file", t}, {"error", e.what()}});
            }
        }
        json out = {{"status", done.empty() ? "error" : "success"}, {"indexed", done},
                    {"chunks", total}, {"retrieval", mode}};
        if (!failed.empty()) out["failed"] = failed;
        if (done.empty()) out["error"] = targets.empty() ? "No supported files at " + path
                                                         : "Nothing could be indexed (see failed).";
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
