// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

(function() {
    'use strict';

    // ---- State ----
    var runs = [];
    var selectedRunId = null;
    var selectedScorecard = null;
    var statusPollTimer = null;

    // ---- Helpers ----

    function escapeHtml(text) {
        if (!text) return '';
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(text));
        return div.innerHTML;
    }

    function scoreColorClass(score) {
        if (score >= 8.0) return 'score-green';
        if (score >= 6.0) return 'score-orange';
        return 'score-red';
    }

    function statusClass(status) {
        if (!status) return '';
        var s = status.toUpperCase();
        if (s === 'PASS') return 'pass';
        if (s === 'FAIL') return 'fail';
        if (s === 'TIMEOUT' || s === 'ERRORED') return 'timeout';
        if (s === 'BLOCKED') return 'blocked';
        return '';
    }

    function formatTimestamp(ts) {
        if (!ts) return '';
        try {
            var d = new Date(ts);
            return d.toLocaleString();
        } catch (e) {
            return ts;
        }
    }

    function formatPercent(val) {
        if (val === null || val === undefined) return '-';
        return (val * 100).toFixed(1) + '%';
    }

    function formatScore(val) {
        if (val === null || val === undefined) return '-';
        return Number(val).toFixed(1);
    }

    function barColor(passRate) {
        if (passRate >= 0.8) return 'var(--green)';
        if (passRate >= 0.5) return 'var(--orange)';
        return 'var(--red)';
    }

    // ---- API calls ----

    function fetchJson(url) {
        return fetch(url).then(function(res) {
            if (!res.ok) {
                return res.json().then(function(data) {
                    throw new Error(data.error || ('Request failed: ' + res.status));
                }, function() {
                    throw new Error('Request failed: ' + res.status);
                });
            }
            return res.json();
        });
    }

    function postJson(url, body) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {})
        }).then(function(res) {
            return res.json();
        });
    }

    // ---- Navigation ----

    function initNav() {
        var btns = document.querySelectorAll('.nav-btn');
        for (var i = 0; i < btns.length; i++) {
            btns[i].addEventListener('click', function() {
                var viewName = this.getAttribute('data-view');
                showView(viewName);
            });
        }
    }

    function showView(name) {
        var views = document.querySelectorAll('.view');
        var btns = document.querySelectorAll('.nav-btn');
        for (var i = 0; i < views.length; i++) {
            views[i].classList.remove('active');
        }
        for (var j = 0; j < btns.length; j++) {
            btns[j].classList.remove('active');
        }
        var view = document.getElementById('view-' + name);
        if (view) view.classList.add('active');
        var btn = document.querySelector('.nav-btn[data-view="' + name + '"]');
        if (btn) btn.classList.add('active');
    }

    // ---- Runs List ----

    function loadRuns() {
        fetchJson('/api/agent-eval/runs').then(function(data) {
            runs = data;
            renderRunsList();
            populateCompareSelectors();
            populateBaselineSelector();
        }).catch(function(err) {
            document.getElementById('runsList').innerHTML =
                '<div class="empty-state">Failed to load runs: ' + escapeHtml(err.message) + '</div>';
        });
    }

    function renderRunsList() {
        var container = document.getElementById('runsList');
        if (!runs || runs.length === 0) {
            container.innerHTML = '<div class="empty-state">No eval runs found</div>';
            return;
        }

        var html = '';
        for (var i = 0; i < runs.length; i++) {
            var run = runs[i];
            var summary = run.summary || {};
            var passRate = summary.pass_rate || 0;
            var avgScore = summary.avg_score || 0;
            var total = summary.total_scenarios || 0;
            var passed = summary.passed || 0;
            var failed = summary.failed || 0;
            var selected = (run.run_id === selectedRunId || run.dir_name === selectedRunId) ? ' selected' : '';

            html += '<div class="run-item' + selected + '" data-run-id="' + escapeHtml(run.dir_name || run.run_id) + '">';
            html += '  <div class="run-item-header">';
            html += '    <span class="run-item-id">' + escapeHtml(run.run_id) + '</span>';
            html += '    <span class="run-item-time">' + escapeHtml(formatTimestamp(run.timestamp)) + '</span>';
            html += '  </div>';
            html += '  <div class="run-item-stats">';
            html += '    <span class="run-item-score ' + scoreColorClass(avgScore) + '">' + formatScore(avgScore) + '</span>';
            html += '    <div class="category-bar"><div class="category-bar-fill" style="width:' + (passRate * 100) + '%;background:' + barColor(passRate) + '"></div></div>';
            html += '    <span class="run-item-count">' + passed + '/' + total + ' passed</span>';
            html += '  </div>';
            html += '</div>';
        }

        container.innerHTML = html;

        // Attach click handlers
        var items = container.querySelectorAll('.run-item');
        for (var j = 0; j < items.length; j++) {
            items[j].addEventListener('click', function() {
                var runId = this.getAttribute('data-run-id');
                selectRun(runId);
            });
        }
    }

    function selectRun(runId) {
        selectedRunId = runId;
        renderRunsList();

        var detailEl = document.getElementById('runDetail');
        detailEl.innerHTML = '<div class="empty-state">Loading...</div>';

        fetchJson('/api/agent-eval/runs/' + encodeURIComponent(runId)).then(function(scorecard) {
            selectedScorecard = scorecard;
            renderRunDetail(scorecard);
        }).catch(function(err) {
            detailEl.innerHTML = '<div class="empty-state">Failed to load: ' + escapeHtml(err.message) + '</div>';
        });
    }

    // ---- Run Detail ----

    function renderRunDetail(sc) {
        var el = document.getElementById('runDetail');
        var summary = sc.summary || {};
        var config = sc.config || {};
        var cost = sc.cost || {};

        var html = '';

        // Header
        html += '<div class="run-detail-header">';
        html += '  <h2>' + escapeHtml(sc.run_id) + '</h2>';
        html += '  <div class="meta">' + escapeHtml(formatTimestamp(sc.timestamp));
        if (config.model) html += ' &middot; Model: ' + escapeHtml(config.model);
        html += '</div>';
        html += '</div>';

        // Summary cards
        html += '<div class="summary-cards">';
        html += summaryCard('Pass Rate', formatPercent(summary.pass_rate), (summary.passed || 0) + ' of ' + (summary.total_scenarios || 0));
        html += summaryCard('Avg Score', formatScore(summary.avg_score), '');
        html += summaryCard('Failed', String(summary.failed || 0), (summary.errored || 0) + ' errored');
        if (cost.estimated_total_usd !== undefined) {
            html += summaryCard('Est. Cost', '$' + Number(cost.estimated_total_usd).toFixed(2), '');
        }
        html += '</div>';

        // Category breakdown
        var byCategory = summary.by_category || {};
        var categories = Object.keys(byCategory);
        if (categories.length > 0) {
            html += '<div class="category-section">';
            html += '<h3>Categories</h3>';
            html += '<table class="category-table">';
            html += '<thead><tr><th>Category</th><th>Passed</th><th>Failed</th><th>Avg Score</th><th>Pass Rate</th></tr></thead>';
            html += '<tbody>';
            for (var i = 0; i < categories.length; i++) {
                var cat = byCategory[categories[i]];
                var catTotal = (cat.passed || 0) + (cat.failed || 0) + (cat.blocked || 0) + (cat.errored || 0);
                var catRate = catTotal > 0 ? (cat.passed || 0) / catTotal : 0;
                html += '<tr>';
                html += '<td>' + escapeHtml(categories[i]) + '</td>';
                html += '<td>' + (cat.passed || 0) + '</td>';
                html += '<td>' + (cat.failed || 0) + '</td>';
                html += '<td class="' + scoreColorClass(cat.avg_score || 0) + '">' + formatScore(cat.avg_score) + '</td>';
                html += '<td>' + formatPercent(catRate) + '</td>';
                html += '</tr>';
            }
            html += '</tbody></table>';
            html += '</div>';
        }

        // Scenarios
        var scenarios = sc.scenarios || [];
        if (scenarios.length > 0) {
            html += '<div class="scenario-section">';
            html += '<h3>Scenarios (' + scenarios.length + ')</h3>';
            for (var j = 0; j < scenarios.length; j++) {
                html += renderScenarioRow(scenarios[j], j);
            }
            html += '</div>';
        }

        el.innerHTML = html;

        // Attach toggle handlers for scenarios
        var headers = el.querySelectorAll('.scenario-header');
        for (var k = 0; k < headers.length; k++) {
            headers[k].addEventListener('click', function() {
                var idx = this.getAttribute('data-idx');
                toggleScenario(idx);
            });
        }
    }

    function summaryCard(label, value, sub) {
        var html = '<div class="summary-card">';
        html += '<div class="label">' + escapeHtml(label) + '</div>';
        html += '<div class="value">' + escapeHtml(value) + '</div>';
        if (sub) html += '<div class="sub">' + escapeHtml(sub) + '</div>';
        html += '</div>';
        return html;
    }

    function renderScenarioRow(scenario, idx) {
        var status = scenario.status || 'UNKNOWN';
        var sc = statusClass(status);
        var score = scenario.overall_score;

        var html = '<div class="scenario-row ' + sc + '">';

        // Header row
        html += '<div class="scenario-header" data-idx="' + idx + '">';
        html += '<span class="scenario-expand" id="expand-' + idx + '">&#9654;</span>';
        html += '<span class="scenario-id">' + escapeHtml(scenario.scenario_id) + '</span>';
        html += '<span class="scenario-status ' + sc + '">' + escapeHtml(status) + '</span>';
        html += '<span class="score-badge ' + scoreColorClass(score) + '">' + formatScore(score) + '</span>';
        html += '</div>';

        // Collapsible detail
        html += '<div class="turn-detail" id="detail-' + idx + '">';

        // Root cause and recommended fix
        if (scenario.root_cause) {
            html += '<div class="root-cause-box">';
            html += '<h4>Root Cause</h4>';
            html += '<p>' + escapeHtml(scenario.root_cause) + '</p>';
            html += '</div>';
        }

        if (scenario.recommended_fix) {
            var fix = scenario.recommended_fix;
            html += '<div class="recommended-fix-box">';
            html += '<h4>Recommended Fix</h4>';
            if (fix.target) html += '<p><span class="fix-target">Target:</span> ' + escapeHtml(fix.target) + '</p>';
            if (fix.file) html += '<p><span class="fix-target">File:</span> ' + escapeHtml(fix.file) + '</p>';
            if (fix.description) html += '<p>' + escapeHtml(fix.description) + '</p>';
            html += '</div>';
        }

        // Elapsed time / cost
        if (scenario.elapsed_s || (scenario.cost_estimate && scenario.cost_estimate.estimated_usd)) {
            html += '<div style="font-size:12px;color:var(--text-muted);margin-top:8px;">';
            if (scenario.elapsed_s) html += 'Duration: ' + Number(scenario.elapsed_s).toFixed(1) + 's';
            if (scenario.cost_estimate && scenario.cost_estimate.estimated_usd) {
                html += (scenario.elapsed_s ? ' &middot; ' : '') + 'Est. cost: $' + Number(scenario.cost_estimate.estimated_usd).toFixed(2);
            }
            html += '</div>';
        }

        // Turns
        var turns = scenario.turns || [];
        for (var t = 0; t < turns.length; t++) {
            html += renderTurnCard(turns[t]);
        }

        html += '</div>'; // .turn-detail
        html += '</div>'; // .scenario-row

        return html;
    }

    function renderTurnCard(turn) {
        var html = '<div class="turn-card">';

        // Header
        html += '<div class="turn-card-header">';
        html += '<span class="turn-number">Turn ' + (turn.turn || '?') + '</span>';
        html += '<span class="score-badge ' + scoreColorClass(turn.overall_score || 0) + '">' + formatScore(turn.overall_score) + '</span>';
        var passText = turn.pass ? 'PASS' : 'FAIL';
        var passClass = turn.pass ? 'pass' : 'fail';
        html += '<span class="turn-pass-badge ' + passClass + '">' + passText + '</span>';
        html += '</div>';

        // User message
        html += '<div class="turn-message">';
        html += '<div class="turn-message-label">User</div>';
        html += '<div class="turn-message-text">' + escapeHtml(turn.user_message || '') + '</div>';
        html += '</div>';

        // Agent response
        html += '<div class="turn-message">';
        html += '<div class="turn-message-label">Agent</div>';
        html += '<div class="turn-message-text">' + escapeHtml(turn.agent_response || '') + '</div>';
        html += '</div>';

        // Tools
        if (turn.agent_tools && turn.agent_tools.length > 0) {
            html += '<div class="turn-tools">';
            html += '<div class="turn-tools-label">Tools Used</div>';
            for (var i = 0; i < turn.agent_tools.length; i++) {
                html += '<span class="tool-tag">' + escapeHtml(turn.agent_tools[i]) + '</span>';
            }
            html += '</div>';
        }

        // Scores grid
        if (turn.scores) {
            html += '<div class="turn-scores">';
            html += '<div class="turn-scores-grid">';
            var keys = Object.keys(turn.scores);
            for (var j = 0; j < keys.length; j++) {
                var sv = turn.scores[keys[j]];
                html += '<div class="turn-score-item">';
                html += '<span class="score-label">' + escapeHtml(keys[j]) + ':</span>';
                html += '<span class="score-value ' + scoreColorClass(sv) + '">' + sv + '</span>';
                html += '</div>';
            }
            html += '</div>';
            html += '</div>';
        }

        // Failure category
        if (turn.failure_category) {
            html += '<div style="font-size:12px;color:var(--red);margin-bottom:4px;">Failure: ' + escapeHtml(turn.failure_category) + '</div>';
        }

        // Reasoning
        if (turn.reasoning) {
            html += '<div class="turn-reasoning">' + escapeHtml(turn.reasoning) + '</div>';
        }

        html += '</div>'; // .turn-card
        return html;
    }

    function toggleScenario(idx) {
        var detail = document.getElementById('detail-' + idx);
        var expand = document.getElementById('expand-' + idx);
        if (!detail) return;
        if (detail.classList.contains('open')) {
            detail.classList.remove('open');
            if (expand) expand.classList.remove('open');
        } else {
            detail.classList.add('open');
            if (expand) expand.classList.add('open');
        }
    }

    // ---- Compare ----

    function populateCompareSelectors() {
        var selA = document.getElementById('compareA');
        var selB = document.getElementById('compareB');
        var optionsHtml = '<option value="">Select run...</option>';
        optionsHtml += '<option value="baseline">baseline.json</option>';
        for (var i = 0; i < runs.length; i++) {
            var r = runs[i];
            var label = r.run_id + ' (' + formatPercent(r.summary.pass_rate) + ')';
            optionsHtml += '<option value="' + escapeHtml(r.dir_name || r.run_id) + '">' + escapeHtml(label) + '</option>';
        }
        selA.innerHTML = optionsHtml;
        selB.innerHTML = optionsHtml;
    }

    function initCompare() {
        document.getElementById('compareBtn').addEventListener('click', function() {
            var a = document.getElementById('compareA').value;
            var b = document.getElementById('compareB').value;
            if (!a || !b) {
                alert('Please select both runs');
                return;
            }
            runCompare(a, b);
        });
    }

    function runCompare(baselineId, currentId) {
        var el = document.getElementById('compareResults');
        el.innerHTML = '<div class="empty-state">Comparing...</div>';

        fetchJson('/api/agent-eval/compare?baseline=' + encodeURIComponent(baselineId) + '&current=' + encodeURIComponent(currentId))
            .then(function(data) {
                renderCompareResults(data);
            })
            .catch(function(err) {
                el.innerHTML = '<div class="empty-state">Compare failed: ' + escapeHtml(err.message) + '</div>';
            });
    }

    function renderCompareResults(data) {
        var el = document.getElementById('compareResults');
        var html = '';

        // Summary comparison
        var bs = data.baseline.summary || {};
        var cs = data.current.summary || {};

        html += '<div class="compare-summary">';
        html += '<div class="compare-summary-card">';
        html += '<h4>Baseline: ' + escapeHtml(data.baseline.run_id) + '</h4>';
        html += '<div class="stat-row"><span>Pass Rate</span><span>' + formatPercent(bs.pass_rate) + '</span></div>';
        html += '<div class="stat-row"><span>Avg Score</span><span>' + formatScore(bs.avg_score) + '</span></div>';
        html += '<div class="stat-row"><span>Total</span><span>' + (bs.total_scenarios || 0) + '</span></div>';
        html += '</div>';

        html += '<div class="compare-summary-card">';
        html += '<h4>Current: ' + escapeHtml(data.current.run_id) + '</h4>';
        html += '<div class="stat-row"><span>Pass Rate</span><span>' + formatPercent(cs.pass_rate) + '</span></div>';
        html += '<div class="stat-row"><span>Avg Score</span><span>' + formatScore(cs.avg_score) + '</span></div>';
        html += '<div class="stat-row"><span>Total</span><span>' + (cs.total_scenarios || 0) + '</span></div>';
        html += '</div>';
        html += '</div>';

        // Regressed (show first since these are most important)
        if (data.regressed.length > 0) {
            html += renderCompareGroup('REGRESSED', data.regressed, 'var(--red)');
        }

        // Improved
        if (data.improved.length > 0) {
            html += renderCompareGroup('IMPROVED', data.improved, 'var(--green)');
        }

        // Unchanged
        if (data.unchanged.length > 0) {
            html += renderCompareGroup('UNCHANGED', data.unchanged, 'var(--text-muted)');
        }

        // Only in baseline / current
        if (data.only_in_baseline.length > 0) {
            html += '<div class="compare-group">';
            html += '<h3 style="color:var(--orange)">Only in Baseline <span class="count">(' + data.only_in_baseline.length + ')</span></h3>';
            html += '<p style="font-size:13px;color:var(--text-secondary)">' + data.only_in_baseline.map(escapeHtml).join(', ') + '</p>';
            html += '</div>';
        }

        if (data.only_in_current.length > 0) {
            html += '<div class="compare-group">';
            html += '<h3 style="color:var(--accent)">Only in Current <span class="count">(' + data.only_in_current.length + ')</span></h3>';
            html += '<p style="font-size:13px;color:var(--text-secondary)">' + data.only_in_current.map(escapeHtml).join(', ') + '</p>';
            html += '</div>';
        }

        if (!data.improved.length && !data.regressed.length && !data.unchanged.length &&
            !data.only_in_baseline.length && !data.only_in_current.length) {
            html += '<div class="empty-state">No scenarios to compare</div>';
        }

        el.innerHTML = html;
    }

    function renderCompareGroup(title, items, color) {
        var html = '<div class="compare-group">';
        html += '<h3 style="color:' + color + '">' + escapeHtml(title) + ' <span class="count">(' + items.length + ')</span></h3>';
        html += '<table class="compare-table">';
        html += '<thead><tr><th>Scenario</th><th>Baseline</th><th>Current</th><th>Delta</th></tr></thead>';
        html += '<tbody>';
        for (var i = 0; i < items.length; i++) {
            var item = items[i];
            var deltaClass = item.delta > 0 ? 'delta-positive' : (item.delta < 0 ? 'delta-negative' : '');
            var deltaStr = item.delta > 0 ? '+' + item.delta.toFixed(1) : item.delta.toFixed(1);
            html += '<tr>';
            html += '<td style="font-family:monospace">' + escapeHtml(item.scenario_id) + '</td>';
            html += '<td><span class="scenario-status ' + statusClass(item.baseline_status) + '">' + escapeHtml(item.baseline_status) + '</span> ' + formatScore(item.baseline_score) + '</td>';
            html += '<td><span class="scenario-status ' + statusClass(item.current_status) + '">' + escapeHtml(item.current_status) + '</span> ' + formatScore(item.current_score) + '</td>';
            html += '<td class="' + deltaClass + '">' + deltaStr + '</td>';
            html += '</tr>';
        }
        html += '</tbody></table>';
        html += '</div>';
        return html;
    }

    // ---- Control Panel ----

    function initControl() {
        document.getElementById('runAllBtn').addEventListener('click', function() {
            startEval({});
        });
        document.getElementById('runFixBtn').addEventListener('click', function() {
            startEval({ fix: true });
        });
        document.getElementById('stopBtn').addEventListener('click', function() {
            stopEval();
        });
        document.getElementById('saveBaselineBtn').addEventListener('click', function() {
            saveBaseline();
        });
    }

    function startEval(opts) {
        postJson('/api/agent-eval/start', opts).then(function(data) {
            if (data.error) {
                alert('Error: ' + data.error);
            }
        }).catch(function(err) {
            alert('Failed to start eval: ' + err.message);
        });
    }

    function stopEval() {
        postJson('/api/agent-eval/stop').then(function(data) {
            if (data.error) {
                alert('Error: ' + data.error);
            }
        }).catch(function(err) {
            alert('Failed to stop eval: ' + err.message);
        });
    }

    function saveBaseline() {
        var sel = document.getElementById('baselineSelect');
        var runId = sel.value;
        if (!runId) {
            alert('Please select a run');
            return;
        }
        postJson('/api/agent-eval/baseline', { runId: runId }).then(function(data) {
            if (data.error) {
                alert('Error: ' + data.error);
            } else {
                alert('Baseline saved from ' + runId);
                loadBaselineInfo();
            }
        }).catch(function(err) {
            alert('Failed to save baseline: ' + err.message);
        });
    }

    function populateBaselineSelector() {
        var sel = document.getElementById('baselineSelect');
        var html = '<option value="">Select run...</option>';
        for (var i = 0; i < runs.length; i++) {
            var r = runs[i];
            html += '<option value="' + escapeHtml(r.dir_name || r.run_id) + '">' + escapeHtml(r.run_id) + '</option>';
        }
        sel.innerHTML = html;
    }

    function loadBaselineInfo() {
        fetchJson('/api/agent-eval/baseline').then(function(data) {
            var info = document.getElementById('baselineInfo');
            var s = data.summary || {};
            info.textContent = 'Baseline: ' + (data.run_id || 'unknown') +
                ' | Pass rate: ' + formatPercent(s.pass_rate) +
                ' | Avg score: ' + formatScore(s.avg_score) +
                ' | Scenarios: ' + (s.total_scenarios || 0);
        }).catch(function() {
            document.getElementById('baselineInfo').textContent = 'No baseline saved';
        });
    }

    // ---- Status Polling ----

    function pollStatus() {
        fetchJson('/api/agent-eval/status').then(function(data) {
            updateStatusUI(data);
        }).catch(function() {
            // Ignore polling errors
        });
    }

    function updateStatusUI(status) {
        var badge = document.getElementById('statusBadge');
        var stopBtn = document.getElementById('stopBtn');
        var progressFill = document.getElementById('progressFill');
        var progressText = document.getElementById('progressText');
        var currentScenario = document.getElementById('currentScenario');

        if (status.running) {
            badge.textContent = 'RUNNING';
            badge.className = 'status-badge running';
            stopBtn.disabled = false;
        } else {
            badge.textContent = 'IDLE';
            badge.className = 'status-badge idle';
            stopBtn.disabled = true;
        }

        var progress = status.progress || { done: 0, total: 0 };
        var pct = progress.total > 0 ? (progress.done / progress.total * 100) : 0;
        progressFill.style.width = pct + '%';

        if (status.running) {
            progressText.textContent = progress.done + '/' + progress.total;
        } else if (status.exit_code !== undefined) {
            progressText.textContent = 'Done (exit ' + status.exit_code + ')';
            // Refresh runs list after completion
            loadRuns();
        } else {
            progressText.textContent = 'Idle';
        }

        if (status.current_scenario) {
            currentScenario.textContent = 'Current: ' + status.current_scenario;
        } else {
            currentScenario.textContent = '';
        }
    }

    // ---- Refresh button ----

    function initRefresh() {
        document.getElementById('refreshRunsBtn').addEventListener('click', function() {
            loadRuns();
        });
    }

    // ---- Init ----

    function init() {
        initNav();
        initCompare();
        initControl();
        initRefresh();
        loadRuns();
        loadBaselineInfo();

        // Poll status every 3 seconds
        pollStatus();
        statusPollTimer = setInterval(pollStatus, 3000);
    }

    // Start when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
