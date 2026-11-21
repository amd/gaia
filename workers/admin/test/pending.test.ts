// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from "vitest";
import type { ItemRow } from "../src/momentum";
import { pendingContributors } from "../src/pending";

const item = (kind: "pr" | "issue", author: string | null, opened: string, closed: string | null = null, assoc = "NONE"): ItemRow => ({
  kind, author, assoc, created_at: opened + "T10:00:00Z", closed_at: closed && closed + "T11:00:00Z", merged_at: null,
});
const UNTIL = "2026-10-10T12:00:00Z";
const run = (items: ItemRow[], core: string[][] = []) =>
  pendingContributors(
    items,
    new Set(core.flat().map((x) => x.toLowerCase())),
    new Map(core.flatMap((g) => g.map((l) => [l.toLowerCase(), g[0]!] as [string, string]))),
    UNTIL,
    "2026-10-10",
  );

describe("pendingContributors", () => {
  it("lists each person with something open once, with their counts and longest waits", () => {
    const { people } = run([
      item("pr", "dana", "2026-10-05"),
      item("pr", "dana", "2026-10-02"),
      item("issue", "dana", "2026-09-20"),
      item("issue", "erin", "2026-10-01"),
      item("pr", "frank", "2026-10-01", "2026-10-03"),
    ]);
    expect(people).toEqual([
      { author: "dana", accounts: ["dana"], core: false, openPrs: 2, openIssues: 1, oldestPr: "2026-10-02T10:00:00Z", oldestIssue: "2026-09-20T10:00:00Z" },
      { author: "erin", accounts: ["erin"], core: false, openPrs: 0, openIssues: 1, oldestPr: null, oldestIssue: "2026-10-01T10:00:00Z" },
    ]);
  });

  it("leaves out draft pull requests: they are work in progress", () => {
    const { people, daily } = run([
      { ...item("pr", "kim", "2026-10-01"), draft: 1 },
      { ...item("pr", "lee", "2026-10-01"), draft: 1 },
      item("issue", "lee", "2026-10-03"),
      { ...item("pr", "max", "2026-10-02"), draft: 0 },
    ]);
    expect(people.map((p) => [p.author, p.openPrs, p.openIssues])).toEqual([
      ["max", 1, 0],
      ["lee", 0, 1],
    ]);
    expect(daily.at(-1)).toMatchObject({ outsidePr: 1, outsideIssue: 1 });
    expect(daily[0]!.day).toBe("2026-10-02");
  });

  it("leaves out bots and items with no author", () => {
    const { people, daily } = run([item("pr", "dependabot[bot]", "2026-10-01"), item("issue", "github-actions", "2026-10-01"), item("issue", null, "2026-10-01")]);
    expect(people).toEqual([]);
    expect(daily).toEqual([]);
  });

  it("puts people with an open pull request first, longest wait first", () => {
    const { people } = run([item("issue", "old-issue", "2026-01-01"), item("pr", "new-pr", "2026-10-09"), item("pr", "old-pr", "2026-09-01")]);
    expect(people.map((p) => p.author)).toEqual(["old-pr", "new-pr", "old-issue"]);
  });

  it("counts a maintainer's accounts as one core person, and org members as core", () => {
    const { people } = run(
      [item("pr", "alice", "2026-10-01"), item("issue", "Alice-Work", "2026-10-02"), item("issue", "gina", "2026-10-03", null, "MEMBER"), item("issue", "hal", "2026-10-04")],
      [["alice", "alice-work"]],
    );
    expect(people.map((p) => [p.author, p.core, p.accounts])).toEqual([
      ["alice", true, ["alice", "Alice-Work"]],
      ["gina", true, ["gina"]],
      ["hal", false, ["hal"]],
    ]);
  });

  it("places each person by their most recent item, for their whole history", () => {
    // A past collaborator who now sends pull requests from outside is waiting as an outsider.
    const was = run([item("issue", "ivy", "2026-09-01", "2026-09-02", "COLLABORATOR"), item("pr", "ivy", "2026-10-01")]);
    expect(was.people[0]).toMatchObject({ author: "ivy", core: false });
    expect(was.daily.at(-1)).toMatchObject({ outsidePr: 1, corePr: 0 });
    // Someone who has since joined counts as core, back to their first item.
    const now = run([item("pr", "jo", "2026-09-01"), item("issue", "jo", "2026-10-01", null, "MEMBER")]);
    expect(now.people[0]).toMatchObject({ author: "jo", core: true });
    expect(now.daily.find((d) => d.day === "2026-09-01")).toMatchObject({ outsidePr: 0, corePr: 1 });
  });

  it("counts people open at the end of each day, a pull request outranking issues", () => {
    const { daily } = run([
      item("issue", "dana", "2026-10-06"),
      item("pr", "dana", "2026-10-07", "2026-10-09"),
      item("issue", "erin", "2026-10-07", "2026-10-08"),
      item("pr", "gina", "2026-10-08", null, "MEMBER"),
    ]);
    const pick = (d: (typeof daily)[number]) => [d.day.slice(8), d.outsidePr, d.outsideIssue, d.corePr, d.coreIssue];
    expect(daily.map(pick)).toEqual([
      ["06", 0, 1, 0, 0],
      ["07", 1, 1, 0, 0],
      ["08", 1, 0, 1, 0],
      ["09", 0, 1, 1, 0],
      ["10", 0, 1, 1, 0],
    ]);
  });

  it("ends the history on the same count as the list of people", () => {
    const { people, daily } = run([item("pr", "dana", "2026-10-05"), item("issue", "erin", "2026-10-01"), item("issue", "hal", "2026-10-02", "2026-10-10")]);
    const last = daily.at(-1)!;
    expect(last.outsidePr + last.outsideIssue).toBe(people.length);
  });

  it("ignores anything opened or closed after the data was collected", () => {
    const { people } = run([
      { ...item("pr", "late", "2026-10-10"), created_at: "2026-10-10T13:00:00Z" },
      { ...item("issue", "dana", "2026-10-01"), closed_at: "2026-10-10T13:00:00Z" },
    ]);
    expect(people.map((p) => p.author)).toEqual(["dana"]);
  });
});
