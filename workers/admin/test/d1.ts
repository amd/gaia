// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A D1Database backed by Node's built-in SQLite, with every migration applied,
 * so tests exercise the real SQL (json_each bulk inserts, window queries)
 * rather than a stub that only records calls.
 */

import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

// Loaded at runtime: the bundler does not yet know node:sqlite as a built-in.
const { DatabaseSync } = createRequire(import.meta.url)("node:sqlite") as typeof import("node:sqlite");

type Value = string | number | null | bigint | Uint8Array;

export function memoryD1(): D1Database {
  const sqlite = new DatabaseSync(":memory:");
  const dir = join(dirname(fileURLToPath(import.meta.url)), "..", "migrations");
  for (const f of readdirSync(dir).filter((x) => x.endsWith(".sql")).sort()) sqlite.exec(readFileSync(join(dir, f), "utf8"));

  const stmt = (sql: string, args: Value[] = []) => ({
    sql,
    args,
    bind: (...a: unknown[]) => stmt(sql, a as Value[]),
    all: async <T>() => ({ results: sqlite.prepare(sql).all(...args) as T[], success: true, meta: {} }),
    first: async <T>() => (sqlite.prepare(sql).get(...args) as T | undefined) ?? null,
    run: async () => {
      sqlite.prepare(sql).run(...args);
      return { success: true, meta: {} };
    },
  });
  return {
    prepare: (sql: string) => stmt(sql),
    batch: async (list: { sql: string; args: Value[] }[]) => {
      sqlite.exec("BEGIN");
      try {
        for (const s of list) sqlite.prepare(s.sql).run(...s.args);
        sqlite.exec("COMMIT");
      } catch (e) {
        sqlite.exec("ROLLBACK");
        throw e;
      }
      return [];
    },
  } as unknown as D1Database;
}
