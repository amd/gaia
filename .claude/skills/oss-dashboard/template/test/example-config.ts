/**
 * Everything project-specific lives here. `scripts/scaffold.py` rewrites this
 * file from what it finds in the repository; edit it by hand after that. Secrets never go here: they are Worker secrets (see README.md).
 */

export const PROJECT = {
  /** Shown in the header and page titles. */
  name: "Example",
  /** owner/name on GitHub. */
  repo: "example-org/example",
  /** The branch whose pushes and nightly runs count as build health. */
  defaultBranch: "main",
  /** Path the dashboard is served under; must match the route in wrangler.toml. */
  basePath: "/admin",
};

/**
 * Daily star history comes from the stargazer list, which GitHub shows only to
 * people with access to the repository and cuts off at 40,000. Set false for a
 * repository past that; star totals then come from the nightly snapshot only.
 */
export const STAR_HISTORY = true;

/**
 * Weekly lines added and deleted, from GitHub's code-frequency statistics,
 * which GitHub does not compute for repositories past 10,000 commits.
 */
export const CODE_STATS = true;

/** Registry packages whose daily downloads are tracked. Leave a list empty to drop that source. */
export const PACKAGES: { npm: string[]; pypi: string[]; crates: string[] } = {
  npm: ["example"],
  pypi: [],
  crates: [],
};

/**
 * Repository views, clones, top referrers and top pages from GitHub's traffic
 * API, which keeps only 14 days: nightly collection is the only record. Needs a
 * token whose owner has push access to the repository; a fine-grained token also
 * needs the repository permission "Administration" set to Read-only. Set false
 * if the token cannot have that.
 */
export const TRAFFIC = true;

/**
 * Install channels beyond npm, PyPI and crates.io. docker: "namespace/repo"
 * ("library/nginx" for an official image); conda: "channel/package"
 * ("conda-forge/numpy"); the rest are package names. Docker Hub, RubyGems,
 * NuGet and conda report running totals, so their daily counts start the day
 * after the first collection; Homebrew reports installs over the last 30 days.
 * Leave every list empty to drop the source.
 */
export const CHANNELS: { homebrew: string[]; homebrewCasks: string[]; docker: string[]; rubygems: string[]; nuget: string[]; conda: string[]; packagist: string[] } = {
  homebrew: [],
  homebrewCasks: [],
  docker: [],
  rubygems: [],
  nuget: [],
  conda: [],
  packagist: [],
};

/**
 * Unit-test counts over time, read from the job logs of one workflow's runs on
 * the default branch. `workflow` is the workflow's display name (the `name:` in
 * its YAML); empty turns the source off. `framework` picks the log parser.
 * `jobs`: "largest" when every job runs the whole suite (an OS or version
 * matrix), "sum" when each job runs a share of it (sharded suites).
 */
export const TESTS: { workflow: string; framework: "pytest" | "jest" | "vitest" | "mocha" | "go" | "cargo" | "rspec" | "dotnet" | "maven" | "phpunit"; jobs: "largest" | "sum" } = {
  workflow: "Tests",
  framework: "pytest",
  jobs: "largest",
};

/**
 * Workflows (by file name) whose artifacts carry eval or benchmark results in
 * the format described in references/evals-contract.md. Empty turns the Evals
 * page and its collector off.
 */
export const EVAL_WORKFLOWS: string[] = [];

/** Days GitHub keeps the eval artifacts (the repository's artifact retention setting). */
export const EVAL_ARTIFACT_DAYS = 14;

/**
 * Idempotent GETs that fail with a dropped connection or HTTP 502/503/504 are
 * retried this many times, with a short pause, before the source fails. A busy
 * repository makes thousands of requests a night, and a few always drop.
 * 0 turns it off.
 */
export const NETWORK_RETRIES = 2;

/**
 * Release tags that are the main product line, as opposed to sub-package or
 * nightly tags. scaffold.py derives it from the repository's tags (for example
 * `^my-pkg@\d+...` in a monorepo). Pre-releases are told apart by RC_TAG.
 */
export const RELEASE_TAG = /^v?\d+(\.\d+){1,3}([-.]?(rc|alpha|beta|a|b|pre|next|dev)\.?\d+)?$/i;
export const RC_TAG = /[-.]?(rc|alpha|beta|a|b|pre|next|dev)\.?\d+$/i;

/**
 * What each release file is. The first rule whose pattern matches the file
 * name sets the product; files no rule matches show as "Unclassified" (never
 * guessed). Keep to five products or fewer: the charts have five series colours.
 * `test/assets.test.ts` checks every file the repository has published.
 */
export const PRODUCT_RULES: { product: string; pattern: RegExp }[] = [
  { product: "Desktop app", pattern: /\.(exe|msi|dmg|pkg|deb|rpm|appimage)$/i },
  { product: "CLI", pattern: /\.(tar\.gz|tgz|zip|tar\.xz)$/i },
];

/**
 * The OS of release files whose names do not say it (an installer zip, a macOS
 * build named only "arm64"). Checked before the built-in name heuristics.
 */
export const OS_RULES: { os: "Windows" | "Linux" | "macOS" | "Android" | "BSD" | "Cross-platform"; pattern: RegExp }[] = [];

/**
 * Release files that are not installs: update feeds, signatures, checksums,
 * SBOMs. Auto-updaters fetch some of these on every start, so counting them
 * would overstate installs. Project-specific metadata files go at the end.
 */
export const META_FILES: RegExp[] = [
  /^latest(-[a-z0-9-]+)?\.(ya?ml|json)$/i, // electron-updater and Tauri update feeds
  /^builder-(debug|effective-config)\.ya?ml$/i, // electron-builder build logs attached by mistake
  /\.blockmap$/i,
  /\.(sig|asc|pem|cert|sigstore|sigstore\.json|intoto\.jsonl|minisig)$/i,
  /(^|[-_.])(sha\d+sums?|checksums?|sums)([-_.]|$)/i,
  /\.(sha1|sha256|sha512|md5)$/i,
  /\.(spdx|cdx)(\.json)?$/i,
  /(^|[-_.])sbom([-_.]|$)/i,
  /^RELEASES$/, // Squirrel.Windows feed
  /\.nupkg$/i, // Squirrel delta/full update packages
  /(^|[-_.])symbols([-_.]|$)|\.(snupkg|pdb|dsym|cer|crt)$/i, // debug symbols and certificates
  /^(changelog|changes|readme|license|notice|release[-_]?notes)(\.[a-z]+)?$/i, // documents attached to a release
];

/** Automation accounts. Matched case-insensitively against GitHub logins. */
export const BOT_LOGINS: RegExp = /\[bot\]$|^(github-actions|dependabot|renovate|copilot|claude|codecov|allcontributors|pre-commit-ci|mergify|stale|imgbot|snyk-bot)$/i;

/**
 * Workflow-run events that measure what landed on the default branch.
 * merge_group runs (a merge queue) count for the default branch: GitHub runs
 * them on gh-readonly-queue/<branch>/... branches.
 */
export const BUILD_EVENTS = ["push", "schedule", "merge_group"];

/** Workflows (by display name) left out of build health: ones meant to fail, reports, bots. */
export const CI_EXCLUDE_WORKFLOWS: string[] = [];

/**
 * Growth targets per 30 days (rates) or per window (shares, counts). `good` and
 * `warn` are the green and amber floors; below `warn` is red. Set the green
 * floor a little above your own recent median, not at a famous project's
 * numbers: see references/metrics.md for how, and for the peer figures.
 */
export const TARGETS = {
  starRate: { good: 0.03, warn: 0.015 },
  forkRate: { good: 0.03, warn: 0.015 },
  activeRatio: { good: 1, warn: 0.8 },
  newPer30: { good: 5, warn: 2 },
  retention: { good: 0.25, warn: 0.15 },
  external: { good: 0.2, warn: 0.1 },
  busFactor: { good: 2, warn: 1 },
  downloads: { good: -0.1, warn: -0.3 },
};

/**
 * Peers to show beside your own numbers on the Growth page, for context rather
 * than as goals. Fill with figures you looked up (and the month you did), or
 * leave empty to hide the table.
 */
export const BENCHMARKS_AS_OF = "";
export const PEER_NAME = "";
export const BENCHMARKS: { key: "stars" | "ratio" | "new" | "ext" | "ret" | "bus"; metric: string; target: string; peer: string; others: string }[] = [];
