// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// SQLite-backed structured persistence for GAIA agents.
//
// Wraps the vendored SQLite amalgamation (cpp/third_party/sqlite/) in RAII
// types: a Database connection, a Statement prepared-statement, a Transaction
// scope guard, and an ordered schema-migration helper.
//
// SQLite itself is a *private* dependency — sqlite3.h is never included from
// this header, so consumers of gaia_core do not need it on their include path.

#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <initializer_list>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

#include "gaia/export.h"

// Opaque SQLite handles. These are the same declarations sqlite3.h makes, so a
// translation unit that includes both headers sees one consistent type.
struct sqlite3;
struct sqlite3_stmt;

namespace gaia {

/// Raw bytes for BLOB columns.
using Blob = std::vector<std::uint8_t>;

/// Storage class of a result column (mirrors SQLITE_INTEGER and friends).
enum class ColumnType {
    Integer,
    Float,
    Text,
    BlobValue,
    Null,
};

/// Failure from any database operation.
///
/// Carries the SQLite message and result code plus the context needed to act on
/// it: which database file, and which statement was running. Nothing in this
/// header ever swallows a SQLite error or degrades to a placeholder value.
class GAIA_API DatabaseError : public std::runtime_error {
public:
    /// @param message   Human-readable summary (already includes the context).
    /// @param code      SQLite result code (0 when the failure was not SQLite's).
    /// @param dbPath    Database file the failure happened on.
    /// @param sql       Statement being prepared/run, or "" if not applicable.
    DatabaseError(const std::string& message, int code, std::string dbPath, std::string sql);

    /// SQLite result code, or 0 when the failure did not come from SQLite.
    int code() const noexcept { return code_; }

    /// Path of the database the failure occurred on (":memory:" for in-memory).
    const std::string& dbPath() const noexcept { return dbPath_; }

    /// The offending SQL, or empty when the failure was not statement-specific.
    const std::string& sql() const noexcept { return sql_; }

private:
    int code_;
    std::string dbPath_;
    std::string sql_;
};

class Database;

// ---------------------------------------------------------------------------
// Statement
// ---------------------------------------------------------------------------

/// A prepared statement. Move-only; finalizes on destruction.
///
/// Bind parameter indices are 1-based (SQLite's convention). Column indices are
/// 0-based. Obtain one from Database::prepare().
///
/// @code
///   auto stmt = db.prepare("SELECT name, size FROM files WHERE size > ?");
///   stmt.bindInt64(1, 1024);
///   while (stmt.step()) {
///       std::string name = stmt.columnText(0);
///       std::int64_t size = stmt.columnInt64(1);
///   }
/// @endcode
class GAIA_API Statement {
public:
    ~Statement();

    Statement(Statement&& other) noexcept;
    Statement& operator=(Statement&& other) noexcept;
    Statement(const Statement&) = delete;
    Statement& operator=(const Statement&) = delete;

    // -- Binding (1-based index) --------------------------------------------

    /// Bind SQL NULL.
    Statement& bindNull(int index);
    /// Bind an INTEGER.
    Statement& bindInt64(int index, std::int64_t value);
    /// Bind an INTEGER (0 or 1).
    Statement& bindBool(int index, bool value);
    /// Bind a REAL.
    Statement& bindDouble(int index, double value);
    /// Bind TEXT. The value is copied, so @p value need not outlive the call.
    Statement& bindText(int index, const std::string& value);
    /// Bind a BLOB. The bytes are copied. A zero-length blob binds as an empty
    /// BLOB, not NULL — bindNull() is how you write NULL.
    Statement& bindBlob(int index, const void* data, std::size_t size);
    /// Bind a BLOB from a byte vector.
    Statement& bindBlob(int index, const Blob& value);

    /// @name Overloaded convenience binds
    /// Sugar over the explicit bind*() methods so bindAll() can take mixed
    /// argument types. Integral types (other than bool) go to bindInt64,
    /// floating-point to bindDouble.
    /// @{
    Statement& bind(int index, std::nullptr_t) { return bindNull(index); }
    Statement& bind(int index, bool value) { return bindBool(index, value); }
    Statement& bind(int index, const std::string& value) { return bindText(index, value); }
    Statement& bind(int index, const char* value);
    Statement& bind(int index, const Blob& value) { return bindBlob(index, value); }

    template <typename T,
              typename std::enable_if<std::is_integral<T>::value &&
                                          !std::is_same<typename std::decay<T>::type, bool>::value,
                                      int>::type = 0>
    Statement& bind(int index, T value) {
        return bindInt64(index, static_cast<std::int64_t>(value));
    }

    template <typename T,
              typename std::enable_if<std::is_floating_point<T>::value, int>::type = 0>
    Statement& bind(int index, T value) {
        return bindDouble(index, static_cast<double>(value));
    }
    /// @}

    /// Bind every parameter left-to-right starting at index 1.
    /// @code
    ///   db.prepare("INSERT INTO t VALUES (?, ?, ?)").bindAll("a", 42, nullptr).execute();
    /// @endcode
    template <typename... Args>
    Statement& bindAll(Args&&... args) {
        int index = 1;
        // Comma fold: evaluation order is left-to-right, so indices stay in sync.
        (void)std::initializer_list<int>{(bind(index++, std::forward<Args>(args)), 0)...};
        return *this;
    }

    /// Number of bind parameters in the statement.
    int parameterCount() const;

    /// Clear all bindings back to NULL.
    Statement& clearBindings();

    // -- Execution ----------------------------------------------------------

    /// Advance to the next row.
    /// @return true if a row is available, false when the statement is done.
    /// @throws DatabaseError on any SQLite error (including SQLITE_BUSY after
    ///         busy_timeout has been exhausted).
    bool step();

    /// Run the statement to completion, discarding any rows.
    /// Leaves the statement finished — call reset() before running it again.
    /// @throws DatabaseError on any SQLite error.
    void execute();

    /// Reset back to the start so the statement can be re-executed. Bindings
    /// are preserved (call clearBindings() to drop them).
    Statement& reset();

    // -- Column access (0-based index) --------------------------------------

    /// Number of columns in the result set.
    int columnCount() const;
    /// Declared name of a result column.
    std::string columnName(int col) const;
    /// Storage class of the value in the current row.
    ColumnType columnType(int col) const;
    /// Whether the value in the current row is SQL NULL.
    bool isNull(int col) const;

    /// Read an INTEGER column. NULL reads as 0 — check isNull() when the
    /// distinction matters.
    std::int64_t columnInt64(int col) const;
    /// Read an INTEGER column as int. Values outside int's range are truncated
    /// by SQLite — use columnInt64() when the column can exceed 32 bits.
    int columnInt(int col) const;
    /// Read an INTEGER column as bool (non-zero is true).
    bool columnBool(int col) const;
    /// Read a REAL column. NULL reads as 0.0.
    double columnDouble(int col) const;
    /// Read a TEXT column. NULL reads as "" — check isNull() to distinguish it
    /// from a stored empty string.
    std::string columnText(int col) const;
    /// Read a BLOB column. NULL and zero-length blobs both read as empty.
    Blob columnBlob(int col) const;

    /// The SQL this statement was prepared from.
    const std::string& sql() const noexcept { return sql_; }

private:
    friend class Database;
    Statement(sqlite3* db, sqlite3_stmt* stmt, std::string sql, std::string dbPath);

    void requireStatement(const char* what) const;
    void checkColumn(int col, const char* what) const;
    [[noreturn]] void fail(const std::string& what, int rc) const;

    sqlite3* db_ = nullptr;
    sqlite3_stmt* stmt_ = nullptr;
    std::string sql_;
    std::string dbPath_;
};

// ---------------------------------------------------------------------------
// Migration
// ---------------------------------------------------------------------------

/// One ordered schema-migration step.
///
/// Mirrors the versioning approach of MemoryStore._migrate_schema_locked
/// (src/gaia/agents/base/memory_store.py): steps are ordered, each advances the
/// stored schema version by exactly one step, and a database at any older
/// version chains forward through every intervening step to current.
///
/// The stored version lives in `PRAGMA user_version` rather than a
/// `schema_version` table, so this helper imposes no table on the caller's
/// schema. A fresh database reports 0.
struct GAIA_API Migration {
    /// Version this step produces. Must be > 0, and strictly ascending across
    /// the list handed to Database::migrate().
    int version = 0;

    /// Human-readable label, quoted in errors so a failing step is identifiable.
    std::string description;

    /// The step itself. Runs inside a transaction that also stamps the new
    /// user_version, so a throwing step leaves the database exactly as it was.
    std::function<void(Database&)> apply;

    /// Build a step that runs a SQL script (one or more statements).
    static Migration fromSql(int version, std::string description, std::string script);
};

// ---------------------------------------------------------------------------
// Database
// ---------------------------------------------------------------------------

/// An open SQLite connection. Move-only; closes on destruction.
///
/// WAL journaling and a busy timeout are applied at open, matching how
/// MemoryStore configures its connection in Python — several processes (the
/// Agent UI backend and an agent instance) share one database file.
///
/// The connection is opened in SQLite's serialized threading mode, so a single
/// Database may be used from multiple threads without external locking.
///
/// @code
///   Database db("agent.db");
///   db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)");
///   {
///       Transaction txn(db);
///       db.prepare("INSERT INTO notes (body) VALUES (?)").bindAll("hello").execute();
///       txn.commit();
///   }
/// @endcode
class GAIA_API Database {
public:
    /// Connection settings applied at open.
    struct Options {
        /// Enable WAL journaling. Does not apply to in-memory databases (no
        /// file to journal) or read-only connections (which cannot change the
        /// journal mode); it is skipped in both cases rather than failing.
        bool walMode = true;
        /// How long a writer waits on a locked database before SQLITE_BUSY.
        int busyTimeoutMs = 5000;
        /// Enforce FOREIGN KEY constraints.
        bool foreignKeys = true;
        /// Open read-only. The file must already exist.
        bool readOnly = false;
        /// Create the file (and any missing parent directories) if absent.
        /// Ignored when readOnly is set.
        bool createIfMissing = true;
    };

    /// Open (or create) a database file with default options.
    /// @param path Filesystem path, or ":memory:" for a private in-memory
    ///        database. An empty path is rejected rather than treated as
    ///        SQLite's anonymous temporary database.
    /// @throws DatabaseError if the path is empty, the file cannot be opened,
    ///         or the pragmas fail.
    // (Two overloads rather than a defaulted argument: Options has default
    // member initializers and is a nested class, so `= Options{}` in a default
    // argument is ill-formed.)
    explicit Database(const std::string& path);

    /// Open (or create) a database file.
    Database(const std::string& path, const Options& options);

    /// Open a private in-memory database with default options. Useful for tests.
    static Database inMemory();

    /// Open a private in-memory database.
    static Database inMemory(const Options& options);

    ~Database();

    Database(Database&& other) noexcept;
    Database& operator=(Database&& other) noexcept;
    Database(const Database&) = delete;
    Database& operator=(const Database&) = delete;

    // -- Statements ---------------------------------------------------------

    /// Prepare a single SQL statement.
    /// @throws DatabaseError with the SQLite message, the offending SQL, and
    ///         the database path if the statement does not compile.
    Statement prepare(const std::string& sql);

    /// Run a SQL script (one or more statements, semicolon-separated).
    /// Not for statements with bind parameters — use prepare() for those.
    /// @throws DatabaseError on any SQLite error.
    void execute(const std::string& sql);

    /// Prepare, bind, and run in one call.
    /// @code
    ///   db.run("INSERT INTO notes (body) VALUES (?)", "hello");
    /// @endcode
    template <typename... Args>
    void run(const std::string& sql, Args&&... args) {
        prepare(sql).bindAll(std::forward<Args>(args)...).execute();
    }

    /// ROWID of the most recent successful INSERT on this connection.
    std::int64_t lastInsertRowId() const;

    /// Rows changed by the most recent INSERT/UPDATE/DELETE on this connection.
    std::int64_t changes() const;

    // -- Schema ------------------------------------------------------------

    /// Read `PRAGMA user_version`. A fresh database returns 0.
    int userVersion() const;

    /// Write `PRAGMA user_version`.
    void setUserVersion(int version);

    /// Apply every migration step newer than the stored user_version, in order.
    ///
    /// Each step runs inside its own transaction that also stamps the new
    /// user_version, so a step that throws rolls back completely and the stored
    /// version does not advance — re-running migrate() retries that same step.
    ///
    /// @param steps Ordered steps with strictly ascending, positive versions.
    /// @throws DatabaseError if the steps are malformed, if the database is at
    ///         a version *newer* than the last step (a downgrade — refused
    ///         rather than silently ignored), or if a step fails. A failing
    ///         step's error names the version and description.
    void migrate(const std::vector<Migration>& steps);

    /// Whether a table (or view) with this name exists.
    bool tableExists(const std::string& table) const;

    /// Whether a column exists on a table. Returns false if the table itself
    /// does not exist.
    bool columnExists(const std::string& table, const std::string& column) const;

    /// Idempotent `ALTER TABLE <table> ADD COLUMN <columnDef>` — a no-op when
    /// the column is already there.
    ///
    /// This is the migration primitive MemoryStore gets in Python by catching
    /// "duplicate column name" and inspecting the message. Checking up front
    /// keeps a partially-applied migration re-runnable without any error being
    /// caught and discarded.
    ///
    /// @param columnDef Full column definition, e.g. "embedding BLOB".
    /// @return true if the column was added, false if it already existed.
    /// @throws DatabaseError if the table does not exist or the ALTER fails.
    bool addColumnIfMissing(const std::string& table, const std::string& columnDef);

    // -- Lifecycle ----------------------------------------------------------

    /// Close the connection. Idempotent; the destructor calls this.
    /// @throws DatabaseError if SQLite refuses to close because statements are
    ///         still live. (The destructor never throws — see the .cpp.)
    void close();

    /// Whether the connection is open.
    bool isOpen() const noexcept { return db_ != nullptr; }

    /// The path this database was opened from.
    const std::string& path() const noexcept { return path_; }

    /// Whether a transaction (or savepoint) is currently open on this connection.
    bool inTransaction() const;

    /// Compile-time SQLite version linked into gaia_core, e.g. "3.53.4".
    static std::string sqliteVersion();

    /// Whether this build has FTS5 compiled in. Always true for the vendored
    /// amalgamation; exposed so a consumer can assert it rather than discover
    /// it from a query failure.
    static bool hasFts5();

private:
    friend class Transaction;
    friend class Statement;

    sqlite3* handle() const noexcept { return db_; }
    void requireOpen(const char* what) const;
    [[noreturn]] void fail(const std::string& what, int rc, const std::string& sql = "") const;
    Statement prepareShared(const std::string& sql) const;

    sqlite3* db_ = nullptr;
    std::string path_;
};

// ---------------------------------------------------------------------------
// Transaction
// ---------------------------------------------------------------------------

/// RAII transaction guard. Rolls back on destruction unless commit() ran.
///
/// If a transaction is already open on the connection, this opens a uniquely
/// named SAVEPOINT instead — SQLite has no nested BEGIN, and a bare BEGIN would
/// otherwise fail. commit()/rollback() do the matching RELEASE / ROLLBACK TO.
///
/// Scope-bound: neither copyable nor movable.
class GAIA_API Transaction {
public:
    /// Locking behaviour of the outermost transaction. Ignored for savepoints.
    enum class Behavior {
        Deferred,   ///< Take locks lazily (SQLite's default).
        Immediate,  ///< Take the write lock up front — use when a write is certain.
        Exclusive,  ///< Take an exclusive lock up front.
    };

    /// Begin a transaction (or savepoint) on @p db.
    /// @throws DatabaseError if the BEGIN fails.
    explicit Transaction(Database& db, Behavior behavior = Behavior::Deferred);

    /// Rolls back if commit() was not called. Never throws: a rollback failure
    /// during unwinding cannot propagate, so it is reported on stderr instead.
    ~Transaction();

    Transaction(const Transaction&) = delete;
    Transaction& operator=(const Transaction&) = delete;
    Transaction(Transaction&&) = delete;
    Transaction& operator=(Transaction&&) = delete;

    /// Commit (or RELEASE the savepoint). Idempotent after the first call.
    /// @throws DatabaseError if the commit fails; the transaction stays active
    ///         so the destructor still rolls back.
    void commit();

    /// Roll back explicitly, before the destructor would.
    /// @throws DatabaseError if the rollback fails.
    void rollback();

    /// Whether the transaction is still open (neither committed nor rolled back).
    bool isActive() const noexcept { return active_; }

private:
    Database& db_;
    std::string savepoint_;  ///< Non-empty when this is a nested savepoint.
    bool active_ = false;
};

}  // namespace gaia
