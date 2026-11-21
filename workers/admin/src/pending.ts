// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Pending contributors: people (bots left out) with at least one issue or pull
 * request still open. Someone with an open pull request is waiting on review;
 * someone with only an open issue is waiting on an answer or a fix. Draft pull
 * requests are work in progress, so nobody is waiting on them.
 */

import { isExternal, type Aliases } from "./growth";
import { dayAxis, dayOf, isBot, type ItemRow } from "./momentum";

export interface PendingPerson {
  /** The person's first account when the core team joins several with +. */
  author: string;
  /** Their accounts that have something open; more than one only when the core team joins them with +. */
  accounts: string[];
  /** Core team: a maintainer account, or an owner, member or collaborator on their most recent item. */
  core: boolean;
  openPrs: number;
  openIssues: number;
  /** When their longest-waiting open pull request and issue were opened. */
  oldestPr: string | null;
  oldestIssue: string | null;
}

export interface PendingDay {
  day: string;
  /** Outside contributors with an open pull request, and those with open issues only, at the end of the day. */
  outsidePr: number;
  outsideIssue: number;
  corePr: number;
  coreIssue: number;
}

export interface Pending {
  people: PendingPerson[];
  daily: PendingDay[];
}

export function pendingContributors(items: ItemRow[], maintainers: Set<string>, alias: Aliases, until: string, lastDay: string): Pending {
  const who = (login: string) => alias.get(login.toLowerCase()) ?? login;
  const key = (login: string) => who(login).toLowerCase();
  const mine = items.filter((i): i is ItemRow & { author: string } => !!i.author && !isBot(i.author) && i.created_at < until);
  // Today's answer for the whole history, so nobody changes group between two of their own items; a past collaborator now outside is outside.
  const latest = new Map<string, ItemRow>();
  for (const i of mine) {
    const l = latest.get(key(i.author));
    if (!l || i.created_at > l.created_at) latest.set(key(i.author), i);
  }
  const core = new Set([...latest].filter(([, i]) => !isExternal(i, maintainers)).map(([k]) => k));
  // Draft state is only known as of the last collection, so a draft is left out for its whole life.
  const waiting = mine.filter((i) => !(i.kind === "pr" && i.draft));

  const people = new Map<string, PendingPerson>();
  for (const i of waiting) {
    if (i.closed_at && i.closed_at < until) continue;
    const k = key(i.author);
    const p = people.get(k) ?? { author: who(i.author), accounts: [], core: core.has(k), openPrs: 0, openIssues: 0, oldestPr: null, oldestIssue: null };
    people.set(k, p);
    if (!p.accounts.includes(i.author)) p.accounts.push(i.author);
    if (i.kind === "pr") {
      p.openPrs++;
      if (!p.oldestPr || i.created_at < p.oldestPr) p.oldestPr = i.created_at;
    } else {
      p.openIssues++;
      if (!p.oldestIssue || i.created_at < p.oldestIssue) p.oldestIssue = i.created_at;
    }
  }
  const first = (p: PendingPerson) => p.oldestPr ?? p.oldestIssue ?? "";
  const list = [...people.values()].sort(
    (a, b) => Number(!a.oldestPr) - Number(!b.oldestPr) || first(a).localeCompare(first(b)) || a.author.localeCompare(b.author),
  );

  if (!waiting.length) return { people: list, daily: [] };
  // An item counts as open from the day it was opened until the day before it closed.
  const changes = new Map<string, { who: string; pr: number; issue: number }[]>();
  const change = (day: string, k: string, kind: string, n: number) => {
    const c = changes.get(day) ?? [];
    changes.set(day, c);
    c.push({ who: k, pr: kind === "pr" ? n : 0, issue: kind === "pr" ? 0 : n });
  };
  for (const i of waiting) {
    change(dayOf(i.created_at), key(i.author), i.kind, 1);
    if (i.closed_at && i.closed_at < until) change(dayOf(i.closed_at), key(i.author), i.kind, -1);
  }
  const open = new Map<string, { pr: number; issue: number }>();
  const n = { outsidePr: 0, outsideIssue: 0, corePr: 0, coreIssue: 0 };
  const slot = (k: string, o: { pr: number; issue: number }): keyof typeof n | null =>
    o.pr > 0 ? (core.has(k) ? "corePr" : "outsidePr") : o.issue > 0 ? (core.has(k) ? "coreIssue" : "outsideIssue") : null;
  const start = waiting.reduce((m, i) => (i.created_at < m ? i.created_at : m), waiting[0]!.created_at);
  const daily = dayAxis(dayOf(start), lastDay).map((day) => {
    for (const c of changes.get(day) ?? []) {
      const o = open.get(c.who) ?? { pr: 0, issue: 0 };
      open.set(c.who, o);
      const before = slot(c.who, o);
      o.pr += c.pr;
      o.issue += c.issue;
      const after = slot(c.who, o);
      if (before) n[before]--;
      if (after) n[after]++;
    }
    return { day, ...n };
  });
  return { people: list, daily };
}
