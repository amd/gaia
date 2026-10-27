// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from "vitest";
import { activity, busFactor, downloadPeriods, downloadWindows, growthPeriods, growthWindow, isExternal, newcomerRetention, per30, periodStart, type DownloadDay } from "../src/growth";
import type { CommitRow, ItemRow } from "../src/momentum";

const c = (author: string, day: string): CommitRow => ({ author, day, at: day + "T12:00:00Z" });
const pr = (author: string, created: string, merged: string | null, assoc = "CONTRIBUTOR"): ItemRow => ({
  kind: "pr", author, assoc, created_at: created + "T10:00:00Z", closed_at: merged && merged + "T11:00:00Z", merged_at: merged && merged + "T11:00:00Z",
});
const none = new Set<string>();

describe("periodStart", () => {
  it("starts weeks on Monday and months on the 1st, in UTC", () => {
    expect(periodStart("2026-10-25", "week")).toBe("2026-10-19"); // a Sunday
    expect(periodStart("2026-10-19", "week")).toBe("2026-10-19");
    expect(periodStart("2026-10-25", "month")).toBe("2026-10-01");
  });
});

describe("activity", () => {
  it("counts people only: bots and commits without a GitHub account are left out", () => {
    const a = activity([pr("dependabot[bot]", "2026-01-02", null), pr("Alice", "2026-01-02", null)], [c("(no GitHub account)", "2026-01-01"), c("claude", "2026-01-01"), c("bob", "2026-01-01")]);
    expect(a.map((x) => x.who).sort()).toEqual(["alice", "bob"]);
  });
});

describe("isExternal", () => {
  it("treats maintainers and org members as core, whatever GitHub's label says", () => {
    expect(isExternal(pr("alice", "2026-01-01", null, "CONTRIBUTOR"), new Set(["alice"]))).toBe(false);
    expect(isExternal(pr("bob", "2026-01-01", null, "MEMBER"), none)).toBe(false);
    expect(isExternal(pr("carol", "2026-01-01", null, "FIRST_TIME_CONTRIBUTOR"), none)).toBe(true);
    expect(isExternal(pr("dependabot[bot]", "2026-01-01", null, "NONE"), none)).toBe(false);
  });
});

describe("growthPeriods", () => {
  const stars = [{ day: "2026-01-05", n: 100 }, { day: "2026-02-10", n: 50 }, { day: "2026-03-03", n: 30 }];
  const forks = [{ day: "2026-02-01", n: 4 }];
  const commits = [c("alice", "2026-01-06"), c("bob", "2026-02-02"), c("alice", "2026-02-03"), c("carol", "2026-03-04")];
  const items = [pr("carol", "2026-03-01", "2026-03-02"), pr("alice", "2026-03-01", "2026-03-05", "MEMBER")];
  const p = growthPeriods(stars, forks, items, commits, none, "month", "2026-03-20");

  it("has one row per month through today, with no gap months skipped", () => {
    expect(p.map((x) => x.start)).toEqual(["2026-01-01", "2026-02-01", "2026-03-01"]);
  });

  it("measures growth against the total at the start of the period, and gives none for the first", () => {
    expect(p[0]).toMatchObject({ stars: 100, starsTotal: 100, starGrowth: null });
    expect(p[1]).toMatchObject({ stars: 50, starsTotal: 150, starGrowth: 0.5, forks: 4, forkGrowth: null });
    expect(p[2]!.starGrowth).toBeCloseTo(30 / 150);
  });

  it("counts active and new contributors, and leaves newcomer retention blank until 90 days have passed", () => {
    expect(p[0]).toMatchObject({ active: 1, newContributors: 1 });
    expect(p[1]).toMatchObject({ active: 2, newContributors: 1 });
    expect(p[2]).toMatchObject({ active: 2, newContributors: 1, newcomerCohort: null, newcomerReturned: null });
  });

  it("splits merged pull requests into core and external", () => {
    expect(p[2]).toMatchObject({ prsMerged: 2, externalMerged: 1 });
  });

  it("returns nothing with no data at all", () => {
    expect(growthPeriods([], [], [], [], none, "week", "2026-03-20")).toEqual([]);
  });
});

describe("busFactor", () => {
  it("is the fewest people who made at least half the commits", () => {
    expect(busFactor([c("a", "d"), c("a", "d"), c("b", "d"), c("c", "d")])).toBe(1);
    expect(busFactor([c("a", "d"), c("b", "d"), c("c", "d"), c("d", "d")])).toBe(2);
    expect(busFactor([c("dependabot[bot]", "d")])).toBeNull();
  });
});

describe("growthWindow", () => {
  it("counts newcomers, external share of human merges and bus factor in the window", () => {
    const commits = [c("alice", "2026-02-25"), c("bob", "2026-02-26"), c("alice", "2026-03-05")];
    const items = [pr("dave", "2026-03-02", "2026-03-03", "NONE"), pr("alice", "2026-03-02", "2026-03-04", "CONTRIBUTOR"), pr("dependabot[bot]", "2026-03-02", "2026-03-04", "NONE")];
    const w = growthWindow([{ day: "2026-02-01", n: 200 }, { day: "2026-03-02", n: 20 }], [], items, commits, new Set(["alice"]), "2026-03-01", "2026-03-08");
    expect(w).toMatchObject({ stars: 20, starGrowth: 0.1, active: 2, newContributors: 1, prsMerged: 2, externalShare: 0.5, busFactor: 1 });
  });
});

describe("newcomerRetention", () => {
  const prs = (who: string, ...days: string[]) => days.map((day) => ({ who, day }));
  it("counts a newcomer who opened another PR on a later day within 90 days", () => {
    const acts = [...prs("a", "2026-01-05", "2026-02-01"), ...prs("b", "2026-01-10", "2026-01-10"), ...prs("c", "2026-01-20", "2026-06-01"), ...prs("old", "2025-06-01", "2026-01-12")];
    expect(newcomerRetention(acts, "2026-01-01", "2026-02-01", "2026-06-30")).toEqual({ cohort: 3, returned: 1, rate: 1 / 3 });
  });
  it("gives no rate until every newcomer in the period has had 90 days", () => {
    expect(newcomerRetention(prs("a", "2026-01-05"), "2026-01-01", "2026-02-01", "2026-04-15").rate).toBeNull();
    expect(newcomerRetention(prs("a", "2026-01-05"), "2026-01-01", "2026-02-01", "2026-05-01").rate).toBe(0);
  });
});

describe("growthPeriods newcomer cohorts", () => {
  it("does not count a newcomer's own PR landing as a commit later as a return", () => {
    const p = growthPeriods([], [], [pr("dave", "2026-01-05", "2026-01-09")], [c("dave", "2026-01-09")], none, "month", "2026-06-30");
    expect(p[0]).toMatchObject({ newcomerCohort: 1, newcomerReturned: 0 });
  });
});

describe("per30", () => {
  it("compounds a window's growth to 30 days", () => {
    expect(per30(0.1, 30)).toBeCloseTo(0.1);
    expect(per30(0.21, 60)).toBeCloseTo(0.1);
    expect(per30(null, 30)).toBeNull();
  });
});

describe("downloadWindows", () => {
  const days = (from: string, n: number, f: (i: number) => Partial<DownloadDay>) =>
    Array.from({ length: n }, (_, i) => {
      const day = new Date(Date.parse(from) + i * 86_400_000).toISOString().slice(0, 10);
      return { day, installs: null, pypi: null, npm: null, ...f(i) };
    });

  it("ends each source's windows on its last reported day, so a registry's lag is not read as a drop", () => {
    // PyPI reported through Jan 28: the 7-day windows are Jan 22–28 and Jan 15–21.
    const d = days("2026-01-01", 28, (i) => ({ pypi: i < 21 ? 10 : 20 }));
    const w = downloadWindows(d, 7, "2026-01-31");
    expect(w.pypi).toEqual({ total: 140, previous: 70, growth: 1, through: "2026-01-28" });
  });

  it("gives no comparison until the source covers the whole earlier window", () => {
    const d = days("2026-01-20", 9, () => ({ installs: 5 }));
    expect(downloadWindows(d, 7, "2026-01-29").installs).toMatchObject({ total: 35, previous: null, growth: null });
    expect(downloadWindows(d, 7, "2026-01-29", { installs: "2026-01-01" }).installs.previous).toBe(10);
    expect(downloadWindows([], 7, "2026-01-29").npm).toEqual({ total: 0, previous: null, growth: null, through: null });
  });
});

describe("downloadPeriods", () => {
  it("sums each source per period and leaves a source with no data in a period blank, not zero", () => {
    const d: DownloadDay[] = [
      { day: "2026-01-05", installs: null, pypi: 3, npm: 0 },
      { day: "2026-01-06", installs: 4, pypi: 2, npm: null },
    ];
    expect(downloadPeriods(d, "week", "", "2026-01-12")).toEqual([
      { start: "2026-01-05", installs: 4, pypi: 5, npm: 0 },
      { start: "2026-01-12", installs: null, pypi: null, npm: null },
    ]);
  });
});
