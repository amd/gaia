-- Re-running failed lanes keeps the run id; the attempt number tells the
-- collector the stored results are stale and must be read again.
ALTER TABLE eval_runs ADD COLUMN run_attempt INTEGER NOT NULL DEFAULT 1;
