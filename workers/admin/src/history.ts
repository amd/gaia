// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Collectors for the traction and development-momentum history: stars, forks,
 * issues and pull requests, commits, and registry download history. Split from
 * collector.ts so each file stays readable; they share its fetch helpers.
 */

import { getJson, ghHeaders, insertMany, utcDay, writeAtomic, type CollectEnv, type Fetcher } from "./collector";

/** Counts ISO timestamps per UTC day, ordered by day. */
export function countByDay(timestamps: string[]): [string, number][] {
  const m = new Map<string, number>();
  for (const t of timestamps) m.set(t.slice(0, 10), (m.get(t.slice(0, 10)) ?? 0) + 1);
  return [...m.entries()].sort(([a], [b]) => a.localeCompare(b));
}

async function pagedList<T>(f: Fetcher, env: CollectEnv, base: string, accept: string, maxPages: number): Promise<T[]> {
  const out: T[] = [];
  for (let page = 1; page <= maxPages; page++) {
    const url = `${base}${base.includes("?") ? "&" : "?"}per_page=100&page=${page}`;
    const headers = { ...(ghHeaders(env) as Record<string, string>), Accept: accept };
    const batch = await getJson<T[]>(f, url, { headers });
    if (!Array.isArray(batch)) throw new Error(`Response from ${url} is not a list.`);
    out.push(...batch);
    if (batch.length < 100) return out;
  }
  throw new Error(`Hit the ${maxPages}-page guard fetching ${base}; raise it deliberately if the repo really is this large.`);
}

/** Replace a per-day table in one transaction, so readers never see a partial history. */
async function replaceDaily(db: D1Database, table: "star_daily" | "fork_daily", rows: [string, number][]): Promise<void> {
  await writeAtomic(db, [db.prepare(`DELETE FROM ${table}`), ...insertMany(db, table, ["day", "n"], rows, "INSERT")]);
}

export async function collectStars(env: CollectEnv, f: Fetcher): Promise<string> {
  const list = await pagedList<{ starred_at: string }>(
    f,
    env,
    `https://api.github.com/repos/${env.GITHUB_REPO}/stargazers`,
    "application/vnd.github.star+json",
    400,
  );
  await replaceDaily(env.DB, "star_daily", countByDay(list.map((x) => x.starred_at)));
  return `${list.length} stars with dates`;
}

export async function collectForks(env: CollectEnv, f: Fetcher): Promise<string> {
  const list = await pagedList<{ created_at: string }>(
    f,
    env,
    `https://api.github.com/repos/${env.GITHUB_REPO}/forks?sort=oldest`,
    "application/vnd.github+json",
    200,
  );
  await replaceDaily(env.DB, "fork_daily", countByDay(list.map((x) => x.created_at)));
  return `${list.length} forks with dates`;
}

interface GhItem {
  number: number;
  user: { login: string } | null;
  created_at: string;
  closed_at: string | null;
  author_association: string;
  pull_request?: { merged_at: string | null };
}

/** Issues and PRs. `sinceDay` limits the nightly run to recently updated items; omit it for the full history. */
export async function collectItems(env: CollectEnv, f: Fetcher, sinceDay?: string): Promise<string> {
  const since = sinceDay ? `&since=${sinceDay}T00:00:00Z` : "";
  const list = await pagedList<GhItem>(
    f,
    env,
    `https://api.github.com/repos/${env.GITHUB_REPO}/issues?state=all&sort=updated&direction=desc${since}`,
    "application/vnd.github+json",
    400,
  );
  await writeAtomic(
    env.DB,
    insertMany(env.DB, "items", ["number", "kind", "created_at", "closed_at", "merged_at", "assoc", "author"],
      list.map((i) => [i.number, i.pull_request ? "pr" : "issue", i.created_at, i.closed_at, i.pull_request?.merged_at ?? null, i.author_association, i.user?.login ?? null])),
  );
  return `${list.length} issues and PRs${sinceDay ? ` updated since ${sinceDay}` : " (full history)"}`;
}

interface GhCommit {
  sha: string;
  commit: { author: { date: string; email?: string } | null };
  author: { login: string } | null;
}

/** Commits on the default branch. `sinceDay` limits the nightly run; omit it for the full history. */
export async function collectCommits(env: CollectEnv, f: Fetcher, sinceDay?: string): Promise<string> {
  const since = sinceDay ? `?since=${sinceDay}T00:00:00Z` : "";
  const list = await pagedList<GhCommit>(
    f,
    env,
    `https://api.github.com/repos/${env.GITHUB_REPO}/commits${since}`,
    "application/vnd.github+json",
    400,
  );
  const rows = list.filter((c) => c.commit.author?.date);
  await writeAtomic(
    env.DB,
    insertMany(env.DB, "commits", ["sha", "day", "author", "at"],
      rows.map((c) => [c.sha, c.commit.author!.date.slice(0, 10), // Commits with no linked GitHub account are grouped together rather than keyed by email.
        c.author?.login ?? "(no GitHub account)", c.commit.author!.date])),
  );
  return `${rows.length} commits${sinceDay ? ` since ${sinceDay}` : " (full history)"}`;
}

export async function collectPackageHistory(env: CollectEnv, f: Fetcher, today: Date): Promise<string> {
  const end = utcDay(today);
  const start = utcDay(new Date(today.getTime() - 360 * 86_400_000));
  const npm = await getJson<{ downloads: { day: string; downloads: number }[] }>(
    f,
    `https://api.npmjs.org/downloads/range/${start}:${end}/${env.NPM_PACKAGE}`,
  );
  const pypi = await getJson<{ data: { category: string; date: string; downloads: number }[] }>(
    f,
    `https://pypistats.org/api/packages/${env.PYPI_PACKAGE}/overall?mirrors=false`,
  );
  // npm reports 0 for days it has not computed yet; the last two days are left out rather than stored as zero.
  const settled = utcDay(new Date(today.getTime() - 2 * 86_400_000));
  const rows = [
    ...npm.downloads.filter((d) => d.day <= settled).map((d) => ["npm", env.NPM_PACKAGE, d.day, d.downloads]),
    ...pypi.data.filter((d) => d.category === "without_mirrors").map((d) => ["pypi", env.PYPI_PACKAGE, d.date, d.downloads]),
  ];
  await writeAtomic(env.DB, insertMany(env.DB, "package_daily", ["source", "package", "day", "downloads"], rows));
  return `${rows.length} daily package rows`;
}

/**
 * GitHub computes repository statistics in the background and answers 202
 * until they are ready. That is reported as a failure of this source (so it is
 * visible and retried on the next run), never stored as an empty history.
 */
async function githubStats<T>(env: CollectEnv, f: Fetcher, path: string): Promise<T> {
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/stats/${path}`;
  const res = await f(url, { headers: ghHeaders(env) });
  if (res.status === 202) throw new Error(`GitHub is still computing ${path} statistics; they will be read on the next run.`);
  if (!res.ok) throw new Error(`GET ${url} returned HTTP ${res.status}.`);
  return (await res.json()) as T;
}

// GitHub's statistics weeks start on Sunday; stored as the following Monday so they line up with the dashboard's Monday weeks.
const statWeek = (unixSeconds: number): string => utcDay(new Date((unixSeconds + 86_400) * 1000));

/** Weekly lines added and deleted on the default branch, and the same per contributor. */
export async function collectCodeStats(env: CollectEnv, f: Fetcher): Promise<string> {
  const freq = await githubStats<[number, number, number][]>(env, f, "code_frequency");
  const people = await githubStats<{ author: { login: string } | null; weeks: { w: number; a: number; d: number; c: number }[] }[]>(env, f, "contributors");
  if (!Array.isArray(freq) || !Array.isArray(people)) throw new Error("GitHub statistics response was not a list.");
  // Summed per (person, week): GitHub lists every deleted account separately, all as "(deleted account)".
  const sums = new Map<string, [string, string, number, number, number]>();
  for (const p of people) {
    const who = p.author?.login ?? "(deleted account)";
    for (const w of p.weeks) {
      if (!(w.a || w.d || w.c)) continue;
      const k = `${who}|${statWeek(w.w)}`;
      const cur = sums.get(k) ?? [who, statWeek(w.w), 0, 0, 0];
      cur[2] += w.c;
      cur[3] += w.a;
      cur[4] += w.d;
      sums.set(k, cur);
    }
  }
  const perPerson = [...sums.values()];
  await writeAtomic(env.DB, [
    env.DB.prepare("DELETE FROM loc_weekly"),
    env.DB.prepare("DELETE FROM contributor_weekly"),
    ...insertMany(env.DB, "loc_weekly", ["week", "additions", "deletions"], freq.map(([w, a, d]) => [statWeek(w), a, Math.abs(d)]), "INSERT"),
    ...insertMany(env.DB, "contributor_weekly", ["author", "week", "commits", "additions", "deletions"], perPerson, "INSERT OR REPLACE"),
  ]);
  return `${freq.length} weeks of line counts, ${people.length} contributors`;
}
