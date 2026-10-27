// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Growth rates and community health: how fast stars, forks and contributors
 * grow, how many people are new, how many stay, and how much of the work comes
 * from outside the core team. Periods are calendar months or Monday-start weeks
 * (UTC); headline numbers compare the selected window with the one before it.
 */

import { isBot, type CommitRow, type DailyRow, type ItemRow } from "./momentum";

const DAY = 86_400_000;
const CORE_ASSOC = new Set(["OWNER", "MEMBER", "COLLABORATOR"]);
const NO_ACCOUNT = "(no GitHub account)";

export type Gran = "week" | "month";

export interface Activity {
  who: string;
  day: string;
}

export interface GrowthPeriod {
  start: string;
  stars: number;
  starsTotal: number;
  /** Stars added over the total at the start of the period; null when there was none. */
  starGrowth: number | null;
  forks: number;
  forksTotal: number;
  forkGrowth: number | null;
  active: number;
  newContributors: number;
  /** People whose first PR fell in the period, and how many opened another within 90 days; null until 90 days have passed. */
  newcomerCohort: number | null;
  newcomerReturned: number | null;
  /** Merged pull requests opened by people (bots left out). */
  prsMerged: number;
  externalMerged: number;
}

export interface GrowthWindow {
  stars: number;
  starGrowth: number | null;
  forks: number;
  forkGrowth: number | null;
  active: number;
  newContributors: number;
  prsMerged: number;
  externalShare: number | null;
  busFactor: number | null;
}

const human = (who: string | null | undefined): who is string => !!who && who !== NO_ACCOUNT && !isBot(who);

/** Who did something on which day: commits to the default branch and pull requests opened, people only. */
export function activity(items: ItemRow[], commits: CommitRow[]): Activity[] {
  const out: Activity[] = [];
  for (const c of commits) if (human(c.author)) out.push({ who: c.author.toLowerCase(), day: c.day });
  for (const i of items) if (i.kind === "pr" && human(i.author)) out.push({ who: i.author.toLowerCase(), day: i.created_at.slice(0, 10) });
  return out;
}

/** Core team: the configured maintainers plus anyone GitHub lists as owner, member or collaborator. */
export function isExternal(item: ItemRow, maintainers: Set<string>): boolean {
  return human(item.author) && !maintainers.has(item.author.toLowerCase()) && !CORE_ASSOC.has(item.assoc);
}

export function periodStart(day: string, gran: Gran): string {
  if (gran === "month") return day.slice(0, 7) + "-01";
  const t = Date.parse(day + "T00:00:00Z");
  return new Date(t - ((new Date(t).getUTCDay() + 6) % 7) * DAY).toISOString().slice(0, 10);
}

function nextStart(start: string, gran: Gran): string {
  if (gran === "week") return new Date(Date.parse(start + "T00:00:00Z") + 7 * DAY).toISOString().slice(0, 10);
  const [y, m] = start.split("-").map(Number) as [number, number];
  return m === 12 ? `${y + 1}-01-01` : `${y}-${String(m + 1).padStart(2, "0")}-01`;
}

const sumBefore = (rows: DailyRow[], day: string) => rows.reduce((s, r) => (r.day < day ? s + r.n : s), 0);
const sumIn = (rows: DailyRow[], from: string, to: string) => rows.reduce((s, r) => (r.day >= from && r.day < to ? s + r.n : s), 0);
const rate = (added: number, base: number) => (base > 0 ? added / base : null);

function peopleIn(acts: Activity[], from: string, to: string): Set<string> {
  const s = new Set<string>();
  for (const a of acts) if (a.day >= from && a.day < to) s.add(a.who);
  return s;
}

function firstSeen(acts: Activity[]): Map<string, string> {
  const m = new Map<string, string>();
  for (const a of acts) {
    const f = m.get(a.who);
    if (!f || a.day < f) m.set(a.who, a.day);
  }
  return m;
}

/** Days a newcomer has to come back to count as retained. */
export const RETURN_DAYS = 90;
const addDays = (day: string, n: number) => new Date(Date.parse(day + "T00:00:00Z") + n * DAY).toISOString().slice(0, 10);

/**
 * Of the people whose first pull request fell in [from, to), the share who opened another on a
 * later day within RETURN_DAYS of it. Pass pull requests only (`activity(items, [])`): a
 * newcomer's own PR landing as a commit days later is not a return. Null when there were none, or when the latest of
 * them has not yet had the full RETURN_DAYS (`today` decides).
 */
export function newcomerRetention(acts: Activity[], from: string, to: string, today: string): { cohort: number; returned: number; rate: number | null } {
  if (addDays(to, RETURN_DAYS - 1) > today) return { cohort: 0, returned: 0, rate: null };
  const first = firstSeen(acts);
  const back = new Set<string>();
  for (const a of acts) {
    const f = first.get(a.who)!;
    if (f >= from && f < to && a.day > f && a.day <= addDays(f, RETURN_DAYS)) back.add(a.who);
  }
  const cohort = [...first.values()].filter((f) => f >= from && f < to).length;
  return { cohort, returned: back.size, rate: cohort ? back.size / cohort : null };
}

/** One row per period from the one containing `startAt` (or the first with data, if later) through the one containing `today`. */
export function growthPeriods(
  stars: DailyRow[],
  forks: DailyRow[],
  items: ItemRow[],
  commits: CommitRow[],
  maintainers: Set<string>,
  gran: Gran,
  today: string,
  startAt = "",
): GrowthPeriod[] {
  const acts = activity(items, commits);
  const prActs = activity(items, []);
  const first = firstSeen(acts);
  const days = [...stars.map((r) => r.day), ...forks.map((r) => r.day), ...acts.map((a) => a.day)].sort();
  if (!days.length) return [];
  const merged = items.filter((i) => i.kind === "pr" && i.merged_at && human(i.author));
  const out: GrowthPeriod[] = [];
  for (let s = periodStart(days[0]! > startAt ? days[0]! : startAt, gran); s <= today; s = nextStart(s, gran)) {
    const e = nextStart(s, gran);
    const sb = sumBefore(stars, s), fb = sumBefore(forks, s);
    const st = sumIn(stars, s, e), fk = sumIn(forks, s, e);
    const who = peopleIn(acts, s, e);
    const ret = newcomerRetention(prActs, s, e, today);
    const done = addDays(e, RETURN_DAYS - 1) <= today;
    const mergedHere = merged.filter((i) => i.merged_at!.slice(0, 10) >= s && i.merged_at!.slice(0, 10) < e);
    out.push({
      start: s,
      stars: st,
      starsTotal: sb + st,
      starGrowth: rate(st, sb),
      forks: fk,
      forksTotal: fb + fk,
      forkGrowth: rate(fk, fb),
      active: who.size,
      newContributors: [...who].filter((w) => (first.get(w) ?? "") >= s).length,
      newcomerCohort: done ? ret.cohort : null,
      newcomerReturned: done ? ret.returned : null,
      prsMerged: mergedHere.length,
      externalMerged: mergedHere.filter((i) => isExternal(i, maintainers)).length,
    });
  }
  return out;
}

/** Fewest people who made at least half of the commits; null with no commits. */
export function busFactor(commits: CommitRow[]): number | null {
  const by = new Map<string, number>();
  for (const c of commits) if (human(c.author)) by.set(c.author.toLowerCase(), (by.get(c.author.toLowerCase()) ?? 0) + 1);
  const counts = [...by.values()].sort((a, b) => b - a);
  const total = counts.reduce((a, b) => a + b, 0);
  if (!total) return null;
  let sum = 0;
  for (let i = 0; i < counts.length; i++) {
    sum += counts[i]!;
    if (sum * 2 >= total) return i + 1;
  }
  return counts.length;
}

/** Headline numbers for the days [from, to). */
export function growthWindow(
  stars: DailyRow[],
  forks: DailyRow[],
  items: ItemRow[],
  commits: CommitRow[],
  maintainers: Set<string>,
  from: string,
  to: string,
): GrowthWindow {
  const acts = activity(items, commits);
  const first = firstSeen(acts);
  const who = peopleIn(acts, from, to);
  const merged = items.filter((i) => i.kind === "pr" && i.merged_at && human(i.author) && i.merged_at.slice(0, 10) >= from && i.merged_at.slice(0, 10) < to);
  const st = sumIn(stars, from, to), fk = sumIn(forks, from, to);
  return {
    stars: st,
    starGrowth: rate(st, sumBefore(stars, from)),
    forks: fk,
    forkGrowth: rate(fk, sumBefore(forks, from)),
    active: who.size,
    newContributors: [...who].filter((w) => (first.get(w) ?? "") >= from).length,
    prsMerged: merged.length,
    externalShare: merged.length ? merged.filter((i) => isExternal(i, maintainers)).length / merged.length : null,
    busFactor: busFactor(commits.filter((c) => c.day >= from && c.day < to)),
  };
}

/** A growth rate over `days` expressed per 30 days, compounding, so windows of any length compare. */
export function per30(growth: number | null, days: number): number | null {
  if (growth == null || days <= 0) return null;
  return Math.pow(1 + growth, 30 / days) - 1;
}

export interface DownloadDay {
  day: string;
  /** GitHub release installs (update checks left out); null before daily snapshots began. */
  installs: number | null;
  pypi: number | null;
  npm: number | null;
}
export type DownloadSource = "installs" | "pypi" | "npm";
const SOURCES: DownloadSource[] = ["installs", "pypi", "npm"];

export interface DownloadWindow {
  total: number;
  previous: number | null;
  /** Change against the equal window before; null when the source does not cover it. */
  growth: number | null;
  /** Last day counted: registries publish a day or two late, so both windows end there. */
  through: string | null;
  /** First day of data from this source. */
  first: string | null;
}

/**
 * Downloads in a window of `span` days ending at `to` (exclusive), per source. Each source's
 * windows end on its own last day with data, so a registry's reporting lag is not read as a drop.
 */
export function downloadWindows(
  days: DownloadDay[],
  span: number,
  to: string,
  /** Each source's first day of data overall, when `days` is a recent slice of it. */
  firsts: Partial<Record<DownloadSource, string>> = {},
): Record<DownloadSource, DownloadWindow> {
  const out = {} as Record<DownloadSource, DownloadWindow>;
  for (const s of SOURCES) {
    const have = days.filter((d) => d[s] != null);
    if (!have.length) {
      out[s] = { total: 0, previous: null, growth: null, through: null, first: null };
      continue;
    }
    const first = firsts[s] ?? have[0]!.day;
    const last = have[have.length - 1]!.day;
    const end = addDays(last, 1) < to ? addDays(last, 1) : to;
    const start = addDays(end, -span);
    const prevStart = addDays(start, -span);
    const sum = (a: string, b: string) => have.reduce((n, d) => (d.day >= a && d.day < b ? n + d[s]! : n), 0);
    const total = sum(start, end);
    const previous = first <= prevStart ? sum(prevStart, start) : null;
    out[s] = { total, previous, growth: previous ? total / previous - 1 : null, through: addDays(end, -1), first };
  }
  return out;
}

/** Downloads per period for each source; null for a period with no data from that source. */
export function downloadPeriods(days: DownloadDay[], gran: Gran, startAt: string, today: string): ({ start: string } & Record<DownloadSource, number | null>)[] {
  if (!days.length) return [];
  const out: ({ start: string } & Record<DownloadSource, number | null>)[] = [];
  const first = days[0]!.day > startAt ? days[0]!.day : startAt;
  for (let s = periodStart(first, gran); s <= today; s = nextStart(s, gran)) {
    const e = nextStart(s, gran);
    const row = { start: s } as { start: string } & Record<DownloadSource, number | null>;
    for (const src of SOURCES) {
      const vals = days.filter((d) => d.day >= s && d.day < e && d[src] != null).map((d) => d[src]!);
      row[src] = vals.length ? vals.reduce((a, b) => a + b, 0) : null;
    }
    out.push(row);
  }
  return out;
}
