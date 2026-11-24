// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from "vitest";
import { contributorFunnel, firstResponses, outsideConcentration, responsePeriods, responseWindow, type NumberedItem, type ResponseRow } from "../src/community";
import { collectComments } from "../src/history";
import { memoryD1 } from "./d1";

const H = 3_600_000;
const T0 = Date.parse("2026-10-01T00:00:00Z");
const at = (hours: number) => new Date(T0 + hours * H).toISOString();
let n = 0;
const item = (kind: "issue" | "pr", author: string, opened: number, o: { closed?: number; merged?: number; assoc?: string; number?: number } = {}): NumberedItem => ({
  number: o.number ?? ++n,
  kind,
  author,
  assoc: o.assoc ?? "CONTRIBUTOR",
  created_at: at(opened),
  closed_at: o.closed != null ? at(o.closed) : o.merged != null ? at(o.merged) : null,
  merged_at: o.merged != null ? at(o.merged) : null,
});
const said = (number: number, login: string, hours: number, assoc = "NONE"): ResponseRow => ({ number, login, assoc, at: at(hours) });
const core = new Set(["kalin", "kalin-work"]);
const none = new Map<string, string>();

describe("firstResponses", () => {
  it("takes the earliest core comment or the close, and ignores the author, bots and outside commenters", () => {
    const a = item("issue", "carol", 0, { closed: 50 });
    const b = item("pr", "dave", 0);
    const c = item("issue", "erin", 0);
    const first = firstResponses([a, b, c], [
      said(a.number, "carol", 1), // the author
      said(a.number, "github-actions[bot]", 2, "MEMBER"),
      said(a.number, "frank", 3), // outside
      said(a.number, "Kalin-Work", 10), // a maintainer's second account, labelled a contributor
      said(b.number, "gina", 5, "COLLABORATOR"),
      said(c.number, "frank", 1),
    ], core);
    expect(first.get(a.number)).toBe(at(10));
    expect(first.get(b.number)).toBe(at(5));
    expect(first.has(c.number)).toBe(false);
  });

  it("counts a merge as the answer when nobody commented first", () => {
    const p = item("pr", "dave", 0, { merged: 30 });
    expect(firstResponses([p], [said(p.number, "kalin", 40)], core).get(p.number)).toBe(at(30));
  });
});

describe("responseWindow", () => {
  it("medians outside items only, counts unanswered ones by their age, and scores within two days over items old enough", () => {
    const items = [
      item("issue", "carol", 0, { closed: 10 }),
      item("issue", "dave", 0), // unanswered, 100 hours old at `until`
      item("issue", "erin", 90), // unanswered, 10 hours old: too young to score
      item("issue", "kalin", 0, { closed: 500 }), // core
      item("pr", "frank", 0, { merged: 60 }),
    ];
    const first = firstResponses(items, [], core);
    const w = responseWindow(items, first, core, "2026-10-01", "2026-10-08", at(100));
    expect(w.issue).toEqual({ opened: 3, medianHours: 10, within2d: 0.5, waiting: 2 });
    expect(w.pr).toEqual({ opened: 1, medianHours: 60, within2d: 0, waiting: 0 });
  });

  it("returns nulls, not zeros, when nothing was opened", () => {
    expect(responseWindow([], new Map(), core, "2026-10-01", "2026-10-08", at(1)).issue).toEqual({ opened: 0, medianHours: null, within2d: null, waiting: 0 });
  });
});

describe("responsePeriods", () => {
  it("groups by the week the item was opened, from the first item on", () => {
    const items = [item("issue", "carol", 0, { closed: 4 }), item("pr", "dave", 24 * 8, { merged: 24 * 8 + 2 })];
    const rows = responsePeriods(items, firstResponses(items, [], core), core, "week", "2026-01-01", "2026-10-12", at(24 * 12));
    expect(rows).toEqual([
      { start: "2026-09-28", issues: 1, issueMedianHours: 4, issueWithin2d: 1, prs: 0, prMedianHours: null, prWithin2d: null },
      { start: "2026-10-05", issues: 0, issueMedianHours: null, issueWithin2d: null, prs: 1, prMedianHours: 2, prWithin2d: 1 },
      { start: "2026-10-12", issues: 0, issueMedianHours: null, issueWithin2d: null, prs: 0, prMedianHours: null, prWithin2d: null },
    ]);
  });
});

describe("contributorFunnel", () => {
  it("follows outside people who first showed up in the window through everything they did after", () => {
    const items = [
      item("issue", "carol", 0), item("pr", "carol", 30, { merged: 40 }), item("pr", "carol", 50, { merged: 60 }),
      item("pr", "dave", 10, { closed: 20 }),
      item("issue", "erin", 5),
      item("pr", "frank", -24 * 30, { merged: -24 * 29 }), item("pr", "frank", 10, { merged: 12 }), // first showed up before the window
      item("pr", "kalin", 0, { merged: 1 }), // core
      item("pr", "gina", 1, { merged: 2, assoc: "NONE" }), item("pr", "gina", 3, { assoc: "COLLABORATOR" }), // core by her latest item
    ];
    expect(contributorFunnel(items, core, none, "2026-10-01", "2026-10-08", at(24 * 7))).toEqual({
      people: 3, openedPr: 2, merged: 1, mergedTwice: 1, issueFirst: 2, issueThenPr: 1,
    });
  });
});

describe("outsideConcentration", () => {
  it("ranks outside people by PRs merged in the window, with the top two's share and when each was last seen", () => {
    const items = [
      item("pr", "carol", 0, { merged: 1 }), item("pr", "carol", 2, { merged: 3 }), item("pr", "carol", 4, { merged: 5 }),
      item("pr", "dave", 0, { merged: 1 }), item("issue", "dave", 100),
      item("pr", "erin", 0, { merged: 1 }),
      item("pr", "kalin", 0, { merged: 1 }),
      item("pr", "frank", -24 * 30, { merged: -24 * 29 }), // merged before the window
    ];
    const c = outsideConcentration(items, core, none, "2026-10-01", "2026-10-08", at(200));
    expect(c.merged).toBe(5);
    expect(c.people).toBe(3);
    expect(c.topTwoShare).toBe(4 / 5);
    expect(c.top).toEqual([
      { author: "carol", merged: 3, lastSeen: at(4) },
      { author: "dave", merged: 1, lastSeen: at(100) },
      { author: "erin", merged: 1, lastSeen: at(0) },
    ]);
    expect(outsideConcentration([], core, none, "2026-10-01", "2026-10-08", at(1))).toEqual({ merged: 0, people: 0, topTwoShare: null, top: [] });
    // The top two are everyone, so there is no share to score.
    expect(outsideConcentration(items.slice(0, 4), core, none, "2026-10-01", "2026-10-08", at(200)).topTwoShare).toBeNull();
  });
});

describe("collectComments", () => {
  const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
  const env = (db: D1Database) => ({ DB: db, GITHUB_REPO: "amd/gaia", NPM_PACKAGE: "x", PYPI_PACKAGE: "y" });

  it("stores each person's earliest comment per item, leaves bots out, and never moves a first comment later", async () => {
    const db = memoryD1();
    const run = (issue: unknown[], review: unknown[]) => collectComments(env(db), async (u: string) => ok(u.includes("/issues/comments") ? issue : review));
    await run(
      [
        { user: { login: "kalin" }, author_association: "CONTRIBUTOR", created_at: at(5), issue_url: "https://api.github.com/repos/amd/gaia/issues/7" },
        { user: { login: "kalin" }, author_association: "CONTRIBUTOR", created_at: at(9), issue_url: "https://api.github.com/repos/amd/gaia/issues/7" },
        { user: { login: "helper", type: "Bot" }, author_association: "NONE", created_at: at(1), issue_url: "https://api.github.com/repos/amd/gaia/issues/7" },
        { user: { login: "github-actions[bot]" }, author_association: "NONE", created_at: at(1), issue_url: "https://api.github.com/repos/amd/gaia/issues/8" },
      ],
      [{ user: { login: "gina" }, author_association: "COLLABORATOR", created_at: at(2), pull_request_url: "https://api.github.com/repos/amd/gaia/pulls/8" }],
    );
    // A later run sees an edited later comment and an earlier one it had missed.
    await run([{ user: { login: "kalin" }, author_association: "MEMBER", created_at: at(20), issue_url: "https://api.github.com/repos/amd/gaia/issues/7" }],
      [{ user: { login: "gina" }, author_association: "COLLABORATOR", created_at: at(1), pull_request_url: "https://api.github.com/repos/amd/gaia/pulls/8" }]);
    const rows = (await db.prepare("SELECT number, login, assoc, at FROM item_responses ORDER BY number").all()).results;
    expect(rows).toEqual([
      { number: 7, login: "kalin", assoc: "CONTRIBUTOR", at: at(5) },
      { number: 8, login: "gina", assoc: "COLLABORATOR", at: at(1) },
    ]);
  });

  it("fails loudly on a comment that names no issue", async () => {
    const f = async (u: string) => ok(u.includes("/issues/comments") ? [{ user: { login: "kalin" }, author_association: "NONE", created_at: at(1) }] : []);
    await expect(collectComments(env(memoryD1()), f)).rejects.toThrow(/names no issue/);
  });
});
