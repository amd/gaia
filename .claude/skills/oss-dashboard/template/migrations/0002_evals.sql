-- Eval and benchmark results from CI, read from `eval-results.json` files in
-- workflow artifacts (see references/evals-contract.md). GitHub deletes
-- artifacts after the repository's retention period, so these rows are the
-- only long-term record.

-- One row per finished workflow run that was read, including runs that had no
-- results (files = 0), so those are not downloaded again every night.
CREATE TABLE eval_runs (
  run_id       INTEGER PRIMARY KEY,   -- GitHub Actions run id
  run_attempt  INTEGER NOT NULL,      -- a re-run keeps the id and bumps this; a newer attempt is read again
  workflow     TEXT NOT NULL,         -- the workflow's display name
  event        TEXT NOT NULL,         -- schedule | push | pull_request | workflow_dispatch | ...
  ref          TEXT,                  -- head_branch: the branch, or the tag for runs on a tag
  sha          TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  conclusion   TEXT,                  -- cancelled runs are stored but left out of pass rates
  html_url     TEXT NOT NULL,
  files        INTEGER NOT NULL,      -- eval-results.json files read
  collected_at TEXT NOT NULL
);
CREATE INDEX eval_runs_created ON eval_runs (created_at);

-- One row per result. Optional text fields the file leaves out are stored as
-- '' rather than NULL so they can be part of the primary key: a run is compared
-- only with earlier runs of the same suite, tier and model.
CREATE TABLE eval_results (
  run_id        INTEGER NOT NULL,
  suite         TEXT NOT NULL,
  tier          TEXT NOT NULL DEFAULT '',
  model         TEXT NOT NULL DEFAULT '',
  category      TEXT NOT NULL DEFAULT '',
  result_id     TEXT NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('PASS', 'FAIL', 'TIMEOUT', 'ERROR', 'SKIPPED')),
  score         REAL,
  elapsed_s     REAL,
  cost_usd      REAL,
  input_tokens  INTEGER,
  output_tokens INTEGER,
  note          TEXT,
  PRIMARY KEY (run_id, suite, tier, model, category, result_id)
);
CREATE INDEX eval_results_suite ON eval_results (suite, run_id);
