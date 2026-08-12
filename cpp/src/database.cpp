// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/database.h"

#include <atomic>
#include <cstdio>
#include <filesystem>
#include <utility>

#include "sqlite3.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

constexpr const char* kMemoryPath = ":memory:";

bool isMemoryPath(const std::string& path) {
    return path == kMemoryPath;
}

/// Build the multi-line error text every DatabaseError carries: the SQLite
/// message, then the context needed to act on it.
std::string formatError(const std::string& what,
                        const std::string& sqliteMessage,
                        int rc,
                        const std::string& dbPath,
                        const std::string& sql) {
    std::string out = what;
    if (!sqliteMessage.empty()) {
        out += ": " + sqliteMessage;
    }
    if (rc != SQLITE_OK) {
        const char* name = sqlite3_errstr(rc);
        out += " [code=" + std::to_string(rc);
        if (name != nullptr) {
            out += " ";
            out += name;
        }
        out += "]";
    }
    out += "\n  database: " + (dbPath.empty() ? std::string("<none>") : dbPath);
    if (!sql.empty()) {
        out += "\n  statement: " + sql;
    }
    return out;
}

std::string messageFor(sqlite3* db) {
    if (db == nullptr) {
        return {};
    }
    const char* msg = sqlite3_errmsg(db);
    return msg != nullptr ? std::string(msg) : std::string{};
}

/// Quote an identifier for interpolation into PRAGMA/DDL, where SQLite offers
/// no bind parameter. Doubling embedded quotes is the only escape SQLite has.
std::string quoteIdentifier(const std::string& name) {
    std::string out = "\"";
    for (char c : name) {
        if (c == '"') {
            out += "\"\"";
        } else {
            out += c;
        }
    }
    out += "\"";
    return out;
}

ColumnType toColumnType(int sqliteType) {
    switch (sqliteType) {
        case SQLITE_INTEGER:
            return ColumnType::Integer;
        case SQLITE_FLOAT:
            return ColumnType::Float;
        case SQLITE_TEXT:
            return ColumnType::Text;
        case SQLITE_BLOB:
            return ColumnType::BlobValue;
        default:
            return ColumnType::Null;
    }
}

}  // namespace

// ---------------------------------------------------------------------------
// DatabaseError
// ---------------------------------------------------------------------------

DatabaseError::DatabaseError(const std::string& message,
                             int code,
                             std::string dbPath,
                             std::string sql)
    : std::runtime_error(message),
      code_(code),
      dbPath_(std::move(dbPath)),
      sql_(std::move(sql)) {}

// ---------------------------------------------------------------------------
// Statement
// ---------------------------------------------------------------------------

Statement::Statement(sqlite3* db, sqlite3_stmt* stmt, std::string sql, std::string dbPath)
    : db_(db), stmt_(stmt), sql_(std::move(sql)), dbPath_(std::move(dbPath)) {}

Statement::~Statement() {
    if (stmt_ != nullptr) {
        // sqlite3_finalize returns the last step()'s error code, which the
        // caller has already seen thrown. Nothing actionable is lost here.
        sqlite3_finalize(stmt_);
        stmt_ = nullptr;
    }
}

Statement::Statement(Statement&& other) noexcept
    : db_(other.db_),
      stmt_(other.stmt_),
      sql_(std::move(other.sql_)),
      dbPath_(std::move(other.dbPath_)) {
    other.db_ = nullptr;
    other.stmt_ = nullptr;
}

Statement& Statement::operator=(Statement&& other) noexcept {
    if (this != &other) {
        if (stmt_ != nullptr) {
            sqlite3_finalize(stmt_);
        }
        db_ = other.db_;
        stmt_ = other.stmt_;
        sql_ = std::move(other.sql_);
        dbPath_ = std::move(other.dbPath_);
        other.db_ = nullptr;
        other.stmt_ = nullptr;
    }
    return *this;
}

void Statement::requireStatement(const char* what) const {
    if (stmt_ == nullptr) {
        throw DatabaseError(
            formatError(std::string(what) + " on a moved-from statement", "", SQLITE_MISUSE,
                        dbPath_, sql_),
            SQLITE_MISUSE, dbPath_, sql_);
    }
}

void Statement::fail(const std::string& what, int rc) const {
    throw DatabaseError(formatError(what, messageFor(db_), rc, dbPath_, sql_), rc, dbPath_, sql_);
}

void Statement::checkColumn(int col, const char* what) const {
    requireStatement(what);
    const int count = sqlite3_column_count(stmt_);
    if (col < 0 || col >= count) {
        const std::string message = std::string(what) + ": column index " + std::to_string(col) +
                                    " is out of range (statement has " + std::to_string(count) +
                                    " column(s))";
        throw DatabaseError(formatError(message, "", SQLITE_RANGE, dbPath_, sql_), SQLITE_RANGE,
                            dbPath_, sql_);
    }
}

Statement& Statement::bindNull(int index) {
    requireStatement("bindNull");
    const int rc = sqlite3_bind_null(stmt_, index);
    if (rc != SQLITE_OK) {
        fail("failed to bind NULL to parameter " + std::to_string(index), rc);
    }
    return *this;
}

Statement& Statement::bindInt64(int index, std::int64_t value) {
    requireStatement("bindInt64");
    const int rc = sqlite3_bind_int64(stmt_, index, static_cast<sqlite3_int64>(value));
    if (rc != SQLITE_OK) {
        fail("failed to bind INTEGER", rc);
    }
    return *this;
}

Statement& Statement::bindBool(int index, bool value) {
    return bindInt64(index, value ? 1 : 0);
}

Statement& Statement::bindDouble(int index, double value) {
    requireStatement("bindDouble");
    const int rc = sqlite3_bind_double(stmt_, index, value);
    if (rc != SQLITE_OK) {
        fail("failed to bind REAL", rc);
    }
    return *this;
}

Statement& Statement::bindText(int index, const std::string& value) {
    requireStatement("bindText");
    // SQLITE_TRANSIENT: SQLite copies, so `value` need not outlive this call.
    const int rc = sqlite3_bind_text64(stmt_, index, value.c_str(),
                                       static_cast<sqlite3_uint64>(value.size()),
                                       SQLITE_TRANSIENT, SQLITE_UTF8);
    if (rc != SQLITE_OK) {
        fail("failed to bind TEXT", rc);
    }
    return *this;
}

Statement& Statement::bind(int index, const char* value) {
    if (value == nullptr) {
        return bindNull(index);
    }
    return bindText(index, std::string(value));
}

Statement& Statement::bindBlob(int index, const void* data, std::size_t size) {
    requireStatement("bindBlob");
    // A null pointer with size 0 must still bind an empty BLOB rather than
    // NULL, so hand SQLite a valid pointer in that case.
    static const char kEmpty = 0;
    const void* payload = (data != nullptr) ? data : static_cast<const void*>(&kEmpty);
    const int rc = sqlite3_bind_blob64(stmt_, index, payload, static_cast<sqlite3_uint64>(size),
                                       SQLITE_TRANSIENT);
    if (rc != SQLITE_OK) {
        fail("failed to bind BLOB", rc);
    }
    return *this;
}

Statement& Statement::bindBlob(int index, const Blob& value) {
    return bindBlob(index, value.data(), value.size());
}

int Statement::parameterCount() const {
    requireStatement("parameterCount");
    return sqlite3_bind_parameter_count(stmt_);
}

Statement& Statement::clearBindings() {
    requireStatement("clearBindings");
    const int rc = sqlite3_clear_bindings(stmt_);
    if (rc != SQLITE_OK) {
        fail("failed to clear bindings", rc);
    }
    return *this;
}

bool Statement::step() {
    requireStatement("step");
    const int rc = sqlite3_step(stmt_);
    if (rc == SQLITE_ROW) {
        return true;
    }
    if (rc == SQLITE_DONE) {
        return false;
    }
    fail("statement failed", rc);
}

void Statement::execute() {
    while (step()) {
        // Discard rows; execute() is for statements whose results don't matter.
    }
}

Statement& Statement::reset() {
    requireStatement("reset");
    // sqlite3_reset returns the error from the *previous* step(), which step()
    // has already thrown. Re-throwing it here would surface the same failure
    // twice; reset itself has no separate failure mode.
    sqlite3_reset(stmt_);
    return *this;
}

int Statement::columnCount() const {
    requireStatement("columnCount");
    return sqlite3_column_count(stmt_);
}

std::string Statement::columnName(int col) const {
    checkColumn(col, "columnName");
    const char* name = sqlite3_column_name(stmt_, col);
    return name != nullptr ? std::string(name) : std::string{};
}

ColumnType Statement::columnType(int col) const {
    checkColumn(col, "columnType");
    return toColumnType(sqlite3_column_type(stmt_, col));
}

bool Statement::isNull(int col) const {
    checkColumn(col, "isNull");
    return sqlite3_column_type(stmt_, col) == SQLITE_NULL;
}

std::int64_t Statement::columnInt64(int col) const {
    checkColumn(col, "columnInt64");
    return static_cast<std::int64_t>(sqlite3_column_int64(stmt_, col));
}

int Statement::columnInt(int col) const {
    checkColumn(col, "columnInt");
    return sqlite3_column_int(stmt_, col);
}

bool Statement::columnBool(int col) const {
    return columnInt64(col) != 0;
}

double Statement::columnDouble(int col) const {
    checkColumn(col, "columnDouble");
    return sqlite3_column_double(stmt_, col);
}

std::string Statement::columnText(int col) const {
    checkColumn(col, "columnText");
    const auto* text = sqlite3_column_text(stmt_, col);
    if (text == nullptr) {
        return {};
    }
    const int size = sqlite3_column_bytes(stmt_, col);
    return std::string(reinterpret_cast<const char*>(text), static_cast<std::size_t>(size));
}

Blob Statement::columnBlob(int col) const {
    checkColumn(col, "columnBlob");
    const void* data = sqlite3_column_blob(stmt_, col);
    const int size = sqlite3_column_bytes(stmt_, col);
    if (data == nullptr || size <= 0) {
        return {};
    }
    const auto* bytes = static_cast<const std::uint8_t*>(data);
    return Blob(bytes, bytes + size);
}

// ---------------------------------------------------------------------------
// Migration
// ---------------------------------------------------------------------------

Migration Migration::fromSql(int version, std::string description, std::string script) {
    Migration step;
    step.version = version;
    step.description = std::move(description);
    step.apply = [script = std::move(script)](Database& db) { db.execute(script); };
    return step;
}

// ---------------------------------------------------------------------------
// Database
// ---------------------------------------------------------------------------

Database::Database(const std::string& path) : Database(path, Options{}) {}

Database::Database(const std::string& path, const Options& options) : path_(path) {
    if (path.empty()) {
        // SQLite would read "" as a private temporary on-disk database that
        // vanishes at close. That is almost never what a caller with an empty
        // path variable meant, so say so instead of quietly obliging.
        throw DatabaseError(
            formatError("database path is empty; pass a file path, or \":memory:\" for an "
                        "in-memory database",
                        "", SQLITE_CANTOPEN, "", ""),
            SQLITE_CANTOPEN, "", "");
    }
    const bool memory = isMemoryPath(path);

    int flags = options.readOnly ? SQLITE_OPEN_READONLY : SQLITE_OPEN_READWRITE;
    if (!options.readOnly && (options.createIfMissing || memory)) {
        flags |= SQLITE_OPEN_CREATE;
    }
    // Serialized mode: a Database may be shared across threads without the
    // caller adding its own mutex.
    flags |= SQLITE_OPEN_FULLMUTEX;

    if (!memory && !options.readOnly && options.createIfMissing) {
        const fs::path parent = fs::path(path).parent_path();
        if (!parent.empty()) {
            std::error_code ec;
            fs::create_directories(parent, ec);
            if (ec && !fs::is_directory(parent)) {
                throw DatabaseError(
                    formatError("cannot create parent directory " + parent.string(), ec.message(),
                                SQLITE_CANTOPEN, path_, ""),
                    SQLITE_CANTOPEN, path_, "");
            }
        }
    }

    const int rc = sqlite3_open_v2(path_.c_str(), &db_, flags, nullptr);
    if (rc != SQLITE_OK) {
        // sqlite3_open_v2 hands back a handle even on failure so the message is
        // readable; it must still be closed.
        const std::string message = messageFor(db_);
        sqlite3_close_v2(db_);
        db_ = nullptr;
        throw DatabaseError(formatError("cannot open database", message, rc, path_, ""), rc, path_,
                            "");
    }

    // Everything past this point can throw, and a constructor that throws does
    // not run ~Database — so the handle has to be closed by hand before the
    // exception leaves, or it leaks for the life of the process.
    try {
        // Refuse to load extensions through this connection. Off by default, but
        // stated explicitly so a future caller cannot flip it on by accident.
        sqlite3_db_config(db_, SQLITE_DBCONFIG_ENABLE_LOAD_EXTENSION, 0, nullptr);
        // Defensive mode: SQL cannot corrupt the schema or write FTS5 shadow tables.
        sqlite3_db_config(db_, SQLITE_DBCONFIG_DEFENSIVE, 1, nullptr);

        if (options.busyTimeoutMs > 0) {
            const int brc = sqlite3_busy_timeout(db_, options.busyTimeoutMs);
            if (brc != SQLITE_OK) {
                fail("cannot set busy_timeout", brc);
            }
        }

        // WAL is a file-backed journal mode. An in-memory database has no file
        // to journal, and a read-only connection cannot change the mode of one,
        // so the option simply does not apply in either case.
        if (options.walMode && !memory && !options.readOnly) {
            auto stmt = prepare("PRAGMA journal_mode=WAL");
            if (!stmt.step()) {
                fail("PRAGMA journal_mode=WAL returned no result", SQLITE_ERROR,
                     "PRAGMA journal_mode=WAL");
            }
            const std::string mode = stmt.columnText(0);
            if (mode != "wal") {
                // Loud rather than silent: a database that could not be switched
                // to WAL (some network mounts cannot) has different concurrency
                // semantics than the caller asked for, and that must not be
                // discovered later as a mysterious SQLITE_BUSY.
                const std::string message =
                    "requested WAL journaling but the database is in \"" + mode +
                    "\" mode; the filesystem may not support WAL (network mount?). Set "
                    "Options::walMode = false to accept it explicitly.";
                throw DatabaseError(
                    formatError(message, "", SQLITE_OK, path_, "PRAGMA journal_mode=WAL"),
                    SQLITE_OK, path_, "PRAGMA journal_mode=WAL");
            }
        }

        // Stated in both directions: the build sets SQLITE_DEFAULT_FOREIGN_KEYS=1,
        // so foreignKeys=false has to actively turn it off rather than skip.
        execute(options.foreignKeys ? "PRAGMA foreign_keys=ON" : "PRAGMA foreign_keys=OFF");
    } catch (...) {
        sqlite3_close_v2(db_);
        db_ = nullptr;
        throw;
    }
}

Database Database::inMemory() {
    return inMemory(Options{});
}

Database Database::inMemory(const Options& options) {
    Options opts = options;
    opts.walMode = false;
    return Database(kMemoryPath, opts);
}

Database::~Database() {
    if (db_ != nullptr) {
        // The destructor cannot throw. sqlite3_close_v2 defers the actual close
        // until any leaked statements finalize, so no handle is ever leaked.
        sqlite3_close_v2(db_);
        db_ = nullptr;
    }
}

Database::Database(Database&& other) noexcept : db_(other.db_), path_(std::move(other.path_)) {
    other.db_ = nullptr;
}

Database& Database::operator=(Database&& other) noexcept {
    if (this != &other) {
        if (db_ != nullptr) {
            sqlite3_close_v2(db_);
        }
        db_ = other.db_;
        path_ = std::move(other.path_);
        other.db_ = nullptr;
    }
    return *this;
}

void Database::requireOpen(const char* what) const {
    if (db_ == nullptr) {
        throw DatabaseError(
            formatError(std::string(what) + " on a closed database", "", SQLITE_MISUSE, path_, ""),
            SQLITE_MISUSE, path_, "");
    }
}

void Database::fail(const std::string& what, int rc, const std::string& sql) const {
    throw DatabaseError(formatError(what, messageFor(db_), rc, path_, sql), rc, path_, sql);
}

// prepare() mutates SQLite's internal statement list but not the logical state
// of the database, so the const query helpers below share this one const-safe
// entry point rather than each casting away const.
Statement Database::prepareShared(const std::string& sql) const {
    return const_cast<Database*>(this)->prepare(sql);
}

Statement Database::prepare(const std::string& sql) {
    requireOpen("prepare");
    sqlite3_stmt* stmt = nullptr;
    const int rc = sqlite3_prepare_v2(db_, sql.c_str(), static_cast<int>(sql.size()) + 1, &stmt,
                                      nullptr);
    if (rc != SQLITE_OK || stmt == nullptr) {
        if (stmt != nullptr) {
            sqlite3_finalize(stmt);
        }
        if (rc == SQLITE_OK) {
            // Empty or comment-only SQL compiles to no statement at all.
            throw DatabaseError(
                formatError("SQL contains no statement to prepare", "", SQLITE_ERROR, path_, sql),
                SQLITE_ERROR, path_, sql);
        }
        fail("cannot prepare statement", rc, sql);
    }
    return Statement(db_, stmt, sql, path_);
}

void Database::execute(const std::string& sql) {
    requireOpen("execute");
    char* errorMessage = nullptr;
    const int rc = sqlite3_exec(db_, sql.c_str(), nullptr, nullptr, &errorMessage);
    const std::string message = errorMessage != nullptr ? std::string(errorMessage)
                                                        : messageFor(db_);
    sqlite3_free(errorMessage);
    if (rc != SQLITE_OK) {
        throw DatabaseError(formatError("cannot execute SQL", message, rc, path_, sql), rc, path_,
                            sql);
    }
}

std::int64_t Database::lastInsertRowId() const {
    requireOpen("lastInsertRowId");
    return static_cast<std::int64_t>(sqlite3_last_insert_rowid(db_));
}

std::int64_t Database::changes() const {
    requireOpen("changes");
    return static_cast<std::int64_t>(sqlite3_changes64(db_));
}

int Database::userVersion() const {
    requireOpen("userVersion");
    auto stmt = prepareShared("PRAGMA user_version");
    if (!stmt.step()) {
        fail("PRAGMA user_version returned no result", SQLITE_ERROR, "PRAGMA user_version");
    }
    return stmt.columnInt(0);
}

void Database::setUserVersion(int version) {
    requireOpen("setUserVersion");
    // PRAGMA user_version takes no bind parameter, so the value is interpolated.
    // It is an int, so there is nothing injectable about it.
    execute("PRAGMA user_version=" + std::to_string(version));
}

bool Database::tableExists(const std::string& table) const {
    requireOpen("tableExists");
    auto stmt = prepareShared(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ? LIMIT 1");
    stmt.bindText(1, table);
    return stmt.step();
}

bool Database::columnExists(const std::string& table, const std::string& column) const {
    requireOpen("columnExists");
    if (!tableExists(table)) {
        return false;
    }
    // table_info's argument is an identifier, not a bindable value.
    auto stmt = prepareShared("PRAGMA table_info(" + quoteIdentifier(table) + ")");
    while (stmt.step()) {
        if (stmt.columnText(1) == column) {
            return true;
        }
    }
    return false;
}

bool Database::addColumnIfMissing(const std::string& table, const std::string& columnDef) {
    requireOpen("addColumnIfMissing");
    if (!tableExists(table)) {
        const std::string message = "cannot add column to \"" + table + "\": no such table";
        throw DatabaseError(formatError(message, "", SQLITE_ERROR, path_, ""), SQLITE_ERROR, path_,
                            "");
    }

    // The column name is the first whitespace-delimited token of the definition.
    const std::size_t start = columnDef.find_first_not_of(" \t\r\n");
    if (start == std::string::npos) {
        throw DatabaseError(
            formatError("addColumnIfMissing: empty column definition", "", SQLITE_ERROR, path_, ""),
            SQLITE_ERROR, path_, "");
    }
    const std::size_t end = columnDef.find_first_of(" \t\r\n", start);
    const std::string name = columnDef.substr(start, end == std::string::npos ? end : end - start);

    if (columnExists(table, name)) {
        return false;
    }
    execute("ALTER TABLE " + quoteIdentifier(table) + " ADD COLUMN " + columnDef);
    return true;
}

void Database::migrate(const std::vector<Migration>& steps) {
    requireOpen("migrate");

    int previous = 0;
    for (const auto& step : steps) {
        if (step.version <= 0) {
            const std::string message = "migration \"" + step.description +
                                        "\" has non-positive version " +
                                        std::to_string(step.version) +
                                        "; versions start at 1 (a fresh database reports 0)";
            throw DatabaseError(formatError(message, "", SQLITE_ERROR, path_, ""), SQLITE_ERROR,
                                path_, "");
        }
        if (step.version <= previous) {
            const std::string message =
                "migration \"" + step.description + "\" (version " +
                std::to_string(step.version) +
                ") is not newer than the preceding step (version " + std::to_string(previous) +
                "); steps must be in strictly ascending version order";
            throw DatabaseError(formatError(message, "", SQLITE_ERROR, path_, ""), SQLITE_ERROR,
                                path_, "");
        }
        if (!step.apply) {
            const std::string message = "migration \"" + step.description + "\" (version " +
                                        std::to_string(step.version) + ") has no apply callable";
            throw DatabaseError(formatError(message, "", SQLITE_ERROR, path_, ""), SQLITE_ERROR,
                                path_, "");
        }
        previous = step.version;
    }

    const int current = userVersion();

    if (!steps.empty() && current > steps.back().version) {
        // A database written by a newer build. Downgrading would need reverse
        // migrations that do not exist, so refuse instead of running against a
        // schema this binary does not understand.
        const std::string message =
            "database is at schema version " + std::to_string(current) +
            " but this build only knows up to version " + std::to_string(steps.back().version) +
            "; it was written by a newer build of GAIA. Downgrades are not supported.";
        throw DatabaseError(formatError(message, "", SQLITE_ERROR, path_, ""), SQLITE_ERROR, path_,
                            "");
    }

    for (const auto& step : steps) {
        if (step.version <= current) {
            continue;
        }
        try {
            Transaction txn(*this);
            step.apply(*this);
            setUserVersion(step.version);
            txn.commit();
        } catch (const DatabaseError& e) {
            const std::string message = "migration to version " + std::to_string(step.version) +
                                        " (\"" + step.description + "\") failed and was rolled " +
                                        "back; the database is still at version " +
                                        std::to_string(userVersion()) + ". Cause: " + e.what();
            throw DatabaseError(message, e.code(), path_, e.sql());
        } catch (const std::exception& e) {
            const std::string message = "migration to version " + std::to_string(step.version) +
                                        " (\"" + step.description + "\") failed and was rolled " +
                                        "back; the database is still at version " +
                                        std::to_string(userVersion()) + ". Cause: " + e.what();
            throw DatabaseError(formatError(message, "", SQLITE_ERROR, path_, ""), SQLITE_ERROR,
                                path_, "");
        }
    }
}

bool Database::inTransaction() const {
    requireOpen("inTransaction");
    return sqlite3_get_autocommit(db_) == 0;
}

void Database::close() {
    if (db_ == nullptr) {
        return;
    }
    const int rc = sqlite3_close(db_);
    if (rc != SQLITE_OK) {
        // SQLITE_BUSY here means a Statement from this connection is still
        // alive. Report it rather than leaking the handle silently.
        const std::string message = messageFor(db_);
        throw DatabaseError(
            formatError("cannot close database (statements still open?)", message, rc, path_, ""),
            rc, path_, "");
    }
    db_ = nullptr;
}

std::string Database::sqliteVersion() {
    return std::string(sqlite3_libversion());
}

bool Database::hasFts5() {
#ifdef SQLITE_ENABLE_FTS5
    return true;
#else
    return false;
#endif
}

// ---------------------------------------------------------------------------
// Transaction
// ---------------------------------------------------------------------------

namespace {

std::string nextSavepointName() {
    static std::atomic<std::uint64_t> counter{0};
    return "gaia_sp_" + std::to_string(counter.fetch_add(1));
}

const char* beginKeyword(Transaction::Behavior behavior) {
    switch (behavior) {
        case Transaction::Behavior::Immediate:
            return "BEGIN IMMEDIATE";
        case Transaction::Behavior::Exclusive:
            return "BEGIN EXCLUSIVE";
        case Transaction::Behavior::Deferred:
        default:
            return "BEGIN DEFERRED";
    }
}

}  // namespace

Transaction::Transaction(Database& db, Behavior behavior) : db_(db) {
    if (db_.inTransaction()) {
        // SQLite has no nested BEGIN; a savepoint is the nested equivalent.
        savepoint_ = nextSavepointName();
        db_.execute("SAVEPOINT " + quoteIdentifier(savepoint_));
    } else {
        db_.execute(beginKeyword(behavior));
    }
    active_ = true;
}

Transaction::~Transaction() {
    if (!active_) {
        return;
    }
    try {
        rollback();
    } catch (const std::exception& e) {
        // A destructor cannot throw — during stack unwinding that terminates
        // the process. Report loudly instead of vanishing.
        std::fprintf(stderr, "[gaia::Transaction] rollback failed during destruction: %s\n",
                     e.what());
        active_ = false;
    }
}

void Transaction::commit() {
    if (!active_) {
        return;
    }
    if (savepoint_.empty()) {
        db_.execute("COMMIT");
    } else {
        db_.execute("RELEASE " + quoteIdentifier(savepoint_));
    }
    active_ = false;
}

void Transaction::rollback() {
    if (!active_) {
        return;
    }
    if (savepoint_.empty()) {
        db_.execute("ROLLBACK");
    } else {
        // ROLLBACK TO leaves the savepoint on the stack; RELEASE pops it.
        db_.execute("ROLLBACK TO " + quoteIdentifier(savepoint_));
        db_.execute("RELEASE " + quoteIdentifier(savepoint_));
    }
    active_ = false;
}

}  // namespace gaia
