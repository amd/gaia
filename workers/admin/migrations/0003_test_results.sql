-- Test counts read from the pytest summary line in the main-branch unit-test
-- run's job log. One row per day (the day's latest finished run).
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
