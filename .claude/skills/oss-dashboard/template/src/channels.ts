/**
 * Install channels beyond npm, PyPI and crates.io. Docker Hub, RubyGems, NuGet
 * and conda publish running totals, stored as one snapshot a night (a day's
 * downloads are the difference from the day before). Homebrew publishes
 * installs over the last 30 days, stored as read and never turned into a
 * daily count.
 */

import { CHANNELS, PROJECT } from "./config";
import { insertMany, writeAtomic, type CollectEnv, type Fetcher } from "./collector";

export type ChannelKey = "homebrew" | "homebrew-cask" | "docker" | "rubygems" | "nuget" | "conda" | "packagist";
type Kind = "total" | "rolling30";

/** Channel, its CHANNELS field, label and kind, in display order. */
export const CHANNEL_LIST: [ChannelKey, keyof typeof CHANNELS, string, Kind][] = [
  ["docker", "docker", "Docker Hub", "total"],
  ["rubygems", "rubygems", "RubyGems", "total"],
  ["nuget", "nuget", "NuGet", "total"],
  ["conda", "conda", "conda", "total"],
  ["packagist", "packagist", "Packagist", "total"],
  ["homebrew", "homebrew", "Homebrew", "rolling30"],
  ["homebrew-cask", "homebrewCasks", "Homebrew casks", "rolling30"],
];

const isCount = (n: unknown): n is number => Number.isInteger(n) && (n as number) >= 0;
const seg = encodeURIComponent;

/** "a/b" split in two, or an error naming the setting to fix. */
function pair(raw: string, field: string, example: string): [string, string] {
  const m = /^([^/\s]+)\/([^/\s]+)$/.exec(raw);
  if (!m) throw new Error(`CHANNELS.${field} entry "${raw}" must look like "${example}" (src/config.ts).`);
  return [m[1]!, m[2]!];
}

async function registryJson<T>(f: Fetcher, url: string, what: string, field: string): Promise<T> {
  // Some registries refuse anonymous clients; name the caller, as crates.io requires.
  const res = await f(url, { headers: { Accept: "application/json", "User-Agent": `${PROJECT.repo.replace("/", "-")}-dashboard-collector (https://github.com/${PROJECT.repo})` } }).catch((e: Error) => {
    throw new Error(`GET ${url} failed: ${e.message}`, { cause: e });
  });
  if (res.status === 404) throw new Error(`GET ${url} returned HTTP 404: ${what} does not exist. Fix CHANNELS.${field} in src/config.ts.`);
  if (!res.ok) throw new Error(`GET ${url} returned HTTP ${res.status}${res.status === 429 ? " (rate limited; read again on the next run)" : ""}.`);
  return (await res.json()) as T;
}

/** Homebrew's analytics map install options ("fzf", "fzf --HEAD") to counts; all of them are installs of the package. */
function brew30(j: { analytics?: { install?: Record<string, Record<string, unknown>> } }, url: string): number {
  const by = j.analytics?.install?.["30d"];
  if (!by || typeof by !== "object") throw new Error(`${url} has no analytics.install["30d"]; Homebrew may have changed its API.`);
  let n = 0;
  for (const v of Object.values(by)) {
    if (!isCount(v)) throw new Error(`${url} has a non-numeric 30-day install count.`);
    n += v;
  }
  return n;
}

async function read(f: Fetcher, channel: ChannelKey, field: string, name: string): Promise<number> {
  switch (channel) {
    case "docker": {
      const [ns, repo] = pair(name, field, "namespace/repo");
      const url = `https://hub.docker.com/v2/repositories/${seg(ns)}/${seg(repo)}/`;
      const j = await registryJson<{ pull_count?: unknown }>(f, url, `Docker Hub repository ${name}`, field);
      if (!isCount(j.pull_count)) throw new Error(`${url} has no pull_count.`);
      return j.pull_count;
    }
    case "rubygems": {
      const url = `https://rubygems.org/api/v1/gems/${seg(name)}.json`;
      const j = await registryJson<{ downloads?: unknown }>(f, url, `gem ${name}`, field);
      if (!isCount(j.downloads)) throw new Error(`${url} has no downloads total.`);
      return j.downloads;
    }
    case "nuget": {
      // The search service listed in https://api.nuget.org/v3/index.json; packageid: matches the id exactly.
      const url = `https://azuresearch-usnc.nuget.org/query?q=packageid:${seg(name)}&prerelease=true`;
      const j = await registryJson<{ data?: { id?: string; totalDownloads?: unknown }[] }>(f, url, `NuGet package ${name}`, field);
      const hit = (j.data ?? []).find((d) => d.id?.toLowerCase() === name.toLowerCase());
      if (!hit) throw new Error(`NuGet has no package "${name}". Fix CHANNELS.nuget in src/config.ts.`);
      if (!isCount(hit.totalDownloads)) throw new Error(`${url} has no totalDownloads for ${name}.`);
      return hit.totalDownloads;
    }
    case "packagist": {
      const [vendor, pkg] = pair(name, field, "vendor/package");
      const url = `https://packagist.org/packages/${seg(vendor)}/${seg(pkg)}/stats.json`;
      const j = await registryJson<{ downloads?: { total?: unknown } }>(f, url, `Packagist package ${name}`, field);
      if (!isCount(j.downloads?.total)) throw new Error(`${url} has no downloads.total.`);
      return j.downloads.total;
    }
    case "conda": {
      const [ch, pkg] = pair(name, field, "conda-forge/numpy");
      const url = `https://api.anaconda.org/package/${seg(ch)}/${seg(pkg)}`;
      const j = await registryJson<{ files?: { ndownloads?: unknown }[] }>(f, url, `conda package ${name}`, field);
      if (!Array.isArray(j.files)) throw new Error(`${url} has no file list.`);
      let n = 0;
      for (const file of j.files) {
        if (!isCount(file.ndownloads)) throw new Error(`${url} has a file without ndownloads.`);
        n += file.ndownloads;
      }
      return n;
    }
    case "homebrew":
    case "homebrew-cask": {
      const url = `https://formulae.brew.sh/api/${channel === "homebrew" ? "formula" : "cask"}/${seg(name)}.json`;
      return brew30(await registryJson(f, url, `Homebrew ${channel === "homebrew" ? "formula" : "cask"} ${name}`, field), url);
    }
  }
}

/**
 * Every configured package, read before the day is written in one batch. A package that fails is left out of
 * that day while the others are stored; channelView then shows its channel's day as a gap, never a partial sum.
 */
export async function collectChannels(env: CollectEnv, f: Fetcher, day: string): Promise<string> {
  const rows: unknown[][] = [];
  const failures: string[] = [];
  for (const [channel, field, , kind] of CHANNEL_LIST) {
    for (const name of CHANNELS[field]) {
      // One registry being down must not cost the others their reading (and their next day's difference).
      try {
        rows.push([day, channel, name, kind, await read(f, channel, field, name)]);
      } catch (e) {
        failures.push((e as Error).message);
      }
    }
  }
  await writeAtomic(env.DB, [env.DB.prepare("DELETE FROM channel_snapshots WHERE day = ?").bind(day), ...insertMany(env.DB, "channel_snapshots", ["day", "channel", "package", "kind", "value"], rows, "INSERT")]);
  const summary = `${rows.length} package(s) across ${new Set(rows.map((r) => r[1])).size} channel(s)`;
  if (failures.length) throw new Error(`${summary} read; ${failures.length} failed: ${failures.join(" | ")}`);
  return summary;
}

export interface ChannelRow {
  day: string;
  channel: string;
  package: string;
  kind: Kind;
  value: number;
}

const prevDay = (d: string) => new Date(Date.parse(d) - 86_400_000).toISOString().slice(0, 10);

const CONFIGURED: Partial<Record<ChannelKey, readonly string[]>> = Object.fromEntries(CHANNEL_LIST.map(([key, field]) => [key, CHANNELS[field]]));

/**
 * Per channel and day: downloads for running totals (the difference from the
 * day before), and the 30-day figure as read for rolling channels. A channel's
 * day counts every package configured for it now (`configured`, CHANNELS by
 * default) or is null: when any of them has no reading that day or, for
 * totals, the day before (a failed read, a missed night, a package just
 * added), or went down (reported in `negativeDays`). Readings of packages no
 * longer configured are ignored.
 */
export function channelView(rows: ChannelRow[], configured: Partial<Record<ChannelKey, readonly string[]>> = CONFIGURED): {
  totals: Record<string, string | number | null>[];
  rolling: Record<string, string | number | null>[];
  negativeDays: { day: string; channel: string; package: string }[];
} {
  const at = new Map<string, number>();
  for (const r of rows) at.set(`${r.day}|${r.channel}|${r.package}`, r.value);
  const groups = new Map<string, ChannelRow>();
  for (const r of rows) if (configured[r.channel as ChannelKey]?.length) groups.set(`${r.day}|${r.channel}`, r);
  const totals = new Map<string, Record<string, string | number | null>>();
  const rolling = new Map<string, Record<string, string | number | null>>();
  const negativeDays: { day: string; channel: string; package: string }[] = [];
  for (const { day, channel, kind } of groups.values()) {
    const pkgs = configured[channel as ChannelKey]!;
    const value = (d: string, p: string) => at.get(`${d}|${channel}|${p}`);
    if (kind === "rolling30") {
      const row = rolling.get(day) ?? rolling.set(day, { day }).get(day)!;
      // One package unread that day would read as a drop to the others' figure.
      row[channel] = pkgs.every((p) => value(day, p) !== undefined) ? pkgs.reduce((s, p) => s + value(day, p)!, 0) : null;
      continue;
    }
    // Nothing the day before (the first snapshot, or after a missed night): left out, which the page shows as a gap.
    if (!pkgs.some((p) => value(prevDay(day), p) !== undefined)) continue;
    let sum: number | null = 0;
    for (const p of pkgs) {
      const now = value(day, p), before = value(prevDay(day), p);
      if (now === undefined || before === undefined) sum = null;
      else if (now < before) {
        negativeDays.push({ day, channel, package: p });
        sum = null;
      } else if (sum !== null) sum += now - before;
    }
    const row = totals.get(day) ?? totals.set(day, { day }).get(day)!;
    row[channel] = sum;
  }
  const byDay = (m: Map<string, Record<string, string | number | null>>) => [...m.values()].sort((a, b) => String(a.day).localeCompare(String(b.day)));
  return { totals: byDay(totals), rolling: byDay(rolling), negativeDays: negativeDays.sort((a, b) => a.day.localeCompare(b.day)) };
}
