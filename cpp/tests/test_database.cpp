// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gaia/database.h>
#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <string>
#include <thread>
#include <vector>

using namespace gaia;
namespace fs = std::filesystem;

namespace {

/// Each test gets its own file under a temp directory so WAL sidecars from one
/// test never leak into the next.
class DatabaseTest : public ::testing::Test {
protected:
    fs::path dir;

    void SetUp() override {
        dir = fs::temp_directory_path() / "gaia_database_test";
        fs::remove_all(dir);
        fs::create_directories(dir);
    }

    void TearDown() override {
        std::error_code ec;
        fs::remove_all(dir, ec);
    }

    std::string dbPath(const std::string& name = "test.db") const {
        return (dir / name).string();
    }
};

/// Count rows in a table.
std::int64_t countRows(Database& db, const std::string& table) {
    auto stmt = db.prepare("SELECT COUNT(*) FROM " + table);
    EXPECT_TRUE(stmt.step());
    return stmt.columnInt64(0);
}

}  // namespace

// ---------------------------------------------------------------------------
// Open / close lifecycle
// ---------------------------------------------------------------------------

TEST_F(DatabaseTest, OpensAndCreatesFile) {
    const std::string path = dbPath();
    ASSERT_FALSE(fs::exists(path));
    {
        Database db(path);
        EXPECT_TRUE(db.isOpen());
        EXPECT_EQ(db.path(), path);
    }
    EXPECT_TRUE(fs::exists(path));
}

TEST_F(DatabaseTest, CreatesMissingParentDirectories) {
    const std::string path = (dir / "nested" / "deeper" / "agent.db").string();
    Database db(path);
    EXPECT_TRUE(db.isOpen());
    EXPECT_TRUE(fs::exists(path));
}

TEST_F(DatabaseTest, InMemoryDatabaseWorks) {
    auto db = Database::inMemory();
    EXPECT_TRUE(db.isOpen());
    EXPECT_EQ(db.path(), ":memory:");
    db.execute("CREATE TABLE t (x INTEGER)");
    db.run("INSERT INTO t VALUES (?)", 7);
    EXPECT_EQ(countRows(db, "t"), 1);
}

TEST_F(DatabaseTest, CloseIsIdempotentAndDetectable) {
    Database db(dbPath());
    db.close();
    EXPECT_FALSE(db.isOpen());
    EXPECT_NO_THROW(db.close());
    EXPECT_THROW(db.execute("SELECT 1"), DatabaseError);
}

TEST_F(DatabaseTest, MoveTransfersOwnership) {
    Database db(dbPath());
    db.execute("CREATE TABLE t (x INTEGER)");
    Database moved = std::move(db);
    EXPECT_TRUE(moved.isOpen());
    // NOLINTNEXTLINE(bugprone-use-after-move) — asserting the moved-from state.
    EXPECT_FALSE(db.isOpen());
    EXPECT_NO_THROW(moved.execute("INSERT INTO t VALUES (1)"));
}

TEST_F(DatabaseTest, EmptyPathIsRejected) {
    // SQLite would read "" as an anonymous temporary database that vanishes at
    // close; an empty path variable is far more likely to be a bug.
    EXPECT_THROW({ Database db(""); }, DatabaseError);
}

TEST_F(DatabaseTest, NonDatabaseFileFailsDuringConfiguration) {
    // sqlite3_open_v2 is lazy — it succeeds on any readable file and only
    // notices the bad header on the first statement. That makes this the
    // post-open throw path, where a constructor that throws never runs
    // ~Database and so has to close the handle itself.
    const std::string path = dbPath("not_a_database.db");
    {
        std::ofstream out(path, std::ios::binary);
        out << "this is plainly not a SQLite file";
    }
    try {
        Database db(path);
        FAIL() << "expected opening a non-database file to throw";
    } catch (const DatabaseError& e) {
        EXPECT_NE(std::string(e.what()).find("not a database"), std::string::npos) << e.what();
    }
}

TEST_F(DatabaseTest, ReadOnlyOpensANonWalDatabase) {
    const std::string path = dbPath("rollback_journal.db");
    {
        Database::Options noWal;
        noWal.walMode = false;
        Database db(path, noWal);
        db.execute("CREATE TABLE t (x INTEGER)");
        db.run("INSERT INTO t VALUES (?)", 1);
    }
    // walMode defaults to true, but a read-only connection cannot change the
    // journal mode — that must not turn into a spurious open failure.
    Database::Options opts;
    opts.readOnly = true;
    Database db(path, opts);
    EXPECT_EQ(countRows(db, "t"), 1);
}

TEST_F(DatabaseTest, ReadOnlyRefusesMissingFile) {
    Database::Options opts;
    opts.readOnly = true;
    EXPECT_THROW(Database(dbPath("nope.db"), opts), DatabaseError);
}

TEST_F(DatabaseTest, ReadOnlyRefusesWrites) {
    const std::string path = dbPath();
    {
        Database db(path);
        db.execute("CREATE TABLE t (x INTEGER)");
    }
    Database::Options opts;
    opts.readOnly = true;
    Database db(path, opts);
    EXPECT_EQ(countRows(db, "t"), 0);
    EXPECT_THROW(db.execute("INSERT INTO t VALUES (1)"), DatabaseError);
}

// ---------------------------------------------------------------------------
// Connection configuration: WAL and busy_timeout
// ---------------------------------------------------------------------------

TEST_F(DatabaseTest, WalModeIsAppliedOnOpen) {
    Database db(dbPath());
    auto stmt = db.prepare("PRAGMA journal_mode");
    ASSERT_TRUE(stmt.step());
    EXPECT_EQ(stmt.columnText(0), "wal");
}

TEST_F(DatabaseTest, WalCanBeDisabled) {
    Database::Options opts;
    opts.walMode = false;
    Database db(dbPath(), opts);
    auto stmt = db.prepare("PRAGMA journal_mode");
    ASSERT_TRUE(stmt.step());
    EXPECT_NE(stmt.columnText(0), "wal");
}

TEST_F(DatabaseTest, BusyTimeoutIsAppliedOnOpen) {
    Database::Options opts;
    opts.busyTimeoutMs = 3210;
    Database db(dbPath(), opts);
    auto stmt = db.prepare("PRAGMA busy_timeout");
    ASSERT_TRUE(stmt.step());
    EXPECT_EQ(stmt.columnInt(0), 3210);
}

TEST_F(DatabaseTest, ForeignKeysEnforcedByDefault) {
    Database db(dbPath());
    db.execute(
        "CREATE TABLE parent (id INTEGER PRIMARY KEY);"
        "CREATE TABLE child (id INTEGER PRIMARY KEY,"
        "                    parent_id INTEGER REFERENCES parent(id));");
    EXPECT_THROW(db.execute("INSERT INTO child VALUES (1, 999)"), DatabaseError);
}

TEST_F(DatabaseTest, ForeignKeysCanBeDisabled) {
    Database::Options opts;
    opts.foreignKeys = false;
    Database db(dbPath(), opts);
    db.execute(
        "CREATE TABLE parent (id INTEGER PRIMARY KEY);"
        "CREATE TABLE child (id INTEGER PRIMARY KEY,"
        "                    parent_id INTEGER REFERENCES parent(id));");
    EXPECT_NO_THROW(db.execute("INSERT INTO child VALUES (1, 999)"));
}

// ---------------------------------------------------------------------------
// CRUD via prepared statements
// ---------------------------------------------------------------------------

TEST_F(DatabaseTest, InsertSelectUpdateDelete) {
    Database db(dbPath());
    db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT, score REAL)");

    // Create
    auto insert = db.prepare("INSERT INTO notes (body, score) VALUES (?, ?)");
    insert.bindAll("first", 1.5).execute();
    const std::int64_t firstId = db.lastInsertRowId();
    insert.reset().bindAll("second", 2.5).execute();

    EXPECT_EQ(countRows(db, "notes"), 2);
    EXPECT_GT(db.lastInsertRowId(), firstId);

    // Read
    auto select = db.prepare("SELECT id, body, score FROM notes ORDER BY id");
    ASSERT_TRUE(select.step());
    EXPECT_EQ(select.columnInt64(0), firstId);
    EXPECT_EQ(select.columnText(1), "first");
    EXPECT_DOUBLE_EQ(select.columnDouble(2), 1.5);
    ASSERT_TRUE(select.step());
    EXPECT_EQ(select.columnText(1), "second");
    EXPECT_FALSE(select.step());

    // Update
    db.run("UPDATE notes SET body = ? WHERE id = ?", "edited", firstId);
    EXPECT_EQ(db.changes(), 1);

    auto check = db.prepare("SELECT body FROM notes WHERE id = ?");
    check.bindInt64(1, firstId);
    ASSERT_TRUE(check.step());
    EXPECT_EQ(check.columnText(0), "edited");

    // Delete
    db.run("DELETE FROM notes WHERE id = ?", firstId);
    EXPECT_EQ(db.changes(), 1);
    EXPECT_EQ(countRows(db, "notes"), 1);
}

TEST_F(DatabaseTest, StatementCanBeResetAndReused) {
    Database db(dbPath());
    db.execute("CREATE TABLE t (x INTEGER)");
    auto stmt = db.prepare("INSERT INTO t VALUES (?)");
    for (int i = 0; i < 5; ++i) {
        stmt.reset().bindInt64(1, i).execute();
    }
    EXPECT_EQ(countRows(db, "t"), 5);
}

TEST_F(DatabaseTest, ReportsParameterAndColumnMetadata) {
    Database db(dbPath());
    db.execute("CREATE TABLE t (a INTEGER, b TEXT)");
    auto insert = db.prepare("INSERT INTO t VALUES (?, ?)");
    EXPECT_EQ(insert.parameterCount(), 2);

    auto select = db.prepare("SELECT a, b FROM t");
    EXPECT_EQ(select.columnCount(), 2);
    EXPECT_EQ(select.columnName(0), "a");
    EXPECT_EQ(select.columnName(1), "b");
}

// ---------------------------------------------------------------------------
// Typed binds — integers, reals, text, NULL, blobs
// ---------------------------------------------------------------------------

TEST_F(DatabaseTest, BindsAndReadsEveryStorageClass) {
    Database db(dbPath());
    db.execute("CREATE TABLE v (i INTEGER, r REAL, t TEXT, b BLOB, n INTEGER, flag INTEGER)");

    const Blob blob{0x00, 0x01, 0xFE, 0xFF, 0x00, 0x42};
    const std::int64_t big = 9007199254740993LL;  // > 2^53: survives only as an integer

    auto insert = db.prepare("INSERT INTO v VALUES (?, ?, ?, ?, ?, ?)");
    insert.bindInt64(1, big);
    insert.bindDouble(2, 3.25);
    insert.bindText(3, "hello \xE2\x9C\x93");  // embedded UTF-8
    insert.bindBlob(4, blob);
    insert.bindNull(5);
    insert.bindBool(6, true);
    insert.execute();

    auto select = db.prepare("SELECT i, r, t, b, n, flag FROM v");
    ASSERT_TRUE(select.step());

    EXPECT_EQ(select.columnInt64(0), big);
    EXPECT_EQ(select.columnType(0), ColumnType::Integer);

    EXPECT_DOUBLE_EQ(select.columnDouble(1), 3.25);
    EXPECT_EQ(select.columnType(1), ColumnType::Float);

    EXPECT_EQ(select.columnText(2), "hello \xE2\x9C\x93");
    EXPECT_EQ(select.columnType(2), ColumnType::Text);

    EXPECT_EQ(select.columnBlob(3), blob);
    EXPECT_EQ(select.columnType(3), ColumnType::BlobValue);

    EXPECT_TRUE(select.isNull(4));
    EXPECT_EQ(select.columnType(4), ColumnType::Null);
    EXPECT_EQ(select.columnInt64(4), 0);  // NULL reads as 0 …
    EXPECT_TRUE(select.columnText(4).empty());

    EXPECT_TRUE(select.columnBool(5));
}

TEST_F(DatabaseTest, BlobWithEmbeddedNullsRoundTripsExactly) {
    Database db(dbPath());
    db.execute("CREATE TABLE b (data BLOB)");

    // A C-string bind would truncate at the first 0x00; a BLOB must not.
    Blob payload;
    for (int i = 0; i < 256; ++i) {
        payload.push_back(static_cast<std::uint8_t>(i));
    }
    db.run("INSERT INTO b VALUES (?)", payload);

    auto stmt = db.prepare("SELECT data, length(data) FROM b");
    ASSERT_TRUE(stmt.step());
    EXPECT_EQ(stmt.columnInt64(1), 256);
    EXPECT_EQ(stmt.columnBlob(0), payload);
}

TEST_F(DatabaseTest, EmptyBlobIsNotNull) {
    Database db(dbPath());
    db.execute("CREATE TABLE b (data BLOB)");
    db.run("INSERT INTO b VALUES (?)", Blob{});

    auto stmt = db.prepare("SELECT data, typeof(data) FROM b");
    ASSERT_TRUE(stmt.step());
    EXPECT_FALSE(stmt.isNull(0));
    EXPECT_EQ(stmt.columnText(1), "blob");
    EXPECT_TRUE(stmt.columnBlob(0).empty());
}

TEST_F(DatabaseTest, NullptrBindsSqlNull) {
    Database db(dbPath());
    db.execute("CREATE TABLE t (x TEXT)");
    db.run("INSERT INTO t VALUES (?)", nullptr);

    auto stmt = db.prepare("SELECT x, typeof(x) FROM t");
    ASSERT_TRUE(stmt.step());
    EXPECT_TRUE(stmt.isNull(0));
    EXPECT_EQ(stmt.columnText(1), "null");
}

TEST_F(DatabaseTest, TextWithEmbeddedNullKeepsItsLength) {
    Database db(dbPath());
    db.execute("CREATE TABLE t (x TEXT)");
    const std::string value = std::string("ab\0cd", 5);
    db.run("INSERT INTO t VALUES (?)", value);

    auto stmt = db.prepare("SELECT x FROM t");
    ASSERT_TRUE(stmt.step());
    EXPECT_EQ(stmt.columnText(0), value);
    EXPECT_EQ(stmt.columnText(0).size(), 5u);
}

TEST_F(DatabaseTest, MixedTypesViaBindAll) {
    Database db(dbPath());
    db.execute("CREATE TABLE t (a TEXT, b INTEGER, c REAL, d INTEGER, e INTEGER)");
    const std::size_t unsignedValue = 12345;
    db.run("INSERT INTO t VALUES (?, ?, ?, ?, ?)", "literal", -17, 0.5, nullptr, unsignedValue);

    auto stmt = db.prepare("SELECT a, b, c, d, e FROM t");
    ASSERT_TRUE(stmt.step());
    EXPECT_EQ(stmt.columnText(0), "literal");
    EXPECT_EQ(stmt.columnInt(1), -17);
    EXPECT_DOUBLE_EQ(stmt.columnDouble(2), 0.5);
    EXPECT_TRUE(stmt.isNull(3));
    EXPECT_EQ(stmt.columnInt64(4), 12345);
}

TEST_F(DatabaseTest, NullCharPointerBindsSqlNull) {
    Database db(dbPath());
    db.execute("CREATE TABLE t (x TEXT)");
    const char* missing = nullptr;
    db.run("INSERT INTO t VALUES (?)", missing);

    auto stmt = db.prepare("SELECT typeof(x) FROM t");
    ASSERT_TRUE(stmt.step());
    EXPECT_EQ(stmt.columnText(0), "null");
}

TEST_F(DatabaseTest, ClearBindingsResetsToNull) {
    Database db(dbPath());
    db.execute("CREATE TABLE t (x TEXT)");
    auto stmt = db.prepare("INSERT INTO t VALUES (?)");
    stmt.bindText(1, "value").execute();
    stmt.reset().clearBindings().execute();

    auto check = db.prepare("SELECT COUNT(*) FROM t WHERE x IS NULL");
    ASSERT_TRUE(check.step());
    EXPECT_EQ(check.columnInt64(0), 1);
}

// ---------------------------------------------------------------------------
// Transactions
// ---------------------------------------------------------------------------

TEST_F(DatabaseTest, TransactionCommitPersists) {
    Database db(dbPath());
    db.execute("CREATE TABLE t (x INTEGER)");
    {
        Transaction txn(db);
        EXPECT_TRUE(txn.isActive());
        EXPECT_TRUE(db.inTransaction());
        db.run("INSERT INTO t VALUES (?)", 1);
        db.run("INSERT INTO t VALUES (?)", 2);
        txn.commit();
        EXPECT_FALSE(txn.isActive());
    }
    EXPECT_FALSE(db.inTransaction());
    EXPECT_EQ(countRows(db, "t"), 2);
}

TEST_F(DatabaseTest, TransactionRollsBackWhenNotCommitted) {
    Database db(dbPath());
    db.execute("CREATE TABLE t (x INTEGER)");
    db.run("INSERT INTO t VALUES (?)", 100);
    {
        Transaction txn(db);
        db.run("INSERT INTO t VALUES (?)", 1);
        db.run("INSERT INTO t VALUES (?)", 2);
        EXPECT_EQ(countRows(db, "t"), 3);
        // No commit — the destructor rolls back.
    }
    EXPECT_FALSE(db.inTransaction());
    EXPECT_EQ(countRows(db, "t"), 1);
}

TEST_F(DatabaseTest, TransactionRollsBackWhenScopeThrows) {
    Database db(dbPath());
    db.execute("CREATE TABLE t (x INTEGER)");
    try {
        Transaction txn(db);
        db.run("INSERT INTO t VALUES (?)", 1);
        throw std::runtime_error("boom");
    } catch (const std::runtime_error&) {
        // Expected.
    }
    EXPECT_FALSE(db.inTransaction());
    EXPECT_EQ(countRows(db, "t"), 0);
}

TEST_F(DatabaseTest, ExplicitRollbackDiscardsWork) {
    Database db(dbPath());
    db.execute("CREATE TABLE t (x INTEGER)");
    Transaction txn(db);
    db.run("INSERT INTO t VALUES (?)", 1);
    txn.rollback();
    EXPECT_FALSE(txn.isActive());
    EXPECT_EQ(countRows(db, "t"), 0);
}

TEST_F(DatabaseTest, ImmediateTransactionTakesTheWriteLock) {
    Database db(dbPath());
    db.execute("CREATE TABLE t (x INTEGER)");
    Transaction txn(db, Transaction::Behavior::Immediate);
    EXPECT_TRUE(db.inTransaction());
    db.run("INSERT INTO t VALUES (?)", 1);
    txn.commit();
    EXPECT_EQ(countRows(db, "t"), 1);
}

TEST_F(DatabaseTest, NestedTransactionUsesSavepoints) {
    Database db(dbPath());
    db.execute("CREATE TABLE t (x INTEGER)");

    Transaction outer(db);
    db.run("INSERT INTO t VALUES (?)", 1);
    {
        // Inner scope rolls back without killing the outer transaction —
        // SQLite has no nested BEGIN, so this must become a SAVEPOINT.
        Transaction inner(db);
        db.run("INSERT INTO t VALUES (?)", 2);
        EXPECT_EQ(countRows(db, "t"), 2);
    }
    EXPECT_TRUE(db.inTransaction());
    EXPECT_EQ(countRows(db, "t"), 1);

    {
        Transaction inner(db);
        db.run("INSERT INTO t VALUES (?)", 3);
        inner.commit();
    }
    outer.commit();
    EXPECT_EQ(countRows(db, "t"), 2);
}

// ---------------------------------------------------------------------------
// Schema migration
// ---------------------------------------------------------------------------

namespace {

/// v1 → v3 chain modelled on MemoryStore's migrations: v2 adds columns to an
/// existing table, v3 adds a new table.
std::vector<Migration> memoryLikeMigrations(std::vector<std::string>* log = nullptr) {
    std::vector<Migration> steps;

    Migration v1;
    v1.version = 1;
    v1.description = "initial schema";
    v1.apply = [log](Database& db) {
        if (log != nullptr) {
            log->push_back("v1");
        }
        db.execute(
            "CREATE TABLE knowledge ("
            "  id TEXT PRIMARY KEY,"
            "  category TEXT NOT NULL,"
            "  content TEXT NOT NULL);"
            "CREATE TABLE conversations ("
            "  id TEXT PRIMARY KEY,"
            "  turn TEXT NOT NULL);");
    };
    steps.push_back(std::move(v1));

    Migration v2;
    v2.version = 2;
    v2.description = "add embedding/superseded_by/consolidated_at";
    v2.apply = [log](Database& db) {
        if (log != nullptr) {
            log->push_back("v2");
        }
        db.addColumnIfMissing("knowledge", "embedding BLOB");
        db.addColumnIfMissing("knowledge", "superseded_by TEXT");
        db.addColumnIfMissing("conversations", "consolidated_at TEXT");
    };
    steps.push_back(std::move(v2));

    Migration v3;
    v3.version = 3;
    v3.description = "add procedures table";
    v3.apply = [log](Database& db) {
        if (log != nullptr) {
            log->push_back("v3");
        }
        db.execute(
            "CREATE TABLE IF NOT EXISTS procedures ("
            "  id TEXT PRIMARY KEY,"
            "  name TEXT NOT NULL);");
    };
    steps.push_back(std::move(v3));

    return steps;
}

}  // namespace

TEST_F(DatabaseTest, FreshDatabaseReportsVersionZero) {
    Database db(dbPath());
    EXPECT_EQ(db.userVersion(), 0);
}

TEST_F(DatabaseTest, UserVersionRoundTrips) {
    Database db(dbPath());
    db.setUserVersion(42);
    EXPECT_EQ(db.userVersion(), 42);
}

TEST_F(DatabaseTest, MigrateFromScratchRunsEveryStep) {
    Database db(dbPath());
    std::vector<std::string> log;
    db.migrate(memoryLikeMigrations(&log));

    EXPECT_EQ(db.userVersion(), 3);
    EXPECT_EQ(log, (std::vector<std::string>{"v1", "v2", "v3"}));
    EXPECT_TRUE(db.tableExists("knowledge"));
    EXPECT_TRUE(db.tableExists("procedures"));
    EXPECT_TRUE(db.columnExists("knowledge", "embedding"));
    EXPECT_TRUE(db.columnExists("conversations", "consolidated_at"));
}

TEST_F(DatabaseTest, MigrateIsIdempotent) {
    const std::string path = dbPath();
    {
        Database db(path);
        db.migrate(memoryLikeMigrations());
        EXPECT_EQ(db.userVersion(), 3);
    }
    Database db(path);
    std::vector<std::string> log;
    db.migrate(memoryLikeMigrations(&log));
    EXPECT_EQ(db.userVersion(), 3);
    EXPECT_TRUE(log.empty()) << "already-current database re-ran migrations";
}

TEST_F(DatabaseTest, MigrateUpgradesAnOlderDatabaseInPlace) {
    const std::string path = dbPath();
    const auto steps = memoryLikeMigrations();

    // Stop at v1 — this is the "older schema version" on disk.
    {
        Database db(path);
        db.migrate({steps[0]});
        EXPECT_EQ(db.userVersion(), 1);
        db.run("INSERT INTO knowledge (id, category, content) VALUES (?, ?, ?)", "k1", "fact",
               "the sky is blue");
        EXPECT_FALSE(db.columnExists("knowledge", "embedding"));
        EXPECT_FALSE(db.tableExists("procedures"));
    }

    // Re-open with the full chain: v1 is skipped, v2 and v3 run.
    std::vector<std::string> log;
    Database db(path);
    db.migrate(memoryLikeMigrations(&log));

    EXPECT_EQ(db.userVersion(), 3);
    EXPECT_EQ(log, (std::vector<std::string>{"v2", "v3"}));
    EXPECT_TRUE(db.columnExists("knowledge", "embedding"));
    EXPECT_TRUE(db.tableExists("procedures"));

    // The pre-existing row survived — migrations are additive.
    auto stmt = db.prepare("SELECT content, embedding FROM knowledge WHERE id = ?");
    stmt.bindText(1, "k1");
    ASSERT_TRUE(stmt.step());
    EXPECT_EQ(stmt.columnText(0), "the sky is blue");
    EXPECT_TRUE(stmt.isNull(1));
}

TEST_F(DatabaseTest, MigrateResumesAfterAPartiallyAppliedStep) {
    const std::string path = dbPath();
    const auto steps = memoryLikeMigrations();

    {
        // A prior run that died mid-v2: one column landed, the version marker
        // never advanced. addColumnIfMissing has to tolerate that.
        Database db(path);
        db.migrate({steps[0]});
        db.execute("ALTER TABLE knowledge ADD COLUMN embedding BLOB");
        EXPECT_EQ(db.userVersion(), 1);
    }

    Database db(path);
    EXPECT_NO_THROW(db.migrate(memoryLikeMigrations()));
    EXPECT_EQ(db.userVersion(), 3);
    EXPECT_TRUE(db.columnExists("knowledge", "superseded_by"));
}

TEST_F(DatabaseTest, FailingMigrationRollsBackAndKeepsTheOldVersion) {
    Database db(dbPath());
    auto steps = memoryLikeMigrations();

    Migration bad;
    bad.version = 4;
    bad.description = "broken step";
    bad.apply = [](Database& inner) {
        inner.execute("CREATE TABLE half_done (x INTEGER)");
        inner.execute("THIS IS NOT SQL");
    };
    steps.push_back(std::move(bad));

    EXPECT_THROW(db.migrate(steps), DatabaseError);

    // Everything up to v3 committed; v4 rolled back completely.
    EXPECT_EQ(db.userVersion(), 3);
    EXPECT_TRUE(db.tableExists("procedures"));
    EXPECT_FALSE(db.tableExists("half_done"));
    EXPECT_FALSE(db.inTransaction());
}

TEST_F(DatabaseTest, FailingMigrationErrorNamesTheStep) {
    Database db(dbPath());
    std::vector<Migration> steps{
        Migration::fromSql(1, "create the widgets table", "THIS IS NOT SQL")};
    try {
        db.migrate(steps);
        FAIL() << "expected the migration to throw";
    } catch (const DatabaseError& e) {
        const std::string message = e.what();
        EXPECT_NE(message.find("create the widgets table"), std::string::npos) << message;
        EXPECT_NE(message.find("version 1"), std::string::npos) << message;
        EXPECT_NE(message.find(db.path()), std::string::npos) << message;
    }
}

TEST_F(DatabaseTest, MigrateRejectsOutOfOrderSteps) {
    Database db(dbPath());
    std::vector<Migration> steps{
        Migration::fromSql(2, "second", "CREATE TABLE a (x INTEGER)"),
        Migration::fromSql(1, "first", "CREATE TABLE b (x INTEGER)"),
    };
    EXPECT_THROW(db.migrate(steps), DatabaseError);
    EXPECT_EQ(db.userVersion(), 0);
    EXPECT_FALSE(db.tableExists("a")) << "a malformed list must not partially apply";
}

TEST_F(DatabaseTest, MigrateRejectsVersionZero) {
    Database db(dbPath());
    std::vector<Migration> steps{Migration::fromSql(0, "zero", "CREATE TABLE a (x INTEGER)")};
    EXPECT_THROW(db.migrate(steps), DatabaseError);
}

TEST_F(DatabaseTest, MigrateRefusesToDowngrade) {
    Database db(dbPath());
    db.migrate(memoryLikeMigrations());
    ASSERT_EQ(db.userVersion(), 3);

    // A binary that only knows about v1 and v2 must not run against a v3 file.
    auto older = memoryLikeMigrations();
    older.pop_back();
    try {
        db.migrate(older);
        FAIL() << "expected a downgrade to be refused";
    } catch (const DatabaseError& e) {
        const std::string message = e.what();
        EXPECT_NE(message.find("newer build"), std::string::npos) << message;
    }
}

TEST_F(DatabaseTest, MigrationFromSqlRunsTheScript) {
    Database db(dbPath());
    db.migrate({Migration::fromSql(1, "create widgets",
                                   "CREATE TABLE widgets (id INTEGER PRIMARY KEY);"
                                   "CREATE INDEX widgets_id ON widgets(id);")});
    EXPECT_EQ(db.userVersion(), 1);
    EXPECT_TRUE(db.tableExists("widgets"));
}

TEST_F(DatabaseTest, AddColumnIfMissingReportsWhetherItActed) {
    Database db(dbPath());
    db.execute("CREATE TABLE t (a INTEGER)");
    EXPECT_TRUE(db.addColumnIfMissing("t", "b TEXT DEFAULT 'x'"));
    EXPECT_FALSE(db.addColumnIfMissing("t", "b TEXT DEFAULT 'x'"));
    EXPECT_TRUE(db.columnExists("t", "b"));
    EXPECT_THROW(db.addColumnIfMissing("missing_table", "c TEXT"), DatabaseError);
}

// ---------------------------------------------------------------------------
// busy_timeout under a concurrent writer
// ---------------------------------------------------------------------------

TEST_F(DatabaseTest, BusyTimeoutLetsAConcurrentWriterThrough) {
    const std::string path = dbPath("concurrent.db");
    {
        Database setup(path);
        setup.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, who TEXT)");
    }

    Database::Options patient;
    patient.busyTimeoutMs = 10000;

    Database holder(path, patient);
    Database waiter(path, patient);

    // The holder takes the write lock and keeps it for ~300 ms.
    std::atomic<bool> holderReady{false};
    std::atomic<bool> holderDone{false};
    std::thread holderThread([&] {
        Transaction txn(holder, Transaction::Behavior::Immediate);
        holder.run("INSERT INTO t (who) VALUES (?)", "holder");
        holderReady = true;
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
        txn.commit();
        holderDone = true;
    });

    while (!holderReady) {
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }

    // This write cannot proceed until the holder commits. With busy_timeout it
    // blocks and then succeeds; without it, SQLite returns SQLITE_BUSY at once.
    const auto start = std::chrono::steady_clock::now();
    EXPECT_NO_THROW(waiter.run("INSERT INTO t (who) VALUES (?)", "waiter"));
    const auto waited =
        std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() -
                                                              start);

    holderThread.join();
    EXPECT_TRUE(holderDone);

    EXPECT_GE(waited.count(), 100) << "the waiter did not actually contend for the write lock";
    EXPECT_EQ(countRows(waiter, "t"), 2);
}

TEST_F(DatabaseTest, ZeroBusyTimeoutFailsFastUnderContention) {
    const std::string path = dbPath("impatient.db");
    {
        Database setup(path);
        setup.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)");
    }

    Database::Options impatient;
    impatient.busyTimeoutMs = 0;

    Database holder(path, impatient);
    Database waiter(path, impatient);

    Transaction txn(holder, Transaction::Behavior::Immediate);
    holder.run("INSERT INTO t (id) VALUES (?)", 1);

    // The counterpart to the test above: with no timeout the same contention
    // raises immediately, which is what proves the timeout above did the work.
    EXPECT_THROW(waiter.run("INSERT INTO t (id) VALUES (?)", 2), DatabaseError);

    txn.commit();
    EXPECT_NO_THROW(waiter.run("INSERT INTO t (id) VALUES (?)", 2));
}

TEST_F(DatabaseTest, WalAllowsReadsDuringAWrite) {
    const std::string path = dbPath("wal_readers.db");
    {
        Database setup(path);
        setup.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)");
        setup.run("INSERT INTO t (id) VALUES (?)", 1);
    }

    Database writer(path);
    Database reader(path);

    Transaction txn(writer, Transaction::Behavior::Immediate);
    writer.run("INSERT INTO t (id) VALUES (?)", 2);

    // WAL readers see the pre-commit snapshot instead of blocking.
    EXPECT_EQ(countRows(reader, "t"), 1);
    txn.commit();
    EXPECT_EQ(countRows(reader, "t"), 2);
}

// ---------------------------------------------------------------------------
// FTS5 smoke test
// ---------------------------------------------------------------------------
// A missing SQLITE_ENABLE_FTS5 builds fine and fails at *query* time, so these
// tests are the only thing standing between the flag silently not taking and a
// runtime failure in whatever ships on top of gaia::Database.

TEST_F(DatabaseTest, ReportsFts5Availability) {
    EXPECT_TRUE(Database::hasFts5())
        << "gaia_core was built without SQLITE_ENABLE_FTS5 — check cpp/CMakeLists.txt";
    EXPECT_FALSE(Database::sqliteVersion().empty());
}

TEST_F(DatabaseTest, Fts5VirtualTableCanBeQueried) {
    Database db(dbPath("fts.db"));

    // A build without FTS5 fails right here with "no such module: fts5".
    db.execute("CREATE VIRTUAL TABLE docs USING fts5(title, body)");

    auto insert = db.prepare("INSERT INTO docs (title, body) VALUES (?, ?)");
    insert.bindAll("Ryzen AI", "The NPU accelerates local inference on AMD hardware").execute();
    insert.reset()
        .bindAll("Lemonade", "Lemonade Server runs GGUF models locally")
        .execute();
    insert.reset().bindAll("Unrelated", "A note about gardening tomatoes").execute();

    // Prefix query: FTS5's default tokenizer does no stemming, so "local*" is
    // what reaches both "local" and "locally".
    auto search = db.prepare("SELECT title FROM docs WHERE docs MATCH ? ORDER BY rank");
    search.bindText(1, "local*");
    std::vector<std::string> hits;
    while (search.step()) {
        hits.push_back(search.columnText(0));
    }
    EXPECT_EQ(hits.size(), 2u);

    // …and the bare term matches only the exact token.
    search.reset().bindText(1, "local");
    int exact = 0;
    while (search.step()) {
        ++exact;
    }
    EXPECT_EQ(exact, 1);

    // BM25 ranking is an FTS5-only feature — a stub module would not have it.
    auto ranked = db.prepare("SELECT title, bm25(docs) FROM docs WHERE docs MATCH ? ORDER BY rank");
    ranked.bindText(1, "NPU AND inference");
    ASSERT_TRUE(ranked.step());
    EXPECT_EQ(ranked.columnText(0), "Ryzen AI");
    EXPECT_LT(ranked.columnDouble(1), 0.0) << "bm25() returns a negative score in FTS5";
    EXPECT_FALSE(ranked.step());
}

TEST_F(DatabaseTest, Fts5ReflectsUpdatesAndDeletes) {
    Database db(dbPath("fts_mutate.db"));
    db.execute("CREATE VIRTUAL TABLE notes USING fts5(body)");
    db.run("INSERT INTO notes (body) VALUES (?)", "the quick brown fox");

    auto matches = [&db](const std::string& query) {
        auto stmt = db.prepare("SELECT COUNT(*) FROM notes WHERE notes MATCH ?");
        stmt.bindText(1, query);
        EXPECT_TRUE(stmt.step());
        return stmt.columnInt64(0);
    };

    EXPECT_EQ(matches("brown"), 1);
    db.run("UPDATE notes SET body = ? WHERE body MATCH ?", "the slow green turtle", "brown");
    EXPECT_EQ(matches("brown"), 0);
    EXPECT_EQ(matches("turtle"), 1);

    db.execute("DELETE FROM notes");
    EXPECT_EQ(matches("turtle"), 0);
}

// ---------------------------------------------------------------------------
// Error reporting
// ---------------------------------------------------------------------------

TEST_F(DatabaseTest, PrepareErrorCarriesSqlAndPath) {
    Database db(dbPath());
    try {
        db.prepare("SELECT * FROM no_such_table");
        FAIL() << "expected prepare to throw";
    } catch (const DatabaseError& e) {
        const std::string message = e.what();
        EXPECT_NE(message.find("no such table"), std::string::npos) << message;
        EXPECT_NE(message.find("SELECT * FROM no_such_table"), std::string::npos) << message;
        EXPECT_NE(message.find(db.path()), std::string::npos) << message;
        EXPECT_EQ(e.sql(), "SELECT * FROM no_such_table");
        EXPECT_EQ(e.dbPath(), db.path());
        EXPECT_NE(e.code(), 0);
    }
}

TEST_F(DatabaseTest, ConstraintViolationSurfacesTheSqliteMessage) {
    Database db(dbPath());
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)");
    db.run("INSERT INTO t (name) VALUES (?)", "dup");
    try {
        db.run("INSERT INTO t (name) VALUES (?)", "dup");
        FAIL() << "expected a UNIQUE constraint failure";
    } catch (const DatabaseError& e) {
        const std::string message = e.what();
        EXPECT_NE(message.find("UNIQUE constraint failed"), std::string::npos) << message;
    }
}

TEST_F(DatabaseTest, OutOfRangeColumnThrows) {
    Database db(dbPath());
    db.execute("CREATE TABLE t (x INTEGER)");
    db.run("INSERT INTO t VALUES (?)", 1);
    auto stmt = db.prepare("SELECT x FROM t");
    ASSERT_TRUE(stmt.step());
    EXPECT_THROW(stmt.columnInt64(5), DatabaseError);
    EXPECT_THROW(stmt.columnText(-1), DatabaseError);
}

TEST_F(DatabaseTest, EmptySqlIsRejected) {
    Database db(dbPath());
    EXPECT_THROW(db.prepare("   -- just a comment\n"), DatabaseError);
}

TEST_F(DatabaseTest, DoubleQuotedStringLiteralsAreRejected) {
    // SQLITE_DQS=0: a mistyped column name must be an error, not a silent
    // string constant that quietly matches nothing.
    Database db(dbPath());
    db.execute("CREATE TABLE t (name TEXT)");
    db.run("INSERT INTO t VALUES (?)", "alice");
    EXPECT_THROW(db.prepare("SELECT * FROM t WHERE name = \"alice\""), DatabaseError);
    EXPECT_NO_THROW(db.prepare("SELECT * FROM t WHERE name = 'alice'"));
}
