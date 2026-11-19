/**
 * Pulls public data once a day (or on demand) and stores one row per metric
 * per day in D1. A failing source is recorded in `collect_log` and re-thrown so
 * the nightly run reports it; nothing is filled in with a guessed value.
 */

import { CHANNELS, CODE_STATS, EVAL_ARTIFACT_DAYS, EVAL_WORKFLOWS, NETWORK_RETRIES, PACKAGES, PROJECT, STAR_HISTORY, TESTS, TRAFFIC } from "./config";
import { collectChannels } from "./channels";
import { collectEvals } from "./evals";
import { collectTraffic } from "./traffic";
import { collectTests } from "./tests";
import { collectCodeStats, collectCommits, collectForks, collectItems, collectPackageHistory, collectStars } from "./history";

export interface CollectEnv {
  DB: D1Database;
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
  /** `.github/workflows/x.yml`, or `dynamic/...` for GitHub's own workflows. */
  path?: string;
}

/** Dynamic workflows (CodeQL default setup, Dependabot) name each run after its PR or package; group them. */
export function workflowName(r: { name: string | null; event?: string; path?: string }): string {
  const name = r.name ?? "(unnamed)";
  if (r.event !== "dynamic") return name;
  if (r.path === "dynamic/github-code-scanning/codeql") return "CodeQL (default setup)";
  if (name.startsWith("Graph Update:")) return "Dependabot graph update";
  if (r.path?.startsWith("dynamic/dependabot/")) return "Dependabot updates";
  return name;
}

const MAX_PAGES = 60;

export const utcDay = (d: Date = new Date()): string => d.toISOString().slice(0, 10);

export function ghHeaders(env: CollectEnv): HeadersInit {
  const h: Record<string, string> = {
    Accept: "application/vnd.github+json",
    "User-Agent": `${PROJECT.repo.replace("/", "-")}-dashboard-collector`,
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
          ? "This is usually the GitHub rate limit: set the GITHUB_TOKEN secret (see README.md)."
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
  const enc = new TextEncoder();
  for (const r of rows) {
    const j = JSON.stringify(r);
    const bytes = enc.encode(j).length;
    if (size + bytes + 1 > MAX_JSON_BYTES) flush();
    chunk.push(j);
    size += bytes + 1;
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
    const url = `https://api.github.com/repos/${PROJECT.repo}/releases?per_page=100&page=${page}`;
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
    `https://api.github.com/repos/${PROJECT.repo}`,
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
    const url = `https://api.github.com/repos/${PROJECT.repo}/actions/runs?per_page=100&page=${page}&created=${created}`;
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
  const failures: string[] = [];
  for (let i = skipDays; i < skipDays + days; i++) {
    const start = dayStart - i * 86_400_000;
    try {
      const runs = await fetchRunWindow(env, f, start, start + 86_399_000);
      const rows = runs.map((r) => {
        const begun = Date.parse(r.run_started_at ?? r.created_at);
        const end = Date.parse(r.updated_at);
        const done = r.status === "completed" && Number.isFinite(begun) && Number.isFinite(end);
        return [r.id, workflowName(r), r.head_branch, r.event, r.status, r.conclusion, r.head_sha, r.run_attempt, r.created_at,
          done ? Math.max(0, Math.round((end - begun) / 1000)) : null];
      });
      await writeAtomic(env.DB, insertMany(env.DB, "workflow_runs",
        ["id", "workflow", "branch", "event", "status", "conclusion", "head_sha", "run_attempt", "created_at", "duration_s"], rows));
      total += runs.length;
    } catch (e) {
      // One unreadable day must not discard the days already read; each day is stored on its own.
      failures.push(`${utcDay(new Date(start))}: ${(e as Error).message}`);
    }
  }
  const summary = `${total} runs over ${days - failures.length} day(s)`;
  if (failures.length) throw new Error(`${summary}; ${failures.length} day(s) could not be read: ${failures.join(" | ")}`);
  return summary;
}

/** A collection in which some sources failed; every other source was collected and stored. */
export class CollectError extends Error {
  constructor(readonly failed: string[], readonly attempted: string[], details: string[]) {
    super(details.join("\n"));
    this.name = "CollectError";
  }
}

const RETRY_STATUS = new Set([502, 503, 504]);
const NULL_BODY_STATUS = new Set([204, 205, 304]);
const defaultPause = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** The response with its body already read into memory: same status and headers, so callers cannot tell. */
async function buffered(res: Response): Promise<Response> {
  // Outside 200-599 (an opaque redirect reports 0) a Response cannot be rebuilt, and there is no body to read.
  if (res.status < 200 || res.status > 599) return res;
  const body = await res.arrayBuffer();
  return new Response(NULL_BODY_STATUS.has(res.status) ? null : body, { status: res.status, statusText: res.statusText, headers: res.headers });
}

/**
 * Wraps a fetcher so a GET that fails with a dropped connection or a gateway
 * error (502/503/504) is retried `retries` times (NETWORK_RETRIES in collectAll),
 * with a pause that grows by a second each time. The final failure names the request.
 * A GET's body is read inside the retry loop, so a connection dropped mid-body
 * is retried too; a redirect (redirect: "manual") comes back as it was sent.
 */
export function withRetries(raw: Fetcher, retries: number, pause: (ms: number) => Promise<void> = defaultPause): Fetcher {
  return async (u, i) => {
    const idempotent = !i?.method || i.method.toUpperCase() === "GET";
    for (let attempt = 0; ; attempt++) {
      const last = !idempotent || attempt >= retries;
      try {
        const res = await raw(u, i);
        if (!last && RETRY_STATUS.has(res.status)) {
          await res.body?.cancel();
          await pause(1000 * (attempt + 1));
          continue;
        }
        return idempotent ? await buffered(res) : res;
      } catch (e) {
        if (!last) {
          await pause(1000 * (attempt + 1));
          continue;
        }
        // A dropped connection says only "Network connection lost."; name the request so the log says what failed.
        const tries = attempt ? ` Tried ${attempt + 1} times.` : "";
        throw new Error(`GET ${u.split("?")[0]} failed: ${(e as Error).message}${tries} Re-run this source with ?only=<source>; the next night also catches up.`, { cause: e });
      }
    }
  };
}

/** Every source this project collects; registries, tests and evals drop out when config.ts leaves them empty. */
export const SOURCES: string[] = [
  "releases", "repo", ...(STAR_HISTORY ? ["stars"] : []), "forks", "issues", "commits", ...(CODE_STATS ? ["code-stats"] : []),
  ...(PACKAGES.npm.length + PACKAGES.pypi.length + PACKAGES.crates.length ? ["package-history"] : []),
  ...(TRAFFIC ? ["traffic"] : []),
  ...(Object.values(CHANNELS).some((list) => list.length) ? ["channels"] : []),
  "runs",
  ...(TESTS.workflow ? ["tests"] : []),
  ...(EVAL_WORKFLOWS.length ? ["evals"] : []),
];

/** A source's name now, for collect_log rows written under an older name (which had spaces, so did not work in ?only=). */
const RENAMED: Record<string, string> = { "code stats": "code-stats", "package history": "package-history" };
export const currentSourceName = (name: string): string => RENAMED[name] ?? name;

/** Names in `only` that are not a known source; the caller should reject them before collecting. */
export function unknownSources(only: string[] | undefined): string[] {
  return (only ?? []).filter((o) => !SOURCES.includes(o));
}

export async function collectAll(
  env: CollectEnv,
  opts: { runDays: number; skipDays?: number; fullHistory?: boolean; only?: string[]; fetcher?: Fetcher; now?: Date; pause?: (ms: number) => Promise<void> } = { runDays: 3 },
): Promise<void> {
  const raw: Fetcher = opts.fetcher ?? ((u, i) => fetch(u, i));
  const f = withRetries(raw, NETWORK_RETRIES, opts.pause);
  const now = opts.now ?? new Date();
  const day = utcDay(now);
  const errors: string[] = [];
  const failed: string[] = [];
  const attempted: string[] = [];
  const steps: [string, () => Promise<string>][] = [
    ["releases", () => collectReleases(env, f, day)],
    ["repo", () => collectRepo(env, f, day)],
    ["stars", () => collectStars(env, f)],
    ["forks", () => collectForks(env, f)],
    ["issues", async () => collectItems(env, f, opts.fullHistory ? undefined : await sinceLastSuccess(env.DB, "issues"))],
    // `since` filters by commit date, so commits merged later with an older date are missed: re-read all weekly.
    ["commits", async () => collectCommits(env, f, opts.fullHistory || now.getUTCDay() === 0 ? undefined : await sinceLastSuccess(env.DB, "commits"))],
    ["code-stats", () => collectCodeStats(env, f)],
    ["package-history", () => collectPackageHistory(env, f, now)],
    ["traffic", () => collectTraffic(env, f, day)],
    ["channels", () => collectChannels(env, f, day)],
    ["runs", () => collectRuns(env, f, opts.runDays, now, opts.skipDays ?? 0)],
    ["tests", async () => {
      // Test counts are read from the runs table; with runs missing, "0 days" would read as success.
      if (failed.includes("runs")) throw new Error("not read: the runs source failed in this collection, and test counts come from its runs.");
      return collectTests(env, f, opts.runDays, now, opts.skipDays ?? 0, opts.fullHistory ?? false);
    }],
    // Eval artifacts expire after 14 days; reading the last 14 every night keeps a missed night recoverable.
    ["evals", () => collectEvals(env, f, EVAL_ARTIFACT_DAYS, now, opts.fullHistory ?? false)],
  ];
  // Independent sources: one failing must not stop the others from recording.
  const unknown = unknownSources(opts.only);
  if (unknown.length) throw new Error(`Unknown source(s): ${unknown.join(", ")}. Known: ${SOURCES.join(", ")}.`);
  for (const [name, fn] of steps) {
    if (!SOURCES.includes(name) || (opts.only && !opts.only.includes(name))) continue;
    attempted.push(name);
    try {
      await step(env.DB, name, fn);
    } catch (e) {
      failed.push(name);
      errors.push((e as Error).message);
    }
  }
  if (errors.length) throw new CollectError(failed, attempted, errors);
}
