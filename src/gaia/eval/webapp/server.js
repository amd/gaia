// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const express = require('express');
const rateLimit = require('express-rate-limit');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const app = express();
const PORT = process.env.PORT || 3000;

// Rate limiting for API endpoints using express-rate-limit
const apiLimiter = rateLimit({
    windowMs: 60 * 1000,
    max: 100,
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: 'Too many requests. Please try again later.' }
});

app.use('/api/', apiLimiter);
app.use(express.static(path.join(__dirname, 'public')));
app.use(express.json());

// Paths
const RESULTS_DIR = process.env.RESULTS_DIR || path.join(__dirname, '../../../../eval/results');
const REPO_ROOT = path.join(__dirname, '../../../..');

// Eval subprocess tracking
let evalProcess = null;
let evalStatus = { running: false, current_scenario: null, progress: { done: 0, total: 0 } };

// Path traversal protection: validate runId
function isValidRunId(runId) {
    return /^[a-zA-Z0-9_-]+$/.test(runId);
}

// Safely load JSON from a file path
function loadJson(filePath) {
    if (!fs.existsSync(filePath)) {
        return null;
    }
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

// ---- API Endpoints ----

// List all eval runs (newest first)
app.get('/api/agent-eval/runs', (req, res) => {
    try {
        if (!fs.existsSync(RESULTS_DIR)) {
            return res.json([]);
        }

        const entries = fs.readdirSync(RESULTS_DIR, { withFileTypes: true });
        const runs = [];

        for (const entry of entries) {
            if (!entry.isDirectory()) continue;
            if (!entry.name.match(/^eval-/) && entry.name !== 'rerun') continue;

            const scorecardPath = path.join(RESULTS_DIR, entry.name, 'scorecard.json');
            if (!fs.existsSync(scorecardPath)) continue;

            try {
                const scorecard = JSON.parse(fs.readFileSync(scorecardPath, 'utf8'));
                runs.push({
                    run_id: scorecard.run_id || entry.name,
                    timestamp: scorecard.timestamp || null,
                    config: scorecard.config || {},
                    summary: scorecard.summary || {},
                    cost: scorecard.cost || null,
                    dir_name: entry.name
                });
            } catch (parseErr) {
                // Skip runs with invalid scorecard
            }
        }

        // Sort newest first by timestamp, then by run_id
        runs.sort((a, b) => {
            if (a.timestamp && b.timestamp) {
                return b.timestamp.localeCompare(a.timestamp);
            }
            return b.run_id.localeCompare(a.run_id);
        });

        res.json(runs);
    } catch (error) {
        res.status(500).json({ error: 'Failed to list runs', details: error.message });
    }
});

// Load full scorecard for a run
app.get('/api/agent-eval/runs/:runId', (req, res) => {
    try {
        const runId = req.params.runId;
        if (!isValidRunId(runId)) {
            return res.status(400).json({ error: 'Invalid run ID' });
        }

        const scorecardPath = path.join(RESULTS_DIR, runId, 'scorecard.json');
        const scorecard = loadJson(scorecardPath);

        if (!scorecard) {
            return res.status(404).json({ error: 'Scorecard not found' });
        }

        res.json(scorecard);
    } catch (error) {
        res.status(500).json({ error: 'Failed to load scorecard', details: error.message });
    }
});

// Load single scenario trace
app.get('/api/agent-eval/runs/:runId/scenario/:id', (req, res) => {
    try {
        const runId = req.params.runId;
        const scenarioId = req.params.id;

        if (!isValidRunId(runId)) {
            return res.status(400).json({ error: 'Invalid run ID' });
        }
        if (!isValidRunId(scenarioId)) {
            return res.status(400).json({ error: 'Invalid scenario ID' });
        }

        const tracePath = path.join(RESULTS_DIR, runId, 'traces', scenarioId + '.json');
        const trace = loadJson(tracePath);

        if (!trace) {
            return res.status(404).json({ error: 'Scenario trace not found' });
        }

        res.json(trace);
    } catch (error) {
        res.status(500).json({ error: 'Failed to load scenario trace', details: error.message });
    }
});

// Compare two scorecards
app.get('/api/agent-eval/compare', (req, res) => {
    try {
        const baselineId = req.query.baseline;
        const currentId = req.query.current;

        if (!baselineId || !currentId) {
            return res.status(400).json({ error: 'Both baseline and current query params required' });
        }

        // Allow "baseline" as a special ID for baseline.json
        let baselineData;
        if (baselineId === 'baseline') {
            const baselinePath = path.join(RESULTS_DIR, 'baseline.json');
            baselineData = loadJson(baselinePath);
            if (!baselineData) {
                return res.status(404).json({ error: 'baseline.json not found' });
            }
        } else {
            if (!isValidRunId(baselineId)) {
                return res.status(400).json({ error: 'Invalid baseline run ID' });
            }
            baselineData = loadJson(path.join(RESULTS_DIR, baselineId, 'scorecard.json'));
            if (!baselineData) {
                return res.status(404).json({ error: 'Baseline scorecard not found' });
            }
        }

        if (!isValidRunId(currentId)) {
            return res.status(400).json({ error: 'Invalid current run ID' });
        }
        const currentData = loadJson(path.join(RESULTS_DIR, currentId, 'scorecard.json'));
        if (!currentData) {
            return res.status(404).json({ error: 'Current scorecard not found' });
        }

        // Build scenario maps
        function scenarioMap(sc) {
            var map = {};
            var scenarios = sc.scenarios || [];
            for (var i = 0; i < scenarios.length; i++) {
                map[scenarios[i].scenario_id] = scenarios[i];
            }
            return map;
        }

        var baseMap = scenarioMap(baselineData);
        var currMap = scenarioMap(currentData);

        // Collect all scenario IDs
        var allIds = Object.keys(baseMap).concat(Object.keys(currMap));
        var uniqueIds = [];
        var seen = {};
        for (var i = 0; i < allIds.length; i++) {
            if (!seen[allIds[i]]) {
                seen[allIds[i]] = true;
                uniqueIds.push(allIds[i]);
            }
        }
        uniqueIds.sort();

        var improved = [];
        var regressed = [];
        var unchanged = [];
        var only_in_baseline = [];
        var only_in_current = [];

        for (var j = 0; j < uniqueIds.length; j++) {
            var sid = uniqueIds[j];
            if (baseMap[sid] && !currMap[sid]) {
                only_in_baseline.push(sid);
                continue;
            }
            if (!baseMap[sid] && currMap[sid]) {
                only_in_current.push(sid);
                continue;
            }

            var b = baseMap[sid];
            var c = currMap[sid];
            var bPass = b.status === 'PASS';
            var cPass = c.status === 'PASS';
            var bScore = b.overall_score || 0;
            var cScore = c.overall_score || 0;
            var delta = cScore - bScore;

            var entry = {
                scenario_id: sid,
                baseline_status: b.status,
                current_status: c.status,
                baseline_score: bScore,
                current_score: cScore,
                delta: Math.round(delta * 100) / 100
            };

            if (!bPass && cPass) {
                improved.push(entry);
            } else if (bPass && !cPass) {
                regressed.push(entry);
            } else {
                unchanged.push(entry);
            }
        }

        res.json({
            baseline: {
                run_id: baselineData.run_id || baselineId,
                summary: baselineData.summary
            },
            current: {
                run_id: currentData.run_id || currentId,
                summary: currentData.summary
            },
            improved: improved,
            regressed: regressed,
            unchanged: unchanged,
            only_in_baseline: only_in_baseline,
            only_in_current: only_in_current
        });
    } catch (error) {
        res.status(500).json({ error: 'Failed to compare scorecards', details: error.message });
    }
});

// Get eval status
app.get('/api/agent-eval/status', (req, res) => {
    res.json(evalStatus);
});

// Load baseline.json
app.get('/api/agent-eval/baseline', (req, res) => {
    try {
        const baselinePath = path.join(RESULTS_DIR, 'baseline.json');
        const baseline = loadJson(baselinePath);

        if (!baseline) {
            return res.status(404).json({ error: 'No baseline.json found' });
        }

        res.json(baseline);
    } catch (error) {
        res.status(500).json({ error: 'Failed to load baseline', details: error.message });
    }
});

// Save a run as baseline
app.post('/api/agent-eval/baseline', (req, res) => {
    try {
        var runId = req.body.runId;
        if (!runId || !isValidRunId(runId)) {
            return res.status(400).json({ error: 'Invalid or missing runId' });
        }

        var scorecardPath = path.join(RESULTS_DIR, runId, 'scorecard.json');
        if (!fs.existsSync(scorecardPath)) {
            return res.status(404).json({ error: 'Scorecard not found for run: ' + runId });
        }

        var baselinePath = path.join(RESULTS_DIR, 'baseline.json');
        fs.copyFileSync(scorecardPath, baselinePath);

        res.json({ success: true, message: 'Baseline saved from run ' + runId });
    } catch (error) {
        res.status(500).json({ error: 'Failed to save baseline', details: error.message });
    }
});

// Start an eval run
app.post('/api/agent-eval/start', (req, res) => {
    try {
        if (evalProcess) {
            return res.status(409).json({ error: 'An eval is already running' });
        }

        var args = ['run', 'python', '-m', 'gaia.cli', 'eval', 'agent'];

        if (req.body.scenario) {
            args.push('--scenario', req.body.scenario);
        }
        if (req.body.category) {
            args.push('--category', req.body.category);
        }
        if (req.body.fix) {
            args.push('--fix');
        }

        evalStatus = { running: true, current_scenario: 'starting...', progress: { done: 0, total: 0 } };

        evalProcess = spawn('uv', args, {
            cwd: REPO_ROOT,
            stdio: ['ignore', 'pipe', 'pipe'],
            shell: true
        });

        var outputLines = [];

        evalProcess.stdout.on('data', function(data) {
            var lines = data.toString().split('\n');
            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].trim();
                if (!line) continue;
                outputLines.push(line);
                if (outputLines.length > 50) outputLines.shift();

                // Parse progress from output
                var progressMatch = line.match(/\[(\d+)\/(\d+)\]/);
                if (progressMatch) {
                    evalStatus.progress.done = parseInt(progressMatch[1], 10);
                    evalStatus.progress.total = parseInt(progressMatch[2], 10);
                }

                // Parse scenario name
                var scenarioMatch = line.match(/scenario[:\s]+(\S+)/i);
                if (scenarioMatch) {
                    evalStatus.current_scenario = scenarioMatch[1];
                }
            }
        });

        evalProcess.stderr.on('data', function(data) {
            var lines = data.toString().split('\n');
            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].trim();
                if (line) {
                    outputLines.push('[stderr] ' + line);
                    if (outputLines.length > 50) outputLines.shift();
                }
            }
        });

        evalProcess.on('close', function(code) {
            evalStatus = { running: false, current_scenario: null, progress: evalStatus.progress, exit_code: code };
            evalProcess = null;
        });

        evalProcess.on('error', function(err) {
            evalStatus = { running: false, current_scenario: null, progress: { done: 0, total: 0 }, error: err.message };
            evalProcess = null;
        });

        res.json({ success: true, message: 'Eval started' });
    } catch (error) {
        res.status(500).json({ error: 'Failed to start eval', details: error.message });
    }
});

// Stop a running eval
app.post('/api/agent-eval/stop', (req, res) => {
    try {
        if (!evalProcess) {
            return res.status(404).json({ error: 'No eval is currently running' });
        }

        evalProcess.kill('SIGTERM');
        evalProcess = null;
        evalStatus = { running: false, current_scenario: null, progress: evalStatus.progress };

        res.json({ success: true, message: 'Eval stopped' });
    } catch (error) {
        res.status(500).json({ error: 'Failed to stop eval', details: error.message });
    }
});

// Serve the main application
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

app.listen(PORT, function() {
    console.log('GAIA Agent Eval webapp running on http://localhost:' + PORT);
    console.log('Results directory: ' + RESULTS_DIR);
    console.log('Repo root: ' + REPO_ROOT);
});
