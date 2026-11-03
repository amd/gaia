-- Traction and development-momentum history that GitHub and the package
-- registries can reconstruct exactly, so these charts have full history from
-- the first collection.

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
  assoc      TEXT NOT NULL
);
CREATE INDEX items_created ON items (created_at);

-- One row per commit on the default branch. `author` is the public GitHub login
-- (or the commit email's local name when there is no linked account).
CREATE TABLE commits (
  sha    TEXT PRIMARY KEY,
  day    TEXT NOT NULL,
  author TEXT NOT NULL
);
CREATE INDEX commits_day ON commits (day);

-- Registry-published daily downloads (npm range API, pypistats).
CREATE TABLE package_daily (
  source    TEXT NOT NULL,
  package   TEXT NOT NULL,
  day       TEXT NOT NULL,
  downloads INTEGER NOT NULL,
  PRIMARY KEY (source, package, day)
);
