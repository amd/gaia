-- Agent-eval results from CI. GitHub keeps eval artifacts for 14 days, so
-- these rows are the only long-term record.

CREATE TABLE eval_runs (
  run_id       INTEGER PRIMARY KEY,   -- GitHub Actions run id
  workflow     TEXT NOT NULL,
  event        TEXT NOT NULL,         -- schedule | push | pull_request | workflow_dispatch
  ref          TEXT,                  -- branch name, or tag for tag pushes
  is_tag       INTEGER NOT NULL,
  sha          TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  conclusion   TEXT,
  tier         TEXT,                  -- smoke | full | pr | ... (from tier.json / lane-status.json)
  agent_model  TEXT,
  judge_model  TEXT,
  minutes      REAL,                  -- longest lane, wall clock
  lanes        INTEGER NOT NULL,
  html_url     TEXT NOT NULL,
  collected_at TEXT NOT NULL
);
CREATE INDEX eval_runs_created ON eval_runs (created_at);

CREATE TABLE eval_scenarios (
  run_id        INTEGER NOT NULL,
  category      TEXT NOT NULL,
  scenario_id   TEXT NOT NULL,
  lane          TEXT,
  status        TEXT NOT NULL,        -- PASS | FAIL | TIMEOUT | INFRA_ERROR | BLOCKED_BY_ARCHITECTURE | ...
  score         REAL,
  elapsed_s     REAL,
  cost_usd      REAL,
  tps           REAL,
  ttft_s        REAL,
  input_tokens  INTEGER,
  output_tokens INTEGER,
  tool_calls    INTEGER,
  turns         INTEGER,
  root_cause    TEXT,
  PRIMARY KEY (run_id, category, scenario_id)
);
CREATE INDEX eval_scenarios_scenario ON eval_scenarios (scenario_id);
