// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Pulls public data once a day (or on demand) and stores one row per metric
 * per day in D1. A failing source is recorded in `collect_log` and re-thrown so
 * the nightly run reports it; nothing is filled in with a guessed value.
 */

import { collectEvals } from "./evals";
import { collectTests } from "./tests";
import { collectCodeStats, collectCommits, collectForks, collectItems, collectPackageHistory, collectStars } from "./history";

export interface CollectEnv {
  DB: D1Database;
  GITHUB_REPO: string;
  NPM_PACKAGE: string;
  PYPI_PACKAGE: string;
  GITHUB_TOKEN?: string;
}

export type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;

interface GhAsset {
  name: string;
  download_count: number;
}
interface GhRelease {
  tag_name: string;
  draft: boolean;
  prerelease: boolean;
  published_at: string | null;
  html_url: string;
  assets: GhAsset[];
}
interface GhRun {
  id: number;
  name: string | null;
  head_branch: string | null;
  event: string;
  status: string;
  conclusion: string | null;
  head_sha: string;
  run_attempt: number;
  created_at: string;
  run_started_at: string | null;
  updated_at: string;
}

const MAX_PAGES = 60;

export const utcDay = (d: Date = new Date()): string => d.toISOString().slice(0, 10);

export function ghHeaders(env: CollectEnv): HeadersInit {
  const h: Record<string, string> = {
    Accept: "application/vnd.github+json",
    "User-Agent": "gaia-admin-collector",
  };
  if (env.GITHUB_TOKEN) h.Authorization = `Bearer ${env.GITHUB_TOKEN}`;
  return h;
}

/**
 * GitHub serves job logs and artifact zips through a redirect to signed
 * storage. The token must not follow it (storage rejects it with 401), so the
 * redirect is taken by hand without credentials.
 */
export async function githubDownload(env: CollectEnv, f: Fetcher, url: string): Promise<Response> {
  let res = await f(url, { headers: ghHeaders(env), redirect: "manual" });
  const location = res.headers.get("location");
  if (res.status >= 300 && res.status < 400 && location) res = await f(location);
  return res;
}

export async function getJson<T>(f: Fetcher, url: string, init?: RequestInit): Promise<T> {
  const res = await f(url, init);
  if (!res.ok) {
    throw new Error(
      `GET ${url} returned HTTP ${res.status}. ` +
        (res.status === 403 || res.status === 429
          ? "This is usually the GitHub rate limit: set the GITHUB_TOKEN secret (see workers/admin/README.md)."
          : "Check the URL and the token's permissions."),
    );
  }
  return (await res.json()) as T;
}

// One JSON value per statement, kept well under D1's 2 MB limit for a bound value.
const MAX_JSON_BYTES = 1_000_000;

/**
 * Bulk INSERT: rows are bound as one JSON array and expanded with json_each, so
 * thousands of rows cost one statement. That keeps a nightly run to a few dozen
 * statements, far below D1's per-invocation query limit.
 */
export function insertMany(db: D1Database, table: string, cols: string[], rows: unknown[][], verb = "INSERT OR REPLACE"): D1PreparedStatement[] {
  const select = cols.map((_, i) => `json_extract(value, '$[${i}]')`).join(", ");
  const sql = `${verb} INTO ${table} (${cols.join(", ")}) SELECT ${select} FROM json_each(?)`;
  const out: D1PreparedStatement[] = [];
  let chunk: string[] = [];
  let size = 2;
  const flush = () => {
    if (chunk.length) out.push(db.prepare(sql).bind(`[${chunk.join(",")}]`));
    chunk = [];
    size = 2;
  };
  for (const r of rows) {
    const j = JSON.stringify(r);
    if (size + j.length + 1 > MAX_JSON_BYTES) flush();
    chunk.push(j);
    size += j.length + 1;
  }
  flush();
  return out;
}

/** All statements in one D1 batch, which runs as a single transaction: all or nothing. */
export async function writeAtomic(db: D1Database, stmts: D1PreparedStatement[]): Promise<void> {
  if (stmts.length) await db.batch(stmts);
}

/**
 * Start of the incremental window for a source: one day before its last
 * successful collection, so a run of missed nights is caught up instead of
 * leaving changes from the gap unseen. Undefined means read the full history.
 */
export async function sinceLastSuccess(db: D1Database, source: string): Promise<string | undefined> {
  const r = await db
    .prepare("SELECT MAX(finished_at) AS at FROM collect_log WHERE source = ? AND ok = 1")
    .bind(source)
    .first<{ at: string | null }>();
  return r?.at ? utcDay(new Date(Date.parse(r.at) - 86_400_000)) : undefined;
}

async function log(db: D1Database, source: string, ok: boolean, detail: string): Promise<void> {
  await db
    .prepare("INSERT INTO collect_log (finished_at, source, ok, detail) VALUES (?, ?, ?, ?)")
    .bind(new Date().toISOString(), source, ok ? 1 : 0, detail)
    .run();
}

async function step(db: D1Database, source: string, fn: () => Promise<string>): Promise<void> {
  try {
    await log(db, source, true, await fn());
  } catch (e) {
    await log(db, source, false, (e as Error).message);
    throw new Error(`[collector:${source}] ${(e as Error).message}`, { cause: e });
  }
}

export async function collectReleases(env: CollectEnv, f: Fetcher, day: string): Promise<string> {
  const all: GhRelease[] = [];
  for (let page = 1; page <= MAX_PAGES; page++) {
    const url = `https://api.github.com/repos/${env.GITHUB_REPO}/releases?per_page=100&page=${page}`;
    const batch = await getJson<GhRelease[]>(f, url, { headers: ghHeaders(env) });
    if (!Array.isArray(batch)) throw new Error(`Response from ${url} is not an array of releases.`);
    all.push(...batch);
    if (batch.length < 100) break;
    if (page === MAX_PAGES) throw new Error(`Hit the ${MAX_PAGES}-page guard listing releases.`);
  }
  const live = all.filter((r) => !r.draft && r.published_at);
  const assets = live.flatMap((r) => r.assets.map((a) => [day, r.tag_name, a.name, a.download_count]));
  // One transaction: a half-written day would show up as a false spike the next day.
  await writeAtomic(env.DB, [
    env.DB.prepare("DELETE FROM asset_snapshots WHERE day = ?").bind(day),
    ...insertMany(env.DB, "releases", ["tag", "published_at", "prerelease", "html_url"], live.map((r) => [r.tag_name, r.published_at, r.prerelease ? 1 : 0, r.html_url])),
    ...insertMany(env.DB, "asset_snapshots", ["day", "tag", "asset", "downloads"], assets),
  ]);
  return `${live.length} releases, ${assets.length} assets`;
}

export async function collectRepo(env: CollectEnv, f: Fetcher, day: string): Promise<string> {
  const r = await getJson<{ stargazers_count: number; forks_count: number; open_issues_count: number }>(
    f,
    `https://api.github.com/repos/${env.GITHUB_REPO}`,
    { headers: ghHeaders(env) },
  );
  await env.DB.prepare(
    "INSERT OR REPLACE INTO repo_snapshots (day, stars, forks, open_issues) VALUES (?, ?, ?, ?)",
  )
    .bind(day, r.stargazers_count, r.forks_count, r.open_issues_count)
    .run();
  return `${r.stargazers_count} stars, ${r.forks_count} forks`;
}

const MIN_WINDOW_MS = 60_000;
const isoSec = (ms: number): string => new Date(ms).toISOString().slice(0, 19) + "Z";

/**
 * Runs created in [startMs, endMs]. The Actions API returns at most 1,000 runs
 * per query, so a window that reports more is split in half and each half is
 * fetched on its own; a window that cannot be split further is an error rather
 * than a silently truncated day.
 */
async function fetchRunWindow(env: CollectEnv, f: Fetcher, startMs: number, endMs: number): Promise<GhRun[]> {
  const created = encodeURIComponent(`${isoSec(startMs)}..${isoSec(endMs)}`);
  const runs: GhRun[] = [];
  for (let page = 1; page <= 10; page++) {
    const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/runs?per_page=100&page=${page}&created=${created}`;
    const res = await getJson<{ total_count: number; workflow_runs: GhRun[] }>(f, url, { headers: ghHeaders(env) });
    if (page === 1 && res.total_count > 1000) {
      if (endMs - startMs <= MIN_WINDOW_MS) {
        throw new Error(
          `More than 1,000 workflow runs between ${isoSec(startMs)} and ${isoSec(endMs)}; the Actions API would truncate.`,
        );
      }
      const mid = Math.floor((startMs + endMs) / 2);
      return [...(await fetchRunWindow(env, f, startMs, mid)), ...(await fetchRunWindow(env, f, mid + 1000, endMs))];
    }
    runs.push(...res.workflow_runs);
    if (res.workflow_runs.length < 100) break;
  }
  return runs;
}

export async function collectRuns(
  env: CollectEnv,
  f: Fetcher,
  days: number,
  today: Date,
  skipDays = 0,
): Promise<string> {
  const dayStart = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  let total = 0;
  for (let i = skipDays; i < skipDays + days; i++) {
    const start = dayStart - i * 86_400_000;
    const runs = await fetchRunWindow(env, f, start, start + 86_399_000);
    const rows = runs.map((r) => {
      const begun = Date.parse(r.run_started_at ?? r.created_at);
      const end = Date.parse(r.updated_at);
      const done = r.status === "completed" && Number.isFinite(begun) && Number.isFinite(end);
      return [r.id, r.name ?? "(unnamed)", r.head_branch, r.event, r.status, r.conclusion, r.head_sha, r.run_attempt, r.created_at,
        done ? Math.max(0, Math.round((end - begun) / 1000)) : null];
    });
    await writeAtomic(env.DB, insertMany(env.DB, "workflow_runs",
      ["id", "workflow", "branch", "event", "status", "conclusion", "head_sha", "run_attempt", "created_at", "duration_s"], rows));
    total += runs.length;
  }
  return `${total} runs over ${days} day(s)`;
}

export async function collectAll(
  env: CollectEnv,
  opts: { runDays: number; skipDays?: number; fullHistory?: boolean; only?: string[]; fetcher?: Fetcher; now?: Date } = { runDays: 3 },
): Promise<void> {
  const f = opts.fetcher ?? ((u, i) => fetch(u, i));
  const now = opts.now ?? new Date();
  const day = utcDay(now);
  const errors: string[] = [];
  const steps: [string, () => Promise<string>][] = [
    ["releases", () => collectReleases(env, f, day)],
    ["repo", () => collectRepo(env, f, day)],
    ["stars", () => collectStars(env, f)],
    ["forks", () => collectForks(env, f)],
    ["issues", async () => collectItems(env, f, opts.fullHistory ? undefined : await sinceLastSuccess(env.DB, "issues"))],
    ["commits", async () => collectCommits(env, f, opts.fullHistory ? undefined : await sinceLastSuccess(env.DB, "commits"))],
    ["code stats", () => collectCodeStats(env, f)],
    ["package history", () => collectPackageHistory(env, f, now)],
    ["runs", () => collectRuns(env, f, opts.runDays, now, opts.skipDays ?? 0)],
    ["tests", () => collectTests(env, f, opts.runDays, now, opts.skipDays ?? 0, opts.fullHistory ?? false)],
    // Eval artifacts expire after 14 days; reading the last 14 every night keeps a missed night recoverable.
    ["evals", () => collectEvals(env, f, Math.max(opts.runDays, 14), now, opts.fullHistory ?? false)],
  ];
  // Independent sources: one failing must not stop the others from recording.
  const unknown = (opts.only ?? []).filter((o) => !steps.some(([n]) => n === o));
  if (unknown.length) throw new Error(`Unknown source(s): ${unknown.join(", ")}. Known: ${steps.map(([n]) => n).join(", ")}.`);
  for (const [name, fn] of steps) {
    if (opts.only && !opts.only.includes(name)) continue;
    try {
      await step(env.DB, name, fn);
    } catch (e) {
      errors.push((e as Error).message);
    }
  }
  if (errors.length) throw new Error(errors.join("\n"));
}
