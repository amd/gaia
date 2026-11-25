-- One row per day per metric; a missed nightly run leaves a gap, never a zero.

CREATE TABLE releases (
  tag          TEXT PRIMARY KEY,
  published_at TEXT NOT NULL,
  prerelease   INTEGER NOT NULL,
  html_url     TEXT NOT NULL
);

-- GitHub only reports cumulative per-file counts; daily deltas are computed
-- from consecutive days of this table, so its history starts at the first run.
CREATE TABLE asset_snapshots (
  day       TEXT NOT NULL,
  tag       TEXT NOT NULL,
  asset     TEXT NOT NULL,
  downloads INTEGER NOT NULL,
  PRIMARY KEY (day, tag, asset)
);

CREATE TABLE repo_snapshots (
  day         TEXT PRIMARY KEY,
  stars       INTEGER NOT NULL,
  forks       INTEGER NOT NULL,
  open_issues INTEGER NOT NULL
);

CREATE TABLE workflow_runs (
  id          INTEGER PRIMARY KEY,
  workflow    TEXT NOT NULL,
  branch      TEXT,
  event       TEXT NOT NULL,
  status      TEXT NOT NULL,
  conclusion  TEXT,
  head_sha    TEXT NOT NULL,
  run_attempt INTEGER NOT NULL,
  created_at  TEXT NOT NULL,
  duration_s  INTEGER
);
CREATE INDEX workflow_runs_created ON workflow_runs (created_at);

-- Every collector step logs its outcome so a failing source is visible on the
-- page instead of silently showing stale data.
CREATE TABLE collect_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  finished_at TEXT NOT NULL,
  source      TEXT NOT NULL,
  ok          INTEGER NOT NULL,
  detail      TEXT NOT NULL
);

-- New stars / forks per day (cumulative totals are computed on read). Replaced
-- wholesale on every collection, so an unstar or a deleted fork corrects itself.
CREATE TABLE star_daily (day TEXT PRIMARY KEY, n INTEGER NOT NULL);
CREATE TABLE fork_daily (day TEXT PRIMARY KEY, n INTEGER NOT NULL);

-- One row per issue or pull request; opened/closed/merged counts and cycle
-- times are derived on read. `assoc` is GitHub's author_association
-- (OWNER, MEMBER, CONTRIBUTOR, NONE, ...), used to split team from outside work.
CREATE TABLE items (
  number     INTEGER PRIMARY KEY,
  kind       TEXT NOT NULL,          -- 'issue' | 'pr'
  created_at TEXT NOT NULL,
  closed_at  TEXT,
  merged_at  TEXT,
  assoc      TEXT NOT NULL,
  author     TEXT
);
CREATE INDEX items_created ON items (created_at);

-- One row per commit on the default branch. `author` is the GitHub login, or
-- "(no GitHub account)" when the commit email is linked to none.
CREATE TABLE commits (
  sha    TEXT PRIMARY KEY,
  day    TEXT NOT NULL,
  author TEXT NOT NULL,
  at     TEXT
);
CREATE INDEX commits_day ON commits (day);

-- Registry-published daily downloads (npm range API, pypistats, crates.io).
CREATE TABLE package_daily (
  source    TEXT NOT NULL,
  package   TEXT NOT NULL,
  day       TEXT NOT NULL,
  downloads INTEGER NOT NULL,
  PRIMARY KEY (source, package, day)
);

-- Test counts read from the test runner's summary in the default-branch run's
-- job log. One row per day (the day's latest finished run).
CREATE TABLE test_results (
  day          TEXT PRIMARY KEY,
  run_id       INTEGER NOT NULL,
  workflow     TEXT NOT NULL,
  passed       INTEGER NOT NULL,
  failed       INTEGER NOT NULL,
  skipped      INTEGER NOT NULL,
  errors       INTEGER NOT NULL,
  total        INTEGER NOT NULL,
  jobs         INTEGER NOT NULL,
  collected_at TEXT NOT NULL
);

-- Lines added and deleted on the default branch per week, from GitHub's
-- code-frequency statistics. Replaced wholesale on each collection.
CREATE TABLE loc_weekly (
  week      TEXT PRIMARY KEY,   -- Monday (UTC) the week starts
  additions INTEGER NOT NULL,
  deletions INTEGER NOT NULL
);

-- Per-author weekly commits and lines on the default branch, from GitHub's
-- contributor statistics (the top 100 contributors).
CREATE TABLE contributor_weekly (
  author    TEXT NOT NULL,
  week      TEXT NOT NULL,
  commits   INTEGER NOT NULL,
  additions INTEGER NOT NULL,
  deletions INTEGER NOT NULL,
  PRIMARY KEY (author, week)
);

-- Settings edited from the dashboard. 'maintainers' holds the core team
-- ("alice+alice-work,bob"); while unset, MAINTAINER_LOGINS from the deploy is used.
CREATE TABLE settings (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  updated_by TEXT NOT NULL
);
