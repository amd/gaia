// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/memory_store.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <map>
#include <random>
#include <regex>
#include <sstream>

#include "gaia/secrets.h"
#include "gaia/types.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

constexpr double kRrfK = 60.0;
constexpr double kRrfWeightVector = 0.6;
constexpr double kRrfWeightBm25 = 0.4;
constexpr const char* kEmbedderMetaKey = "embedder_id";

std::vector<float> normalizeVector(const std::vector<float>& raw, const std::string& context,
                                   const std::string& model) {
    if (raw.empty()) {
        throw std::runtime_error("Embedder returned an empty vector for " + context +
                                 (model.empty() ? "" : " (model " + model + ")"));
    }
    double normSq = 0.0;
    for (float x : raw) {
        if (!std::isfinite(x)) {
            throw std::runtime_error("Embedder returned a non-finite value for " + context +
                                     (model.empty() ? "" : " (model " + model + ")"));
        }
        normSq += static_cast<double>(x) * static_cast<double>(x);
    }
    if (normSq == 0.0) {
        throw std::runtime_error("Embedder returned an all-zero vector for " + context +
                                 (model.empty() ? "" : " (model " + model + ")"));
    }
    const double invNorm = 1.0 / std::sqrt(normSq);
    std::vector<float> out(raw.size());
    for (size_t i = 0; i < raw.size(); ++i) {
        out[i] = static_cast<float>(static_cast<double>(raw[i]) * invNorm);
    }
    return out;
}

Blob encodeVector(const std::vector<float>& unit) {
    Blob out(unit.size() * 4);
    for (size_t i = 0; i < unit.size(); ++i) {
        std::uint32_t bits = 0;
        std::memcpy(&bits, &unit[i], sizeof(bits));
        out[i * 4 + 0] = static_cast<std::uint8_t>(bits & 0xFFu);
        out[i * 4 + 1] = static_cast<std::uint8_t>((bits >> 8) & 0xFFu);
        out[i * 4 + 2] = static_cast<std::uint8_t>((bits >> 16) & 0xFFu);
        out[i * 4 + 3] = static_cast<std::uint8_t>((bits >> 24) & 0xFFu);
    }
    return out;
}

std::vector<float> decodeVector(const Blob& blob) {
    if (blob.empty() || (blob.size() % 4) != 0) return {};
    const size_t n = blob.size() / 4;
    std::vector<float> out(n);
    for (size_t i = 0; i < n; ++i) {
        const std::uint32_t bits = static_cast<std::uint32_t>(blob[i * 4 + 0]) |
                                   (static_cast<std::uint32_t>(blob[i * 4 + 1]) << 8) |
                                   (static_cast<std::uint32_t>(blob[i * 4 + 2]) << 16) |
                                   (static_cast<std::uint32_t>(blob[i * 4 + 3]) << 24);
        float v = 0.0f;
        std::memcpy(&v, &bits, sizeof(v));
        if (!std::isfinite(v)) return {};
        out[i] = v;
    }
    return out;
}

double dotProduct(const std::vector<float>& a, const std::vector<float>& b) {
    double sum = 0.0;
    for (size_t i = 0; i < a.size(); ++i) {
        sum += static_cast<double>(a[i]) * static_cast<double>(b[i]);
    }
    return sum;
}

std::string nowIso() {
    const std::time_t t = std::time(nullptr);
    std::tm tm{};
#ifdef _WIN32
    gmtime_s(&tm, &t);
#else
    gmtime_r(&t, &tm);
#endif
    char buf[32];
    std::strftime(buf, sizeof buf, "%Y-%m-%dT%H:%M:%SZ", &tm);
    return buf;
}

std::string newId() {
    static thread_local std::mt19937_64 rng(std::random_device{}());
    std::ostringstream out;
    out << "m" << std::hex << rng();
    return out.str();
}

// FTS5 query from free text: each word quoted, OR-joined, so punctuation in
// the user's words can never be read as FTS syntax.
std::string ftsQuery(const std::string& text) {
    std::string out, word;
    auto flush = [&] {
        if (word.empty()) return;
        if (!out.empty()) out += " OR ";
        out += "\"" + word + "\"";
        word.clear();
    };
    for (unsigned char c : text) {
        if (std::isalnum(c) || c >= 0x80) word.push_back(static_cast<char>(c));
        else flush();
    }
    flush();
    return out;
}

MemoryEntry rowToEntry(Statement& s) {
    MemoryEntry e;
    e.id = s.columnText(0);
    e.category = s.columnText(1);
    e.content = s.columnText(2);
    e.domain = s.isNull(3) ? "" : s.columnText(3);
    e.entity = s.isNull(4) ? "" : s.columnText(4);
    e.sensitive = s.columnInt(5) != 0;
    e.createdAt = s.columnText(6);
    e.updatedAt = s.columnText(7);
    return e;
}

json entryJson(const MemoryEntry& e) {
    json j = {{"id", e.id}, {"category", e.category}, {"content", e.content},
              {"updated_at", e.updatedAt}};
    if (!e.domain.empty()) j["domain"] = e.domain;
    if (!e.entity.empty()) j["entity"] = e.entity;
    if (e.sensitive) j["sensitive"] = true;
    return j;
}

}  // namespace

std::string MemoryStore::defaultPath() {
    std::string base = getEnvVar("GAIA_HOME");
    if (base.empty()) {
#ifdef _WIN32
        const std::string home = getEnvVar("USERPROFILE");
#else
        const std::string home = getEnvVar("HOME");
#endif
        if (home.empty()) throw std::runtime_error("Can't place the memory store: HOME is not set");
        base = (fs::path(home) / ".gaia").string();
    }
    return (fs::path(base) / "memory-cpp.db").string();
}

const std::vector<std::string>& MemoryStore::categories() {
    static const std::vector<std::string> c = {"fact", "preference", "error", "skill", "note", "reminder"};
    return c;
}

std::string MemoryStore::refusalFor(const std::string& content) {
    static const std::regex secret(
        R"((sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY|\b(password|passwd|api[ _-]?key|secret|token)\b\s*[:=]\s*\S{6,}))",
        std::regex::icase);
    // In overlapping windows: std::regex recurses once per character and a long fact overflows the stack.
    constexpr size_t kWindow = 4096, kOverlap = 256;
    bool matched = false;
    for (size_t at = 0; !matched && at < content.size(); at += kWindow - kOverlap) {
        const auto first = content.begin() + static_cast<std::string::difference_type>(at);
        const auto last = content.begin() + static_cast<std::string::difference_type>((std::min)(content.size(), at + kWindow));
        matched = std::regex_search(first, last, secret);
        if (at + kWindow >= content.size()) break;
    }
    if (matched || looksLikeCredential(content)) {
        return "Not stored — this looks like a credential (API key, password, or similar "
               "secret). GAIA does not keep secrets in memory, so a new chat won't know it. "
               "The user's message is still in this chat, as it was sent.";
    }
    return "";
}

MemoryStore::MemoryStore(const std::string& path, EmbedFunction embed, std::string embeddingModel,
                         std::string embeddingNote)
    : embed_(std::move(embed)),
      embeddingModel_(std::move(embeddingModel)),
      embeddingNote_(std::move(embeddingNote)) {
    if (path == ":memory:") {
        db_ = std::make_unique<Database>(Database::inMemory());
    } else {
        std::error_code ec;
        fs::create_directories(fs::path(path).parent_path(), ec);
        db_ = std::make_unique<Database>(path);
    }
    db_->migrate({
        Migration::fromSql(1, "knowledge + fts", R"(
CREATE TABLE knowledge (
    id TEXT PRIMARY KEY, category TEXT NOT NULL, content TEXT NOT NULL,
    domain TEXT, entity TEXT, sensitive INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE VIRTUAL TABLE knowledge_fts USING fts5(id UNINDEXED, content, domain, category,
    tokenize='porter unicode61');
)"),
        Migration{2, "embeddings + meta", [](Database& db) {
            db.addColumnIfMissing("knowledge", "embedding BLOB");
            db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);");
        }},
    });
    if (embed_ && !embeddingModel_.empty()) {
        std::lock_guard<std::mutex> lock(mutex_);
        reconcileEmbedderLocked();
    }
}

void MemoryStore::reconcileEmbedderLocked() {
    std::string prior;
    {
        auto s = db_->prepare("SELECT value FROM meta WHERE key = ?");
        s.bindText(1, kEmbedderMetaKey);
        if (s.step()) prior = s.columnText(0);
    }
    if (prior == embeddingModel_) return;
    Transaction txn(*db_, Transaction::Behavior::Immediate);
    db_->execute("UPDATE knowledge SET embedding = NULL WHERE embedding IS NOT NULL");
    db_->prepare("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value")
        .bindAll(kEmbedderMetaKey, embeddingModel_)
        .execute();
    txn.commit();
}

std::string MemoryStore::remember(const std::string& content, const std::string& category,
                                  const std::string& domain, const std::string& entity,
                                  bool sensitive) {
    if (content.find_first_not_of(" \t\r\n") == std::string::npos) {
        throw std::invalid_argument("Nothing to remember: the fact is empty.");
    }
    const auto& cats = categories();
    if (std::find(cats.begin(), cats.end(), category) == cats.end()) {
        throw std::invalid_argument("Unknown category '" + category +
                                    "'. Use fact, preference, error, skill, note or reminder.");
    }
    const std::string why = refusalFor(content);
    if (!why.empty()) throw std::invalid_argument(why);

    Blob emb;
    const bool hasEmb = static_cast<bool>(embed_);
    if (hasEmb) {
        const auto vecs = embed_({content});
        if (vecs.size() != 1) {
            throw std::runtime_error("Embedder returned " + std::to_string(vecs.size()) +
                                     " vectors for 1 memory entry");
        }
        emb = encodeVector(normalizeVector(vecs[0], "memory entry", embeddingModel_));
    }

    std::lock_guard<std::mutex> lock(mutex_);
    if (hasEmb && !embeddingModel_.empty()) reconcileEmbedderLocked();
    // One write transaction: the duplicate check and both tables change together.
    Transaction txn(*db_, Transaction::Behavior::Immediate);
    const std::string now = nowIso();
    std::string existing;
    bool existingMissingEmb = false;
    {
        // The same statement twice is one memory, refreshed.
        auto dup = db_->prepare("SELECT id, embedding IS NULL FROM knowledge WHERE content = ? AND category = ?");
        dup.bindAll(content, category);
        if (dup.step()) {
            existing = dup.columnText(0);
            existingMissingEmb = dup.columnInt(1) != 0;
        }
    }
    if (!existing.empty()) {
        if (hasEmb && existingMissingEmb) {
            db_->prepare("UPDATE knowledge SET updated_at = ?, embedding = ? WHERE id = ?")
                .bindAll(now, emb, existing)
                .execute();
        } else {
            db_->prepare("UPDATE knowledge SET updated_at = ? WHERE id = ?")
                .bindAll(now, existing)
                .execute();
        }
        txn.commit();
        return existing;
    }
    const std::string id = newId();
    auto ins = db_->prepare(
        "INSERT INTO knowledge (id, category, content, domain, entity, sensitive, created_at, updated_at, embedding)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)");
    ins.bindText(1, id);
    ins.bindText(2, category);
    ins.bindText(3, content);
    ins.bindText(4, domain);
    ins.bindText(5, entity);
    ins.bindInt64(6, sensitive ? 1 : 0);
    ins.bindText(7, now);
    ins.bindText(8, now);
    if (hasEmb) ins.bindBlob(9, emb);
    else ins.bindNull(9);
    ins.execute();
    db_->prepare("INSERT INTO knowledge_fts (id, content, domain, category) VALUES (?, ?, ?, ?)")
        .bindAll(id, content, domain, category)
        .execute();
    txn.commit();
    return id;
}

size_t MemoryStore::backfillEmbeddings() {
    if (!embed_) return 0;
    std::vector<std::pair<std::string, std::string>> missing;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!embeddingModel_.empty()) reconcileEmbedderLocked();
        auto s = db_->prepare("SELECT id, content FROM knowledge WHERE embedding IS NULL");
        while (s.step()) missing.emplace_back(s.columnText(0), s.columnText(1));
    }
    if (missing.empty()) return 0;

    std::vector<std::string> texts;
    texts.reserve(missing.size());
    for (const auto& [_, text] : missing) texts.push_back(text);
    const auto vecs = embed_(texts);
    if (vecs.size() != missing.size()) {
        throw std::runtime_error("Embedder returned " + std::to_string(vecs.size()) +
                                 " vectors for " + std::to_string(missing.size()) + " memory entries");
    }
    std::vector<Blob> blobs;
    blobs.reserve(vecs.size());
    for (size_t i = 0; i < vecs.size(); ++i) {
        blobs.push_back(encodeVector(normalizeVector(vecs[i], "memory " + missing[i].first, embeddingModel_)));
    }

    std::lock_guard<std::mutex> lock(mutex_);
    Transaction txn(*db_, Transaction::Behavior::Immediate);
    size_t updated = 0;
    auto upd = db_->prepare("UPDATE knowledge SET embedding = ? WHERE id = ? AND content = ?");
    for (size_t i = 0; i < missing.size(); ++i) {
        upd.reset();
        upd.clearBindings();
        upd.bindBlob(1, blobs[i]);
        upd.bindText(2, missing[i].first);
        upd.bindText(3, missing[i].second);
        upd.execute();
        if (db_->changes() > 0) ++updated;
    }
    txn.commit();
    return updated;
}

std::vector<MemoryEntry> MemoryStore::recall(const std::string& query, const std::string& category,
                                             int limit, bool includeSensitive) {
    limit = std::clamp(limit, 1, 1000);
    const std::string cols =
        "k.id, k.category, k.content, k.domain, k.entity, k.sensitive, k.created_at, k.updated_at";
    std::string where = includeSensitive ? "1=1" : "k.sensitive = 0";
    if (!category.empty()) where += " AND k.category = ?";
    const std::string fts = ftsQuery(query);
    if (fts.empty()) {
        std::lock_guard<std::mutex> lock(mutex_);
        std::vector<MemoryEntry> out;
        auto s = db_->prepare("SELECT " + cols + " FROM knowledge k WHERE " + where +
                              " ORDER BY k.updated_at DESC LIMIT ?");
        int i = 1;
        if (!category.empty()) s.bindText(i++, category);
        s.bindInt64(i, limit);
        while (s.step()) out.push_back(rowToEntry(s));
        return out;
    }
    if (!embed_) {
        std::lock_guard<std::mutex> lock(mutex_);
        std::vector<MemoryEntry> out;
        auto s = db_->prepare("SELECT " + cols +
                              " FROM knowledge_fts f JOIN knowledge k ON k.id = f.id"
                              " WHERE knowledge_fts MATCH ? AND " + where +
                              " ORDER BY bm25(knowledge_fts) LIMIT ?");
        int i = 1;
        s.bindText(i++, fts);
        if (!category.empty()) s.bindText(i++, category);
        s.bindInt64(i, limit);
        while (s.step()) out.push_back(rowToEntry(s));
        return out;
    }

    backfillEmbeddings();
    const auto qvecs = embed_({query});
    if (qvecs.size() != 1) {
        throw std::runtime_error("Embedder returned " + std::to_string(qvecs.size()) +
                                 " vectors for memory query (expected 1)");
    }
    const std::vector<float> qvec = normalizeVector(qvecs[0], "memory query", embeddingModel_);

    std::lock_guard<std::mutex> lock(mutex_);
    const size_t depth = std::max<size_t>(static_cast<size_t>(limit) * 4, 20);
    std::map<std::string, MemoryEntry> entries;
    std::map<std::string, double> fused;

    {
        auto s = db_->prepare("SELECT " + cols +
                              " FROM knowledge_fts f JOIN knowledge k ON k.id = f.id"
                              " WHERE knowledge_fts MATCH ? AND " + where +
                              " ORDER BY bm25(knowledge_fts) LIMIT ?");
        int i = 1;
        s.bindText(i++, fts);
        if (!category.empty()) s.bindText(i++, category);
        s.bindInt64(i, static_cast<std::int64_t>(depth));
        size_t rank = 0;
        while (s.step()) {
            MemoryEntry e = rowToEntry(s);
            const std::string id = e.id;
            entries.emplace(id, std::move(e));
            fused[id] += kRrfWeightBm25 / (kRrfK + static_cast<double>(rank) + 1.0);
            ++rank;
        }
    }

    struct VecHit {
        double sim;
        MemoryEntry entry;
    };
    std::vector<VecHit> vecHits;
    {
        auto s = db_->prepare("SELECT " + cols + ", k.embedding FROM knowledge k WHERE " + where +
                              " AND k.embedding IS NOT NULL");
        int i = 1;
        if (!category.empty()) s.bindText(i++, category);
        while (s.step()) {
            const std::vector<float> vec = decodeVector(s.columnBlob(8));
            if (vec.size() != qvec.size()) {
                throw std::runtime_error(
                    "Stored memory embedding dimension (" + std::to_string(vec.size()) +
                    ") does not match query embedding dimension (" + std::to_string(qvec.size()) +
                    "); check that the configured embedding model matches the store.");
            }
            const double sim = dotProduct(qvec, vec);
            if (sim >= kMinVectorSimilarity) vecHits.push_back({sim, rowToEntry(s)});
        }
    }
    std::sort(vecHits.begin(), vecHits.end(), [](const VecHit& a, const VecHit& b) {
        if (a.sim != b.sim) return a.sim > b.sim;
        if (a.entry.updatedAt != b.entry.updatedAt) return a.entry.updatedAt > b.entry.updatedAt;
        return a.entry.id < b.entry.id;
    });
    for (size_t rank = 0; rank < vecHits.size() && rank < depth; ++rank) {
        const std::string id = vecHits[rank].entry.id;
        entries.emplace(id, std::move(vecHits[rank].entry));
        fused[id] += kRrfWeightVector / (kRrfK + static_cast<double>(rank) + 1.0);
    }

    std::vector<std::pair<double, std::string>> ranked;
    ranked.reserve(fused.size());
    for (const auto& [id, score] : fused) ranked.emplace_back(score, id);
    std::sort(ranked.begin(), ranked.end(), [&entries](const auto& a, const auto& b) {
        if (a.first != b.first) return a.first > b.first;
        const auto& ea = entries.at(a.second);
        const auto& eb = entries.at(b.second);
        if (ea.updatedAt != eb.updatedAt) return ea.updatedAt > eb.updatedAt;
        return a.second < b.second;
    });
    if (ranked.size() > static_cast<size_t>(limit)) ranked.resize(static_cast<size_t>(limit));

    std::vector<MemoryEntry> out;
    out.reserve(ranked.size());
    for (const auto& [_, id] : ranked) out.push_back(std::move(entries.at(id)));
    return out;
}

bool MemoryStore::update(const std::string& id, const std::string& content) {
    const std::string why = refusalFor(content);
    if (!why.empty()) throw std::invalid_argument(why);

    Blob emb;
    const bool hasEmb = static_cast<bool>(embed_);
    if (hasEmb) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            auto exists = db_->prepare("SELECT 1 FROM knowledge WHERE id = ?");
            exists.bindText(1, id);
            if (!exists.step()) return false;
        }
        const auto vecs = embed_({content});
        if (vecs.size() != 1) {
            throw std::runtime_error("Embedder returned " + std::to_string(vecs.size()) +
                                     " vectors for updated memory entry");
        }
        emb = encodeVector(normalizeVector(vecs[0], "memory " + id, embeddingModel_));
    }

    std::lock_guard<std::mutex> lock(mutex_);
    if (hasEmb && !embeddingModel_.empty()) reconcileEmbedderLocked();
    Transaction txn(*db_, Transaction::Behavior::Immediate);
    auto upd = db_->prepare("UPDATE knowledge SET content = ?, updated_at = ?, embedding = ? WHERE id = ?");
    upd.bindText(1, content);
    upd.bindText(2, nowIso());
    if (hasEmb) upd.bindBlob(3, emb);
    else upd.bindNull(3);
    upd.bindText(4, id);
    upd.execute();
    if (db_->changes() == 0) return false;
    db_->prepare("UPDATE knowledge_fts SET content = ? WHERE id = ?").bindAll(content, id).execute();
    txn.commit();
    return true;
}

bool MemoryStore::forget(const std::string& id) {
    std::lock_guard<std::mutex> lock(mutex_);
    Transaction txn(*db_, Transaction::Behavior::Immediate);
    db_->prepare("DELETE FROM knowledge WHERE id = ?").bindAll(id).execute();
    const bool removed = db_->changes() > 0;
    db_->prepare("DELETE FROM knowledge_fts WHERE id = ?").bindAll(id).execute();
    txn.commit();
    return removed;
}

size_t MemoryStore::count() {
    std::lock_guard<std::mutex> lock(mutex_);
    auto s = db_->prepare("SELECT COUNT(*) FROM knowledge");
    s.step();
    return static_cast<size_t>(s.columnInt64(0));
}

std::string MemoryStore::promptBlock(int limit) {
    const auto prefs = recall("", "preference", limit, false);
    if (prefs.empty()) {
        return count() > 0 ? "You remember facts from earlier chats: use recall to look one up when it could matter.\n"
                           : "";
    }
    std::string out = "==== WHAT YOU KNOW ABOUT THE USER ====\n";
    for (const auto& p : prefs) out += "- " + p.content + "\n";
    return out + "Use recall to look up other remembered facts when they could matter.\n";
}

void MemoryStore::registerTools(ToolRegistry& registry) {
    ToolInfo remember;
    remember.name = "remember";
    remember.description =
        "Store a fact, preference or note the user wants kept across sessions. Store the "
        "user's statement in their own words, one fact per call. Credentials are refused.";
    remember.parameters = {
        {"fact", ToolParamType::STRING, true, "The statement to store, in the user's words."},
        {"category", ToolParamType::STRING, false,
         "fact (default), preference, error, skill, note or reminder."},
        {"domain", ToolParamType::STRING, false, "Sub-type, e.g. work, todo, journal."},
        {"entity", ToolParamType::STRING, false, "Linked entity, e.g. person:Linda."},
        {"sensitive", ToolParamType::BOOLEAN, false, "true to keep it out of default listings."},
    };
    remember.policy = ToolPolicy::ALLOW;
    remember.callback = [this](const json& a) -> json {
        try {
            const std::string id =
                this->remember(a.value("fact", ""), a.value("category", "fact"), a.value("domain", ""),
                               a.value("entity", ""), a.value("sensitive", false));
            return {{"status", "success"}, {"id", id}};
        } catch (const std::exception& e) {
            return {{"status", "error"}, {"error", e.what()}};
        }
    };
    registry.registerTool(std::move(remember));

    const std::string mode =
        usesEmbeddings()
            ? "hybrid (keyword + " + (embeddingModel_.empty() ? std::string("embeddings") : embeddingModel_) + ")"
            : "keyword only (" +
                  (embeddingNote_.empty() ? std::string("no embedding model configured") : embeddingNote_) + ")";

    ToolInfo recall;
    recall.name = "recall";
    recall.description = usesEmbeddings()
                             ? "Search remembered facts by meaning and keywords, or list the newest ones when no query is given."
                             : "Search remembered facts by keywords, or list the newest ones when no query is given.";
    recall.parameters = {
        {"query", ToolParamType::STRING, false, "Question or words to search for."},
        {"category", ToolParamType::STRING, false, "Only this category."},
        {"limit", ToolParamType::INTEGER, false, "Most entries to return (default 10)."},
        {"include_sensitive", ToolParamType::BOOLEAN, false,
         "Include entries marked sensitive in a listing; a search always includes them."},
    };
    recall.policy = ToolPolicy::ALLOW;
    recall.callback = [this, mode](const json& a) -> json {
        try {
            json items = json::array();
            const std::string query = a.value("query", "");
            // Sensitive only keeps a fact out of listings; asked for by name, it is the user's own.
            const bool sensitive = !ftsQuery(query).empty() || a.value("include_sensitive", false);
            // The model's own recall stays small; the Memory page asks for more.
            const int limit = std::clamp(a.value("limit", 10), 1, 100);
            for (const auto& e : this->recall(query, a.value("category", ""), limit, sensitive)) {
                items.push_back(entryJson(e));
            }
            return {{"status", "success"}, {"memories", items}, {"count", items.size()}, {"retrieval", mode}};
        } catch (const std::exception& e) {
            return {{"status", "error"}, {"error", e.what()}};
        }
    };
    registry.registerTool(std::move(recall));

    ToolInfo update;
    update.name = "update_memory";
    update.description = "Replace the text of a remembered fact (by id from recall) when it changed.";
    update.parameters = {{"id", ToolParamType::STRING, true, "Memory id."},
                         {"fact", ToolParamType::STRING, true, "The corrected statement."}};
    update.policy = ToolPolicy::ALLOW;
    update.callback = [this](const json& a) -> json {
        try {
            if (!this->update(a.value("id", ""), a.value("fact", ""))) {
                return {{"status", "error"}, {"error", "No memory with id " + a.value("id", "")}};
            }
            return {{"status", "success"}};
        } catch (const std::exception& e) {
            return {{"status", "error"}, {"error", e.what()}};
        }
    };
    registry.registerTool(std::move(update));

    ToolInfo forget;
    forget.name = "forget";
    forget.description = "Delete one remembered fact by id (from recall).";
    forget.parameters = {{"id", ToolParamType::STRING, true, "Memory id."}};
    forget.policy = ToolPolicy::CONFIRM;
    forget.callback = [this](const json& a) -> json {
        if (!this->forget(a.value("id", ""))) {
            return {{"status", "error"}, {"error", "No memory with id " + a.value("id", "")}};
        }
        return {{"status", "success"}};
    };
    registry.registerTool(std::move(forget));
}

}  // namespace gaia
