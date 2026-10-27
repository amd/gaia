// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from "vitest";
import { cumulativeDaily, median, momentumDaily, packageDaily, rangeSummary, type ItemRow } from "../src/momentum";

const issue = (created: string, closed: string | null = null): ItemRow => ({
  kind: "issue", created_at: created, closed_at: closed, merged_at: null, assoc: "NONE",
});
const pr = (created: string, merged: string | null, closed: string | null = merged, assoc = "MEMBER"): ItemRow => ({
  kind: "pr", created_at: created, closed_at: closed, merged_at: merged, assoc,
});

describe("median", () => {
  it("handles empty, odd and even lists", () => {
    expect(median([])).toBeNull();
    expect(median([3, 1, 2])).toBe(2);
    expect(median([1, 2, 3, 4])).toBe(2.5);
  });
});

describe("cumulativeDaily", () => {
  it("fills days with no new stars as zero and keeps a running total", () => {
    expect(cumulativeDaily([{ day: "2026-10-01", n: 2 }, { day: "2026-10-03", n: 1 }], "2026-10-04")).toEqual([
      { day: "2026-10-01", added: 2, total: 2 },
      { day: "2026-10-02", added: 0, total: 2 },
      { day: "2026-10-03", added: 1, total: 3 },
      { day: "2026-10-04", added: 0, total: 3 },
    ]);
  });
});

describe("packageDaily", () => {
  it("marks a source null before its data starts instead of zero", () => {
    const out = packageDaily(
      [
        { source: "pypi", day: "2026-10-01", downloads: 5 },
        { source: "npm", day: "2026-10-02", downloads: 1 },
      ],
      "2026-10-02",
    );
    expect(out).toEqual([
      { day: "2026-10-01", npm: null, pypi: 5 },
      { day: "2026-10-02", npm: 1, pypi: null },
    ]);
  });
});

describe("momentumDaily", () => {
  const items = [
    issue("2026-10-01T10:00:00Z", "2026-10-03T10:00:00Z"),
    issue("2026-10-02T10:00:00Z"),
    pr("2026-10-01T09:00:00Z", "2026-10-02T09:00:00Z"),
    pr("2026-10-02T09:00:00Z", null, "2026-10-03T09:00:00Z", "NONE"),
    pr("2026-10-03T09:00:00Z", null, null, "CONTRIBUTOR"),
  ];
  const commits = [
    { day: "2026-10-01", at: null, author: "a" },
    { day: "2026-10-02", at: null, author: "b" },
    { day: "2026-10-03", at: null, author: "a" },
  ];
  const out = momentumDaily(items, commits, "2026-10-03");

  it("counts flows on the day they happened", () => {
    expect(out.map((d) => [d.issuesOpened, d.issuesClosed, d.prsOpened, d.prsMerged, d.prsClosedUnmerged])).toEqual([
      [1, 0, 1, 0, 0],
      [1, 0, 1, 1, 0],
      [0, 1, 1, 0, 1],
    ]);
  });

  it("tracks open backlogs, counting unmerged closes as leaving the PR backlog", () => {
    expect(out.map((d) => [d.openIssues, d.openPrs])).toEqual([[1, 1], [2, 1], [1, 1]]);
  });

  it("counts new and active contributors", () => {
    expect(out.map((d) => d.newContributors)).toEqual([1, 1, 0]);
    expect(out.map((d) => d.activeContributors28d)).toEqual([1, 2, 2]);
    expect(out.at(-1)!.totalContributors).toBe(2);
  });

  it("returns nothing when there is no data", () => {
    expect(momentumDaily([], [], "2026-10-03")).toEqual([]);
  });
});

describe("rangeSummary", () => {
  it("counts by exact timestamp, so a 24-hour window is not a whole day bucket", () => {
    const items = [pr("2026-10-24T00:00:00Z", "2026-10-26T11:00:00Z"), pr("2026-10-20T00:00:00Z", "2026-10-25T09:00:00Z")];
    const s = rangeSummary(items, [], "2026-10-25T12:00:00Z");
    expect(s.prsMerged).toBe(1);
    expect(s.mergeDurations[0]).toBeCloseTo(2.458, 2);
  });

  it("gives open counts for now and medians only over closures in range", () => {
    const items = [issue("2026-10-01T00:00:00Z", "2026-10-03T00:00:00Z"), issue("2026-10-02T00:00:00Z"), pr("2026-10-01T00:00:00Z", null, null)];
    const s = rangeSummary(items, [], "2026-10-02T00:00:00Z");
    expect(s).toMatchObject({ issuesOpened: 1, issuesClosed: 1, openIssues: 1, openPrs: 1, medianDaysToClose: 2, medianDaysToMerge: null });
  });

  it("uses commit timestamps when present and falls back to the day", () => {
    const s = rangeSummary(
      [],
      [
        { day: "2026-10-25", at: "2026-10-25T08:00:00Z", author: "a" },
        { day: "2026-10-25", at: "2026-10-25T20:00:00Z", author: "b" },
        { day: "2026-10-26", at: null, author: "b" },
      ],
      "2026-10-25T12:00:00Z",
    );
    expect(s).toMatchObject({ commits: 2, contributors: 1 });
  });
});

describe("people filters and contributors", () => {
  it("recognises bots and filters excluded accounts case-insensitively", async () => {
    const { isBot, keepAuthor, parsePeople } = await import("../src/momentum");
    expect(["dependabot[bot]", "github-actions", "claude"].every(isBot)).toBe(true);
    expect(isBot("kovtcharov")).toBe(false);
    const keep = keepAuthor(parsePeople(new URLSearchParams("exclude=Kovtcharov,kovtcharov-amd&bots=0")));
    expect(["kovtcharov", "KOVTCHAROV-AMD", "dependabot[bot]"].map(keep)).toEqual([false, false, false]);
    expect(keep("itomek")).toBe(true);
  });

  it("totals each person's commits, pull requests, issues and lines in the range", async () => {
    const { contributors } = await import("../src/momentum");
    const items = [
      { ...pr("2026-10-20T00:00:00Z", "2026-10-21T00:00:00Z"), author: "ann" },
      { ...pr("2026-09-01T00:00:00Z", "2026-10-22T00:00:00Z"), author: "ann" },
      { ...issue("2026-10-23T00:00:00Z"), author: "bob" },
    ];
    const commits = [{ day: "2026-10-20", at: "2026-10-20T10:00:00Z", author: "ann" }, { day: "2026-09-01", at: "2026-09-01T00:00:00Z", author: "ann" }];
    const weekly = [
      { author: "ann", week: "2026-10-19", commits: 1, additions: 100, deletions: 10 },
      { author: "ann", week: "2026-10-12", commits: 1, additions: 5, deletions: 5 },
      { author: "ann", week: "2026-08-31", commits: 1, additions: 999, deletions: 0 },
    ];
    const out = contributors(items, commits, weekly, "2026-10-15T00:00:00Z", new Map([["ann", "2026-09-01"]]));
    expect(out.find((r) => r.author === "ann")).toMatchObject({ commits: 1, prsOpened: 1, prsMerged: 2, additions: 105, deletions: 15, firstCommit: "2026-09-01" });
    expect(out.find((r) => r.author === "bob")).toMatchObject({ issuesOpened: 1, commits: 0 });
  });

  it("gives P90 alongside the median and counts stale open PRs", () => {
    const now = Date.now();
    const iso = (daysAgo: number) => new Date(now - daysAgo * 864e5).toISOString();
    const items = [pr(iso(30), null, null), pr(iso(3), null, null), ...[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((d) => pr(iso(20), iso(20 - d)))];
    const s = rangeSummary(items, [], iso(25));
    expect(s.p90DaysToMerge).toBeCloseTo(9, 0);
    expect(s.medianDaysToMerge).toBeCloseTo(5.5, 0);
    expect(s).toMatchObject({ openPrs: 2, staleOpenPrs: 1 });
  });

  it("can summarise an earlier window, with open counts as of its end", () => {
    const items = [issue("2026-10-01T00:00:00Z", "2026-10-10T00:00:00Z"), issue("2026-10-02T00:00:00Z")];
    const prev = rangeSummary(items, [], "2026-09-25T00:00:00Z", "2026-10-05T00:00:00Z");
    expect(prev).toMatchObject({ issuesOpened: 2, issuesClosed: 0, openIssues: 2 });
  });
});
