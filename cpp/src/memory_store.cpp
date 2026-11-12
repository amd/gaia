// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/memory_store.h"

#include <algorithm>
#include <chrono>
#include <ctime>
#include <filesystem>
#include <random>
#include <regex>
#include <sstream>

#include "gaia/secrets.h"
#include "gaia/types.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

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
    if (std::regex_search(content, secret) || looksLikeCredential(content)) {
        return "Not stored — this looks like a credential (API key, password, or similar "
               "secret). GAIA does not keep secrets in memory, so a new chat won't know it. "
               "The user's message is still in this chat, as it was sent.";
    }
    return "";
}

MemoryStore::MemoryStore(const std::string& path) {
    if (path == ":memory:") {
        db_ = std::make_unique<Database>(Database::inMemory());
    } else {
        std::error_code ec;
        fs::create_directories(fs::path(path).parent_path(), ec);
        db_ = std::make_unique<Database>(path);
    }
    db_->migrate({Migration::fromSql(1, "knowledge + fts", R"(
CREATE TABLE knowledge (
    id TEXT PRIMARY KEY, category TEXT NOT NULL, content TEXT NOT NULL,
    domain TEXT, entity TEXT, sensitive INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE VIRTUAL TABLE knowledge_fts USING fts5(id UNINDEXED, content, domain, category,
    tokenize='porter unicode61');
)")});
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

    std::lock_guard<std::mutex> lock(mutex_);
    // The same statement twice is one memory, refreshed.
    auto dup = db_->prepare("SELECT id FROM knowledge WHERE content = ? AND category = ?");
    dup.bindAll(content, category);
    const std::string now = nowIso();
    if (dup.step()) {
        const std::string id = dup.columnText(0);
        db_->prepare("UPDATE knowledge SET updated_at = ? WHERE id = ?").bindAll(now, id).execute();
        return id;
    }
    const std::string id = newId();
    db_->prepare("INSERT INTO knowledge VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
        .bindAll(id, category, content, domain, entity, sensitive ? 1 : 0, now, now)
        .execute();
    db_->prepare("INSERT INTO knowledge_fts (id, content, domain, category) VALUES (?, ?, ?, ?)")
        .bindAll(id, content, domain, category)
        .execute();
    return id;
}

std::vector<MemoryEntry> MemoryStore::recall(const std::string& query, const std::string& category,
                                             int limit, bool includeSensitive) {
    limit = std::clamp(limit, 1, 100);
    std::lock_guard<std::mutex> lock(mutex_);
    const std::string cols =
        "k.id, k.category, k.content, k.domain, k.entity, k.sensitive, k.created_at, k.updated_at";
    std::string where = includeSensitive ? "1=1" : "k.sensitive = 0";
    if (!category.empty()) where += " AND k.category = ?";
    const std::string fts = ftsQuery(query);
    std::vector<MemoryEntry> out;
    if (!fts.empty()) {
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
    auto s = db_->prepare("SELECT " + cols + " FROM knowledge k WHERE " + where +
                          " ORDER BY k.updated_at DESC LIMIT ?");
    int i = 1;
    if (!category.empty()) s.bindText(i++, category);
    s.bindInt64(i, limit);
    while (s.step()) out.push_back(rowToEntry(s));
    return out;
}

bool MemoryStore::update(const std::string& id, const std::string& content) {
    const std::string why = refusalFor(content);
    if (!why.empty()) throw std::invalid_argument(why);
    std::lock_guard<std::mutex> lock(mutex_);
    db_->prepare("UPDATE knowledge SET content = ?, updated_at = ? WHERE id = ?")
        .bindAll(content, nowIso(), id)
        .execute();
    if (db_->changes() == 0) return false;
    db_->prepare("UPDATE knowledge_fts SET content = ? WHERE id = ?").bindAll(content, id).execute();
    return true;
}

bool MemoryStore::forget(const std::string& id) {
    std::lock_guard<std::mutex> lock(mutex_);
    db_->prepare("DELETE FROM knowledge WHERE id = ?").bindAll(id).execute();
    const bool removed = db_->changes() > 0;
    db_->prepare("DELETE FROM knowledge_fts WHERE id = ?").bindAll(id).execute();
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
    if (prefs.empty()) return "";
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

    ToolInfo recall;
    recall.name = "recall";
    recall.description =
        "Search remembered facts by keywords, or list the newest ones when no query is given.";
    recall.parameters = {
        {"query", ToolParamType::STRING, false, "Words to search for."},
        {"category", ToolParamType::STRING, false, "Only this category."},
        {"limit", ToolParamType::INTEGER, false, "Most entries to return (default 10)."},
        {"include_sensitive", ToolParamType::BOOLEAN, false, "Include entries marked sensitive."},
    };
    recall.policy = ToolPolicy::ALLOW;
    recall.callback = [this](const json& a) -> json {
        json items = json::array();
        for (const auto& e : this->recall(a.value("query", ""), a.value("category", ""),
                                          a.value("limit", 10), a.value("include_sensitive", false))) {
            items.push_back(entryJson(e));
        }
        return {{"status", "success"}, {"memories", items}, {"count", items.size()}};
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
