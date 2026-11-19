/** What the pages' own scripts draw from given data, run in Node (see test/client.ts). */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderGrowthPage } from "../src/growthpage";
import { renderCiPage, renderDevelopersPage, renderUsersPage } from "../src/page";
import { runPage } from "./client";

const NOW = new Date("2026-11-18T23:00:00Z");
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => vi.useRealTimers());

const DAY = 86_400_000;
const day = (daysAgo: number) => new Date(NOW.getTime() - daysAgo * DAY).toISOString().slice(0, 10);
const status = (sources = ["issues", "commits", "runs", "releases"]) => ({
  log: sources.map((source) => ({ finished_at: "2026-11-18T22:49:00.000Z", source, ok: 1, detail: "ok" })),
});
const team = { people: [["alice", "alice-work"], ["bob"]], source: "deploy", updatedAt: null, updatedBy: null };

describe("Developers page", () => {
  const summary = (openIssues: number) => ({
    prsOpened: 0, prsMerged: 0, issuesOpened: 0, issuesClosed: 0, openIssues, openPrs: 0, staleOpenPrs: 0, commits: 0, contributors: 0,
    medianDaysToMerge: null, p90DaysToMerge: null, medianDaysToClose: null, p90DaysToClose: null, mergeDurations: [], closeDurations: [],
  });
  // Many issues filed on the window's first day: the line starts above where the window began.
  const openByDay = [434, 474, 572, 580, 548, 493, 502, 501, 498];
  const momentum = () => ({
    window: { since: "2026-11-11T22:49:00.998Z", until: "2026-11-18T22:49:00.998Z" },
    summary: summary(498),
    previous: summary(423),
    daily: openByDay.map((openIssues, i) => ({ day: day(openByDay.length - 1 - i), openIssues, openPrs: 0, issuesOpened: 0, issuesClosed: 0, prsOpened: 0, prsMerged: 0, commits: 0, activeContributors28d: 0, newContributors: 0 })),
    contributors: [{ author: "alice", accounts: ["alice", "alice-work"], bot: false, commits: 3, prsOpened: 0, prsMerged: 0, issuesOpened: 0, additions: 0, deletions: 0, firstCommit: "2026-01-01" }],
    loc: [],
    split: null,
    botOpenIssues: 0,
  });
  const page = () => {
    const p = runPage(renderDevelopersPage(team), "?range=7");
    const D = p<Record<string, unknown>>("D");
    D.momentum = momentum();
    D.status = status();
    return p;
  };

  it("does not colour or raise an alarm from the core/outside table on fewer than 10 merged PRs", () => {
    const p = page();
    const side = (n: number, med: number) => ({ ...summary(0), prsMerged: n, medianDaysToMerge: med, p90DaysToMerge: med * 3 });
    p<Record<string, { split: unknown }>>("D").momentum!.split = { core: side(8, 24), outside: side(1, 11.4), corePrevious: null, outsidePrevious: null };
    p("drawSplit()");
    expect(p.el("split").innerHTML).not.toMatch(/class="[^"]*\b(bad|warnc|good)\b/);
    expect(p<{ key: string }[]>("ATTN").some((a) => a.key === "split")).toBe(false);
  });

  it("draws the open-issue count at the window's start, which the Open issues card counts from", () => {
    const p = page();
    p("drawKpis()");
    p("drawCharts()");
    expect(p.el("ov").innerHTML).toContain("+75 since Nov 11 (423 then)");
    const spec = p<{ target: number; ymin: number }>("SPECS.backlog");
    expect(spec.target).toBe(423);
    expect(spec.ymin).toBeLessThanOrEqual(423);
    expect(p.el("backlog").innerHTML).toContain("open when the range began: 423");
  });

  it("labels the active-contributor line as a 28-day rolling count", () => {
    const p = page();
    p("drawCharts()");
    expect(p<{ series: { name: string }[] }>("SPECS.people").series[0]!.name).toMatch(/rolling 28-day/);
  });

  it("asks for contributors with the core team's + groups, and shows a person's accounts in one row", () => {
    const p = page();
    expect(p<string[][]>("jobs()")[0]![1]).toContain("core=" + encodeURIComponent("alice+alice-work,bob"));
    p("drawPeople()");
    const who = p.el("who").innerHTML;
    expect(who.match(/<tr/g)).toHaveLength(2); // header and one person
    expect(who).toContain('data-person="alice,alice-work"');
  });

  it("shows why issue, PR and commit figures are missing instead of zero cards when they were never collected", () => {
    const issues = "Issues and pull requests have not been collected yet: GET https://api.github.com/repos/o/r/issues returned HTTP 403.";
    const commits = "Commits have not been collected yet. Run the collector (see README).";
    const p = runPage(renderDevelopersPage(team), "?range=7");
    const blank = (o: Record<string, unknown>) => Object.fromEntries(Object.keys(o).map((k) => [k, Array.isArray(o[k]) ? [] : null]));
    const m = momentum();
    Object.assign(p<Record<string, unknown>>("D"), {
      status: status(["runs", "releases"]),
      momentum: { ...m, summary: blank(m.summary), previous: blank(m.previous), split: { empty: true, reason: issues }, botOpenIssues: null,
        daily: m.daily.map((d) => ({ ...blank(d), day: d.day })), notCollected: { issues: { empty: true, reason: issues }, commits: { empty: true, reason: commits } } },
    });
    p("drawKpis()");
    p("drawCharts()");
    const ov = p.el("ov").innerHTML;
    expect(ov).toContain(issues);
    expect(ov).toContain(commits);
    expect(ov).not.toContain('id="k-k-merged"');
    expect(ov).not.toMatch(/class="kv num[^"]*">0</);
    expect(p.el("split").innerHTML).toContain(issues);
    for (const id of ["issues", "backlog", "prs", "pr-backlog", "ttm-h", "ttc-h"]) expect(p.el(id).innerHTML).toContain(issues);
    for (const id of ["commits", "people"]) expect(p.el(id).innerHTML).toContain(commits);
    expect(p<{ key: string }[]>("ATTN")).toEqual([]);
  });

  it("hides the core-team editor's empty card until it is opened", () => {
    expect(renderDevelopersPage(team)).toContain(".team[hidden]{display:none}");
  });
});

describe("Users page", () => {
  const rel = (tag: string, daysAgo: number, rc: boolean) => ({
    tag, main: true, rc, publishedAt: new Date(NOW.getTime() - daysAgo * DAY).toISOString(), install: 10, meta: 0, byOs: { Windows: 10 }, byProduct: {},
  });
  const users = (range: string) => {
    const p = runPage(renderUsersPage(), `?range=${range}`);
    p<Record<string, unknown>>("D").status = status();
    return p;
  };

  it("shows release candidates per release without colouring it", () => {
    const p = users("30");
    const releases = [rel("v1.0.0-rc1", 20, true), ...["rc2", "rc3", "rc4", "rc5", "rc6", "rc7", "rc8"].map((r, i) => rel(`v1.0.0-${r}`, 19 - i, true)), rel("v1.0.0", 5, false)];
    Object.assign(p<Record<string, unknown>>("D"), { downloads: { releases, daily: [], negativeDays: [], snapshotDay: day(0), firstSnapshotDay: day(40) }, traction: null });
    p("drawOverview()");
    expect(p.el("ov").innerHTML).toContain("8 per release");
    expect(p.el("ov").innerHTML).not.toMatch(/id="k-rels"[^>]*lv-|kpi lv-\w+" id="k-rels"/);
    expect(p<{ key: string }[]>("ATTN").map((a) => a.key)).not.toContain("rels");
  });

  // 40 quiet days, then one spike day: a 7-day window reads it as a huge drop afterwards.
  const packages = Array.from({ length: 70 }, (_, i) => ({ day: day(70 - i), npm: i === 60 ? 50_000 : 100, pypi: null, crates: null }));
  const withPackages = (range: string) => {
    const p = users(range);
    Object.assign(p<Record<string, unknown>>("D"), { downloads: { empty: true, reason: "none" }, traction: { packages, stars: [], forks: [] } });
    p("drawOverview()");
    return p;
  };

  it("colours the package-download trend only over 28 days or more", () => {
    expect(withPackages("7")<{ key: string }[]>("ATTN").map((a) => a.key)).not.toContain("pkg");
    expect(withPackages("30")<{ key: string }[]>("ATTN").map((a) => a.key)).toContain("pkg");
  });

  it("says where coverage starts without claiming collection began there", () => {
    const p = users("30");
    const note = p<string>("coverNote({first:'2026-11-04',start:'2026-10-20'},'traffic')");
    expect(note).toBe("No traffic before Nov 4, 2026 (the earliest day collected).");
    Object.assign(p<Record<string, unknown>>("D"), { downloads: { releases: [], daily: [{ day: day(3), install: 5, meta: 0 }], negativeDays: [], firstSnapshotDay: day(4) } });
    p("drawDaily()");
    expect(p<{ cover: string }>("SPECS.daily").cover).toBe(`No daily counts before Nov 15, 2026: collection began Nov 14, 2026, and each day’s count is the change from the snapshot before it.`);
  });

  it("warns that clones include CI checkouts and that referrer uniques are GitHub's own count", () => {
    const p = users("30");
    p<Record<string, unknown>>("D").traffic = {
      firstDay: day(20), lastDay: day(1), window: { from: day(30), to: day(1), days: 20, views: 10, clones: 5 },
      daily: [{ day: day(1), views: 10, view_uniques: 3, clones: 5, clone_uniques: 1 }],
      referrers: { day: day(1), rows: [{ referrer: "github.com", count: 2, uniques: 5 }] }, paths: { day: day(1), rows: [] },
    };
    p("drawTraffic()");
    expect(p.el("traffic-sum").textContent).toContain("clones include CI checkouts");
    expect(p.el("traffic-top").innerHTML).toContain("can exceed views");
  });
});

describe("CI page", () => {
  it("puts the coverage caveat on CI time when runs do not cover the range", () => {
    const p = runPage(renderCiPage(), "?range=90");
    Object.assign(p<Record<string, unknown>>("D"), {
      status: status(),
      ci: { coverage: { first: `${day(20)}T00:00:00Z`, last: `${day(0)}T00:00:00Z` }, main: { workflows: [], flakyCommits: 0, commits: 0 } },
      tests: { rows: [] },
      citime: { current: [{ workflow: "Tests", seconds: 7200, runs: 4, long: 0 }], previous: null, rows: [] },
    });
    p("drawKpis()");
    const time = p.el("ov").innerHTML.split('id="k-k-time"')[1]!;
    expect(time).toContain(`since ${new Date(`${day(20)}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })} (earliest collected)`);
  });

  it("does not colour the pass rate or the below-90% count on fewer than 5 runs, and shows CI time below 10 h with a decimal", () => {
    const p = runPage(renderCiPage(), "?range=7");
    const wf = { workflow: "CI", runs: 2, passed: 1, failed: 1, passRate: 0.5, p50: 60, p90: 60, totalSeconds: 720, longRuns: 0, flaky: 0 };
    Object.assign(p<Record<string, unknown>>("D"), {
      status: status(),
      ci: { coverage: { first: `${day(20)}T00:00:00Z`, last: `${day(0)}T00:00:00Z` }, main: { workflows: [wf], flakyCommits: 0, commits: 2 } },
      tests: { rows: [] },
      citime: { current: [{ workflow: "CI", seconds: 720, runs: 2, long: 0 }], previous: null, rows: [] },
    });
    p("drawKpis()");
    const html = p.el("ov").innerHTML;
    const pass = html.split('id="k-k-pass"')[1]!.split('id="k-')[0]!;
    expect(pass).toContain("too few runs to colour");
    expect(pass).not.toMatch(/lv-(bad|warn|good)/);
    expect(html.split('id="k-k-below"')[1]!.split('id="k-')[0]!).toContain("none has 5 runs yet");
    expect(html.split('id="k-k-time"')[1]!).toContain("0.2 h");
    expect(p<{ key: string }[]>("ATTN").some((a) => a.key === "k-pass" || a.key === "k-below")).toBe(false);
  });
});

describe("Growth page", () => {
  const window = (o: Record<string, unknown> = {}) => ({
    starGrowth: 0.02, forkGrowth: 0.02, stars: 20, forks: 2, active: 3, newContributors: 1, prsMerged: 0, externalShare: null, busFactor: 1,
    starRate30: 0.02, forkRate30: 0.02, ...o,
  });
  const growth = (days: number, o: Record<string, unknown> = {}) => ({
    from: day(days), days, gran: "week", starHistory: { from: "list", since: day(400) }, forkCheck: { listed: 2, github: 2 },
    current: window(), previous: window({ starGrowth: 0.01, forkGrowth: 0.03, starRate30: 0.01, forkRate30: 0.03 }),
    newcomers: { cohort: 0, returned: 0, rate: null },
    downloads: { packages: { total: 100, previous: 1000, growth: -0.9, through: day(1), first: day(200) }, installs: { first: null } },
    periods: [], downloadPeriods: [], ...o,
  });
  const page = (days: number, o: Record<string, unknown> = {}) => {
    const p = runPage(renderGrowthPage([["alice", "alice-work"]]), `?range=${days}`);
    Object.assign(p<Record<string, unknown>>("D"), { growth: growth(days, o), status: status() });
    return p;
  };

  it("says which window the earlier star and fork growth is from", () => {
    const p = page(7);
    p("drawKpis()");
    expect(p.el("ov").innerHTML).toContain("was 1.0% in the 7 days before");
    expect(p.el("ov").innerHTML).toContain("was 3.0% in the 7 days before");
  });

  it("colours package growth only over 28 days or more", () => {
    const short = page(7);
    short("drawKpis()");
    expect(short<{ key: string }[]>("ATTN").map((a) => a.key)).not.toContain("g-pkg");
    const long = page(30);
    long("drawKpis()");
    expect(long<{ key: string }[]>("ATTN").map((a) => a.key)).toContain("g-pkg");
  });

  it("shows why PR-based figures are missing, instead of 0 cards, when issues and PRs were never collected", () => {
    const reason = "Issues and pull requests have not been collected yet: GET https://api.github.com/repos/o/r/issues returned HTTP 403.";
    const unknown = { prsMerged: null, externalShare: null, active: null, newContributors: null };
    const weeks = [35, 28, 21, 14, 7, 0].map((d) => ({ start: day(d), active: null, newContributors: null, starGrowth: 0.01, forkGrowth: 0.01, prsMerged: null, externalMerged: null, newcomerCohort: null, newcomerReturned: null, starsTotal: 100, forksTotal: 10 }));
    const p = page(30, {
      notCollected: { issues: { empty: true, reason }, commits: null },
      current: window(unknown), previous: window(unknown), newcomers: { empty: true, reason }, periods: weeks,
    });
    p("drawKpis()");
    p("drawCharts()");
    for (const id of ["ov", "hv"]) expect(p.el(id).innerHTML).toContain(reason);
    for (const key of ["g-active", "g-new", "g-ret", "g-ext"]) {
      const card = p.el(key === "g-active" || key === "g-new" ? "ov" : "hv").innerHTML.split(`id="k-${key}"`)[1]!.split('<div class="card')[0]!;
      expect(card).toContain('<div class="kv num">—</div>');
      expect(card).toContain(reason);
    }
    for (const key of ["g-active", "g-new", "g-ret", "g-ext"]) expect(p<{ key: string }[]>("ATTN").map((a) => a.key)).not.toContain(key);
    expect(p.el("retention").innerHTML).toContain(reason);
    expect(p.el("outside").innerHTML).toContain(reason);
  });

  it("explains an empty momentum chart instead of drawing isolated dots", () => {
    const weeks = [35, 28, 21, 14, 7].map((d) => {
      const k = day(d);
      return { start: k, active: 5, starGrowth: 0, forkGrowth: 0, prsMerged: 0, externalMerged: 0, newcomerCohort: null, newcomerReturned: null };
    });
    const p = page(30, { periods: weeks, gran: "week" });
    p("drawCharts()");
    expect(p.el("momentum").innerHTML).toContain("needs at least four earlier periods to compare with; pick a longer range");
  });
});
