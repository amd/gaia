import { describe, expect, it, vi } from "vitest";
import { memoryD1 } from "./d1";

// One package per channel, named after the packages the endpoints were checked against.
vi.mock("../src/config", async (importOriginal) => {
  const real = await importOriginal<typeof import("../src/config")>();
  return {
    ...real,
    TRAFFIC: true,
    CHANNELS: {
      homebrew: ["fzf"],
      homebrewCasks: ["visual-studio-code"],
      docker: ["grafana/grafana"],
      rubygems: ["rails"],
      nuget: ["Newtonsoft.Json"],
      conda: ["conda-forge/numpy"],
      packagist: [],
    },
  };
});

const { channelView, collectChannels } = await import("../src/channels");
const { handleApi } = await import("../src/api");
const { SOURCES } = await import("../src/collector");
const { renderUsersPage } = await import("../src/page");

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

/** Registries answering in the shapes they returned when checked live; `n` scales the running totals. */
function registries(n = 1, over: Record<string, () => Response> = {}) {
  const calls: { url: string; headers: Record<string, string> }[] = [];
  const f = async (url: string, init?: RequestInit) => {
    calls.push({ url, headers: init?.headers as Record<string, string> });
    const host = new URL(url).host;
    if (over[host]) return over[host]!();
    if (url === "https://formulae.brew.sh/api/formula/fzf.json") return ok({ analytics: { install: { "30d": { fzf: 59277, "fzf --HEAD": 86 }, "90d": { fzf: 131599 } } } });
    if (url === "https://formulae.brew.sh/api/cask/visual-studio-code.json") return ok({ analytics: { install: { "30d": { "visual-studio-code": 36520 } } } });
    if (url === "https://hub.docker.com/v2/repositories/grafana/grafana/") return ok({ name: "grafana", pull_count: 1000 * n });
    if (url === "https://rubygems.org/api/v1/gems/rails.json") return ok({ downloads: 2000 * n, version_downloads: 5 });
    if (url === "https://azuresearch-usnc.nuget.org/query?q=packageid:Newtonsoft.Json&prerelease=true") {
      return ok({ totalHits: 2, data: [{ id: "Newtonsoft.Json.Bson", totalDownloads: 1 }, { id: "newtonsoft.json", totalDownloads: 3000 * n }] });
    }
    if (url === "https://api.anaconda.org/package/conda-forge/numpy") return ok({ ndownloads: 1, files: [{ ndownloads: 400 * n }, { ndownloads: 600 * n }] });
    throw new Error(`unexpected ${url}`);
  };
  return { f, calls };
}

const rows = async (db: D1Database, sql: string) => (await db.prepare(sql).all()).results;
const get = async (db: D1Database, path: string, q = "") => (await handleApi(db, path, new URL(`http://localhost/admin${path}?${q}`))).json() as Promise<Record<string, any>>;

describe("collectChannels", () => {
  it("stores running totals and Homebrew's 30-day figure, each with its kind", async () => {
    const db = memoryD1();
    const { f, calls } = registries();
    expect(await collectChannels({ DB: db }, f, "2026-11-18")).toBe("6 package(s) across 6 channel(s)");
    expect(await rows(db, "SELECT channel, package, kind, value FROM channel_snapshots ORDER BY channel")).toEqual([
      { channel: "conda", package: "conda-forge/numpy", kind: "total", value: 1000 },
      { channel: "docker", package: "grafana/grafana", kind: "total", value: 1000 },
      // Every install option of the formula counts; the 90-day figure is ignored.
      { channel: "homebrew", package: "fzf", kind: "rolling30", value: 59363 },
      { channel: "homebrew-cask", package: "visual-studio-code", kind: "rolling30", value: 36520 },
      // The exact id, not the first search hit.
      { channel: "nuget", package: "Newtonsoft.Json", kind: "total", value: 3000 },
      { channel: "rubygems", package: "rails", kind: "total", value: 2000 },
    ]);
    // Registries that refuse anonymous clients get a User-Agent naming the project.
    for (const c of calls) expect(c.headers["User-Agent"]).toMatch(/^example-org-example-dashboard-collector \(https:\/\/github\.com\/example-org\/example\)$/);
  });

  it("re-collecting a day replaces it rather than adding to it", async () => {
    const db = memoryD1();
    await collectChannels({ DB: db }, registries(1).f, "2026-11-18");
    await collectChannels({ DB: db }, registries(2).f, "2026-11-18");
    expect(await rows(db, "SELECT value FROM channel_snapshots WHERE channel = 'docker'")).toEqual([{ value: 2000 }]);
  });

  it("names the setting to fix for a package that does not exist, and still keeps every other package's reading", async () => {
    const db = memoryD1();
    const gone = registries(1, { "hub.docker.com": () => new Response("{}", { status: 404 }) });
    await expect(collectChannels({ DB: db }, gone.f, "2026-11-18")).rejects.toThrow(/Docker Hub repository grafana\/grafana does not exist\. Fix CHANNELS\.docker/);
    const none = registries(1, { "azuresearch-usnc.nuget.org": () => ok({ totalHits: 0, data: [] }) });
    await expect(collectChannels({ DB: db }, none.f, "2026-11-18")).rejects.toThrow(/NuGet has no package "Newtonsoft.Json"\. Fix CHANNELS\.nuget/);
    const noAnalytics = registries(1, { "formulae.brew.sh": () => ok({ name: "fzf" }) });
    await expect(collectChannels({ DB: db }, noAnalytics.f, "2026-11-18")).rejects.toThrow(/analytics\.install\["30d"\]/);
    const noCount = registries(1, { "rubygems.org": () => ok({ name: "rails" }) });
    await expect(collectChannels({ DB: db }, noCount.f, "2026-11-18")).rejects.toThrow(/no downloads total/);
    // The last run failed only RubyGems: the five other packages were read and stored, so their next difference exists.
    const kept = (await rows(db, "SELECT channel FROM channel_snapshots ORDER BY channel")).map((r) => (r as { channel: string }).channel);
    expect(kept).toEqual(["conda", "docker", "homebrew", "homebrew-cask", "nuget"]);
  });
});

describe("collectChannels network failures", () => {
  it("names the URL that failed, so the footer says which registry to look at", async () => {
    const down = registries(1, { "api.anaconda.org": () => { throw new Error("Network connection lost."); } });
    await expect(collectChannels({ DB: memoryD1() }, down.f, "2026-11-18")).rejects.toThrow("GET https://api.anaconda.org/package/conda-forge/numpy failed: Network connection lost.");
  });
});

describe("channelView", () => {
  const r = (day: string, channel: string, pkg: string, kind: "total" | "rolling30", value: number) => ({ day, channel, package: pkg, kind, value });

  it("turns running totals into daily differences, starting the day after the first reading", () => {
    const v = channelView([r("2026-11-01", "docker", "a/b", "total", 100), r("2026-11-02", "docker", "a/b", "total", 130), r("2026-11-03", "docker", "a/b", "total", 135)], { docker: ["a/b"] });
    expect(v.totals).toEqual([{ day: "2026-11-02", docker: 30 }, { day: "2026-11-03", docker: 5 }]);
    expect(v.negativeDays).toEqual([]);
  });

  it("leaves a missed night as a gap: no difference for the day after it, never a zero or a two-day sum", () => {
    const v = channelView([r("2026-11-01", "rubygems", "x", "total", 100), r("2026-11-03", "rubygems", "x", "total", 160), r("2026-11-04", "rubygems", "x", "total", 170)], { rubygems: ["x"] });
    expect(v.totals).toEqual([{ day: "2026-11-04", rubygems: 10 }]);
  });

  it("reports a total that went down and leaves that day blank", () => {
    const v = channelView([r("2026-11-01", "conda", "c/p", "total", 100), r("2026-11-02", "conda", "c/p", "total", 90), r("2026-11-03", "conda", "c/p", "total", 95)], { conda: ["c/p"] });
    expect(v.totals).toEqual([{ day: "2026-11-02", conda: null }, { day: "2026-11-03", conda: 5 }]);
    expect(v.negativeDays).toEqual([{ day: "2026-11-02", channel: "conda", package: "c/p" }]);
  });

  it("sums a channel's packages, and leaves the day a package was added blank rather than partly counted", () => {
    const v = channelView([
      r("2026-11-01", "nuget", "A", "total", 10),
      r("2026-11-02", "nuget", "A", "total", 15),
      r("2026-11-02", "nuget", "B", "total", 500),
      r("2026-11-03", "nuget", "A", "total", 17),
      r("2026-11-03", "nuget", "B", "total", 510),
    ], { nuget: ["A", "B"] });
    expect(v.totals).toEqual([{ day: "2026-11-02", nuget: null }, { day: "2026-11-03", nuget: 12 }]);
  });

  it("keeps Homebrew's 30-day figure as read and never differences it", () => {
    const v = channelView([
      r("2026-11-01", "homebrew", "fzf", "rolling30", 59000),
      r("2026-11-02", "homebrew", "fzf", "rolling30", 59300),
      r("2026-11-02", "homebrew", "jq", "rolling30", 100),
      r("2026-11-02", "homebrew-cask", "code", "rolling30", 36520),
    ], { homebrew: ["fzf", "jq"], "homebrew-cask": ["code"] });
    // jq has no reading on Nov 1, so that day is blank rather than fzf alone.
    expect(v.rolling).toEqual([{ day: "2026-11-01", homebrew: null }, { day: "2026-11-02", homebrew: 59400, "homebrew-cask": 36520 }]);
    expect(v.totals).toEqual([]);
  });

  // Two packages per channel; "small" fails to read on Nov 2 only.
  const failedNight = (kind: "total" | "rolling30", channel: string) => {
    const big = kind === "total" ? [5000, 5100, 5200, 5300] : [5000, 5000, 5000, 5000];
    const small = kind === "total" ? [100, null, 120, 130] : [100, null, 100, 100];
    return ["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04"].flatMap((d, i) => [
      r(d, channel, "big", kind, big[i]!),
      ...(small[i] == null ? [] : [r(d, channel, "small", kind, small[i]!)]),
    ]);
  };

  it("leaves a running-total day blank when one package failed that night or the night before, never a false drop", () => {
    const v = channelView(failedNight("total", "docker"), { docker: ["big", "small"] });
    expect(v.totals).toEqual([
      { day: "2026-11-02", docker: null },
      { day: "2026-11-03", docker: null },
      { day: "2026-11-04", docker: 110 },
    ]);
  });

  it("leaves a 30-day day blank when one package failed that night, never a false drop", () => {
    const v = channelView(failedNight("rolling30", "homebrew"), { homebrew: ["big", "small"] });
    expect(v.rolling).toEqual([
      { day: "2026-11-01", homebrew: 5100 },
      { day: "2026-11-02", homebrew: null },
      { day: "2026-11-03", homebrew: 5100 },
      { day: "2026-11-04", homebrew: 5100 },
    ]);
  });

  it("counts the packages in CHANNELS by default, and ignores one no longer configured", () => {
    const v = channelView([
      r("2026-11-01", "homebrew", "fzf", "rolling30", 100),
      r("2026-11-01", "homebrew", "removed", "rolling30", 7),
      r("2026-11-02", "homebrew", "removed", "rolling30", 7),
    ]);
    expect(v.rolling).toEqual([{ day: "2026-11-01", homebrew: 100 }, { day: "2026-11-02", homebrew: null }]);
  });
});

describe("/api/channels", () => {
  it("is empty with a reason before the first reading", async () => {
    expect(await get(memoryD1(), "/api/channels")).toEqual({ empty: true, reason: "No install channel readings yet. Run the collector (see README)." });
  });

  it("returns daily differences, the rolling line and the latest readings, up to the last collection", async () => {
    const db = memoryD1();
    await collectChannels({ DB: db }, registries(1).f, "2026-11-16");
    await collectChannels({ DB: db }, registries(2).f, "2026-11-17");
    await db.prepare("INSERT INTO collect_log (finished_at, source, ok, detail) VALUES ('2026-11-17T03:20:00Z', 'channels', 1, 'ok')").run();
    const c = await get(db, "/api/channels", "range=7");
    expect(c.firstDay).toBe("2026-11-16");
    expect(c.lastDay).toBe("2026-11-17");
    expect(c.totals).toEqual([{ day: "2026-11-17", conda: 1000, docker: 1000, nuget: 3000, rubygems: 2000 }]);
    expect(c.rolling).toEqual([
      { day: "2026-11-16", homebrew: 59363, "homebrew-cask": 36520 },
      { day: "2026-11-17", homebrew: 59363, "homebrew-cask": 36520 },
    ]);
    expect(c.latest).toHaveLength(6);
    expect(c.latest[0]).toEqual({ day: "2026-11-17", channel: "conda", package: "conda-forge/numpy", kind: "total", value: 2000 });
    expect(c.negativeDays).toEqual([]);
  });
});

describe("a project with traffic and channels on", () => {
  const html = renderUsersPage();
  it("collects both", () => {
    expect(SOURCES).toContain("traffic");
    expect(SOURCES).toContain("channels");
  });
  it("has both cards, separate charts for totals and Homebrew's 30-day figure, and scripts that parse", () => {
    expect(html).toContain('id="traffic"');
    expect(html).toContain('id="traffic-top"');
    expect(html).toContain('id="ch-totals"');
    expect(html).toContain('id="ch-rolling"');
    expect(html).toContain("Docker Hub, RubyGems, NuGet, conda: downloads per day");
    expect(html).toContain("TRAFFIC=true");
    expect(html).toContain('["homebrew-cask","Homebrew casks","rolling30"]');
    expect(html).toContain("'/api/traffic'");
    expect(html).toContain("'/api/channels'");
    for (const [, s] of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) expect(() => new Function(s!)).not.toThrow();
    expect(html).not.toMatch(/__[A-Z_]+__/);
  });
  it("escapes referrer, path and package names before they reach the page", () => {
    // Values from GitHub and the registries are rendered by the client script; every one goes through esc().
    for (const v of ["esc(r.referrer)", "esc(r.path)", "esc(r.title)", "esc(r.package)"]) expect(html).toContain(v);
  });
});
