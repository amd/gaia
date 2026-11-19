/**
 * Collectors for the traction and development-momentum history: stars, forks,
 * issues and pull requests, commits, and registry download history. Split from
 * collector.ts so each file stays readable; they share its fetch helpers.
 */

import { PACKAGES, PROJECT } from "./config";
import { getJson, ghHeaders, insertMany, utcDay, writeAtomic, type CollectEnv, type Fetcher } from "./collector";

/** Counts ISO timestamps per UTC day, ordered by day. */
export function countByDay(timestamps: string[]): [string, number][] {
  const m = new Map<string, number>();
  for (const t of timestamps) m.set(t.slice(0, 10), (m.get(t.slice(0, 10)) ?? 0) + 1);
  return [...m.entries()].sort(([a], [b]) => a.localeCompare(b));
}

/** The rel="next" URL of a GitHub Link header, or null on the last page. */
export function nextLink(link: string | null): string | null {
  if (!link) return null;
  for (const part of link.split(",")) {
    const m = /<([^>]+)>\s*;\s*rel="next"/.exec(part);
    if (m) return m[1]!;
  }
  return null;
}

/**
 * Every item of a GitHub list, following the Link header's next URL. Large
 * repositories refuse page numbers past a point (HTTP 422) and expect the
 * cursor GitHub puts in that URL, so page numbers are never built by hand.
 * `keep` reduces each item as its page arrives: full fork or issue objects for
 * a large repository do not fit in a Worker's 128 MB.
 */
export async function pagedList<T, U>(
  f: Fetcher,
  env: CollectEnv,
  base: string,
  accept: string,
  maxPages: number,
  keep: (item: T) => U,
): Promise<U[]> {
  const out: U[] = [];
  const headers = { ...(ghHeaders(env) as Record<string, string>), Accept: accept };
  let url: string | null = `${base}${base.includes("?") ? "&" : "?"}per_page=100`;
  for (let page = 1; url; page++) {
    if (page > maxPages) {
      throw new Error(`Hit the ${maxPages}-page guard fetching ${base}; raise it deliberately if the repo really is this large.`);
    }
    const res = await f(url, { headers });
    if (!res.ok) {
      const hint =
        res.status === 404 && base.endsWith("/stargazers")
          ? " GitHub lists stargazers only to people with access to the repository: use a token from one of its maintainers."
          : res.status === 403 || res.status === 429
            ? " This is usually the rate limit or a token without access: check GITHUB_TOKEN (see README.md)."
            : "";
      throw new Error(`GET ${url} returned HTTP ${res.status}.${hint}`);
    }
    const batch = (await res.json()) as T[];
    if (!Array.isArray(batch)) throw new Error(`Response from ${url} is not a list.`);
    for (const item of batch) out.push(keep(item));
    url = nextLink(res.headers.get("link"));
  }
  return out;
}

/** Replace a per-day table in one transaction, so readers never see a partial history. */
async function replaceDaily(db: D1Database, table: "star_daily" | "fork_daily", rows: [string, number][]): Promise<void> {
  await writeAtomic(db, [db.prepare(`DELETE FROM ${table}`), ...insertMany(db, table, ["day", "n"], rows, "INSERT")]);
}

export const STARGAZER_LIST_CAP = 40_000;

export async function collectStars(env: CollectEnv, f: Fetcher): Promise<string> {
  const list = await pagedList(
    f,
    env,
    `https://api.github.com/repos/${PROJECT.repo}/stargazers`,
    "application/vnd.github.star+json",
    400,
    (x: { starred_at: string }) => x.starred_at,
  );
  // GitHub stops listing stargazers at 40,000; a cut-off list would read as growth stopping.
  if (list.length >= STARGAZER_LIST_CAP) {
    throw new Error(
      `GitHub lists at most ${STARGAZER_LIST_CAP.toLocaleString("en-US")} stargazers, so this repository's star history cannot be read in full. ` +
        "Set STAR_HISTORY = false in src/config.ts; star totals still come from the nightly repository snapshot.",
    );
  }
  await replaceDaily(env.DB, "star_daily", countByDay(list));
  return `${list.length} stars with dates`;
}

export async function collectForks(env: CollectEnv, f: Fetcher): Promise<string> {
  const list = await pagedList(
    f,
    env,
    `https://api.github.com/repos/${PROJECT.repo}/forks?sort=oldest`,
    "application/vnd.github+json",
    400,
    (x: { created_at: string }) => x.created_at,
  );
  await replaceDaily(env.DB, "fork_daily", countByDay(list));
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
  const list = await pagedList(
    f,
    env,
    `https://api.github.com/repos/${PROJECT.repo}/issues?state=all&sort=updated&direction=desc${since}`,
    "application/vnd.github+json",
    400,
    (i: GhItem) => [i.number, i.pull_request ? "pr" : "issue", i.created_at, i.closed_at, i.pull_request?.merged_at ?? null, i.author_association, i.user?.login ?? null],
  );
  await writeAtomic(env.DB, insertMany(env.DB, "items", ["number", "kind", "created_at", "closed_at", "merged_at", "assoc", "author"], list));
  return `${list.length} issues and PRs${sinceDay ? ` updated since ${sinceDay}` : " (full history)"}`;
}

interface GhCommit {
  sha: string;
  commit: { author: { date: string; email?: string } | null; committer: { date: string } | null };
  author: { login: string } | null;
}

/** Commits on the default branch. `sinceDay` limits the nightly run; omit it for the full history. */
export async function collectCommits(env: CollectEnv, f: Fetcher, sinceDay?: string): Promise<string> {
  const since = sinceDay ? `&since=${sinceDay}T00:00:00Z` : "";
  const list = await pagedList(
    f,
    env,
    `https://api.github.com/repos/${PROJECT.repo}/commits?sha=${encodeURIComponent(PROJECT.defaultBranch)}${since}`,
    "application/vnd.github+json",
    400,
    // Commits with no linked GitHub account are grouped together rather than keyed by email.
    // Dated when it landed (committer date), not when it was first written: rebased and merged branches keep old author dates.
    (c: GhCommit) => {
      const at = c.commit.committer?.date ?? c.commit.author?.date;
      return at ? [c.sha, at.slice(0, 10), c.author?.login ?? "(no GitHub account)", at] : null;
    },
  );
  const rows = list.filter((r): r is NonNullable<typeof r> => r !== null);
  await writeAtomic(env.DB, insertMany(env.DB, "commits", ["sha", "day", "author", "at"], rows));
  return `${rows.length} commits${sinceDay ? ` since ${sinceDay}` : " (full history)"}`;
}

/**
 * Daily registry downloads for every configured package. Registries keep a
 * limited window (npm 18 months, pypistats 180 days, crates.io 90 days), so
 * each night upserts that window and the table keeps everything older.
 */
export async function collectPackageHistory(env: CollectEnv, f: Fetcher, today: Date): Promise<string> {
  const end = utcDay(today);
  const start = utcDay(new Date(today.getTime() - 360 * 86_400_000));
  // Registries report 0 for days they have not computed yet; the last two days are left out rather than stored as zero.
  const settled = utcDay(new Date(today.getTime() - 2 * 86_400_000));
  const rows: unknown[][] = [];
  const gaps: D1PreparedStatement[] = [];
  for (const pkg of PACKAGES.npm) {
    const npm = await getJson<{ downloads: { day: string; downloads: number }[] }>(
      f,
      `https://api.npmjs.org/downloads/range/${start}:${end}/${pkg}`,
    );
    const days = npm.downloads.filter((d) => d.day <= settled);
    // npm reports 0 for days it never computed, mid-history too. For a package that normally has downloads a 0 is
    // such a day: a gap (its old row is removed), not a real zero.
    const sorted = days.map((d) => d.downloads).sort((x, y) => x - y);
    const typical = sorted.length ? sorted[Math.floor(sorted.length / 2)]! : 0;
    for (const d of days) {
      if (d.downloads === 0 && typical >= 5) gaps.push(env.DB.prepare("DELETE FROM package_daily WHERE source = 'npm' AND package = ? AND day = ?").bind(pkg, d.day));
      else rows.push(["npm", pkg, d.day, d.downloads]);
    }
  }
  for (const pkg of PACKAGES.pypi) {
    const pypi = await getJson<{ data: { category: string; date: string; downloads: number }[] }>(
      f,
      `https://pypistats.org/api/packages/${pkg.toLowerCase()}/overall?mirrors=false`,
    );
    rows.push(...pypi.data.filter((d) => d.category === "without_mirrors" && d.date <= settled).map((d) => ["pypi", pkg, d.date, d.downloads]));
  }
  for (const pkg of PACKAGES.crates) {
    // crates.io refuses requests without a User-Agent naming the caller.
    const c = await getJson<{ version_downloads: { date: string; downloads: number }[]; meta?: { extra_downloads?: { date: string; downloads: number }[] } }>(
      f,
      `https://crates.io/api/v1/crates/${pkg}/downloads`,
      { headers: { "User-Agent": `${PROJECT.repo.replace("/", "-")}-dashboard-collector (https://github.com/${PROJECT.repo})` } },
    );
    const perDay = new Map<string, number>();
    for (const d of [...c.version_downloads, ...(c.meta?.extra_downloads ?? [])]) perDay.set(d.date, (perDay.get(d.date) ?? 0) + d.downloads);
    rows.push(...[...perDay].filter(([day]) => day <= settled).map(([day, n]) => ["crates", pkg, day, n]));
  }
  await writeAtomic(env.DB, [...gaps, ...insertMany(env.DB, "package_daily", ["source", "package", "day", "downloads"], rows)]);
  return `${rows.length} daily package rows from ${PACKAGES.npm.length + PACKAGES.pypi.length + PACKAGES.crates.length} package(s)`;
}

/**
 * GitHub computes repository statistics in the background and answers 202
 * until they are ready. That is reported as a failure of this source (so it is
 * visible and retried on the next run), never stored as an empty history.
 */
async function githubStats<T>(env: CollectEnv, f: Fetcher, path: string): Promise<T> {
  const url = `https://api.github.com/repos/${PROJECT.repo}/stats/${path}`;
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
