// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// GitHub + npm traction data for the homepage stats row and release stream.
//
// Same discipline as ./catalog.ts: a build-time fetch with no bundled fixture
// and no silent fallback — a malformed response or an unreachable endpoint
// fails the build loudly rather than shipping a stale or zeroed number. The
// one deliberate difference is auth: HUB_CATALOG_URL is REQUIRED because no
// fixture exists at all, but GITHUB_TOKEN is OPTIONAL — GitHub's and npm's
// read endpoints work fine unauthenticated for a public repo/package, so its
// absence must never fail the build. It only raises the GitHub rate limit
// from 60/hr to 5,000/hr, which matters for CI, not for an occasional local build.

const REPO = "amd/gaia";
const NPM_PACKAGE = "@amd-gaia/gaia";

// Confirmed directly against registry.npmjs.org/@amd-gaia/gaia (`time.created`)
// — a real fact, not a guess. The npm downloads/range API also caps at ~18
// months, which this date is well within, so "lifetime" and "since this date"
// are the same thing today.
const NPM_PACKAGE_CREATED = "2026-08-18";

export interface RepoStats {
  stars: number;
  forks: number;
}

export interface ReleaseSummary {
  tag: string;
  publishedAt: string;
  htmlUrl: string;
}

export interface ReleaseStats {
  // Main product releases only (tag matches isMainReleaseTag), newest first.
  releases: ReleaseSummary[];
  totalReleaseCount: number;
  // Sum of download_count across every release asset the API returns,
  // INCLUDING sub-package/prerelease tags — a download counts regardless of
  // which tag it shipped under. This is a deliberate asymmetry with
  // `releases`/`totalReleaseCount` (main tags only): don't "fix" it into
  // false consistency.
  totalDownloadCount: number;
}

export interface NpmDownloadStats {
  weekly: number;
  lifetime: number;
}

export interface GithubReleaseAsset {
  name: string;
  browser_download_url: string;
  size: number;
  download_count: number;
}

export interface GithubRelease {
  tag_name: string;
  draft: boolean;
  prerelease: boolean;
  published_at: string | null;
  html_url: string;
  assets: GithubReleaseAsset[];
}

// Main product tags look like "v0.24.1", including the occasional 4-segment
// hotfix ("v0.15.4.1") — those are real main-line ships, just a deeper patch
// level, and undercounting them would understate cadence as much as
// overcounting would overstate it. Sub-package releases (e.g.
// "agent-pkg-email-v0.5.0") are real GitHub releases with real downloads, but
// they are not a GAIA release for cadence/count purposes — the extra text
// before the "v" is what excludes them here.
const MAIN_RELEASE_TAG_RE = /^v\d+(\.\d+){2,3}$/;

export function isMainReleaseTag(tag: string): boolean {
  return MAIN_RELEASE_TAG_RE.test(tag);
}

// A release candidate: "v0.25.0-rc1". publish.yml marks its GitHub release a
// pre-release; the hub catalog never carries one.
const RC_TAG_RE = /^v(\d+)\.(\d+)\.(\d+)-rc([1-9]\d*)$/;

export interface ReleaseCandidateDownload {
  label: string;
  url: string;
  sizeBytes: number;
}

export interface ReleaseCandidate {
  tag: string;
  // The final release this is a candidate for, e.g. "0.25.0".
  version: string;
  htmlUrl: string;
  downloads: ReleaseCandidateDownload[];
}

// The desktop app installers publish.yml attaches to every release, in the
// order the list shows them. Everything else (terminal binaries, the wheel)
// is one click away on the release page.
const RC_DOWNLOADS: { label: string; match: RegExp }[] = [
  { label: 'Windows — desktop app (.exe)', match: /^gaia-agent-ui-.+-x64-setup\.exe$/ },
  { label: 'macOS, Apple Silicon — desktop app (.dmg)', match: /^gaia-agent-ui-.+-arm64\.dmg$/ },
  { label: 'Linux — desktop app (.AppImage)', match: /^gaia-agent-ui-.+-x86_64\.AppImage$/ },
  { label: 'Debian / Ubuntu — desktop app (.deb)', match: /^gaia-agent-ui-.+-amd64\.deb$/ },
];

// Numeric parts of a main tag ("v0.24.1" or "v0.15.4.1"), padded to four so
// a hotfix sorts after its base.
function versionParts(version: string): number[] {
  const parts = version.split('.').map(Number);
  while (parts.length < 4) parts.push(0);
  return parts;
}

function compareParts(a: number[], b: number[]): number {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const d = (a[i] ?? 0) - (b[i] ?? 0);
    if (d !== 0) return d;
  }
  return 0;
}

export function isReleaseCandidateTag(tag: string): boolean {
  return RC_TAG_RE.test(tag);
}

// The release candidate worth offering, or null. Only a candidate for a
// version NEWER than the latest stable release qualifies: once v0.25.0 ships,
// v0.25.0-rc2 is history, not something to try.
export function pickReleaseCandidate(releases: GithubRelease[]): ReleaseCandidate | null {
  const live = releases.filter((r) => !r.draft);

  const stable = live
    .filter((r) => !r.prerelease && isMainReleaseTag(r.tag_name))
    .map((r) => versionParts(r.tag_name.slice(1)))
    .sort(compareParts)
    .at(-1);

  const candidates = live
    .filter((r) => r.prerelease)
    .flatMap((r) => {
      const m = RC_TAG_RE.exec(r.tag_name);
      return m ? [{ release: r, parts: [m[1], m[2], m[3], '0', m[4]].map(Number) }] : [];
    })
    .sort((a, b) => compareParts(a.parts, b.parts));
  const newest = candidates.at(-1);
  if (!newest) return null;
  if (stable && compareParts(newest.parts.slice(0, 4), stable) <= 0) return null;

  const { release } = newest;
  const downloads = RC_DOWNLOADS.flatMap(({ label, match }) => {
    const asset = release.assets.find((a) => match.test(a.name));
    return asset ? [{ label, url: asset.browser_download_url, sizeBytes: asset.size }] : [];
  });
  return {
    tag: release.tag_name,
    version: newest.parts.slice(0, 3).join('.'),
    htmlUrl: release.html_url,
    downloads,
  };
}

export function sumAssetDownloads(assets: { download_count: number }[]): number {
  return assets.reduce((sum, a) => sum + a.download_count, 0);
}

// Whole calendar days between two dates, computed in UTC (not the build
// machine's local timezone) so the result doesn't shift depending on where
// the site happens to build — GitHub's published_at is already UTC.
export function daysSince(date: Date, now: Date = new Date()): number {
  const dayMs = 24 * 60 * 60 * 1000;
  const startOfUtcDay = (d: Date) => Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
  return Math.round((startOfUtcDay(now) - startOfUtcDay(date)) / dayMs);
}

// now defaults to `new Date()` for callers, but the parameter makes it
// deterministic and testable without mocking the global clock.
export function formatRelativeDate(date: Date, now: Date = new Date()): string {
  const days = daysSince(date, now);
  if (days <= 0) return "Today";
  if (days === 1) return "1 day ago";
  return `${days} days ago`;
}

export function formatCount(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${trimZero(n / 1000)}k`;
  return `${trimZero(n / 1_000_000)}M`;
}

function trimZero(n: number): string {
  return n.toFixed(1).replace(/\.0$/, "");
}

function githubHeaders(): Record<string, string> {
  const headers: Record<string, string> = { Accept: "application/vnd.github+json" };
  const token = process.env.GITHUB_TOKEN;
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  } else {
    console.log(
      "[github] GITHUB_TOKEN is not set — using the unauthenticated GitHub API " +
        "(60 requests/hr). Fine for local dev; set GITHUB_TOKEN in CI to raise " +
        "the limit to 5,000/hr.",
    );
  }
  return headers;
}

async function githubFetch(url: string): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(url, { headers: githubHeaders() });
  } catch (e) {
    throw new Error(
      `[github] Failed to fetch ${url}: ${(e as Error).message}. Check network ` +
        `connectivity; the homepage stats row has no bundled fallback.`,
    );
  }
  if (!res.ok) {
    throw new Error(
      `[github] Request to ${url} returned HTTP ${res.status}. If this is a ` +
        `rate limit (403), set GITHUB_TOKEN in the build environment.`,
    );
  }
  return res;
}

async function fetchRepo(): Promise<RepoStats> {
  const url = `https://api.github.com/repos/${REPO}`;
  const res = await githubFetch(url);
  const json = (await res.json()) as { stargazers_count?: unknown; forks_count?: unknown };
  if (typeof json.stargazers_count !== "number" || typeof json.forks_count !== "number") {
    throw new Error(
      `[github] Response from ${url} is missing numeric stargazers_count/forks_count.`,
    );
  }
  return { stars: json.stargazers_count, forks: json.forks_count };
}

// GitHub paginates at up to 100 per page. The hard page cap is a guard
// against a runaway loop from a misbehaving API, not an assumption about how
// many releases this repo will ever have.
const MAX_RELEASE_PAGES = 50;

async function fetchAllReleases(): Promise<GithubRelease[]> {
  const releases: GithubRelease[] = [];
  for (let page = 1; page <= MAX_RELEASE_PAGES; page++) {
    const url = `https://api.github.com/repos/${REPO}/releases?per_page=100&page=${page}`;
    const res = await githubFetch(url);
    const json = (await res.json()) as unknown;
    if (!Array.isArray(json)) {
      throw new Error(`[github] Response from ${url} is not an array of releases.`);
    }
    releases.push(...(json as GithubRelease[]));
    if (json.length < 100) break;
    if (page === MAX_RELEASE_PAGES) {
      throw new Error(
        `[github] Hit the ${MAX_RELEASE_PAGES}-page pagination guard fetching ` +
          `${REPO}'s releases — either the repo has an unexpectedly huge release ` +
          `history, or pagination is stuck. Investigate before raising the cap.`,
      );
    }
  }
  return releases;
}

async function fetchNpmWeekly(): Promise<number> {
  const url = `https://api.npmjs.org/downloads/point/last-week/${NPM_PACKAGE}`;
  let res: Response;
  try {
    res = await fetch(url);
  } catch (e) {
    throw new Error(`[github] Failed to fetch npm weekly downloads from ${url}: ${(e as Error).message}.`);
  }
  if (!res.ok) {
    throw new Error(`[github] npm weekly downloads request to ${url} returned HTTP ${res.status}.`);
  }
  const json = (await res.json()) as { downloads?: unknown };
  if (typeof json.downloads !== "number") {
    throw new Error(`[github] Response from ${url} is missing a numeric 'downloads' field.`);
  }
  return json.downloads;
}

async function fetchNpmLifetime(): Promise<number> {
  const today = new Date().toISOString().slice(0, 10);
  const url = `https://api.npmjs.org/downloads/range/${NPM_PACKAGE_CREATED}:${today}/${NPM_PACKAGE}`;
  let res: Response;
  try {
    res = await fetch(url);
  } catch (e) {
    throw new Error(`[github] Failed to fetch npm lifetime downloads from ${url}: ${(e as Error).message}.`);
  }
  if (!res.ok) {
    throw new Error(`[github] npm lifetime downloads request to ${url} returned HTTP ${res.status}.`);
  }
  const json = (await res.json()) as { downloads?: unknown };
  if (!Array.isArray(json.downloads)) {
    throw new Error(`[github] Response from ${url} is missing a 'downloads' array.`);
  }
  return (json.downloads as { downloads: number }[]).reduce((sum, d) => sum + d.downloads, 0);
}

// One fetch chain per build, shared across pages.
let repoStatsPromise: Promise<RepoStats> | null = null;
let allReleasesPromise: Promise<GithubRelease[]> | null = null;
let releaseStatsPromise: Promise<ReleaseStats> | null = null;
let releaseCandidatePromise: Promise<ReleaseCandidate | null> | null = null;
let npmStatsPromise: Promise<NpmDownloadStats> | null = null;

export async function getRepoStats(): Promise<RepoStats> {
  repoStatsPromise ??= fetchRepo();
  return repoStatsPromise;
}

function getAllReleases(): Promise<GithubRelease[]> {
  allReleasesPromise ??= fetchAllReleases();
  return allReleasesPromise;
}

export async function getReleaseStats(): Promise<ReleaseStats> {
  releaseStatsPromise ??= (async () => {
    const all = await getAllReleases();
    const nonDraft = all.filter((r) => !r.draft);
    const main = nonDraft
      .filter((r) => isMainReleaseTag(r.tag_name))
      .sort((a, b) => (b.published_at ?? "").localeCompare(a.published_at ?? ""));
    return {
      releases: main.map((r) => ({
        tag: r.tag_name,
        publishedAt: r.published_at ?? "",
        htmlUrl: r.html_url,
      })),
      totalReleaseCount: main.length,
      totalDownloadCount: sumAssetDownloads(nonDraft.flatMap((r) => r.assets)),
    };
  })();
  return releaseStatsPromise;
}

export async function getReleaseCandidate(): Promise<ReleaseCandidate | null> {
  releaseCandidatePromise ??= getAllReleases().then(pickReleaseCandidate);
  return releaseCandidatePromise;
}

export async function getNpmDownloadStats(): Promise<NpmDownloadStats> {
  npmStatsPromise ??= (async () => {
    const [weekly, lifetime] = await Promise.all([fetchNpmWeekly(), fetchNpmLifetime()]);
    return { weekly, lifetime };
  })();
  return npmStatsPromise;
}
