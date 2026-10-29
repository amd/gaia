-- Who opened each issue or pull request (GitHub login), so contributions can
-- be broken down by person and the maintainers' own accounts filtered out.
ALTER TABLE items ADD COLUMN author TEXT;

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
