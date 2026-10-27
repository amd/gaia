// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from "vitest";
import { ciStats, dailyDeltas, percentile, releaseDownloads, type RunRow } from "../src/stats";

const rel = (tag: string, at = "2026-10-01T00:00:00Z") => ({ tag, published_at: at, prerelease: 0, html_url: "" });

describe("releaseDownloads", () => {
  it("splits install files from updater metadata and by OS", () => {
    const out = releaseDownloads(
      [rel("v1.0.0")],
      [
        { tag: "v1.0.0", asset: "latest.yml", downloads: 100 },
        { tag: "v1.0.0", asset: "gaia-agent-ui-1.0.0-x64-setup.exe", downloads: 7 },
        { tag: "v1.0.0", asset: "gaia-agent-ui-1.0.0-amd64.deb", downloads: 3 },
      ],
    );
    expect(out[0]).toMatchObject({ install: 10, meta: 100, byOs: { Windows: 7, Linux: 3 } });
  });

  it("ignores assets whose release is unknown and marks RCs", () => {
    const out = releaseDownloads([rel("v1.0.0-rc1")], [{ tag: "ghost", asset: "x.exe", downloads: 5 }]);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ rc: true, main: true, install: 0 });
  });

  it("never counts a sub-package tag as the main line", () => {
    expect(releaseDownloads([rel("agent-pkg-email-v0.5.0")], [])[0]!.main).toBe(false);
  });
});

describe("dailyDeltas", () => {
  const row = (day: string, downloads: number, asset = "a.exe") => ({ day, tag: "v1", asset, downloads });

  it("computes day-over-day change", () => {
    const { points } = dailyDeltas([row("2026-10-01", 10), row("2026-10-02", 25), row("2026-10-03", 26)]);
    expect(points.map((p) => p.install)).toEqual([15, 1]);
  });

  it("separates update-feed polls from installs", () => {
    const { points } = dailyDeltas([
      row("2026-10-01", 10),
      row("2026-10-01", 100, "latest.yml"),
      row("2026-10-02", 12),
      row("2026-10-02", 140, "latest.yml"),
    ]);
    expect(points[0]).toMatchObject({ install: 2, meta: 40 });
  });

  it("drops a delta that spans a missed day instead of spreading it", () => {
    const { points } = dailyDeltas([row("2026-10-01", 10), row("2026-10-03", 40)]);
    expect(points).toEqual([]);
  });

  it("flags a decrease (deleted asset) and does not clip it into the total", () => {
    const { points, negativeDays } = dailyDeltas([
      row("2026-10-01", 50),
      row("2026-10-01", 9, "b.exe"),
      row("2026-10-02", 40),
    ]);
    expect(negativeDays).toEqual(["2026-10-02"]);
    expect(points[0]!.install).toBe(0);
  });

  it("returns nothing for a single snapshot day", () => {
    expect(dailyDeltas([row("2026-10-01", 10)]).points).toEqual([]);
  });
});

describe("percentile", () => {
  it("handles empty and small lists", () => {
    expect(percentile([], 50)).toBeNull();
    expect(percentile([5], 95)).toBe(5);
    expect(percentile([1, 2, 3, 4], 50)).toBe(2);
    expect(percentile([1, 2, 3, 4], 95)).toBe(4);
  });
});

describe("ciStats", () => {
  let id = 0;
  const run = (over: Partial<RunRow>): RunRow => ({
    id: ++id,
    workflow: "Unit",
    branch: "main",
    event: "push",
    status: "completed",
    conclusion: "success",
    head_sha: "aaa",
    run_attempt: 1,
    created_at: "2026-10-20T10:00:00Z",
    duration_s: 60,
    ...over,
  });

  it("ignores cancelled and skipped runs in the pass rate", () => {
    const { workflows } = ciStats([
      run({ conclusion: "success" }),
      run({ conclusion: "failure", head_sha: "bbb" }),
      run({ conclusion: "cancelled", head_sha: "ccc" }),
      run({ conclusion: "skipped", head_sha: "ddd" }),
    ]);
    expect(workflows[0]).toMatchObject({ runs: 4, passed: 1, failed: 1, passRate: 0.5 });
  });

  it("reports no pass rate (not 0%) when nothing was decided", () => {
    const { workflows } = ciStats([run({ conclusion: "cancelled" })]);
    expect(workflows[0]!.passRate).toBeNull();
  });

  it("does not call a commit flaky when it passed first and failed later", () => {
    const { workflows, flakyCommits } = ciStats([
      run({ conclusion: "success", head_sha: "z", created_at: "2026-10-20T10:00:00Z" }),
      run({ conclusion: "failure", head_sha: "z", created_at: "2026-10-20T11:00:00Z" }),
    ]);
    expect(workflows[0]!.flaky).toBe(0);
    expect(flakyCommits).toBe(0);
  });

  it("counts a commit flaky in several workflows once in flakyCommits", () => {
    const fail = (w: string) => run({ workflow: w, conclusion: "failure", head_sha: "q", created_at: "2026-10-20T10:00:00Z" });
    const pass = (w: string) => run({ workflow: w, conclusion: "success", head_sha: "q", created_at: "2026-10-20T11:00:00Z" });
    const out = ciStats([fail("A"), pass("A"), fail("B"), pass("B"), fail("C"), pass("C")]);
    expect(out.workflows.map((w) => w.flaky)).toEqual([1, 1, 1]);
    expect(out).toMatchObject({ flakyCommits: 1, commits: 1 });
  });

  it("does not call a pass on a re-run attempt flaky by itself (the earlier attempt may have been cancelled)", () => {
    const { workflows } = ciStats([run({ head_sha: "r", run_attempt: 2 }), run({ head_sha: "s", run_attempt: 1 })]);
    expect(workflows[0]!.flaky).toBe(0);
  });

  it("leaves runs that waited longer than the limit out of runtimes and totals, and counts them", () => {
    const { workflows } = ciStats([run({ duration_s: 100, head_sha: "1" }), run({ duration_s: 200, head_sha: "2" }), run({ duration_s: 448714, head_sha: "3" })]);
    expect(workflows[0]).toMatchObject({ p90: 200, totalSeconds: 300, longRuns: 1 });
  });

  it("counts a commit as flaky when it both failed and passed", () => {
    const { workflows } = ciStats([
      run({ conclusion: "failure", head_sha: "x" }),
      run({ conclusion: "success", head_sha: "x", run_attempt: 2 }),
      run({ conclusion: "success", head_sha: "y" }),
    ]);
    expect(workflows[0]!.flaky).toBe(1);
  });

  it("computes durations from successful runs only", () => {
    const { workflows } = ciStats([
      run({ duration_s: 10, head_sha: "1" }),
      run({ duration_s: 30, head_sha: "2" }),
      run({ conclusion: "failure", duration_s: 9999, head_sha: "3" }),
    ]);
    expect(workflows[0]).toMatchObject({ p50: 10, p90: 30, totalSeconds: 10039 });
  });

  it("sorts the most-failing workflow first", () => {
    const { workflows } = ciStats([
      run({ workflow: "A" }),
      run({ workflow: "B", conclusion: "failure" }),
      run({ workflow: "B", conclusion: "failure", head_sha: "z" }),
    ]);
    expect(workflows.map((w) => w.workflow)).toEqual(["B", "A"]);
  });
});
