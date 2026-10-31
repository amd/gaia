// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The core team, as saved from the dashboard, or MAINTAINER_LOGINS until someone saves one. */

import { parseMaintainerGroups } from "./page";

export interface CoreTeam {
  /** One list per person: their GitHub accounts. */
  people: string[][];
  /** "dashboard" once saved there; "deploy" while it still comes from MAINTAINER_LOGINS. */
  source: "dashboard" | "deploy";
  updatedAt: string | null;
  updatedBy: string | null;
}

export const formatPeople = (people: string[][]): string => people.map((g) => g.join("+")).join(",");

export async function coreTeam(db: D1Database, deployDefault: string | undefined): Promise<CoreTeam> {
  const row = await db
    .prepare("SELECT value, updated_at, updated_by FROM settings WHERE key = 'maintainers'")
    .first<{ value: string; updated_at: string; updated_by: string }>();
  if (row) return { people: parseMaintainerGroups(row.value), source: "dashboard", updatedAt: row.updated_at, updatedBy: row.updated_by };
  return { people: parseMaintainerGroups(deployDefault), source: "deploy", updatedAt: null, updatedBy: null };
}

/** Validates a core team typed in the dashboard; throws naming any entry that is not a login. */
export const parseCoreTeam = (raw: string): string[][] => parseMaintainerGroups(raw, "The core team");

/** Stores a validated core team. */
export async function saveCoreTeam(db: D1Database, people: string[][], by: string, now = new Date()): Promise<void> {
  await db
    .prepare(
      "INSERT INTO settings (key, value, updated_at, updated_by) VALUES ('maintainers', ?, ?, ?) " +
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
    )
    .bind(formatPeople(people), now.toISOString(), by)
    .run();
}
