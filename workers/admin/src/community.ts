// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * How the community is treated and how it holds together: how fast the core team answers
 * outside contributors, how far newcomers go, and how much of the outside work rests on a few people.
 */

import { CORE_ASSOC, isExternal, nextStart, periodStart, type Aliases, type Gran } from "./growth";
import { isBot, type ItemRow } from "./momentum";

const HOUR = 3_600_000;

export type NumberedItem = ItemRow & { number: number };

export interface ResponseRow {
  number: number;
  login: string;
  assoc: string;
  at: string;
}

/**
 * When the core team first answered each item: their earliest comment, or the item being closed
 * or merged, whichever came first. The author's own comments do not count. Absent while nobody has.
 */
export function firstResponses(items: NumberedItem[], comments: ResponseRow[], maintainers: Set<string>, alias: Aliases = new Map()): Map<number, string> {
  const who = (login: string) => (alias.get(login.toLowerCase()) ?? login).toLowerCase();
  const author = new Map(items.map((i) => [i.number, who(i.author ?? "")]));
  const out = new Map<number, string>();
  const earlier = (n: number, at: string) => {
    const cur = out.get(n);
    if (!cur || at < cur) out.set(n, at);
  };
  for (const c of comments) {
    const login = c.login.toLowerCase();
    if (isBot(c.login) || who(c.login) === author.get(c.number)) continue;
    if (maintainers.has(login) || CORE_ASSOC.has(c.assoc)) earlier(c.number, c.at);
  }
  for (const i of items) {
    const end = i.merged_at ?? i.closed_at;
    if (end) earlier(i.number, end);
  }
  return out;
}

export interface ResponseStats {
  opened: number;
  /** Median hours to the first response. An item nobody has answered yet counts with its age so far, so this can only read low while many wait. */
  medianHours: number | null;
  /** Of the items at least two days old, the share answered within two days. */
  within2d: number | null;
  /** Open, and nobody from the core team has answered. */
  waiting: number;
}

const median = (xs: number[]): number | null => {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m]! : (s[m - 1]! + s[m]!) / 2;
};

function stats(items: NumberedItem[], first: Map<number, string>, until: number): ResponseStats {
  const hours: number[] = [];
  let old = 0, fast = 0, waiting = 0;
  for (const i of items) {
    const t0 = Date.parse(i.created_at);
    const r = first.get(i.number);
    const h = ((r ? Date.parse(r) : until) - t0) / HOUR;
    hours.push(h);
    if (!r) waiting++;
    if (until - t0 >= 48 * HOUR) {
      old++;
      if (r && h <= 48) fast++;
    }
  }
  return { opened: items.length, medianHours: median(hours), within2d: old ? fast / old : null, waiting };
}

/** First-response figures for outside issues and pull requests opened in [from, to), as of `until` (ISO). */
export function responseWindow(items: NumberedItem[], first: Map<number, string>, maintainers: Set<string>, from: string, to: string, until: string): { issue: ResponseStats; pr: ResponseStats } {
  const end = Date.parse(until);
  const mine = items.filter((i) => isExternal(i, maintainers) && i.created_at.slice(0, 10) >= from && i.created_at.slice(0, 10) < to && i.created_at < until);
  return {
    issue: stats(mine.filter((i) => i.kind === "issue"), first, end),
    pr: stats(mine.filter((i) => i.kind === "pr"), first, end),
  };
}

export interface ResponsePeriod {
  start: string;
  issues: number;
  issueMedianHours: number | null;
  issueWithin2d: number | null;
  prs: number;
  prMedianHours: number | null;
  prWithin2d: number | null;
}

/** First-response figures per period, by when the item was opened, from the period containing `startAt` through the one containing `today`. */
export function responsePeriods(items: NumberedItem[], first: Map<number, string>, maintainers: Set<string>, gran: Gran, startAt: string, today: string, until: string): ResponsePeriod[] {
  const out: ResponsePeriod[] = [];
  const firstDay = items.reduce((m, i) => (i.created_at < m ? i.created_at : m), "9999").slice(0, 10);
  if (firstDay === "9999") return out;
  for (let s = periodStart(firstDay > startAt ? firstDay : startAt, gran); s <= today; s = nextStart(s, gran)) {
    const w = responseWindow(items, first, maintainers, s, nextStart(s, gran), until);
    out.push({
      start: s,
      issues: w.issue.opened,
      issueMedianHours: w.issue.medianHours,
      issueWithin2d: w.issue.within2d,
      prs: w.pr.opened,
      prMedianHours: w.pr.medianHours,
      prWithin2d: w.pr.within2d,
    });
  }
  return out;
}

/** People outside the core team: their most recent item, as on the Developers page, so nobody counts on both sides. */
function outsidePeople(items: ItemRow[], maintainers: Set<string>, alias: Aliases, until: string) {
  const key = (login: string) => (alias.get(login.toLowerCase()) ?? login).toLowerCase();
  const mine = items.filter((i): i is ItemRow & { author: string } => !!i.author && !isBot(i.author) && i.created_at < until);
  const latest = new Map<string, ItemRow>();
  for (const i of mine) {
    const l = latest.get(key(i.author));
    if (!l || i.created_at > l.created_at) latest.set(key(i.author), i);
  }
  const outside = new Set([...latest].filter(([, i]) => isExternal(i, maintainers)).map(([k]) => k));
  const by = new Map<string, (ItemRow & { author: string })[]>();
  for (const i of mine) {
    const k = key(i.author);
    if (!outside.has(k)) continue;
    const list = by.get(k) ?? [];
    by.set(k, list);
    list.push(i);
  }
  for (const list of by.values()) list.sort((a, b) => a.created_at.localeCompare(b.created_at));
  return by;
}

export interface Funnel {
  /** Outside people whose first issue or pull request was opened in the window. */
  people: number;
  openedPr: number;
  merged: number;
  mergedTwice: number;
  /** Of those who started with an issue, how many later opened a pull request. */
  issueFirst: number;
  issueThenPr: number;
}

/** How far the outside people who first showed up in [from, to) have gone since, as of `until`. */
export function contributorFunnel(items: ItemRow[], maintainers: Set<string>, alias: Aliases, from: string, to: string, until: string): Funnel {
  const f: Funnel = { people: 0, openedPr: 0, merged: 0, mergedTwice: 0, issueFirst: 0, issueThenPr: 0 };
  for (const list of outsidePeople(items, maintainers, alias, until).values()) {
    const start = list[0]!.created_at.slice(0, 10);
    if (start < from || start >= to) continue;
    const prs = list.filter((i) => i.kind === "pr");
    const merged = prs.filter((i) => i.merged_at && i.merged_at < until).length;
    f.people++;
    if (prs.length) f.openedPr++;
    if (merged) f.merged++;
    if (merged >= 2) f.mergedTwice++;
    if (list[0]!.kind === "issue") {
      f.issueFirst++;
      if (prs.length) f.issueThenPr++;
    }
  }
  return f;
}

export interface TopContributor {
  author: string;
  merged: number;
  /** Their most recent issue or pull request, whenever it was. */
  lastSeen: string;
}

export interface Concentration {
  /** Merged outside pull requests in the window, as counted by the outside-share figures. */
  merged: number;
  /** People they came from. */
  people: number;
  /** Share of them from the two people with the most; null with fewer than three people. */
  topTwoShare: number | null;
  top: TopContributor[];
}

/** Who the outside pull requests merged in [from, to) came from, most first. */
export function outsideConcentration(items: ItemRow[], maintainers: Set<string>, alias: Aliases, from: string, to: string, until: string, limit = 10): Concentration {
  const who = (login: string) => alias.get(login.toLowerCase()) ?? login;
  const last = new Map<string, string>();
  for (const i of items) {
    if (!i.author || i.created_at >= until) continue;
    const k = who(i.author).toLowerCase();
    if (!last.has(k) || i.created_at > last.get(k)!) last.set(k, i.created_at);
  }
  const by = new Map<string, TopContributor>();
  let merged = 0;
  for (const i of items) {
    if (i.kind !== "pr" || !i.merged_at || !isExternal(i, maintainers)) continue;
    const d = i.merged_at.slice(0, 10);
    if (d < from || d >= to) continue;
    merged++;
    const k = who(i.author!).toLowerCase();
    const p = by.get(k) ?? { author: who(i.author!), merged: 0, lastSeen: last.get(k) ?? i.created_at };
    by.set(k, p);
    p.merged++;
  }
  const ranked = [...by.values()].sort((a, b) => b.merged - a.merged || a.author.localeCompare(b.author));
  const two = ranked.slice(0, 2).reduce((s, p) => s + p.merged, 0);
  // With two people or fewer the top two are everyone, so the share says nothing.
  return { merged, people: ranked.length, topTwoShare: ranked.length >= 3 ? two / merged : null, top: ranked.slice(0, limit) };
}
