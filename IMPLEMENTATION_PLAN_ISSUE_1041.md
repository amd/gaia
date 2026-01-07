# Implementation Plan: SQLAlchemy Database Mixin (Issue #1041)

## Executive Summary

Create a new database mixin using SQLAlchemy Core to support multiple database backends (SQLite, PostgreSQL, MySQL) with connection pooling and proper transaction management. This will enable the Medical Intake PoC and other agents to use enterprise-grade databases.

**Location:** `src/gaia/agents/base/database_mixin.py` (new file)
**Estimated Effort:** 4-6 hours (AI-assisted)
**Priority:** Critical
**Component:** Agents

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

### 6.1 Coexistence Strategy

Both mixins will coexist:

| Aspect | Existing (`gaia.database.mixin`) | New (`gaia.agents.base.database_mixin`) |
|--------|----------------------------------|----------------------------------------|
| **Location** | `src/gaia/database/mixin.py` | `src/gaia/agents/base/database_mixin.py` |
| **Technology** | `sqlite3` | SQLAlchemy Core |
| **Databases** | SQLite only | SQLite, PostgreSQL, MySQL |
| **Dependencies** | Zero (built-in) | `sqlalchemy>=2.0` |
| **Use Case** | Simple agents, prototyping | Production agents, multi-DB |

### 6.2 When to Use Which Mixin?

**Use existing sqlite3 mixin when:**
- Prototyping or simple agents
- SQLite is sufficient
- Want zero dependencies
- Need maximum simplicity

**Use new SQLAlchemy mixin when:**
- Need PostgreSQL or MySQL
- Require connection pooling
- Production deployment
- Multi-database support

### 6.3 Migrating Existing Code

**Example: Updating MedicalIntakeAgent**

```python
# Before (using sqlite3 mixin)
from gaia.database import DatabaseMixin

class MedicalIntakeAgent(Agent, DatabaseMixin, FileWatcherMixin):
    def __init__(self, db_path: str = "./data/patients.db", **kwargs):
        super().__init__(**kwargs)
        self.init_db(db_path)  # SQLite only

# After (using SQLAlchemy mixin)
from gaia.agents.base.database_mixin import DatabaseMixin

class MedicalIntakeAgent(Agent, DatabaseMixin, FileWatcherMixin):
    def __init__(self, db_url: str = "sqlite:///./data/patients.db", **kwargs):
        super().__init__(**kwargs)
        self.init_database(db_url, pool_size=5)  # Multi-DB support

    # Method name changes:
    # - query() -> execute_query()
    # - insert() -> execute_insert()
    # - update() -> execute_update()
    # - delete() -> execute_delete()
    # - execute() -> execute_raw()
    # - transaction() stays the same
```

---

## 7. Step-by-Step Implementation Plan

### Phase 1: Core Implementation (2-3 hours)

#### Step 1.1: Update Dependencies
- [ ] Modify `setup.py` to add `sqlalchemy>=2.0` to `install_requires`

#### Step 1.2: Create DatabaseMixin Class
- [ ] Create `src/gaia/agents/base/database_mixin.py`
- [ ] Add copyright header and module docstring
- [ ] Implement `__init__` to initialize instance variables
- [ ] Implement `init_database()` with engine creation and connection pooling
- [ ] Implement `close_database()` for cleanup
- [ ] Implement `get_connection()` for obtaining connections
- [ ] Implement `db_ready` property
- [ ] Add `_require_db()` internal validation method

#### Step 1.3: Implement Query Operations
- [ ] Implement `execute_query()` for SELECT operations
  - Support parameterized queries with `text()` and binding
  - Return list of dictionaries
  - Proper error handling

#### Step 1.4: Implement Insert Operations
- [ ] Implement `execute_insert()` for INSERT operations
  - Support `returning` parameter for PostgreSQL/MySQL
  - Return inserted ID or specified column value
  - Generate SQL dynamically based on data dict

#### Step 1.5: Implement Update Operations
- [ ] Implement `execute_update()` for UPDATE operations
  - Support WHERE clause with parameters
  - Return affected row count
  - Proper parameter merging (avoid collisions)

#### Step 1.6: Implement Delete Operations
- [ ] Implement `execute_delete()` for DELETE operations
  - Support WHERE clause with parameters
  - Return deleted row count

#### Step 1.7: Implement Transactions
- [ ] Implement `transaction()` context manager
  - Auto-commit on success
  - Auto-rollback on exception
  - Proper connection cleanup

#### Step 1.8: Implement Utilities
- [ ] Implement `execute_raw()` for DDL (CREATE TABLE, etc.)
- [ ] Implement `table_exists()` utility
  - Use database-specific queries for different backends

#### Step 1.9: Export the Mixin
- [ ] Update `src/gaia/agents/base/__init__.py` to export `DatabaseMixin`

### Phase 2: Testing (1-2 hours)

#### Step 2.1: Create Test File
- [ ] Create `tests/unit/test_database_mixin_sqlalchemy.py`
- [ ] Add copyright header and imports
- [ ] Create test helper class that uses the mixin

#### Step 2.2: Write Initialization Tests
- [ ] Test in-memory SQLite initialization
- [ ] Test file-based SQLite initialization
- [ ] Test custom pool size
- [ ] Test reinitialization
- [ ] Test operations before init (should raise error)
- [ ] Test close_database idempotency

#### Step 2.3: Write CRUD Tests
- [ ] Test execute_query (various scenarios)
- [ ] Test execute_insert (with and without returning)
- [ ] Test execute_update (single and multiple rows)
- [ ] Test execute_delete (single and multiple rows)
- [ ] Test execute_raw for DDL

#### Step 2.4: Write Transaction Tests
- [ ] Test transaction commit
- [ ] Test transaction rollback on exception
- [ ] Test multiple operations in single transaction

#### Step 2.5: Write Utility Tests
- [ ] Test table_exists (true and false cases)

#### Step 2.6: Write Security Tests
- [ ] Test parameterized queries prevent SQL injection
- [ ] Test special characters in data

#### Step 2.7: Run Tests
- [ ] Execute: `pytest tests/unit/test_database_mixin_sqlalchemy.py -v`
- [ ] Ensure 100% pass rate
- [ ] Check code coverage

### Phase 3: Documentation (1 hour)

#### Step 3.1: Comprehensive Docstrings
- [ ] Ensure all methods have detailed docstrings
- [ ] Add usage examples in docstrings
- [ ] Document parameters, return values, and exceptions

#### Step 3.2: Module-Level Documentation
- [ ] Add comprehensive module docstring with overview
- [ ] Include complete usage examples
- [ ] Document database URL formats
- [ ] Explain connection pooling parameters

#### Step 3.3: Optional: Update External Docs
- [ ] Update `docs/sdk/mixins/database-mixin.mdx` (if time permits)
- [ ] Add section comparing sqlite3 vs SQLAlchemy mixin
- [ ] Provide migration guide

### Phase 4: Validation & Polish (30 min - 1 hour)

#### Step 4.1: Code Review
- [ ] Review code for clarity and consistency
- [ ] Ensure proper error handling throughout
- [ ] Verify SQL injection prevention
- [ ] Check connection cleanup in all paths

#### Step 4.2: Run Full Test Suite
- [ ] Run: `pytest tests/unit/test_database_mixin_sqlalchemy.py -v`
- [ ] Run: `python util/lint.py` (if available)
- [ ] Fix any linting issues

#### Step 4.3: Manual Testing (Optional)
- [ ] Create simple test agent using the mixin
- [ ] Test with SQLite
- [ ] Test with PostgreSQL (if available)
- [ ] Verify connection pooling behavior

---

## 8. Acceptance Criteria Checklist

From the original issue:

- [x] Create `src/gaia/agents/base/database_mixin.py`
- [x] Support SQLite, PostgreSQL, MySQL via SQLAlchemy connection URLs
- [x] Implement connection pooling for concurrent requests
- [x] Provide transaction management with context managers
- [x] Include parameterized queries (SQL injection prevention)
- [x] Add methods: `init_database()`, `execute_query()`, `execute_insert()`, `execute_update()`, `transaction()`
- [x] Add unit tests for all database operations
- [x] Document usage in docstrings
- [x] Add `sqlalchemy>=2.0` to `install_requires` in `setup.py`

**Additional Items (Nice-to-Have):**
- [ ] `execute_delete()` method (not in original spec but useful)
- [ ] `table_exists()` utility (not in original spec but useful)
- [ ] `get_connection()` method (in spec)
- [ ] Integration tests for PostgreSQL/MySQL (optional)
- [ ] Documentation updates (optional)

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

This implementation plan provides a comprehensive roadmap for creating a production-ready database mixin using SQLAlchemy Core. The mixin will:

✅ Support multiple databases (SQLite, PostgreSQL, MySQL)
✅ Provide connection pooling for performance
✅ Implement robust transaction management
✅ Prevent SQL injection through parameterized queries
✅ Include comprehensive unit tests
✅ Be fully documented with examples
✅ Coexist peacefully with the existing sqlite3-based mixin

**Estimated Timeline:** 4-6 hours (AI-assisted)

**Files to Create:** 2 (mixin + tests)
**Files to Modify:** 2 (setup.py + __init__.py)
**Lines of Code:** ~800-1000 total

The implementation follows GAIA's architecture patterns and maintains consistency with existing database tooling while providing enterprise-grade features for production deployments.
