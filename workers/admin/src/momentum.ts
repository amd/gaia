// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Traction and development-momentum series. Everything is returned as a dense
 * daily series (one row per UTC day from the first day with data to today) so
 * the page can cut any time range and regroup it by day, week or month. A day
 * with no activity is a real zero: the underlying sources are complete lists.
 * Headline numbers for a range are computed from exact timestamps instead, so
 * "last 24 hours" means the last 24 hours, not "today's bucket".
 */

export interface ItemRow {
  kind: string;
  created_at: string;
  closed_at: string | null;
  merged_at: string | null;
  assoc: string;
  author?: string | null;
}
export interface CommitRow {
  day: string;
  at: string | null;
  author: string;
}
export interface DailyRow {
  day: string;
  n: number;
}

const DAY = 86_400_000;

export const dayOf = (iso: string): string => iso.slice(0, 10);

export function dayAxis(firstDay: string, lastDay: string): string[] {
  const out: string[] = [];
  for (let t = Date.parse(firstDay + "T00:00:00Z"); t <= Date.parse(lastDay + "T00:00:00Z"); t += DAY) {
    out.push(new Date(t).toISOString().slice(0, 10));
  }
  return out;
}

export function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid]! : (s[mid - 1]! + s[mid]!) / 2;
}

const daysBetween = (a: string, b: string): number => (Date.parse(b) - Date.parse(a)) / DAY;

/** Added-per-day and running total for a per-day count table (stars, forks). */
export function cumulativeDaily(rows: DailyRow[], lastDay: string): { day: string; added: number; total: number }[] {
  if (rows.length === 0) return [];
  const per = new Map(rows.map((r) => [r.day, r.n]));
  let total = 0;
  return dayAxis(rows[0]!.day, lastDay).map((day) => {
    const added = per.get(day) ?? 0;
    total += added;
    return { day, added, total };
  });
}

/**
 * Daily package downloads. A source's days before its first reported day (or
 * outside the window the registry keeps) are null, not zero.
 */
export function packageDaily(
  rows: { source: string; day: string; downloads: number }[],
  lastDay: string,
): { day: string; npm: number | null; pypi: number | null }[] {
  if (rows.length === 0) return [];
  const by = { npm: new Map<string, number>(), pypi: new Map<string, number>() };
  for (const r of rows) if (r.source === "npm" || r.source === "pypi") by[r.source].set(r.day, r.downloads);
  const first = rows.reduce((m, r) => (r.day < m ? r.day : m), rows[0]!.day);
  const firstOf = (m: Map<string, number>) => [...m.keys()].sort()[0] ?? "9999-12-31";
  const fNpm = firstOf(by.npm);
  const fPypi = firstOf(by.pypi);
  return dayAxis(first, lastDay).map((day) => ({
    day,
    npm: day < fNpm ? null : (by.npm.get(day) ?? null),
    pypi: day < fPypi ? null : (by.pypi.get(day) ?? null),
  }));
}

export interface MomentumDay {
  day: string;
  issuesOpened: number;
  issuesClosed: number;
  openIssues: number;
  prsOpened: number;
  prsMerged: number;
  prsClosedUnmerged: number;
  openPrs: number;
  commits: number;
  activeContributors28d: number;
  newContributors: number;
  totalContributors: number;
}

export function momentumDaily(items: ItemRow[], commits: CommitRow[], lastDay: string): MomentumDay[] {
  const days = [...items.map((i) => dayOf(i.created_at)), ...commits.map((c) => c.day)];
  if (days.length === 0) return [];
  const axis = dayAxis(days.reduce((m, d) => (d < m ? d : m), days[0]!), lastDay);
  const w = new Map<string, MomentumDay>(
    axis.map((day) => [
      day,
      {
        day, issuesOpened: 0, issuesClosed: 0, openIssues: 0, prsOpened: 0, prsMerged: 0, prsClosedUnmerged: 0,
        openPrs: 0, commits: 0, activeContributors28d: 0, newContributors: 0, totalContributors: 0,
      },
    ]),
  );
  const at = (iso: string) => w.get(dayOf(iso));
  for (const i of items) {
    const c = at(i.created_at);
    if (i.kind === "pr") {
      if (c) c.prsOpened++;
      if (i.merged_at) {
        const m = at(i.merged_at);
        if (m) m.prsMerged++;
      } else if (i.closed_at) {
        const x = at(i.closed_at);
        if (x) x.prsClosedUnmerged++;
      }
    } else {
      if (c) c.issuesOpened++;
      if (i.closed_at) {
        const x = at(i.closed_at);
        if (x) x.issuesClosed++;
      }
    }
  }
  let oi = 0;
  let op = 0;
  for (const day of axis) {
    const d = w.get(day)!;
    oi += d.issuesOpened - d.issuesClosed;
    op += d.prsOpened - d.prsMerged - d.prsClosedUnmerged;
    d.openIssues = oi;
    d.openPrs = op;
  }

  const firstSeen = new Map<string, string>();
  for (const c of [...commits].sort((a, b) => a.day.localeCompare(b.day))) if (!firstSeen.has(c.author)) firstSeen.set(c.author, c.day);
  const authorsByDay = new Map<string, Set<string>>();
  for (const c of commits) {
    const d = w.get(c.day);
    if (d) d.commits++;
    const s = authorsByDay.get(c.day) ?? new Set<string>();
    s.add(c.author);
    authorsByDay.set(c.day, s);
  }
  for (const first of firstSeen.values()) {
    const d = w.get(first);
    if (d) d.newContributors++;
  }
  let total = 0;
  const window: Set<string>[] = [];
  for (const day of axis) {
    const d = w.get(day)!;
    total += d.newContributors;
    d.totalContributors = total;
    window.push(authorsByDay.get(day) ?? new Set());
    if (window.length > 28) window.shift();
    const active = new Set<string>();
    for (const s of window) for (const a of s) active.add(a);
    d.activeContributors28d = active.size;
  }
  return axis.map((day) => w.get(day)!);
}

export interface RangeSummary {
  prsOpened: number;
  prsMerged: number;
  issuesOpened: number;
  issuesClosed: number;
  openIssues: number;
  openPrs: number;
  /** Open pull requests created more than 14 days ago. */
  staleOpenPrs: number;
  commits: number;
  contributors: number;
  medianDaysToMerge: number | null;
  p90DaysToMerge: number | null;
  medianDaysToClose: number | null;
  p90DaysToClose: number | null;
  /** [days to merge] for PRs merged in the range; [days to close] for issues closed in it. */
  mergeDurations: number[];
  closeDurations: number[];
}

/** Nearest-rank percentile (p in 0..100); null for an empty list. */
export function percentileOf(values: number[], p: number): number | null {
  if (!values.length) return null;
  const s = [...values].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.max(0, Math.ceil((p / 100) * s.length) - 1))]!;
}

/**
 * Exact counts for items and commits whose timestamps fall in [since, until).
 * Open counts are as of `until` (now, for the current window).
 */
export function rangeSummary(items: ItemRow[], commits: CommitRow[], since: string, until = "9999"): RangeSummary {
  const inRange = (iso: string | null) => iso !== null && iso >= since && iso < until;
  const openAt = (i: ItemRow) => i.created_at < until && (!i.closed_at || i.closed_at >= until);
  const staleBefore = new Date((until === "9999" ? Date.now() : Date.parse(until)) - 14 * 86_400_000).toISOString();
  const mergeDurations: number[] = [];
  const closeDurations: number[] = [];
  let prsOpened = 0;
  let prsMerged = 0;
  let issuesOpened = 0;
  let issuesClosed = 0;
  let openIssues = 0;
  let openPrs = 0;
  let staleOpenPrs = 0;
  for (const i of items) {
    if (i.kind === "pr") {
      if (inRange(i.created_at)) prsOpened++;
      if (inRange(i.merged_at)) {
        prsMerged++;
        mergeDurations.push(daysBetween(i.created_at, i.merged_at!));
      }
      if (openAt(i)) {
        openPrs++;
        if (i.created_at < staleBefore) staleOpenPrs++;
      }
    } else {
      if (inRange(i.created_at)) issuesOpened++;
      if (inRange(i.closed_at)) {
        issuesClosed++;
        closeDurations.push(daysBetween(i.created_at, i.closed_at!));
      }
      if (openAt(i)) openIssues++;
    }
  }
  // Commits carry a full timestamp when collected after it was added; older rows only have a day.
  const recent = commits.filter((c) => {
    const t = c.at ?? c.day;
    return t >= (c.at ? since : since.slice(0, 10)) && t < (c.at ? until : until.slice(0, 10));
  });
  return {
    prsOpened, prsMerged, issuesOpened, issuesClosed, openIssues, openPrs, staleOpenPrs,
    commits: recent.length,
    contributors: new Set(recent.map((c) => c.author)).size,
    medianDaysToMerge: median(mergeDurations),
    p90DaysToMerge: percentileOf(mergeDurations, 90),
    medianDaysToClose: median(closeDurations),
    p90DaysToClose: percentileOf(closeDurations, 90),
    mergeDurations,
    closeDurations,
  };
}

/** Automation accounts, by GitHub login. */
export const isBot = (login: string | null | undefined): boolean =>
  !!login && (/\[bot\]$/i.test(login) || /^(claude|github-actions|dependabot|renovate|copilot)$/i.test(login));

export interface PeopleFilter {
  exclude: Set<string>;
  bots: boolean;
}

export function parsePeople(q: URLSearchParams): PeopleFilter {
  const exclude = new Set((q.get("exclude") ?? "").split(",").map((x) => x.trim().toLowerCase()).filter(Boolean));
  return { exclude, bots: q.get("bots") !== "0" };
}

export const keepAuthor = (p: PeopleFilter) => (login: string | null | undefined): boolean =>
  !(login && p.exclude.has(login.toLowerCase())) && (p.bots || !isBot(login));

export interface ContributorRow {
  author: string;
  bot: boolean;
  commits: number;
  prsOpened: number;
  prsMerged: number;
  issuesOpened: number;
  additions: number;
  deletions: number;
  firstCommit: string | null;
}

/** Per-person activity in [since, now): commits, pull requests, issues and lines changed. */
export function contributors(
  items: (ItemRow & { author?: string | null })[],
  commits: CommitRow[],
  weekly: { author: string; week: string; commits: number; additions: number; deletions: number }[],
  since: string,
  firstCommitBy: Map<string, string>,
): ContributorRow[] {
  const m = new Map<string, ContributorRow>();
  const get = (a: string) => {
    let r = m.get(a);
    if (!r) {
      r = { author: a, bot: isBot(a), commits: 0, prsOpened: 0, prsMerged: 0, issuesOpened: 0, additions: 0, deletions: 0, firstCommit: firstCommitBy.get(a) ?? null };
      m.set(a, r);
    }
    return r;
  };
  for (const c of commits) if ((c.at ?? c.day) >= (c.at ? since : since.slice(0, 10))) get(c.author).commits++;
  for (const i of items) {
    if (!i.author) continue;
    if (i.kind === "pr") {
      if (i.created_at >= since) get(i.author).prsOpened++;
      if (i.merged_at && i.merged_at >= since) get(i.author).prsMerged++;
    } else if (i.created_at >= since) get(i.author).issuesOpened++;
  }
  // Line counts are weekly; a week counts when any part of it falls in the range.
  for (const w of weekly) {
    if (Date.parse(w.week) + 7 * 86_400_000 <= Date.parse(since)) continue;
    const r = get(w.author);
    r.additions += w.additions;
    r.deletions += w.deletions;
  }
  return [...m.values()].sort((a, b) => b.commits - a.commits || b.prsMerged - a.prsMerged || a.author.localeCompare(b.author));
}
