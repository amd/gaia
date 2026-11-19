// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// GitHub + npm data for the homepage: the download buttons, the stats row and
// the release stream.
//
// Production, main, tag and deploy builds fetch the live GitHub API. PR CI sets
// GAIA_GITHUB_FIXTURE to a checked-in response so validation does not spend the
// workflow token's hourly API quota. A malformed response or a missing fixture
// fails the build loudly rather than shipping a stale number or a dead download
// link. GITHUB_TOKEN is OPTIONAL: GitHub's and npm's read endpoints work
// unauthenticated for a public repo/package, so its absence must never fail the
// build. It only raises the GitHub rate limit from 60/hr to 5,000/hr, which
// matters for CI, not for an occasional local build.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

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
  // 'rc' marks a release candidate for a version that has not shipped yet.
  kind: 'stable' | 'rc';
}

export interface ReleaseStats {
  // Main product releases plus the release candidates for the next version
  // (see streamReleases), newest first.
  releases: ReleaseSummary[];
  // Main releases only: an RC is a preview of a release, not one.
  totalReleaseCount: number;
  // Sum of download_count across every release asset the API returns,
  // INCLUDING sub-package/prerelease tags — a download counts regardless of
  // which tag it shipped under. This is a deliberate asymmetry with
  // `releases`/`totalReleaseCount` (main tags only): don't "fix" it into
  // false consistency.
  totalDownloadCount: number;
  // The newest stable release and its downloads so far, leaving out update
  // feeds, checksums and signatures (see sumInstallDownloads). Null before the first.
  latestRelease: { tag: string; installDownloads: number } | null;
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

/** One file attached to the stable release, as a download button needs it. */
export interface ReleaseAsset {
  name: string;
  url: string;
  sizeBytes: number;
}

/** The newest stable release: what every main download button links into. */
export interface StableRelease {
  tag: string;
  // The tag without its "v", e.g. "0.25.0" — the version in the asset names.
  version: string;
  assets: ReleaseAsset[];
}

export interface GithubRelease {
  tag_name: string;
  draft: boolean;
  prerelease: boolean;
  published_at: string | null;
  html_url: string;
  assets: GithubReleaseAsset[];
}

interface GithubFixture {
  repo: { stargazers_count: number; forks_count: number };
  releases: GithubRelease[];
}

const GITHUB_FIXTURE_ENV = "GAIA_GITHUB_FIXTURE";

let fixturePromise: Promise<GithubFixture | null> | null = null;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

// PR CI points this at tests/fixtures/github/releases.json. The fixture goes
// through the same parsing and rendering code as a live response; a malformed
// fixture fails instead of silently falling back to the network.
function readGithubFixture(): Promise<GithubFixture | null> {
  fixturePromise ??= (async () => {
    const path = (process.env[GITHUB_FIXTURE_ENV] ?? "").trim();
    if (!path) return null;

    let parsed: unknown;
    try {
      parsed = JSON.parse(readFileSync(resolve(path), "utf8"));
    } catch (e) {
      throw new Error(
        `[github] Failed to read ${GITHUB_FIXTURE_ENV}=${path}: ${(e as Error).message}`,
      );
    }

    if (!isRecord(parsed)) {
      throw new Error(`[github] ${GITHUB_FIXTURE_ENV}=${path} must be a JSON object.`);
    }
    const repo = parsed.repo;
    const releases = parsed.releases;
    if (!isRecord(repo) || !Array.isArray(releases)) {
      throw new Error(
        `[github] ${GITHUB_FIXTURE_ENV}=${path} must contain a "repo" object ` +
          `and a "releases" array.`,
      );
    }
    if (
      typeof repo.stargazers_count !== "number" ||
      typeof repo.forks_count !== "number"
    ) {
      throw new Error(
        `[github] ${GITHUB_FIXTURE_ENV}=${path} is missing numeric ` +
          `repo.stargazers_count/repo.forks_count.`,
      );
    }
    if (
      !releases.every(
        (r: unknown) =>
          isRecord(r) &&
          typeof r.tag_name === "string" &&
          typeof r.draft === "boolean" &&
          typeof r.prerelease === "boolean" &&
          typeof r.html_url === "string" &&
          Array.isArray(r.assets) &&
          r.assets.every(
            (a: unknown) =>
              isRecord(a) &&
              typeof a.name === "string" &&
              typeof a.browser_download_url === "string" &&
              typeof a.size === "number" &&
              typeof a.download_count === "number",
          ),
      )
    ) {
      throw new Error(
        `[github] ${GITHUB_FIXTURE_ENV}=${path} has a malformed release or asset.`,
      );
    }

    console.log(
      `[github] Using checked-in fixture ${path} (${releases.length} releases).`,
    );
    return {
      repo: {
        stargazers_count: repo.stargazers_count,
        forks_count: repo.forks_count,
      },
      releases: releases as GithubRelease[],
    };
  })();
  return fixturePromise;
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
// pre-release.
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

// The installers publish.yml attaches to a release candidate, in the order the
// list shows them: per platform as the stable downloads order them, the one
// Windows setup first. The macOS chip names follow PLATFORM_LABELS and the Linux
// package families follow INSTALLER_CTA_LABELS (src/scripts/download-target.ts).
// Raw terminal binaries and the wheel are one click away on the release page.
const RC_DOWNLOADS: { label: string; match: RegExp }[] = [
  { label: 'Windows — desktop app, terminal, or both (.exe)', match: /^gaia-\d[^/]*-win-x64-setup\.exe$/ },
  { label: 'Windows — desktop app only (.exe)', match: /^gaia-agent-ui-.+-x64-setup\.exe$/ },
  { label: 'macOS (Apple Silicon) — desktop app (.dmg)', match: /^gaia-agent-ui-.+-arm64\.dmg$/ },
  { label: 'macOS (Apple Silicon) — terminal app (.pkg)', match: /^gaia-\d[^/]*-darwin-arm64\.pkg$/ },
  { label: 'macOS (Intel) — terminal app (.pkg)', match: /^gaia-\d[^/]*-darwin-x64\.pkg$/ },
  { label: 'Linux — desktop app (.AppImage)', match: /^gaia-agent-ui-.+-x86_64\.AppImage$/ },
  { label: 'Debian / Ubuntu — desktop app (.deb)', match: /^gaia-agent-ui-.+-amd64\.deb$/ },
  { label: 'Debian / Ubuntu — terminal app (.deb)', match: /^gaia_\d[^/]*_amd64\.deb$/ },
  { label: 'Fedora / RHEL / SUSE — terminal app (.rpm)', match: /^gaia-\d[^/]*\.x86_64\.rpm$/ },
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

// Highest version, not newest date or GitHub's "latest" flag: a hotfix to an
// older line published after a newer release must not take over the buttons.
function latestStable(live: GithubRelease[]): GithubRelease | undefined {
  return live
    .filter((r) => !r.draft && !r.prerelease && isMainReleaseTag(r.tag_name))
    .sort((a, b) => compareParts(versionParts(a.tag_name.slice(1)), versionParts(b.tag_name.slice(1))))
    .at(-1);
}

function latestStableParts(live: GithubRelease[]): number[] | undefined {
  const stable = latestStable(live);
  return stable ? versionParts(stable.tag_name.slice(1)) : undefined;
}

// Release candidates still worth trying: marked pre-release, in the vX.Y.Z-rcN
// form, and for a version NEWER than the latest stable release — once v0.25.0
// ships, v0.25.0-rc2 is history, not something to try. Oldest first.
function openCandidates(live: GithubRelease[]): { release: GithubRelease; parts: number[] }[] {
  const stable = latestStableParts(live);
  return live
    .filter((r) => r.prerelease)
    .flatMap((r) => {
      const m = RC_TAG_RE.exec(r.tag_name);
      return m ? [{ release: r, parts: [m[1], m[2], m[3], '0', m[4]].map(Number) }] : [];
    })
    .filter((c) => !stable || compareParts(c.parts.slice(0, 4), stable) > 0)
    .sort((a, b) => compareParts(a.parts, b.parts));
}

// The release stream: every main release, plus the newest open release
// candidate per upcoming version, newest first. Superseded RCs stay out — rc12
// replaces rc11 for anyone trying it, and a run of rc1..rc12 would bury the
// releases the stream exists to show.
export function streamReleases(releases: GithubRelease[]): ReleaseSummary[] {
  const live = releases.filter((r) => !r.draft);
  const newestPerVersion = new Map<string, GithubRelease>();
  for (const c of openCandidates(live)) newestPerVersion.set(c.parts.slice(0, 3).join('.'), c.release);
  const summary = (r: GithubRelease, kind: ReleaseSummary['kind']): ReleaseSummary => ({
    tag: r.tag_name,
    publishedAt: r.published_at ?? '',
    htmlUrl: r.html_url,
    kind,
  });
  return [
    ...live.filter((r) => isMainReleaseTag(r.tag_name)).map((r) => summary(r, 'stable')),
    ...[...newestPerVersion.values()].map((r) => summary(r, 'rc')),
  ].sort((a, b) => b.publishedAt.localeCompare(a.publishedAt));
}

// The release candidate worth offering, or null: the newest open candidate.
export function pickReleaseCandidate(releases: GithubRelease[]): ReleaseCandidate | null {
  const newest = openCandidates(releases.filter((r) => !r.draft)).at(-1);
  if (!newest) return null;

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

/**
 * The newest stable release, with its assets, or null when there is none.
 * Pre-releases and drafts are skipped: a release candidate is offered in its
 * own section (pickReleaseCandidate), never behind the main buttons.
 */
export function pickStableRelease(releases: GithubRelease[]): StableRelease | null {
  const release = latestStable(releases);
  if (!release) return null;
  return {
    tag: release.tag_name,
    version: release.tag_name.slice(1),
    assets: release.assets.map((a) => ({
      name: a.name,
      url: a.browser_download_url,
      sizeBytes: a.size,
    })),
  };
}

export function sumAssetDownloads(assets: { download_count: number }[]): number {
  return assets.reduce((sum, a) => sum + a.download_count, 0);
}

// Files that are not GAIA itself: the desktop app's update feeds (fetched on
// every start), differential-update data, checksums and signatures. Installer
// files an app downloads to update itself (the macOS .zip) still count. Kept in
// step with META_PATTERNS in workers/admin/src/classify.ts.
const NOT_INSTALL_FILES: RegExp[] = [
  /^latest(-[a-z]+)?\.yml$/i,
  /\.blockmap$/i,
  /\.sigstore\.json$/i,
  /sha256sums/i,
  /^gaia-agent-release\.json$/i,
  /^gaia-agent\.yaml$/i,
];

export function sumInstallDownloads(assets: { name: string; download_count: number }[]): number {
  return sumAssetDownloads(assets.filter((a) => !NOT_INSTALL_FILES.some((p) => p.test(a.name))));
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

/** Human-readable download size, e.g. "2.3 MB". */
export function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  const value = bytes / Math.pow(1024, i);
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function formatCount(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${trimZero(n / 1000)}k`;
  return `${trimZero(n / 1_000_000)}M`;
}

function trimZero(n: number): string {
  return n.toFixed(1).replace(/\.0$/, "");
}

// Opt-in only: website-ci.yml sets GITHUB_FETCH_RETRIES=3 on live build steps.
// Deploys and local builds leave it unset, so a real failure still fails on the
// first response rather than hiding behind a wait.
const MAX_RETRIES = 5;
const RETRY_WAIT_CAP_MS = 30_000;
const RETRY_WAIT_BUDGET_MS = 30_000;

function githubFetchRetries(): number {
  const raw = (process.env.GITHUB_FETCH_RETRIES ?? "").trim();
  if (!raw) return 0;
  const n = Number(raw);
  if (!Number.isInteger(n)) {
    throw new Error(
      `[github] GITHUB_FETCH_RETRIES must be an integer, got ${JSON.stringify(raw)}.`,
    );
  }
  if (n < 0 || n > MAX_RETRIES) {
    throw new Error(
      `[github] GITHUB_FETCH_RETRIES must be from 0 to ${MAX_RETRIES}, got ${n}.`,
    );
  }
  return n;
}

/** How long to wait before the next attempt, never more than 30 s. */
export function retryDelayMs(res: Response, attempt: number): number {
  const retryAfter = res.headers.get("retry-after");
  if (retryAfter) {
    const seconds = Number(retryAfter);
    if (Number.isFinite(seconds)) return clampWait(seconds * 1000);
    const when = Date.parse(retryAfter);
    if (!Number.isNaN(when)) return clampWait(when - Date.now());
  }
  const reset = res.headers.get("x-ratelimit-reset");
  if (reset) {
    const epochSeconds = Number(reset);
    if (Number.isFinite(epochSeconds)) return clampWait(epochSeconds * 1000 - Date.now());
  }
  return clampWait(500 * 2 ** (attempt - 1));
}

function clampWait(ms: number): number {
  return Math.max(0, Math.min(ms, RETRY_WAIT_CAP_MS));
}

/** The wait before a retry, or null when it would take the total past 30 s. */
export function retryWaitMs(
  res: Response,
  attempt: number,
  waitedMs: number,
): number | null {
  const delayMs = retryDelayMs(res, attempt);
  return delayMs <= RETRY_WAIT_BUDGET_MS - waitedMs ? delayMs : null;
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

function rateLimitError(url: string, status: number, attempts: number): Error {
  if (process.env.GITHUB_TOKEN) {
    return new Error(
      `[github] Request to ${url} returned HTTP ${status} after ${attempts} attempt(s). ` +
        `GITHUB_TOKEN is set, so this is the GitHub API rate limit or an exhausted ` +
        `workflow-token hourly quota, not a missing token. Try again after the quota ` +
        `resets, or set GITHUB_FETCH_RETRIES to wait for a transient limit.`,
    );
  }
  return new Error(
    `[github] Request to ${url} returned HTTP ${status}. If this is a ` +
      `rate limit (403), set GITHUB_TOKEN in the build environment.`,
  );
}

async function githubFetch(url: string): Promise<Response> {
  const retries = githubFetchRetries();
  const headers = githubHeaders();
  let waitedMs = 0;

  for (let attempt = 1; ; attempt++) {
    let res: Response;
    try {
      res = await fetch(url, { headers });
    } catch (e) {
      throw new Error(
        `[github] Failed to fetch ${url}: ${(e as Error).message}. Check network ` +
          `connectivity; a live build has no bundled fallback.`,
      );
    }
    if (res.ok) return res;

    if (res.status !== 403 && res.status !== 429) {
      throw new Error(
        `[github] Request to ${url} returned HTTP ${res.status}. If this is a ` +
          `rate limit (403), set GITHUB_TOKEN in the build environment.`,
      );
    }
    if (attempt > retries) throw rateLimitError(url, res.status, attempt);

    const delayMs = retryWaitMs(res, attempt, waitedMs);
    // A wait that lands exactly on the budget is allowed; one that would take
    // the total past it is not, so the cumulative wait never exceeds 30 s.
    if (delayMs === null) throw rateLimitError(url, res.status, attempt);
    waitedMs += delayMs;
    if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs));
  }
}

async function fetchRepo(): Promise<RepoStats> {
  githubFetchRetries();
  const fixture = await readGithubFixture();
  if (fixture) {
    return {
      stars: fixture.repo.stargazers_count,
      forks: fixture.repo.forks_count,
    };
  }

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
  githubFetchRetries();
  const fixture = await readGithubFixture();
  if (fixture) return fixture.releases;

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
let stableReleasePromise: Promise<StableRelease> | null = null;
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
    const latest = latestStable(all);
    return {
      releases: streamReleases(all),
      totalReleaseCount: nonDraft.filter((r) => isMainReleaseTag(r.tag_name)).length,
      totalDownloadCount: sumAssetDownloads(nonDraft.flatMap((r) => r.assets)),
      latestRelease: latest
        ? { tag: latest.tag_name, installDownloads: sumInstallDownloads(latest.assets) }
        : null,
    };
  })();
  return releaseStatsPromise;
}

export async function getReleaseCandidate(): Promise<ReleaseCandidate | null> {
  releaseCandidatePromise ??= getAllReleases().then(pickReleaseCandidate);
  return releaseCandidatePromise;
}

/** The release the download buttons link into. Throws when there is none. */
export async function getStableRelease(): Promise<StableRelease> {
  stableReleasePromise ??= getAllReleases().then((all) => {
    const stable = pickStableRelease(all);
    if (!stable) {
      throw new Error(
        `[github] ${REPO} has no published stable release (a non-draft, non-pre-release ` +
          `vX.Y.Z tag), so the download buttons have nothing to link to. Check ` +
          `https://github.com/${REPO}/releases.`,
      );
    }
    console.log(`[github] Download links come from ${stable.tag} (${stable.assets.length} assets)`);
    return stable;
  });
  return stableReleasePromise;
}

export async function getNpmDownloadStats(): Promise<NpmDownloadStats> {
  npmStatsPromise ??= (async () => {
    const [weekly, lifetime] = await Promise.all([fetchNpmWeekly(), fetchNpmLifetime()]);
    return { weekly, lifetime };
  })();
  return npmStatsPromise;
}
