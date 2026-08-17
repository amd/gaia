# Vendored SQLite amalgamation

<!-- Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved. -->
<!-- SPDX-License-Identifier: MIT -->

`sqlite3.c` and `sqlite3.h` are the unmodified [SQLite](https://sqlite.org)
amalgamation, compiled directly into `gaia_core`. The exact version, upstream
URL, and upstream checksums are recorded in [`SQLITE_VERSION.txt`](SQLITE_VERSION.txt) so the SBOM
story stays honest.

> The version file is `SQLITE_VERSION.txt`, not `VERSION`. This directory is on
> the compiler's include path, and on a case-insensitive filesystem (macOS,
> Windows) a file named `VERSION` gets picked up as the C++ standard library's
> `<version>` header, breaking every translation unit in `gaia_core`. Do not add
> extension-less files here whose name collides with a standard header.

**These files are third-party sources. Do not edit, reformat, or lint them.**
`.gitattributes` marks them `linguist-vendored` and excludes them from diffs;
the CMake build compiles them with warnings disabled.

## Why vendored, and why no `find_package` fallback

A single amalgamation compiled into the library means all three platform builds
(Windows, Linux, macOS) get byte-identical SQLite semantics with the same
compile-time feature set. A `find_package` fallback would silently pick up a
distro SQLite built without FTS5, or with different defaults — producing a
"works on Linux, fails on Windows" class of bug that only shows up at query
time. There is deliberately no such path. This is how most C++ projects ship
SQLite.

## Compile-time configuration

Set in `cpp/CMakeLists.txt` on the `gaia_sqlite3` object library. The full list
lives there; the ones that matter for correctness:

| Flag | Why |
|------|-----|
| `SQLITE_ENABLE_FTS5` | Full-text search. `gaia::Database` consumers (agent memory) need it. A missing flag fails at *query* time, not build time — `cpp/tests/test_database.cpp` has an FTS5 smoke test that catches it. |
| `SQLITE_THREADSAFE=1` | Serialized mode. `Database` objects may be used from multiple threads, as `MemoryStore` does in Python. |
| `SQLITE_DQS=0` | Rejects double-quoted string literals. Without it a typo'd column name silently becomes a string constant instead of an error. |
| `SQLITE_DEFAULT_FOREIGN_KEYS=1` | Foreign-key constraints enforced by default; `Database::Options::foreignKeys` can still turn them off explicitly. |
| `SQLITE_TRUSTED_SCHEMA=0` | Views, triggers, and indexes can't invoke functions not marked `SQLITE_INNOCUOUS`. |
| `SQLITE_LIKE_DOESNT_MATCH_BLOBS` | `LIKE` against a BLOB is false rather than a silent coercion. |
| `SQLITE_DEFAULT_WAL_SYNCHRONOUS=1` | The standard WAL durability setting: commits sync the WAL, not the whole database. |
| `SQLITE_DEFAULT_MEMSTATUS=0` | Drops global allocation counters nothing reads. |
| `SQLITE_MAX_EXPR_DEPTH=0` | Expression depth is bounded by `SQLITE_MAX_SQL_LENGTH` instead of a separate, lower limit that rejects legitimate generated SQL. |

Two things are deliberately **not** compile flags:

- **No `SQLITE_OMIT_*`.** Upstream documents those as unsupported against the
  amalgamation — they need a build from canonical sources.
- **Extension loading** is disabled per-connection with
  `sqlite3_db_config(SQLITE_DBCONFIG_ENABLE_LOAD_EXTENSION, 0)` in
  `src/database.cpp`, alongside `SQLITE_DBCONFIG_DEFENSIVE`. A runtime call is a
  stronger guarantee than a compile flag here, because it also covers a caller
  who tries to turn it back on.

## Upgrading

1. Download the amalgamation zip from <https://sqlite.org/download.html>.
2. Verify the SHA3-256 against the `PRODUCT,` line on that page
   (`python3 -c "import hashlib,sys;print(hashlib.sha3_256(open(sys.argv[1],'rb').read()).hexdigest())" sqlite.zip`).
   Note it is **SHA3-256**, not SHA-256 — checking the wrong algorithm will
   look like a tampered download.
3. Replace `sqlite3.c` and `sqlite3.h` with the new files, unmodified.
4. Update [`SQLITE_VERSION.txt`](SQLITE_VERSION.txt) with the new version, source id, URL, and both checksums.
5. Rebuild and run `ctest -R Database --output-on-failure`.
