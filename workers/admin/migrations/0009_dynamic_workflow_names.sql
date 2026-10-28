-- GitHub's dynamic workflows (CodeQL default setup, Dependabot) name each run after its pull
-- request or package, which made one workflow look like hundreds. Group them under one name each,
-- as the collector now stores them.
UPDATE workflow_runs SET workflow = 'CodeQL (default setup)' WHERE event = 'dynamic' AND (workflow GLOB 'PR #[0-9]*' OR workflow = 'CodeQL');
UPDATE workflow_runs SET workflow = 'Dependabot graph update' WHERE event = 'dynamic' AND workflow LIKE 'Graph Update:%';
UPDATE workflow_runs SET workflow = 'Dependabot updates' WHERE event = 'dynamic' AND workflow LIKE '% in /%';
