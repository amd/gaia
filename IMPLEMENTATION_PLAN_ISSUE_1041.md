# Implementation Plan: SQLAlchemy Database Mixin (Issue #1041)

## Executive Summary

Create a new database mixin using SQLAlchemy Core to support multiple database backends (SQLite, PostgreSQL, MySQL) with connection pooling and proper transaction management. This will enable the Medical Intake PoC and other agents to use enterprise-grade databases.

**Location:** `src/gaia/agents/base/database_mixin.py` (new file)
**Estimated Effort:** 4-6 hours (AI-assisted)
**Priority:** Critical
**Component:** Agents
**Development Approach:** Test-Driven Development (TDD)

---

## 1. Current State Analysis

### Existing Implementation

**Location:** `src/gaia/database/mixin.py`

**Technology:** Python's built-in `sqlite3` module

**Features:**
- SQLite-only support
- Simple connection management (no pooling)
- Basic transaction support via context manager
- Methods: `init_db()`, `query()`, `insert()`, `update()`, `delete()`, `execute()`, `transaction()`, `table_exists()`

**Current Usage:**
- `MedicalIntakeAgent` (EMR agent) in `src/gaia/agents/emr/agent.py`
- `DatabaseAgent` wrapper class in `src/gaia/database/agent.py`
- Well-tested with comprehensive unit and integration tests

### Why a New Mixin?

The existing mixin is **SQLite-only** and uses the built-in `sqlite3` module. The new requirements include:

1. **Multi-database support:** PostgreSQL, MySQL, SQLite
2. **Connection pooling:** For concurrent requests and better performance
3. **SQLAlchemy Core:** Industry-standard database abstraction
4. **Enterprise readiness:** Production-grade transaction management

The issue specifically requests creating `src/gaia/agents/base/database_mixin.py`, suggesting this is a **separate, complementary implementation** rather than a replacement.

---

## 2. Implementation Design

### Architecture Decision: SQLAlchemy Core (not ORM)

**Why Core over ORM:**
- ✅ More explicit control over SQL generation
- ✅ Better performance (no object-relational mapping overhead)
- ✅ Simpler for agent tools that execute parameterized queries
- ✅ Easier to understand and debug SQL operations
- ✅ More flexible for dynamic table structures

### Key Components

#### 2.1 Connection Management
```python
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

engine = create_engine(
    db_url,
    poolclass=QueuePool,
    pool_size=pool_size,
    max_overflow=10,
    pool_pre_ping=True  # Verify connections before use
)
```

#### 2.2 Transaction Management
```python
@contextmanager
def transaction(self):
    """Context manager for atomic operations."""
    conn = self.engine.connect()
    trans = conn.begin()
    try:
        yield conn
        trans.commit()
    except Exception:
        trans.rollback()
        raise
    finally:
        conn.close()
```

#### 2.3 Parameterized Queries
All queries will use SQLAlchemy's `text()` with parameterized bindings:
```python
conn.execute(text("SELECT * FROM users WHERE id = :id"), {"id": user_id})
```

### Target Interface (from Issue)

```python
class DatabaseMixin(ABC):
    def init_database(self, db_url: str, pool_size: int = 5): ...
    def get_connection(self) -> Connection: ...
    def transaction(self) -> Connection: ...
    def execute_query(self, query: str, params: dict) -> List[Dict]: ...
    def execute_insert(self, table: str, data: dict, returning: str = None) -> Any: ...
    def execute_update(self, table: str, data: dict, where: str, where_params: dict) -> int: ...
```

### Enhanced Design

We'll add a few more methods for feature parity with the existing mixin and better usability:

```python
class DatabaseMixin(ABC):
    # Core initialization
    def init_database(self, db_url: str, pool_size: int = 5) -> None
    def close_database(self) -> None

    # Connection management
    def get_connection(self) -> Connection

    # Transaction management
    @contextmanager
    def transaction(self) -> Connection

    # Query operations
    def execute_query(self, query: str, params: dict = None) -> List[Dict]
    def execute_insert(self, table: str, data: dict, returning: str = None) -> Any
    def execute_update(self, table: str, data: dict, where: str, where_params: dict) -> int
    def execute_delete(self, table: str, where: str, where_params: dict) -> int

    # Utility methods
    def execute_raw(self, sql: str) -> None  # For DDL (CREATE TABLE, etc.)
    def table_exists(self, table: str) -> bool

    # Properties
    @property
    def db_ready(self) -> bool
```

### 2.4 Thread Safety Design

**CRITICAL: This mixin MUST be thread-safe for production use.**

#### Thread Safety Guarantees:

✅ **SQLAlchemy Engine is thread-safe**
- The `Engine` object is designed for concurrent access from multiple threads
- Multiple threads can safely call `engine.connect()` simultaneously
- Source: [SQLAlchemy docs on Engine threading](https://docs.sqlalchemy.org/en/20/core/connections.html#engine-disposal)

✅ **Connection Pool (QueuePool) is thread-safe**
- Uses internal threading locks to manage connection checkout/checkin
- `pool_size` parameter limits concurrent connections
- When pool is exhausted, threads block until a connection is available
- `max_overflow` allows temporary additional connections beyond pool_size

✅ **Our Implementation Pattern is thread-safe**
- Store only the `engine` as an instance variable (thread-safe)
- **Never store connections** as instance variables (would be thread-unsafe)
- Each operation gets its own connection via `engine.connect()`
- Connections are always released in `finally` blocks
- Transaction context manager provides per-thread transaction isolation

#### Anti-Patterns to Avoid:

❌ **DO NOT store connections as instance variables:**
```python
# BAD - Not thread-safe!
class DatabaseMixin:
    def init_database(self, db_url):
        self._connection = self.engine.connect()  # Shared across threads!

    def execute_query(self, sql):
        return self._connection.execute(text(sql))  # Race conditions!
```

✅ **DO get connections per-operation:**
```python
# GOOD - Thread-safe!
class DatabaseMixin:
    def execute_query(self, sql, params=None):
        conn = self.engine.connect()  # New connection per operation
        try:
            result = conn.execute(text(sql), params or {})
            return [dict(row) for row in result]
        finally:
            conn.close()  # Always release back to pool
```

#### Transaction Isolation:

Each call to `transaction()` gets its own connection and transaction:

```python
@contextmanager
def transaction(self):
    """Thread-safe transaction context manager."""
    conn = self.engine.connect()  # Each thread gets own connection
    trans = conn.begin()
    try:
        yield conn
        trans.commit()
    except Exception:
        trans.rollback()
        raise
    finally:
        conn.close()  # Release connection back to pool
```

**Thread Isolation Guarantee:**
- Thread A's transaction is isolated from Thread B's transaction
- Each has its own connection from the pool
- ACID properties are maintained per-transaction
- Default isolation level: READ_COMMITTED

#### Testing Thread Safety:

We will add comprehensive thread safety tests (see Phase 2, Step 2.10):
- Concurrent queries from multiple threads
- Concurrent inserts/updates/deletes
- Transaction isolation verification
- Connection pool exhaustion handling
- Connection cleanup under load

---

## 3. File Changes Required

### 3.1 New Files

#### `src/gaia/agents/base/database_mixin.py` ⭐ NEW
- Main implementation file
- ~300-400 lines of code
- Full docstrings and examples

#### `tests/unit/test_database_mixin_sqlalchemy.py` ⭐ NEW
- Comprehensive unit tests
- Test all database operations
- Test with SQLite (in-memory)
- ~300-400 lines

#### `tests/integration/test_database_mixin_multidb.py` ⭐ NEW (Optional)
- Integration tests for PostgreSQL and MySQL
- Requires test database setup
- Can be marked with `@pytest.mark.integration`
- ~200-300 lines

### 3.2 Modified Files

#### `setup.py` ✏️ MODIFY
- Add `sqlalchemy>=2.0` to `install_requires`
- Line ~72 (in the install_requires list)

#### `src/gaia/agents/base/__init__.py` ✏️ MODIFY
- Export the new DatabaseMixin
- Add: `from gaia.agents.base.database_mixin import DatabaseMixin`

#### `docs/sdk/mixins/database-mixin.mdx` ✏️ MODIFY (Optional)
- Add section about SQLAlchemy-based mixin
- Document multi-database support
- Migration guide from sqlite3 to SQLAlchemy version

---

## 4. Implementation Details

### 4.1 Database URL Format

Support standard SQLAlchemy connection strings:

```python
# SQLite
"sqlite:///path/to/database.db"
"sqlite:///:memory:"

# PostgreSQL
"postgresql://user:pass@localhost:5432/dbname"
"postgresql+psycopg2://user:pass@localhost/dbname"

# MySQL
"mysql://user:pass@localhost:3306/dbname"
"mysql+pymysql://user:pass@localhost/dbname"
```

### 4.2 Connection Pooling

```python
# QueuePool parameters:
- pool_size: Number of connections to keep open (default: 5)
- max_overflow: Additional connections when pool exhausted (default: 10)
- pool_pre_ping: Test connections before use (default: True)
- pool_recycle: Recycle connections after N seconds (default: 3600)
```

### 4.3 SQL Injection Prevention

All user inputs must use parameterized queries:

```python
# ✅ GOOD - Parameterized
self.execute_query(
    "SELECT * FROM users WHERE email = :email",
    {"email": user_email}
)

# ❌ BAD - String interpolation (vulnerable)
self.execute_query(f"SELECT * FROM users WHERE email = '{user_email}'")
```

### 4.4 Transaction Isolation

```python
# Default isolation level: READ_COMMITTED
# Can be customized per-engine:
engine = create_engine(db_url, isolation_level="SERIALIZABLE")
```

### 4.5 Error Handling

Wrap SQLAlchemy exceptions in descriptive errors:

```python
try:
    result = conn.execute(query)
except SQLAlchemyError as e:
    logger.error(f"Database error: {e}")
    raise RuntimeError(f"Database operation failed: {e}") from e
```

---

## 5. Testing Strategy

### 5.1 Unit Tests (Required)

**File:** `tests/unit/test_database_mixin_sqlalchemy.py`

**Test Coverage:**
- ✅ Initialization with various database URLs
- ✅ Connection pooling behavior
- ✅ Transaction commit and rollback
- ✅ execute_query (SELECT operations)
- ✅ execute_insert (with and without RETURNING)
- ✅ execute_update (row count verification)
- ✅ execute_delete (row count verification)
- ✅ execute_raw (DDL operations)
- ✅ table_exists utility
- ✅ Parameterized query SQL injection prevention
- ✅ Error handling and connection cleanup
- ✅ Multiple connections concurrently

**Database:** SQLite in-memory (`:memory:`) for fast, isolated tests

### 5.2 Integration Tests (Optional)

**File:** `tests/integration/test_database_mixin_multidb.py`

**Test Coverage:**
- PostgreSQL connection and operations
- MySQL connection and operations
- Connection pool exhaustion and recovery
- Long-running transactions

**Note:** These tests require external database services and can be marked with `@pytest.mark.integration` to run separately.

### 5.3 Test Organization

```python
# Test structure
class TestDatabaseMixinInitialization:
    def test_init_sqlite_memory()
    def test_init_sqlite_file()
    def test_init_with_custom_pool_size()
    def test_reinit_closes_previous()
    def test_require_init()

class TestDatabaseMixinQueries:
    def test_execute_query_select_all()
    def test_execute_query_with_params()
    def test_execute_query_empty_result()

class TestDatabaseMixinInserts:
    def test_execute_insert_basic()
    def test_execute_insert_with_returning()

class TestDatabaseMixinUpdates:
    def test_execute_update_single_row()
    def test_execute_update_multiple_rows()
    def test_execute_update_no_match()

class TestDatabaseMixinDeletes:
    def test_execute_delete_single()
    def test_execute_delete_multiple()

class TestDatabaseMixinTransactions:
    def test_transaction_commit()
    def test_transaction_rollback_on_error()
    def test_nested_operations_in_transaction()

class TestDatabaseMixinUtilities:
    def test_table_exists_true()
    def test_table_exists_false()
    def test_execute_raw_ddl()

class TestDatabaseMixinSecurity:
    def test_parameterized_query_prevents_sql_injection()
    def test_special_characters_in_data()
```

---

## 6. Migration Path

### 6.1 Migration Strategy: Replace sqlite3 Mixin in MedicalIntakeAgent

**IMPORTANT:** As part of this issue, we WILL migrate the MedicalIntakeAgent (EMR agent) to use the new SQLAlchemy-based mixin. This is required because the issue states: "This is the only new framework component required for the Medical Intake PoC."

**Files to Migrate:**
- `src/gaia/agents/emr/agent.py` - Update to use new mixin
- `src/gaia/agents/emr/cli.py` - Update if needed for new db_url parameter
- `tests/integration/test_database_mixin_integration.py` - May need updates
- `docs/playbooks/emr-agent/*.mdx` - Update documentation

### 6.2 Coexistence Strategy (Long-term)

Both mixins will coexist in the codebase:

| Aspect | Existing (`gaia.database.mixin`) | New (`gaia.agents.base.database_mixin`) |
|--------|----------------------------------|----------------------------------------|
| **Location** | `src/gaia/database/mixin.py` | `src/gaia/agents/base/database_mixin.py` |
| **Technology** | `sqlite3` | SQLAlchemy Core |
| **Databases** | SQLite only | SQLite, PostgreSQL, MySQL |
| **Dependencies** | Zero (built-in) | `sqlalchemy>=2.0` |
| **Use Case** | Simple agents, prototyping | Production agents, multi-DB |
| **Status** | Keep for backward compatibility | New default for production |

### 6.3 When to Use Which Mixin?

**Use new SQLAlchemy mixin (RECOMMENDED):**
- Production deployments (like Medical Intake PoC)
- Need PostgreSQL or MySQL
- Require connection pooling
- Multi-database support
- **Default choice for new agents**

**Use existing sqlite3 mixin:**
- Legacy code / backward compatibility
- Prototyping with zero dependencies
- Simple scripts where SQLite is sufficient

### 6.4 Migrating MedicalIntakeAgent (THIS ISSUE)

**Changes Required in `src/gaia/agents/emr/agent.py`:**

```python
# BEFORE (current implementation using sqlite3 mixin)
from gaia.database import DatabaseMixin

class MedicalIntakeAgent(Agent, DatabaseMixin, FileWatcherMixin):
    def __init__(self, db_path: str = "./data/patients.db", **kwargs):
        super().__init__(**kwargs)
        self.init_db(db_path)  # SQLite only

    def _init_database(self):
        self.init_db(self._db_path)
        self.execute(PATIENT_SCHEMA)

    # Uses: self.query(), self.insert(), self.update(), self.delete()

# AFTER (updated to use SQLAlchemy mixin)
from gaia.agents.base.database_mixin import DatabaseMixin

class MedicalIntakeAgent(Agent, DatabaseMixin, FileWatcherMixin):
    def __init__(self, db_url: str = "sqlite:///./data/patients.db", **kwargs):
        super().__init__(**kwargs)
        self._db_url = db_url  # Changed from db_path to db_url
        # ... rest of init ...

    def _init_database(self):
        self.init_database(self._db_url, pool_size=5)  # Multi-DB support
        self.execute_raw(PATIENT_SCHEMA)  # execute() -> execute_raw()

    # Update all database calls throughout the file:
    # - self.query() -> self.execute_query()
    # - self.insert() -> self.execute_insert()
    # - self.update() -> self.execute_update()
    # - self.delete() -> self.execute_delete()
    # - self.execute() -> self.execute_raw()
    # - self.transaction() stays the same
```

**Backward Compatibility:** To maintain backward compatibility, the CLI can accept both `--db-path` (converted to SQLite URL) and `--db-url` parameters.

---

## 7. Step-by-Step Implementation Plan (Test-Driven Development)

**CRITICAL: This implementation follows Test-Driven Development (TDD).**

**TDD Workflow:**
1. ✅ Write tests FIRST (define expected behavior)
2. ✅ Verify tests are correct (review test logic, ensure they test the right things)
3. ✅ Run tests - they should FAIL (no implementation yet)
4. ✅ Implement minimal code to make tests pass
5. ✅ Run tests - iterate until all pass
6. ✅ Refactor and polish

---

### Phase 1: Setup & Dependencies (15 min)

#### Step 1.1: Update Dependencies
- [ ] Modify `setup.py` to add `sqlalchemy>=2.0` to `install_requires`
- [ ] Install SQLAlchemy: `pip install sqlalchemy>=2.0`

#### Step 1.2: Create Stub Implementation
- [ ] Create `src/gaia/agents/base/database_mixin.py`
- [ ] Add copyright header and module docstring
- [ ] Create `DatabaseMixin` class with method stubs (all raise `NotImplementedError`)
- [ ] Update `src/gaia/agents/base/__init__.py` to export `DatabaseMixin`

**Why stub first?** This allows us to import the mixin in our tests without errors.

---

### Phase 2: Write Tests FIRST (1.5-2 hours)

**CRITICAL: Write ALL tests before implementing any functionality!**

#### Step 2.1: Setup Test Infrastructure
- [ ] Create `tests/unit/test_database_mixin_sqlalchemy.py`
- [ ] Add copyright header and imports
- [ ] Create test helper class `TestDB(DatabaseMixin)`
- [ ] Add pytest fixtures for common setup

#### Step 2.2: Write Initialization Tests
Write tests that define how initialization should work:

- [ ] `test_init_memory()` - In-memory SQLite should initialize correctly
- [ ] `test_init_file()` - File-based SQLite should create file and parent dirs
- [ ] `test_init_custom_pool_size()` - Custom pool_size parameter should work
- [ ] `test_reinit_closes_previous()` - Calling init_database twice should close first connection
- [ ] `test_require_init()` - Operations before init should raise RuntimeError
- [ ] `test_close_idempotent()` - close_database should be safe to call multiple times
- [ ] `test_db_ready_property()` - db_ready should return True/False appropriately

#### Step 2.3: Write Query Tests (SELECT)
- [ ] `test_execute_query_select_all()` - SELECT * should return all rows as list of dicts
- [ ] `test_execute_query_with_params()` - Parameterized queries should work correctly
- [ ] `test_execute_query_empty_result()` - Empty results should return []
- [ ] `test_execute_query_no_params()` - Queries without params should work

#### Step 2.4: Write Insert Tests
- [ ] `test_execute_insert_basic()` - Basic insert should return row ID
- [ ] `test_execute_insert_with_returning()` - RETURNING clause should return specified column
- [ ] `test_execute_insert_multiple()` - Multiple inserts should work correctly

#### Step 2.5: Write Update Tests
- [ ] `test_execute_update_single_row()` - Update single row should return count=1
- [ ] `test_execute_update_multiple_rows()` - Update multiple rows should return correct count
- [ ] `test_execute_update_no_match()` - Update with no matches should return count=0
- [ ] `test_execute_update_param_collision()` - Data and where params shouldn't collide

#### Step 2.6: Write Delete Tests
- [ ] `test_execute_delete_single()` - Delete single row should return count=1
- [ ] `test_execute_delete_multiple()` - Delete multiple rows should return correct count
- [ ] `test_execute_delete_no_match()` - Delete with no matches should return count=0

#### Step 2.7: Write Transaction Tests
- [ ] `test_transaction_commit()` - Successful transaction should commit all changes
- [ ] `test_transaction_rollback_on_error()` - Exception should rollback all changes
- [ ] `test_transaction_multiple_operations()` - Multiple operations in transaction should be atomic
- [ ] `test_transaction_connection_cleanup()` - Connection should be closed after transaction

#### Step 2.8: Write Utility Tests
- [ ] `test_execute_raw_create_table()` - execute_raw should handle CREATE TABLE
- [ ] `test_table_exists_true()` - table_exists should return True for existing table
- [ ] `test_table_exists_false()` - table_exists should return False for missing table

#### Step 2.9: Write Security Tests
- [ ] `test_parameterized_query_prevents_sql_injection()` - SQL injection attempts should be safe
- [ ] `test_special_characters_in_data()` - Special chars (quotes, semicolons) should work

#### Step 2.10: Write Thread Safety Tests
**CRITICAL: Verify concurrent access is safe**

- [ ] `test_concurrent_queries()` - Multiple threads doing SELECT simultaneously should work
- [ ] `test_concurrent_inserts()` - Multiple threads inserting simultaneously should work
- [ ] `test_concurrent_transactions()` - Multiple transactions in different threads should be isolated
- [ ] `test_connection_pool_exhaustion()` - Verify behavior when pool is exhausted (should block, not fail)
- [ ] `test_connection_cleanup_under_load()` - Connections should be released properly under concurrent load

**Test Implementation Strategy:**
```python
import threading
import concurrent.futures

def test_concurrent_queries():
    """Verify multiple threads can query simultaneously."""
    db = TestDB()
    db.init_database("sqlite:///:memory:", pool_size=5)
    db.execute_raw("CREATE TABLE items (id INTEGER, value TEXT)")
    for i in range(10):
        db.execute_insert("items", {"id": i, "value": f"item{i}"})

    def query_worker(thread_id):
        # Each thread performs multiple queries
        for _ in range(10):
            results = db.execute_query("SELECT * FROM items WHERE id = :id", {"id": thread_id % 10})
            assert len(results) == 1
            assert results[0]["value"] == f"item{thread_id % 10}"
        return thread_id

    # Run 20 threads concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(query_worker, i) for i in range(20)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 20
    db.close_database()
```

#### Step 2.11: **VERIFY TESTS ARE CORRECT** ⚠️
**CRITICAL STEP - DO NOT SKIP!**

- [ ] Review EVERY test carefully
- [ ] Ask: "Does this test actually verify the intended behavior?"
- [ ] Check test assertions are meaningful (not just "no error thrown")
- [ ] Verify test data covers edge cases
- [ ] Ensure parameterized queries are actually used (not string interpolation)
- [ ] Check that security tests would catch vulnerabilities
- [ ] Run tests - they should ALL FAIL (no implementation yet)

**Questions to ask for each test:**
- ✅ Is the test name clear and descriptive?
- ✅ Does the test check the right thing?
- ✅ Are assertions specific and meaningful?
- ✅ Does it test edge cases?
- ✅ Would it catch bugs if the code is wrong?

---

### Phase 3: Implement Code to Pass Tests (2-3 hours)

**Now implement the mixin to make the tests pass!**

#### Step 3.1: Core Infrastructure (Thread-Safe Implementation)
- [ ] Implement `__init__` to initialize instance variables
  - Store only the `engine` (thread-safe)
  - **DO NOT** store connections as instance variables (not thread-safe)
- [ ] Implement `init_database()` with SQLAlchemy engine creation
  - Use `create_engine()` with connection pooling
  - Set pool_size, max_overflow, pool_pre_ping
  - Set `pool_recycle=3600` to recycle stale connections
- [ ] Implement `close_database()` for cleanup (dispose engine)
- [ ] Implement `get_connection()` to return a NEW connection from pool
  - Always returns `self.engine.connect()` (creates new connection)
  - Never reuse connections across calls
- [ ] Implement `db_ready` property (check if engine exists)
- [ ] Implement `_require_db()` internal validation method
- [ ] Run initialization tests - should PASS now

**Thread Safety Pattern:**
```python
class DatabaseMixin:
    def __init__(self):
        self.engine = None  # Engine is thread-safe
        # DO NOT store: self._connection (not thread-safe!)

    def init_database(self, db_url, pool_size=5):
        self.engine = create_engine(
            db_url,
            poolclass=QueuePool,
            pool_size=pool_size,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600
        )

    def get_connection(self):
        """Returns a NEW connection (thread-safe)."""
        self._require_db()
        return self.engine.connect()
```

#### Step 3.2: Query Operations (SELECT) - Thread-Safe
- [ ] Implement `execute_query()` for SELECT operations
  - **Get connection per-operation** (not shared)
  - Use `text()` with parameter binding
  - Convert rows to list of dictionaries
  - Handle empty results
  - **Always close connection in finally block**
  - Proper error handling
- [ ] Run query tests - should PASS now
- [ ] Run thread safety tests - should PASS now

**Thread-Safe Pattern:**
```python
def execute_query(self, sql, params=None):
    """Thread-safe query execution."""
    self._require_db()
    conn = self.engine.connect()  # Get new connection from pool
    try:
        result = conn.execute(text(sql), params or {})
        return [dict(row) for row in result]
    finally:
        conn.close()  # Always release back to pool
```

#### Step 3.3: Insert Operations
- [ ] Implement `execute_insert()` for INSERT operations
  - Generate INSERT SQL from data dict
  - Support `returning` parameter for PostgreSQL/MySQL
  - Return inserted ID or RETURNING value
  - Use parameterized queries
- [ ] Run insert tests - should PASS now

#### Step 3.4: Update Operations
- [ ] Implement `execute_update()` for UPDATE operations
  - Generate UPDATE SQL from data dict
  - Support WHERE clause with parameters
  - Merge data and where params (avoid collisions with prefix)
  - Return affected row count
- [ ] Run update tests - should PASS now

#### Step 3.5: Delete Operations
- [ ] Implement `execute_delete()` for DELETE operations
  - Generate DELETE SQL with WHERE clause
  - Use parameterized queries
  - Return deleted row count
- [ ] Run delete tests - should PASS now

#### Step 3.6: Transactions
- [ ] Implement `transaction()` context manager
  - Get connection from pool
  - Begin transaction
  - Yield connection to caller
  - Auto-commit on success
  - Auto-rollback on exception
  - Always close connection in finally block
- [ ] Run transaction tests - should PASS now

#### Step 3.7: Utilities
- [ ] Implement `execute_raw()` for DDL (CREATE TABLE, etc.)
- [ ] Implement `table_exists()` using SQLAlchemy inspector
- [ ] Run utility tests - should PASS now

#### Step 3.8: Verify All Tests Pass
- [ ] Run: `pytest tests/unit/test_database_mixin_sqlalchemy.py -v`
- [ ] ALL tests should PASS (if not, iterate!)
- [ ] Fix any failing tests

---

### Phase 4: Migrate MedicalIntakeAgent (1 hour)

**Update the EMR agent to use the new SQLAlchemy mixin**

#### Step 4.1: Update EMR Agent Implementation
- [ ] Modify `src/gaia/agents/emr/agent.py`:
  - Change import from `gaia.database` to `gaia.agents.base.database_mixin`
  - Update `__init__` parameter from `db_path` to `db_url`
  - Convert `db_path` to SQLite URL format: `sqlite:///./data/patients.db`
  - Update `_init_database()` method
  - Find/replace method calls throughout the file:
    - `self.query()` → `self.execute_query()`
    - `self.insert()` → `self.execute_insert()`
    - `self.update()` → `self.execute_update()`
    - `self.delete()` → `self.execute_delete()`
    - `self.execute()` → `self.execute_raw()`
    - `self.transaction()` stays the same
  - Add pool_size parameter to `init_database()` call

#### Step 4.2: Update EMR CLI (if needed)
- [ ] Check `src/gaia/agents/emr/cli.py` for any database path handling
- [ ] Add support for `--db-url` parameter
- [ ] Maintain backward compatibility with `--db-path` (convert to SQLite URL)

#### Step 4.3: Test EMR Agent Migration
- [ ] Run existing EMR agent tests
- [ ] Fix any breaking changes
- [ ] Verify backward compatibility (SQLite still works)

---

### Phase 5: Documentation & Polish (1 hour)

#### Step 5.1: Add Comprehensive Docstrings
- [ ] Add detailed module-level docstring to `database_mixin.py`
  - Overview of the mixin
  - Complete usage example
  - Database URL formats (SQLite, PostgreSQL, MySQL)
  - Connection pooling parameters
- [ ] Ensure all methods have detailed docstrings with:
  - Description of what it does
  - Args documentation
  - Returns documentation
  - Raises documentation
  - Usage examples

#### Step 5.2: Code Review & Refactoring
- [ ] Review all code for clarity and consistency
- [ ] Ensure proper error handling throughout
- [ ] Verify SQL injection prevention (all queries use parameterization)
- [ ] Check connection cleanup in all code paths
- [ ] Refactor any duplicated code

#### Step 5.3: Run Full Test Suite
- [ ] Run: `pytest tests/unit/test_database_mixin_sqlalchemy.py -v`
- [ ] Run EMR agent tests: `pytest tests/ -k emr -v`
- [ ] Run all database tests: `pytest tests/ -k database -v`
- [ ] Ensure 100% pass rate

#### Step 5.4: Linting
- [ ] Run: `python util/lint.py` (if available) or `black`, `flake8`
- [ ] Fix any linting issues
- [ ] Ensure code follows GAIA style guidelines

#### Step 5.5: Final Verification
- [ ] Verify all acceptance criteria are met (see Section 8)
- [ ] Test manually with SQLite (create a simple test script)
- [ ] Verify connection pooling is working
- [ ] **Verify thread safety tests pass** (concurrent operations work correctly)
- [ ] Check that migrations don't break existing functionality

---

## 8. Acceptance Criteria Checklist

### From the Original Issue:

- [ ] Create `src/gaia/agents/base/database_mixin.py`
- [ ] Support SQLite, PostgreSQL, MySQL via SQLAlchemy connection URLs
- [ ] Implement connection pooling for concurrent requests
- [ ] Provide transaction management with context managers
- [ ] Include parameterized queries (SQL injection prevention)
- [ ] Add methods: `init_database()`, `execute_query()`, `execute_insert()`, `execute_update()`, `transaction()`
- [ ] Add unit tests for all database operations
- [ ] Document usage in docstrings
- [ ] Add `sqlalchemy>=2.0` to `install_requires` in `setup.py`

### Additional Required Items:

- [ ] `execute_delete()` method (needed for CRUD completeness)
- [ ] `get_connection()` method (specified in issue interface)
- [ ] `table_exists()` utility (useful for schema management)
- [ ] `execute_raw()` method (for DDL operations like CREATE TABLE)
- [ ] `close_database()` method (for cleanup)
- [ ] `db_ready` property (for checking initialization state)

### Migration Requirements:

**CRITICAL: Issue states "This is the only new framework component required for the Medical Intake PoC"**

- [ ] Migrate `MedicalIntakeAgent` in `src/gaia/agents/emr/agent.py` to use new mixin
- [ ] Update all database method calls in EMR agent
- [ ] Ensure EMR agent tests still pass
- [ ] Maintain backward compatibility where possible

### Quality Requirements:

- [ ] All tests written FIRST (TDD approach)
- [ ] Tests verified to test the correct behavior
- [ ] All tests pass (100% pass rate)
- [ ] **Thread safety tests pass** (concurrent operations work correctly)
- [ ] Code follows GAIA style guidelines (linting passes)
- [ ] No SQL injection vulnerabilities
- [ ] Proper connection cleanup in all code paths
- [ ] **No shared connection state** (connections are per-operation, not instance variables)
- [ ] Comprehensive docstrings with examples

---

## 9. Potential Issues & Solutions

### Issue 1: Database Driver Dependencies

**Problem:** PostgreSQL and MySQL require additional drivers (`psycopg2`, `pymysql`)

**Solution:**
- SQLAlchemy doesn't include drivers by default
- Document required drivers in docstrings
- Consider adding to `extras_require` in setup.py:
  ```python
  "database": [
      "sqlalchemy>=2.0",
      "psycopg2-binary>=2.9.0",  # PostgreSQL
      "pymysql>=1.0.0",           # MySQL
  ]
  ```

### Issue 2: RETURNING Clause Support

**Problem:** SQLite doesn't support `RETURNING` until version 3.35.0 (2021)

**Solution:**
- Check SQLite version at runtime
- Fall back to `cursor.lastrowid` for older versions
- Document this limitation in docstrings

### Issue 3: Connection Pool Exhaustion

**Problem:** If all connections are in use, requests will block

**Solution:**
- Set reasonable `pool_size` and `max_overflow`
- Document pool configuration in docstrings
- Consider pool timeout parameter
- Ensure connections are always released (use `finally` blocks)

### Issue 4: Transaction Isolation Differences

**Problem:** Different databases have different default isolation levels

**Solution:**
- Document default isolation level (READ_COMMITTED)
- Allow customization via engine parameters
- Test behavior with all supported databases

### Issue 5: Table Existence Check

**Problem:** Different databases use different system tables

**Solution:**
```python
def table_exists(self, table: str) -> bool:
    # Use SQLAlchemy's inspector
    from sqlalchemy import inspect
    inspector = inspect(self.engine)
    return table in inspector.get_table_names()
```

---

## 10. Future Enhancements (Out of Scope)

These are NOT part of issue #1041 but could be considered later:

1. **Async Support:** SQLAlchemy supports async with `asyncio`
2. **Query Builder:** Higher-level query building (stay in Core, not ORM)
3. **Migration Support:** Alembic integration for schema migrations
4. **Connection Retry Logic:** Automatic retry on transient failures
5. **Read Replicas:** Support for read/write splitting
6. **Prepared Statements:** For repeated queries
7. **Batch Operations:** Bulk inserts/updates for efficiency
8. **Connection Pooling Metrics:** Expose pool statistics
9. **Schema Reflection:** Automatic table structure discovery
10. **Database Agent Wrapper:** Like existing `DatabaseAgent` but for SQLAlchemy mixin

---

## 11. Testing Commands

```bash
# Install dependencies (if not already installed)
pip install -e ".[dev]"
pip install sqlalchemy>=2.0

# Run unit tests for the new mixin
pytest tests/unit/test_database_mixin_sqlalchemy.py -v

# Run with coverage
pytest tests/unit/test_database_mixin_sqlalchemy.py --cov=gaia.agents.base.database_mixin --cov-report=html

# Run all database-related tests
pytest tests/ -k database -v

# Linting
python util/lint.py  # If available
black src/gaia/agents/base/database_mixin.py
flake8 src/gaia/agents/base/database_mixin.py
```

---

## 12. Summary

This implementation plan provides a comprehensive roadmap for creating a production-ready database mixin using SQLAlchemy Core and migrating the Medical Intake PoC to use it.

### What We're Building:

✅ **SQLAlchemy-based database mixin** supporting multiple databases (SQLite, PostgreSQL, MySQL)
✅ **Connection pooling** for concurrent requests and better performance
✅ **Robust transaction management** with automatic rollback
✅ **SQL injection prevention** through parameterized queries
✅ **Comprehensive unit tests** written FIRST (TDD approach)
✅ **Full documentation** with examples and usage guides
✅ **Migration of MedicalIntakeAgent** to use the new mixin

### Development Approach: Test-Driven Development (TDD)

**Critical:** This plan follows TDD methodology:
1. ✅ Write ALL tests first (define expected behavior)
2. ✅ Verify tests are correct and test the right things
3. ✅ Run tests - they should fail (no implementation yet)
4. ✅ Implement code to make tests pass
5. ✅ Iterate until all tests pass
6. ✅ Refactor and polish

### Implementation Phases:

1. **Phase 1:** Setup & Dependencies (15 min)
2. **Phase 2:** Write Tests FIRST (1.5-2 hours) ⚠️ CRITICAL PHASE
3. **Phase 3:** Implement Code to Pass Tests (2-3 hours)
4. **Phase 4:** Migrate MedicalIntakeAgent (1 hour)
5. **Phase 5:** Documentation & Polish (1 hour)

**Estimated Timeline:** 5-7 hours (AI-assisted with TDD)

### Files Affected:

**New Files:**
- `src/gaia/agents/base/database_mixin.py` (~300-400 lines)
- `tests/unit/test_database_mixin_sqlalchemy.py` (~300-400 lines)

**Modified Files:**
- `setup.py` (add sqlalchemy dependency)
- `src/gaia/agents/base/__init__.py` (export DatabaseMixin)
- `src/gaia/agents/emr/agent.py` (migrate to new mixin)
- `src/gaia/agents/emr/cli.py` (update if needed)

**Total Lines of Code:** ~800-1200

### Key Success Factors:

1. **Tests written FIRST** - No implementation before tests
2. **Test verification** - Ensure tests actually test the right behavior
3. **100% test pass rate** - All tests must pass before completion
4. **Successful migration** - EMR agent must work with new mixin
5. **No regressions** - Existing tests must still pass

The implementation follows GAIA's architecture patterns and maintains consistency with existing database tooling while providing enterprise-grade features for production deployments of the Medical Intake PoC.
