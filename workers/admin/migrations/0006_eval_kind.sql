-- Task-suite results (gaia eval tasks: SWE-bench, the flagship "outcomes" job)
-- are a different measurement from judged agent scenarios and must not be
-- counted in the scenario pass rate.
ALTER TABLE eval_scenarios ADD COLUMN kind TEXT NOT NULL DEFAULT 'scenario';
