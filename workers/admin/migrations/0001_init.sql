-- One row per day per metric; a missed nightly run leaves a gap, never a zero.

CREATE TABLE releases (
  tag          TEXT PRIMARY KEY,
  published_at TEXT NOT NULL,
  prerelease   INTEGER NOT NULL,
  html_url     TEXT NOT NULL
);

-- GitHub only reports cumulative per-asset counts; daily deltas are computed
-- from consecutive days of this table.
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

CREATE TABLE package_snapshots (
  day            TEXT NOT NULL,
  source         TEXT NOT NULL,
  package        TEXT NOT NULL,
  downloads_week INTEGER NOT NULL,
  downloads_total INTEGER,
  PRIMARY KEY (day, source, package)
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
