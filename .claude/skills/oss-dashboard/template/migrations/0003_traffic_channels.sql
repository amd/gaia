-- GitHub keeps repository traffic for 14 days only, so these nightly copies are
-- the only long-term record. `*_uniques` are GitHub's distinct visitors or
-- cloners on that one day: they cannot be added across days.
CREATE TABLE traffic_daily (
  day           TEXT PRIMARY KEY,
  views         INTEGER NOT NULL,
  view_uniques  INTEGER NOT NULL,
  clones        INTEGER NOT NULL,
  clone_uniques INTEGER NOT NULL
);

-- GitHub's top 10 referrers and pages over the 14 days before `day`, as read
-- that day. Each day is its own snapshot; rows from different days overlap.
CREATE TABLE traffic_referrers (
  day      TEXT NOT NULL,
  referrer TEXT NOT NULL,
  count    INTEGER NOT NULL,
  uniques  INTEGER NOT NULL,
  PRIMARY KEY (day, referrer)
);
CREATE TABLE traffic_paths (
  day     TEXT NOT NULL,
  path    TEXT NOT NULL,
  title   TEXT NOT NULL,
  count   INTEGER NOT NULL,
  uniques INTEGER NOT NULL,
  PRIMARY KEY (day, path)
);

-- One reading per install channel package per day. 'total': a running total
-- (Docker Hub pulls, RubyGems, NuGet, conda), so a day's downloads are the
-- difference from the day before. 'rolling30': installs in the 30 days before
-- the reading (Homebrew), never a daily count.
CREATE TABLE channel_snapshots (
  day     TEXT NOT NULL,
  channel TEXT NOT NULL,
  package TEXT NOT NULL,
  kind    TEXT NOT NULL CHECK (kind IN ('total', 'rolling30')),
  value   INTEGER NOT NULL,
  PRIMARY KEY (day, channel, package)
);
