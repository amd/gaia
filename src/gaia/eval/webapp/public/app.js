// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

class EvaluationVisualizer {
    constructor() {
        console.log('EvaluationVisualizer constructor called');
        this.loadedReports = new Map();
        this.initializeEventListeners();
        this.loadAvailableFiles();
    }

    // Helper method to identify main evaluation entries (skip individual meeting files)
    isMainEvaluationEntry(evalData) {
        const name = evalData.experiment_name || evalData.file_path || '';
        // Skip entries that are individual meeting files
        const meetingPatterns = [
            'all_hands_meeting_', 'standup_meeting_', 'budget_planning_meeting_',
            'client_call_meeting_', 'design_review_meeting_', 'performance_review_meeting_',
            'planning_meeting_', 'product_roadmap_meeting_', 'transcript_metadata'
        ];
        
        // Check if file_path indicates it's in a subdirectory (meetings/ or misc/)
        if (evalData.file_path && evalData.file_path.includes('/')) {
            return false; // It's an individual file in a subdirectory
        }
        
        return !meetingPatterns.some(pattern => name.includes(pattern));
    }

    initializeEventListeners() {
        const addBtn = document.getElementById('addReportBtn');
        const compareBtn = document.getElementById('compareBtn');
        const clearBtn = document.getElementById('clearBtn');

        if (!addBtn || !compareBtn || !clearBtn) {
            console.error('One or more buttons not found in DOM');
            return;
        }

        console.log('Adding event listeners to buttons');
        addBtn.addEventListener('click', () => {
            console.log('Add Report button clicked');
            this.addSelectedReports();
        });
        compareBtn.addEventListener('click', () => {
            console.log('Compare button clicked');
            this.compareSelected();
        });
        clearBtn.addEventListener('click', () => {
            console.log('Clear button clicked');
            this.clearAllReports();
        });
    }

    async loadAvailableFiles() {
        try {
            console.log('Loading available files...');
            const [filesResponse, testDataResponse, groundtruthResponse] = await Promise.all([
                fetch('/api/files'),
                fetch('/api/test-data'),
                fetch('/api/groundtruth')
            ]);

            console.log('Responses received:', filesResponse.status, testDataResponse.status, groundtruthResponse.status);

            if (!filesResponse.ok) {
                throw new Error(`HTTP error! status: ${filesResponse.status}`);
            }

            const filesData = await filesResponse.json();
            const testData = testDataResponse.ok ? await testDataResponse.json() : { directories: [] };
            const groundtruthData = groundtruthResponse.ok ? await groundtruthResponse.json() : { files: [] };

            console.log('Data received:', { files: filesData, testData, groundtruthData });

            this.populateFileSelects({ ...filesData, testData, groundtruthData });
        } catch (error) {
            console.error('Failed to load available files:', error);
            this.showError('Failed to load available files');
        }
    }

    populateFileSelects(data) {
        console.log('Populating file selects with data:', data);
        const experimentSelect = document.getElementById('experimentSelect');
        const evaluationSelect = document.getElementById('evaluationSelect');
        const testDataSelect = document.getElementById('testDataSelect');
        const groundtruthSelect = document.getElementById('groundtruthSelect');
        const agentOutputSelect = document.getElementById('agentOutputSelect');

        if (!experimentSelect || !evaluationSelect || !testDataSelect || !groundtruthSelect || !agentOutputSelect) {
            console.error('Select elements not found in DOM');
            return;
        }

        // Clear existing options
        experimentSelect.innerHTML = '';
        evaluationSelect.innerHTML = '';
        testDataSelect.innerHTML = '';
        groundtruthSelect.innerHTML = '';
        agentOutputSelect.innerHTML = '';

        // Populate experiments
        if (data.experiments.length === 0) {
            experimentSelect.innerHTML = '<option disabled>No experiment files found</option>';
        } else {
            console.log(`Adding ${data.experiments.length} experiment files`);
            data.experiments.forEach(file => {
                const option = document.createElement('option');
                option.value = file.name;
                option.textContent = file.name.replace('.experiment.json', '');
                option.title = file.name; // Add tooltip showing full filename
                experimentSelect.appendChild(option);
            });
        }

        // Populate evaluations
        if (data.evaluations.length === 0) {
            evaluationSelect.innerHTML = '<option disabled>No evaluation files found</option>';
        } else {
            console.log(`Adding ${data.evaluations.length} evaluation files`);
            data.evaluations.forEach(file => {
                const option = document.createElement('option');
                option.value = file.name;
                option.textContent = file.name.replace('.experiment.eval.json', '');
                option.title = file.name; // Add tooltip showing full filename
                evaluationSelect.appendChild(option);
            });
        }

        // Populate test data
        if (!data.testData || data.testData.directories.length === 0) {
            testDataSelect.innerHTML = '<option disabled>No test data found</option>';
        } else {
            console.log(`Adding ${data.testData.directories.length} test data directories`);
            data.testData.directories.forEach(dir => {
                dir.files.forEach(file => {
                    const option = document.createElement('option');
                    const fullPath = `${dir.name}/${file}`;
                    option.value = fullPath;
                    option.textContent = `${dir.name}/${file.replace('.txt', '')}`;
                    option.title = fullPath; // Add tooltip showing full path
                    testDataSelect.appendChild(option);
                });
            });
        }

        // Populate groundtruth
        if (!data.groundtruthData || data.groundtruthData.files.length === 0) {
            groundtruthSelect.innerHTML = '<option disabled>No groundtruth files found</option>';
        } else {
            console.log(`Adding ${data.groundtruthData.files.length} groundtruth files`);
            data.groundtruthData.files.forEach(file => {
                const option = document.createElement('option');
                option.value = file.path;
                const displayName = file.name
                    .replace('.summarization.groundtruth.json', '')
                    .replace('.qa.groundtruth.json', '')
                    .replace('.groundtruth.json', '');
                option.textContent = file.directory === 'root' ? displayName : `${file.directory}/${displayName}`;
                if (file.type === 'consolidated') {
                    option.textContent += ' [Consolidated]';
                }
                option.title = file.path; // Add tooltip showing full path
                groundtruthSelect.appendChild(option);
            });
        }
        
        // Populate agent outputs
        if (!data.agentOutputs || data.agentOutputs.length === 0) {
            agentOutputSelect.innerHTML = '<option disabled>No agent outputs found</option>';
        } else {
            console.log(`Adding ${data.agentOutputs.length} agent output files`);
            data.agentOutputs.forEach(file => {
                const option = document.createElement('option');
                option.value = file.name;
                const displayName = file.name
                    .replace('agent_output_', '')
                    .replace('.json', '');
                option.textContent = file.directory === 'single' ? `${displayName} [Single]` : displayName;
                option.title = file.name; // Add tooltip showing full filename
                agentOutputSelect.appendChild(option);
            });
        }
        
        console.log('File selects populated successfully');

        // Add double-click event listeners to enable direct file loading
        this.addDoubleClickHandlers();
    }

    async addSelectedReports() {
        console.log('addSelectedReports function called');

        const experimentSelect = document.getElementById('experimentSelect');
        const evaluationSelect = document.getElementById('evaluationSelect');
        const testDataSelect = document.getElementById('testDataSelect');
        const groundtruthSelect = document.getElementById('groundtruthSelect');
        const agentOutputSelect = document.getElementById('agentOutputSelect');

        if (!experimentSelect || !evaluationSelect || !testDataSelect || !groundtruthSelect || !agentOutputSelect) {
            console.error('Select elements not found');
            alert('Error: File selection elements not found');
            return;
        }

        const selectedExperiments = Array.from(experimentSelect.selectedOptions);
        const selectedEvaluations = Array.from(evaluationSelect.selectedOptions);
        const selectedTestData = Array.from(testDataSelect.selectedOptions);
        const selectedGroundtruth = Array.from(groundtruthSelect.selectedOptions);
        const selectedAgentOutputs = Array.from(agentOutputSelect.selectedOptions);

        console.log('Selected experiments:', selectedExperiments.length);
        console.log('Selected evaluations:', selectedEvaluations.length);
        console.log('Selected test data:', selectedTestData.length);
        console.log('Selected groundtruth:', selectedGroundtruth.length);
        console.log('Selected agent outputs:', selectedAgentOutputs.length);

        if (selectedExperiments.length === 0 && selectedEvaluations.length === 0 &&
            selectedTestData.length === 0 && selectedGroundtruth.length === 0 && 
            selectedAgentOutputs.length === 0) {
            alert('Please select at least one file to load');
            return;
        }

        // Load selected experiments
        for (const option of selectedExperiments) {
            await this.loadExperiment(option.value);
        }

        // Load selected evaluations
        for (const option of selectedEvaluations) {
            await this.loadEvaluation(option.value);
        }

        // Load selected test data
        for (const option of selectedTestData) {
            await this.loadTestData(option.value);
        }

        // Load selected groundtruth
        for (const option of selectedGroundtruth) {
            await this.loadGroundtruth(option.value);
        }

        // Load selected agent outputs
        for (const option of selectedAgentOutputs) {
            await this.loadAgentOutput(option.value);
        }

        // Clear selections
        experimentSelect.selectedIndex = -1;
        evaluationSelect.selectedIndex = -1;
        testDataSelect.selectedIndex = -1;
        groundtruthSelect.selectedIndex = -1;
        agentOutputSelect.selectedIndex = -1;

        this.updateDisplay();
    }

    addDoubleClickHandlers() {
        const experimentSelect = document.getElementById('experimentSelect');
        const evaluationSelect = document.getElementById('evaluationSelect');
        const testDataSelect = document.getElementById('testDataSelect');
        const groundtruthSelect = document.getElementById('groundtruthSelect');
        const agentOutputSelect = document.getElementById('agentOutputSelect');

        if (experimentSelect) {
            experimentSelect.addEventListener('dblclick', (e) => {
                if (e.target.tagName === 'OPTION' && !e.target.disabled) {
                    this.addSingleReport('experiment', e.target.value);
                }
            });
        }

        if (evaluationSelect) {
            evaluationSelect.addEventListener('dblclick', (e) => {
                if (e.target.tagName === 'OPTION' && !e.target.disabled) {
                    this.addSingleReport('evaluation', e.target.value);
                }
            });
        }

        if (testDataSelect) {
            testDataSelect.addEventListener('dblclick', (e) => {
                if (e.target.tagName === 'OPTION' && !e.target.disabled) {
                    this.addSingleReport('testData', e.target.value);
                }
            });
        }

        if (groundtruthSelect) {
            groundtruthSelect.addEventListener('dblclick', (e) => {
                if (e.target.tagName === 'OPTION' && !e.target.disabled) {
                    this.addSingleReport('groundtruth', e.target.value);
                }
            });
        }

        if (agentOutputSelect) {
            agentOutputSelect.addEventListener('dblclick', (e) => {
                if (e.target.tagName === 'OPTION' && !e.target.disabled) {
                    this.addSingleReport('agentOutput', e.target.value);
                }
            });
        }

        console.log('Double-click handlers added to all select elements');
    }

    async addSingleReport(type, filename) {
        console.log(`Adding single ${type} report: ${filename}`);

        try {
            switch (type) {
                case 'experiment':
                    await this.loadExperiment(filename);
                    break;
                case 'evaluation':
                    await this.loadEvaluation(filename);
                    break;
                case 'testData':
                    await this.loadTestData(filename);
                    break;
                case 'groundtruth':
                    await this.loadGroundtruth(filename);
                    break;
                case 'agentOutput':
                    await this.loadAgentOutput(filename);
                    break;
                default:
                    console.error(`Unknown report type: ${type}`);
                    return;
            }

            this.updateDisplay();
            console.log(`Successfully added ${type} report: ${filename}`);
        } catch (error) {
            console.error(`Failed to add ${type} report:`, error);
            alert(`Failed to load ${type} report: ${filename}`);
        }
    }

    async loadExperiment(filename) {
        try {
            const response = await fetch(`/api/experiment/${filename}`);
            const data = await response.json();

            const reportId = filename.replace('.experiment.json', '');
            this.loadedReports.set(reportId, {
                ...this.loadedReports.get(reportId),
                experiment: data,
                filename: filename,
                type: 'experiment'
            });
        } catch (error) {
            console.error(`Failed to load experiment ${filename}:`, error);
            this.showError(`Failed to load experiment ${filename}`);
        }
    }

    async loadEvaluation(filename) {
        try {
            const response = await fetch(`/api/evaluation/${filename}`);
            const data = await response.json();

            // Check if this is a consolidated evaluation report
            if (data.metadata && data.metadata.report_type === 'consolidated_evaluations') {
                // No need to load experiment files - consolidated report has all the data
                const reportId = filename.replace('.json', '');
                this.loadedReports.set(reportId, {
                    consolidatedEvaluation: data,
                    filename: filename,
                    type: 'consolidated_evaluation'
                });
                return;
            }

            // For individual evaluation files
            const reportId = filename.replace('.experiment.eval.json', '');
            this.loadedReports.set(reportId, {
                ...this.loadedReports.get(reportId),
                evaluation: data,
                filename: filename,
                type: 'evaluation'
            });
        } catch (error) {
            console.error(`Failed to load evaluation ${filename}:`, error);
            this.showError(`Failed to load evaluation ${filename}`);
        }
    }

    async loadTestData(fileSpec) {
        try {
            const [type, filename] = fileSpec.split('/');
            const [contentResponse, metadataResponse] = await Promise.all([
                fetch(`/api/test-data/${type}/${filename}`),
                fetch(`/api/test-data/${type}/metadata`)
            ]);

            if (!contentResponse.ok) {
                throw new Error(`Failed to load test data: ${contentResponse.status}`);
            }

            const contentData = await contentResponse.json();
            const metadataData = metadataResponse.ok ? await metadataResponse.json() : null;

            const reportId = `testdata-${type}-${filename.replace('.txt', '')}`;
            this.loadedReports.set(reportId, {
                testData: {
                    content: contentData,
                    metadata: metadataData,
                    type: type,
                    filename: filename
                },
                filename: fileSpec,
                type: 'testdata'
            });
        } catch (error) {
            console.error(`Failed to load test data ${fileSpec}:`, error);
            this.showError(`Failed to load test data ${fileSpec}`);
        }
    }

    async loadGroundtruth(filename) {
        try {
            const response = await fetch(`/api/groundtruth/${filename}`);

            if (!response.ok) {
                throw new Error(`Failed to load groundtruth: ${response.status}`);
            }

            const data = await response.json();

            const reportId = `groundtruth-${filename.replace(/\.(summarization|qa)\.groundtruth\.json$/, '').replace(/\//g, '-')}`;
            this.loadedReports.set(reportId, {
                groundtruth: data,
                filename: filename,
                type: 'groundtruth'
            });
        } catch (error) {
            console.error(`Failed to load groundtruth ${filename}:`, error);
            this.showError(`Failed to load groundtruth ${filename}`);
        }
    }

    async loadAgentOutput(filename) {
        try {
            const response = await fetch(`/api/agent-output/${filename}`);

            if (!response.ok) {
                throw new Error(`Failed to load agent output: ${response.status}`);
            }

            const data = await response.json();

            const reportId = `agent-${filename.replace('agent_output_', '').replace('.json', '')}`;
            this.loadedReports.set(reportId, {
                agentOutput: data,
                filename: filename,
                type: 'agent_output'
            });
        } catch (error) {
            console.error(`Failed to load agent output ${filename}:`, error);
            this.showError(`Failed to load agent output ${filename}`);
        }
    }

    updateDisplay() {
        const reportsGrid = document.getElementById('reportsGrid');

        if (this.loadedReports.size === 0) {
            reportsGrid.innerHTML = `
                <div class="empty-state">
                    <h3>No reports loaded</h3>
                    <p>Select experiment, evaluation, test data, and/or groundtruth files to visualize results</p>
                </div>
            `;
            return;
        }

        let html = '';
        this.loadedReports.forEach((report, reportId) => {
            html += this.generateReportCard(reportId, report);
        });

        reportsGrid.innerHTML = html;

        // Add event listeners for close buttons
        document.querySelectorAll('.report-close').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const reportId = e.target.dataset.reportId;
                this.removeReport(reportId);
            });
        });

        // Add event listeners for collapsible sections
        document.querySelectorAll('.collapsible-header').forEach(header => {
            header.addEventListener('click', (e) => {
                // Don't toggle if clicking on the view source button
                if (e.target.classList.contains('view-source-btn')) {
                    return;
                }
                
                const section = e.currentTarget.parentElement;
                const content = section.querySelector('.collapsible-content');
                const toggle = section.querySelector('.collapsible-toggle');

                if (content.classList.contains('expanded')) {
                    content.classList.remove('expanded');
                    toggle.classList.remove('expanded');
                    toggle.textContent = '▶';
                } else {
                    content.classList.add('expanded');
                    toggle.classList.add('expanded');
                    toggle.textContent = '▼';
                }
            });
        });

        // Add event listeners for view source buttons
        document.querySelectorAll('.view-source-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation(); // Prevent collapsible toggle
                const sourcePath = e.target.dataset.sourcePath;
                this.viewSourceFile(sourcePath);
            });
        });

        // Add event listeners for export dropdowns
        document.querySelectorAll('.export-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const dropdown = btn.closest('.export-dropdown');
                const menu = dropdown.querySelector('.export-menu');

                // Close all other open menus
                document.querySelectorAll('.export-menu.show').forEach(m => {
                    if (m !== menu) m.classList.remove('show');
                });

                menu.classList.toggle('show');
            });
        });

        // Add event listeners for export options
        document.querySelectorAll('.export-option').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const format = e.target.dataset.format;
                const reportId = e.target.dataset.reportId;
                const menu = e.target.closest('.export-menu');
                menu.classList.remove('show');

                if (format === 'png') {
                    this.exportReportAsPNG(reportId);
                } else if (format === 'pdf') {
                    this.exportReportAsPDF(reportId);
                }
            });
        });

        // Close export menus when clicking elsewhere
        document.addEventListener('click', () => {
            document.querySelectorAll('.export-menu.show').forEach(menu => {
                menu.classList.remove('show');
            });
        });
    }

    generateReportCard(reportId, report) {
        const hasExperiment = report.experiment !== undefined;
        const hasEvaluation = report.evaluation !== undefined;
        const hasTestData = report.testData !== undefined;
        const hasGroundtruth = report.groundtruth !== undefined;
        const hasAgentOutput = report.agentOutput !== undefined;
        const hasConsolidatedEvaluation = report.consolidatedEvaluation !== undefined;

        // Handle consolidated evaluation reports separately
        if (hasConsolidatedEvaluation) {
            return this.generateConsolidatedReportCard(reportId, report.consolidatedEvaluation, report.filename);
        }

        // Handle agent outputs separately
        if (hasAgentOutput) {
            return this.generateAgentOutputReportCard(reportId, report.agentOutput, report.filename);
        }

        let title = reportId;
        let subtitle = '';
        let fullPath = report.filename || reportId; // Use filename if available, otherwise reportId

        if (hasGroundtruth) {
            const gtFile = report.filename;
            title = gtFile.replace(/\.(summarization|qa)\.groundtruth\.json$/, '').replace(/\//g, '/');
            subtitle = 'Groundtruth';
            if (gtFile.includes('consolidated')) {
                subtitle += ' [Consolidated]';
            }
        } else if (hasTestData) {
            title = `${report.testData.type}/${report.testData.filename.replace('.txt', '')}`;
            subtitle = 'Test Data';
            fullPath = `${report.testData.type}/${report.testData.filename}`; // Full path for test data
        } else if (hasExperiment && hasEvaluation) {
            subtitle = 'Experiment + Evaluation';
        } else if (hasExperiment) {
            subtitle = 'Experiment Only';
        } else if (hasEvaluation) {
            subtitle = 'Evaluation Only';
        }

        return `
            <div class="report-card" data-report-id="${reportId}">
                <div class="report-header">
                    <h3 title="${fullPath}">${title}</h3>
                    <div class="meta">${subtitle}</div>
                    <div class="report-actions">
                        <div class="export-dropdown">
                            <button class="export-btn" title="Export report">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                    <polyline points="7 10 12 15 17 10"/>
                                    <line x1="12" y1="15" x2="12" y2="3"/>
                                </svg>
                            </button>
                            <div class="export-menu">
                                <button class="export-option" data-format="png" data-report-id="${reportId}">📷 Export as PNG</button>
                                <button class="export-option" data-format="pdf" data-report-id="${reportId}">📄 Export as PDF</button>
                            </div>
                        </div>
                        <button class="report-close" data-report-id="${reportId}">×</button>
                    </div>
                </div>
                <div class="report-content">
                    ${hasGroundtruth ? this.generateGroundtruthSection(report.groundtruth) :
                      hasTestData ? this.generateTestDataSection(report.testData) : this.generateMetricsSection(report)}
                    ${hasEvaluation ? this.generateEvaluationSummary(report.evaluation) : ''}
                    ${hasEvaluation ? this.generateQualitySection(report.evaluation) : ''}
                    ${this.generateCostBreakdownSection(report)}
                    ${this.generateTimingSection(report)}
                    ${hasExperiment ? this.generateExperimentDetails(report.experiment) : ''}
                    ${hasExperiment ? this.generateExperimentSummaries(report.experiment) : ''}
                </div>
            </div>
        `;
    }

    generateAgentOutputReportCard(reportId, agentData, filename) {
        const metadata = agentData.metadata || {};
        const conversation = agentData.conversation || [];
        const systemPrompt = agentData.system_prompt || '';
        const systemPromptTokens = agentData.system_prompt_tokens || null;
        
        // Extract performance metrics from conversation
        const performanceStats = [];
        let totalInputTokens = 0;
        let totalOutputTokens = 0;
        let avgTokensPerSecond = 0;
        let avgTimeToFirstToken = 0;
        let stepCount = 0;

        conversation.forEach(msg => {
            if (msg.role === 'system' && msg.content?.type === 'stats' && msg.content.performance_stats) {
                const stats = msg.content.performance_stats;
                performanceStats.push(stats);
                totalInputTokens += stats.input_tokens || 0;
                totalOutputTokens += stats.output_tokens || 0;
                if (stats.tokens_per_second) avgTokensPerSecond += stats.tokens_per_second;
                if (stats.time_to_first_token) avgTimeToFirstToken += stats.time_to_first_token;
                stepCount++;
            }
        });

        if (stepCount > 0) {
            avgTokensPerSecond /= stepCount;
            avgTimeToFirstToken /= stepCount;
        }

        // Extract tool calls
        const toolCalls = [];
        conversation.forEach(msg => {
            if (msg.role === 'assistant' && msg.content?.tool) {
                toolCalls.push({
                    tool: msg.content.tool,
                    args: msg.content.tool_args,
                    thought: msg.content.thought,
                    goal: msg.content.goal
                });
            }
        });

        // Generate summary
        const summary = {
            status: agentData.status || 'unknown',
            result: agentData.result || 'N/A',
            steps_taken: agentData.steps_taken || 0,
            error_count: agentData.error_count || 0,
            conversation_length: conversation.length,
            tool_calls_count: toolCalls.length,
            has_performance_stats: performanceStats.length > 0
        };

        const displayName = filename?.replace('agent_output_', '').replace('.json', '') || reportId;
        
        return `
            <div class="report-card agent-output" data-report-id="${reportId}">
                <div class="report-header">
                    <h3 title="${filename || 'N/A'}">${displayName}</h3>
                    <div class="meta">Agent Output Analysis</div>
                    <div class="report-actions">
                        <div class="export-dropdown">
                            <button class="export-btn" title="Export report">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                    <polyline points="7 10 12 15 17 10"/>
                                    <line x1="12" y1="15" x2="12" y2="3"/>
                                </svg>
                            </button>
                            <div class="export-menu">
                                <button class="export-option" data-format="png" data-report-id="${reportId}">📷 Export as PNG</button>
                                <button class="export-option" data-format="pdf" data-report-id="${reportId}">📄 Export as PDF</button>
                            </div>
                        </div>
                        <button class="report-close" data-report-id="${reportId}">×</button>
                    </div>
                </div>
                <div class="report-content">
                    ${this.generateAgentSummarySection(summary)}
                    ${this.generateConversationFlowSection(conversation)}
                    ${this.generatePerformanceMetricsSection(performanceStats, totalInputTokens, totalOutputTokens, avgTokensPerSecond, avgTimeToFirstToken)}
                    ${this.generateToolExecutionSection(toolCalls)}
                    ${this.generateSystemPromptSection(systemPrompt, systemPromptTokens)}
                </div>
            </div>
        `;
    }

    generateMetricsSection(report) {
        const metrics = [];

        if (report.experiment) {
            const exp = report.experiment;
            const isLocal = exp.metadata.inference_type === 'local' || exp.metadata.llm_type?.toLowerCase() === 'lemonade';
            const totalCost = exp.metadata.total_cost?.total_cost || 0;

            // Add inference type indicator
            metrics.push({
                label: '<span data-tooltip="Whether the model runs locally or remotely">Inference</span>',
                value: isLocal ?
                    '<span style="color: #28a745; font-weight: bold;" data-tooltip="Running on your local machine">🖥️ Local</span>' :
                    '<span style="color: #007bff; font-weight: bold;" data-tooltip="Running on cloud servers">☁️ Cloud</span>'
            });

            // Show cost with special formatting for local (free) inference
            metrics.push({
                label: '<span data-tooltip="Cost of generating summaries (not evaluation cost)">Total Cost</span>',
                value: isLocal ?
                    '<span style="color: #28a745; font-weight: bold;" data-tooltip="No cost for local models">FREE</span>' :
                    `<span data-tooltip="API usage cost">$${totalCost.toFixed(4)}</span>`
            });

            metrics.push(
                { label: '<span data-tooltip="Total tokens processed (input + output)">Total Tokens</span>', value: exp.metadata.total_usage?.total_tokens?.toLocaleString() || 'N/A' },
                { label: '<span data-tooltip="Number of test cases processed">Items</span>', value: exp.metadata.total_items || 0 }
            );

            // Add experiment timing metrics
            if (exp.metadata.timing) {
                const timing = exp.metadata.timing;
                if (timing.total_experiment_time_seconds) {
                    metrics.push({ label: 'Total Time', value: this.formatTime(timing.total_experiment_time_seconds) });
                }
                if (timing.average_per_item_seconds) {
                    metrics.push({ label: 'Avg/Item', value: this.formatTime(timing.average_per_item_seconds) });
                }
            }
        }

        if (report.evaluation) {
            const evalData = report.evaluation;
            const metrics_data = evalData.overall_rating?.metrics;
            if (metrics_data) {
                // Check if this is a Q&A evaluation (has accuracy_percentage) or summarization (has quality_score)
                if (metrics_data.accuracy_percentage !== undefined) {
                    // Q&A evaluation metrics
                    metrics.push(
                        { label: 'Accuracy', value: `${metrics_data.accuracy_percentage}%` },
                        { label: 'Pass Rate', value: `${(metrics_data.pass_rate * 100).toFixed(1)}%` },
                        { label: 'Questions', value: metrics_data.num_questions || 0 },
                        { label: 'Passed', value: metrics_data.num_passed || 0 },
                        { label: 'Failed', value: metrics_data.num_failed || 0 }
                    );
                } else if (metrics_data.quality_score !== undefined) {
                    // Summarization evaluation metrics
                    metrics.push(
                        { label: 'Grade', value: this.formatQualityScore(metrics_data.quality_score) },
                        { label: 'Excellent', value: metrics_data.excellent_count || 0 },
                        { label: 'Good', value: metrics_data.good_count || 0 },
                        { label: 'Fair', value: metrics_data.fair_count || 0 },
                        { label: 'Poor', value: metrics_data.poor_count || 0 }
                    );
                }
            }

            // Add evaluation cost and usage metrics
            if (evalData.total_cost) {
                metrics.push(
                    { label: 'Eval Cost', value: `$${evalData.total_cost.total_cost?.toFixed(4) || 'N/A'}` }
                );
            }
            if (evalData.total_usage) {
                metrics.push(
                    { label: 'Eval Tokens', value: evalData.total_usage.total_tokens?.toLocaleString() || 'N/A' }
                );
            }

            // Add evaluation timing metrics
            if (evalData.timing) {
                const timing = evalData.timing;
                if (timing.total_processing_time_seconds) {
                    metrics.push({ label: 'Eval Time', value: this.formatTime(timing.total_processing_time_seconds) });
                }
                if (timing.average_per_question_seconds) {
                    metrics.push({ label: 'Avg/Q', value: this.formatTime(timing.average_per_question_seconds) });
                } else if (timing.average_per_summary_seconds) {
                    metrics.push({ label: 'Avg/Summary', value: this.formatTime(timing.average_per_summary_seconds) });
                }
            }

            // Add report generation time if available
            if (evalData.metadata?.report_generation_time_seconds) {
                metrics.push({ label: 'Report Gen', value: this.formatTime(evalData.metadata.report_generation_time_seconds) });
            }
        }

        if (metrics.length === 0) {
            return '<p>No metrics available</p>';
        }

        return `
            <div class="metrics-grid">
                ${metrics.map(metric => `
                    <div class="metric-card">
                        <div class="metric-value">${metric.value}</div>
                        <div class="metric-label">${metric.label}</div>
                    </div>
                `).join('')}
            </div>
        `;
    }

    formatQualityScore(score) {
        // Handle both old (1-4 scale) and new (0-100 percentage) formats
        let percentage;
        if (score <= 4) {
            // Old format: convert from 1-4 scale to percentage
            percentage = ((score - 1) / 3) * 100;
        } else {
            // New format: already a percentage
            percentage = score;
        }

        // Add qualitative label based on percentage ranges
        let label, cssClass;
        if (percentage >= 85) {
            label = 'Excellent';
            cssClass = 'quality-excellent';
        } else if (percentage >= 67) {
            label = 'Good';
            cssClass = 'quality-good';
        } else if (percentage >= 34) {
            label = 'Fair';
            cssClass = 'quality-fair';
        } else {
            label = 'Poor';
            cssClass = 'quality-poor';
        }

        return `${percentage.toFixed(1)}% <span class="${cssClass}">${label}</span>`;
    }

    formatTime(seconds) {
        // Format time values with appropriate precision
        if (seconds === undefined || seconds === null) {
            return 'N/A';
        }

        // For very small values, show more precision
        if (seconds < 1) {
            return `${(seconds * 1000).toFixed(0)}ms`;
        } else if (seconds < 10) {
            return `${seconds.toFixed(2)}s`;
        } else if (seconds < 60) {
            return `${seconds.toFixed(1)}s`;
        } else {
            // Convert to minutes:seconds for longer durations
            const minutes = Math.floor(seconds / 60);
            const remainingSeconds = (seconds % 60).toFixed(0);
            return `${minutes}m ${remainingSeconds}s`;
        }
    }

    generateCostBreakdownSection(report) {
        if (!report.experiment) return '';

        const exp = report.experiment;
        const isLocal = exp.metadata.inference_type === 'local' || exp.metadata.llm_type?.toLowerCase() === 'lemonade';
        const totalCost = exp.metadata.total_cost?.total_cost || 0;
        const totalItems = exp.metadata.total_items || 1;
        const costPerItem = totalCost / totalItems;

        // Don't show cost breakdown for local inference since it's free
        if (isLocal) {
            return `
                <div class="cost-breakdown-section">
                    <div class="cost-banner-free">
                        <div class="cost-banner-icon">🎉</div>
                        <div class="cost-banner-text">
                            <div class="cost-banner-title">Local Inference - No Cost!</div>
                            <div class="cost-banner-subtitle">Running on your hardware with Lemonade</div>
                        </div>
                    </div>
                </div>
            `;
        }

        return `
            <div class="cost-breakdown-section">
                <h4>💰 Cost Breakdown</h4>
                <div class="cost-grid">
                    <div class="cost-card">
                        <div class="cost-value">$${totalCost.toFixed(4)}</div>
                        <div class="cost-label">Total Cost</div>
                    </div>
                    <div class="cost-card">
                        <div class="cost-value">$${costPerItem.toFixed(5)}</div>
                        <div class="cost-label">Per Item</div>
                    </div>
                    <div class="cost-card">
                        <div class="cost-value">$${(exp.metadata.total_cost?.input_cost || 0).toFixed(4)}</div>
                        <div class="cost-label">Input Tokens</div>
                    </div>
                    <div class="cost-card">
                        <div class="cost-value">$${(exp.metadata.total_cost?.output_cost || 0).toFixed(4)}</div>
                        <div class="cost-label">Output Tokens</div>
                    </div>
                </div>
            </div>
        `;
    }

    generateTimingSection(report) {
        const timingData = [];
        let hasTimingData = false;

        // Collect experiment timing data
        if (report.experiment?.metadata?.timing) {
            const timing = report.experiment.metadata.timing;
            hasTimingData = true;

            if (timing.total_experiment_time_seconds) {
                timingData.push({
                    label: 'Experiment Execution',
                    total: timing.total_experiment_time_seconds,
                    average: timing.average_per_item_seconds,
                    min: timing.min_per_item_seconds,
                    max: timing.max_per_item_seconds,
                    count: timing.per_item_times_seconds?.length || 0,
                    type: 'items'
                });
            }
        }

        // Collect evaluation timing data
        if (report.evaluation?.timing) {
            const timing = report.evaluation.timing;
            hasTimingData = true;

            const type = timing.per_question_times_seconds ? 'questions' : 'summaries';
            const avgKey = type === 'questions' ? 'average_per_question_seconds' : 'average_per_summary_seconds';
            const minKey = type === 'questions' ? 'min_per_question_seconds' : 'min_per_summary_seconds';
            const maxKey = type === 'questions' ? 'max_per_question_seconds' : 'max_per_summary_seconds';
            const itemsKey = type === 'questions' ? 'per_question_times_seconds' : 'per_summary_times_seconds';

            timingData.push({
                label: 'Evaluation Analysis',
                total: timing.total_processing_time_seconds,
                average: timing[avgKey],
                min: timing[minKey],
                max: timing[maxKey],
                count: timing[itemsKey]?.length || 0,
                type: type
            });
        }

        // Add report generation time if available
        if (report.evaluation?.metadata?.report_generation_time_seconds) {
            hasTimingData = true;
            timingData.push({
                label: 'Report Generation',
                total: report.evaluation.metadata.report_generation_time_seconds,
                type: 'single'
            });
        }

        if (!hasTimingData) {
            return '';
        }

        return `
            <div class="collapsible-section">
                <div class="collapsible-header">
                    <h4>⏱️ Performance Timing</h4>
                    <span class="collapsible-toggle">▶</span>
                </div>
                <div class="collapsible-content">
                    <div class="collapsible-body">
                        <div class="timing-grid">
                            ${timingData.map(item => `
                                <div class="timing-card">
                                    <div class="timing-header">${item.label}</div>
                                    <div class="timing-metrics">
                                        <div class="timing-stat">
                                            <span class="timing-value">${this.formatTime(item.total)}</span>
                                            <span class="timing-label">Total</span>
                                        </div>
                                        ${item.type !== 'single' ? `
                                            <div class="timing-stat">
                                                <span class="timing-value">${this.formatTime(item.average)}</span>
                                                <span class="timing-label">Average</span>
                                            </div>
                                            <div class="timing-stat">
                                                <span class="timing-value">${this.formatTime(item.min)}</span>
                                                <span class="timing-label">Min</span>
                                            </div>
                                            <div class="timing-stat">
                                                <span class="timing-value">${this.formatTime(item.max)}</span>
                                                <span class="timing-label">Max</span>
                                            </div>
                                            <div class="timing-stat">
                                                <span class="timing-value">${item.count}</span>
                                                <span class="timing-label">${item.type.charAt(0).toUpperCase() + item.type.slice(1)}</span>
                                            </div>
                                        ` : ''}
                                    </div>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    generateEvaluationSummary(evaluation) {
        if (!evaluation) return '';

        const hasOverallAnalysis = evaluation.overall_analysis;
        const hasStrengths = evaluation.strengths && evaluation.strengths.length > 0;
        const hasWeaknesses = evaluation.weaknesses && evaluation.weaknesses.length > 0;
        const hasRecommendations = evaluation.recommendations && evaluation.recommendations.length > 0;
        const hasUseCaseFit = evaluation.use_case_fit;

        if (!hasOverallAnalysis && !hasStrengths && !hasWeaknesses && !hasRecommendations && !hasUseCaseFit) {
            return '';
        }

        return `
            <div class="evaluation-summary">
                <h4>Evaluation Summary</h4>
                ${hasOverallAnalysis ? `
                    <div class="summary-item">
                        <div class="summary-label">Overall Analysis</div>
                        <div class="summary-text">${this.escapeHtml(evaluation.overall_analysis)}</div>
                    </div>
                ` : ''}
                ${hasStrengths ? `
                    <div class="summary-item">
                        <div class="summary-label">Strengths</div>
                        <ul class="summary-list">
                            ${evaluation.strengths.map(strength => `<li>${this.escapeHtml(strength)}</li>`).join('')}
                        </ul>
                    </div>
                ` : ''}
                ${hasWeaknesses ? `
                    <div class="summary-item">
                        <div class="summary-label">Weaknesses</div>
                        <ul class="summary-list">
                            ${evaluation.weaknesses.map(weakness => `<li>${this.escapeHtml(weakness)}</li>`).join('')}
                        </ul>
                    </div>
                ` : ''}
                ${hasRecommendations ? `
                    <div class="summary-item">
                        <div class="summary-label">Recommendations</div>
                        <ul class="summary-list">
                            ${evaluation.recommendations.map(rec => `<li>${this.escapeHtml(rec)}</li>`).join('')}
                        </ul>
                    </div>
                ` : ''}
                ${hasUseCaseFit ? `
                    <div class="summary-item">
                        <div class="summary-label">Use Case Fit</div>
                        <div class="summary-text">${this.escapeHtml(evaluation.use_case_fit)}</div>
                    </div>
                ` : ''}
            </div>
        `;
    }

    generateQualitySection(evaluation) {
        if (!evaluation.per_question || evaluation.per_question.length === 0) {
            return '';
        }

        // Show overall quality score first
        let overallSection = '';
        if (evaluation.overall_rating) {
            const rating = evaluation.overall_rating;
            const metrics = rating.metrics || {};

            overallSection = `
                <div class="quality-overview">
                    <h4>📊 Overall Quality Assessment <span class="info-icon" data-tooltip="Summary of evaluation results across all test cases">?</span></h4>
                    <div class="quality-score-card">
                        <div class="quality-score-main">
                            <span class="quality-score-value" data-tooltip="Weighted average score: (Excellent×4 + Good×3 + Fair×2 + Poor×1) normalized to 0-100%">${metrics.quality_score ? Math.round(metrics.quality_score) : 'N/A'}%</span>
                            <span class="quality-score-rating rating-${rating.rating}" data-tooltip="Overall rating: Excellent (≥70% excellent), Good (≥70% good+), Fair (≥70% fair+), or Poor">${rating.rating.toUpperCase()}</span>
                        </div>
                        <div class="quality-distribution">
                            <div class="quality-counts">
                                <span class="count-item excellent" data-tooltip="Summaries with excellent quality: comprehensive, accurate, and well-structured">Excellent: ${metrics.excellent_count || 0}</span>
                                <span class="count-item good" data-tooltip="Summaries with good quality: mostly accurate with minor issues">Good: ${metrics.good_count || 0}</span>
                                <span class="count-item fair" data-tooltip="Summaries with fair quality: acceptable but missing key details">Fair: ${metrics.fair_count || 0}</span>
                                <span class="count-item poor" data-tooltip="Summaries with poor quality: significant errors or omissions">Poor: ${metrics.poor_count || 0}</span>
                            </div>
                            <div class="quality-explanation">${this.escapeHtml(rating.explanation || '')}</div>
                        </div>
                    </div>
                </div>
            `;
        }

        // Show detailed analysis for each item
        let detailsSection = '';
        evaluation.per_question.forEach((item, index) => {
            const analysis = item.analysis;
            if (!analysis) return;

            const sourceFile = item.source_file ? item.source_file.split('\\').pop().split('/').pop() : `Item ${index + 1}`;
            const fullSourcePath = item.source_file || null;

            // Support both old and new field names from evaluation structure
            // New format (from Claude evaluations): uses "accuracy" terminology  
            // Old format (legacy): uses "quality" terminology
            // This ensures backward compatibility while supporting the latest evaluation format
            const qualityItems = [
                // New field names (accuracy-based) - Current evaluation format
                { key: 'executive_summary_accuracy', label: 'Executive Summary Accuracy', data: analysis.executive_summary_accuracy },
                { key: 'completeness', label: 'Completeness', data: analysis.completeness },
                { key: 'action_items_accuracy', label: 'Action Items Accuracy', data: analysis.action_items_accuracy },
                { key: 'key_decisions_accuracy', label: 'Key Decisions Accuracy', data: analysis.key_decisions_accuracy },
                { key: 'participant_identification', label: 'Participant Identification', data: analysis.participant_identification },
                { key: 'topic_coverage', label: 'Topic Coverage', data: analysis.topic_coverage },
                // Old field names (quality-based) for backward compatibility
                { key: 'executive_summary_quality', label: 'Executive Summary Quality', data: analysis.executive_summary_quality },
                { key: 'detail_completeness', label: 'Detail Completeness', data: analysis.detail_completeness },
                { key: 'action_items_structure', label: 'Action Items Structure', data: analysis.action_items_structure },
                { key: 'key_decisions_clarity', label: 'Key Decisions Clarity', data: analysis.key_decisions_clarity },
                { key: 'participant_information', label: 'Participant Information', data: analysis.participant_information },
                { key: 'topic_organization', label: 'Topic Organization', data: analysis.topic_organization }
            ].filter(item => item.data && item.data.rating);

            if (qualityItems.length > 0) {
                detailsSection += `
                    <div class="quality-details">
                        <div class="collapsible-section" data-section="quality-${index}">
                            <div class="collapsible-header">
                                <h5>🎯 Detailed Analysis - ${sourceFile}</h5>
                                ${fullSourcePath ? `<button class="view-source-btn" data-source-path="${fullSourcePath}" title="View source file: ${fullSourcePath}">📄 View Source</button>` : ''}
                                <span class="collapsible-toggle">▶</span>
                            </div>
                            <div class="collapsible-content">
                                <div class="collapsible-body">
                                    <div class="quality-grid">
                                        ${qualityItems.map(item => `
                                            <div class="quality-detail-card expanded">
                                                <div class="quality-detail-header">
                                                    <span class="quality-detail-label">${item.label}</span>
                                                    <span class="quality-rating rating-${item.data.rating}">${item.data.rating}</span>
                                                </div>
                                                <div class="quality-detail-explanation full">
                                                    ${this.escapeHtml(item.data.explanation || 'No explanation provided')}
                                                </div>
                                            </div>
                                        `).join('')}
                                    </div>
                                    <div class="overall-item-rating">
                                        Overall Item Quality: <span class="quality-rating rating-${item.overall_quality || analysis.overall_quality}">${(item.overall_quality || analysis.overall_quality || 'N/A').toUpperCase()}</span>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                `;
            }
        });

        return `
            <div class="quality-section">
                ${overallSection}
                ${detailsSection}
            </div>
        `;
    }

    generateExperimentDetails(experiment) {
        const metadata = experiment.metadata;
        const isLocal = metadata.inference_type === 'local' || metadata.llm_type?.toLowerCase() === 'lemonade';

        return `
            <div class="experiment-details">
                <h4>Experiment Details</h4>
                <div class="detail-grid">
                    <div><strong>Model:</strong> ${metadata.model || 'N/A'}</div>
                    <div><strong>Inference Type:</strong> ${isLocal ?
                        '<span style="color: #28a745;">🖥️ Local (Free)</span>' :
                        '<span style="color: #007bff;">☁️ Cloud (Paid)</span>'}</div>
                    <div><strong>Temperature:</strong> ${metadata.temperature || 'N/A'}</div>
                    <div><strong>Max Tokens:</strong> ${metadata.max_tokens || 'N/A'}</div>
                    <div><strong>Date:</strong> ${metadata.timestamp || 'N/A'}</div>
                    ${metadata.errors && metadata.errors.length > 0 ?
                        `<div style="color: #dc3545;"><strong>Errors:</strong> ${metadata.errors.length}</div>` :
                        ''}
                </div>
            </div>
        `;
    }

    generateExperimentSummaries(experiment) {
        if (!experiment.analysis) {
            return '';
        }

        let contentHtml = '';

        // Handle Q&A results
        if (experiment.analysis.qa_results) {
            const qaResults = experiment.analysis.qa_results;
            if (qaResults.length > 0) {
                contentHtml += `
                    <div class="collapsible-section" data-section="qa-results">
                        <div class="collapsible-header">
                            <h4>Q&A Results</h4>
                            <span class="collapsible-toggle">▶</span>
                        </div>
                        <div class="collapsible-content">
                            <div class="collapsible-body">
                                ${qaResults.map((qa, index) => `
                                    <div class="qa-item" style="margin-bottom: 20px; padding: 15px; border: 1px solid #e0e0e0; border-radius: 5px;">
                                        <div style="margin-bottom: 10px;">
                                            <strong>Question ${index + 1}:</strong>
                                            <div style="color: #333; margin-top: 5px;">${this.escapeHtml(qa.query)}</div>
                                        </div>
                                        <div style="margin-bottom: 10px;">
                                            <strong>Model Response:</strong>
                                            <div style="color: #444; margin-top: 5px; white-space: pre-wrap;">${this.escapeHtml(qa.response)}</div>
                                        </div>
                                        <div style="margin-bottom: 10px;">
                                            <strong>Ground Truth:</strong>
                                            <div style="color: #666; margin-top: 5px;">${this.escapeHtml(qa.ground_truth)}</div>
                                        </div>
                                        ${qa.processing_time_seconds ? `
                                            <div style="color: #888; font-size: 0.9em;">
                                                <strong>Processing Time:</strong> ${qa.processing_time_seconds.toFixed(2)}s
                                            </div>
                                        ` : ''}
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                    </div>
                `;
            }
        }

        // Handle summarization results
        if (experiment.analysis.summarization_results) {
            const results = experiment.analysis.summarization_results;
            if (results.length > 0) {
                // Generate content for all summarization results
                results.forEach((result, index) => {
            if (result.generated_summaries) {
                const summaries = result.generated_summaries;
                const sourceFile = result.source_file ? result.source_file.split('\\').pop().split('/').pop() : `Item ${index + 1}`;

                contentHtml += `
                    <div class="collapsible-section" data-section="summaries-${index}">
                        <div class="collapsible-header">
                            <h4>Generated Summaries - ${sourceFile}</h4>
                            <span class="collapsible-toggle">▶</span>
                        </div>
                        <div class="collapsible-content">
                            <div class="collapsible-body">
                                ${Object.entries(summaries).map(([key, value]) => `
                                    <div class="summary-item">
                                        <div class="summary-label">${key.replace(/_/g, ' ').toUpperCase()}</div>
                                        <div class="summary-text">${this.escapeHtml(value)}</div>
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                    </div>
                `;
            }
        });
            }
        }

        return contentHtml;
    }

    generateEvaluationExplanations(evaluation) {
        if (!evaluation.per_question || evaluation.per_question.length === 0) {
            return '';
        }

        let explanationsHtml = '';
        evaluation.per_question.forEach((item, index) => {
            if (item.analysis) {
                const analysis = item.analysis;
                const sourceFile = item.source_file ? item.source_file.split('\\').pop().split('/').pop() : `Item ${index + 1}`;

                // Use correct field names from actual evaluation structure
                const explanationItems = [
                    { key: 'executive_summary_quality', label: 'Executive Summary Quality' },
                    { key: 'detail_completeness', label: 'Detail Completeness' },
                    { key: 'action_items_structure', label: 'Action Items Structure' },
                    { key: 'key_decisions_clarity', label: 'Key Decisions Clarity' },
                    { key: 'participant_information', label: 'Participant Information' },
                    { key: 'topic_organization', label: 'Topic Organization' }
                ].filter(item => analysis[item.key] && analysis[item.key].explanation);

                if (explanationItems.length > 0) {
                    explanationsHtml += `
                        <div class="collapsible-section" data-section="explanations-${index}">
                            <div class="collapsible-header">
                                <h4>📝 Detailed Quality Explanations - ${sourceFile}</h4>
                                <span class="collapsible-toggle">▶</span>
                            </div>
                            <div class="collapsible-content">
                                <div class="collapsible-body">
                                    ${explanationItems.map(item => {
                                        const data = analysis[item.key];
                                        return `
                                            <div class="explanation-item">
                                                <div class="explanation-label">${item.label}</div>
                                                <div class="explanation-rating">
                                                    <span class="quality-rating rating-${data.rating}">${data.rating}</span>
                                                </div>
                                                <div class="explanation-text">${this.escapeHtml(data.explanation)}</div>
                                            </div>
                                        `;
                                    }).join('')}
                                </div>
                            </div>
                        </div>
                    `;
                }
            }
        });

        return explanationsHtml;
    }

    generateTestDataSection(testData) {
        const { content, metadata, type, filename, isPdf, message } = testData;

        let metadataInfo = '';
        if (metadata) {
            const info = metadata.generation_info || {};
            const fileInfo = metadata[type === 'emails' ? 'emails' : 'transcripts']?.find(
                item => item.filename === filename
            );

            metadataInfo = `
                <div class="metrics-grid">
                    <div class="metric-card">
                        <div class="metric-value">${type}</div>
                        <div class="metric-label">Type</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value">${fileInfo?.estimated_tokens || 'N/A'}</div>
                        <div class="metric-label">Est. Tokens</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value">$${fileInfo?.claude_cost?.total_cost?.toFixed(4) || 'N/A'}</div>
                        <div class="metric-label">Generation Cost</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value">${info.claude_model || 'N/A'}</div>
                        <div class="metric-label">Model</div>
                    </div>
                </div>
            `;
        }

        return `
            ${metadataInfo}
            <div class="collapsible-section">
                <div class="collapsible-header">
                    <h4>Content</h4>
                    <span class="collapsible-toggle">▶</span>
                </div>
                <div class="collapsible-content">
                    <div class="collapsible-body">
                        <div class="summary-item">
                            <div class="summary-label">${filename}</div>
                            <div class="summary-text">
                                ${isPdf ? 
                                    `<div style="padding: 20px; background: #f0f0f0; border-radius: 5px; text-align: center;">
                                        <span style="font-size: 48px;">📄</span>
                                        <p style="margin-top: 10px; color: #666;">${message || 'PDF file - preview not available'}</p>
                                        <p style="color: #888; font-size: 0.9em;">Size: ${testData.size ? (testData.size / 1024).toFixed(2) + ' KB' : 'Unknown'}</p>
                                    </div>` :
                                    this.escapeHtml(content || '')
                                }
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            ${metadata ? this.generateTestDataMetadataSection(metadata, type) : ''}
        `;
    }

    generateTestDataMetadataSection(metadata, type) {
        const info = metadata.generation_info || {};
        const items = metadata[type === 'emails' ? 'emails' : 'transcripts'] || [];

        return `
            <div class="collapsible-section">
                <div class="collapsible-header">
                    <h4>Generation Metadata</h4>
                    <span class="collapsible-toggle">▶</span>
                </div>
                <div class="collapsible-content">
                    <div class="collapsible-body">
                        <div class="detail-grid">
                            <div><strong>Generated:</strong> ${new Date(info.generated_date).toLocaleString()}</div>
                            <div><strong>Total Files:</strong> ${info.total_files}</div>
                            <div><strong>Target Tokens:</strong> ${info.target_tokens_per_file}</div>
                            <div><strong>Total Cost:</strong> $${info.total_claude_cost?.total_cost?.toFixed(4) || 'N/A'}</div>
                            <div><strong>Total Tokens:</strong> ${info.total_claude_usage?.total_tokens || 'N/A'}</div>
                            <div><strong>Model:</strong> ${info.claude_model}</div>
                        </div>
                        ${items.length > 0 ? `
                            <h5 style="margin-top: 15px;">All ${type === 'emails' ? 'Emails' : 'Transcripts'}</h5>
                            <div class="detail-grid">
                                ${items.map(item => `
                                    <div style="grid-column: 1 / -1; margin-bottom: 10px; padding: 10px; background: #f8f9fa; border-radius: 5px;">
                                        <strong>${item.filename}</strong><br>
                                        <small>${item.description}</small><br>
                                        <small>Tokens: ${item.estimated_tokens}, Cost: $${item.claude_cost?.total_cost?.toFixed(4) || 'N/A'}</small>
                                    </div>
                                `).join('')}
                            </div>
                        ` : ''}
                    </div>
                </div>
            </div>
        `;
    }

    generateGroundtruthSection(groundtruth) {
        const { metadata, analysis } = groundtruth;

        let metadataInfo = '';
        if (metadata) {
            metadataInfo = `
                <div class="metrics-grid">
                    <div class="metric-card">
                        <div class="metric-value">${metadata.use_case || 'N/A'}</div>
                        <div class="metric-label">Use Case</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value">${metadata.usage?.total_tokens || 'N/A'}</div>
                        <div class="metric-label">Total Tokens</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value">$${metadata.cost?.total_cost?.toFixed(4) || 'N/A'}</div>
                        <div class="metric-label">Generation Cost</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value">${metadata.model || 'N/A'}</div>
                        <div class="metric-label">Model</div>
                    </div>
                </div>
            `;
        }

        return `
            ${metadataInfo}
            ${analysis?.summaries ? this.generateGroundtruthSummaries(analysis.summaries) : ''}
            ${analysis?.evaluation_criteria ? this.generateGroundtruthCriteria(analysis.evaluation_criteria) : ''}
            ${metadata ? this.generateGroundtruthMetadataSection(metadata, analysis) : ''}
        `;
    }

    generateGroundtruthSummaries(summaries) {
        return `
            <div class="collapsible-section">
                <div class="collapsible-header">
                    <h4>Ground Truth Summaries</h4>
                    <span class="collapsible-toggle">▶</span>
                </div>
                <div class="collapsible-content">
                    <div class="collapsible-body">
                        ${Object.entries(summaries).map(([key, value]) => {
                            if (Array.isArray(value)) {
                                return `
                                    <div class="summary-item">
                                        <div class="summary-label">${key.replace(/_/g, ' ').toUpperCase()}</div>
                                        <div class="summary-text">${value.map(item => `• ${this.escapeHtml(item)}`).join('\n')}</div>
                                    </div>
                                `;
                            } else {
                                return `
                                    <div class="summary-item">
                                        <div class="summary-label">${key.replace(/_/g, ' ').toUpperCase()}</div>
                                        <div class="summary-text">${this.escapeHtml(value)}</div>
                                    </div>
                                `;
                            }
                        }).join('')}
                    </div>
                </div>
            </div>
        `;
    }

    generateGroundtruthCriteria(criteria) {
        return `
            <div class="collapsible-section">
                <div class="collapsible-header">
                    <h4>Evaluation Criteria</h4>
                    <span class="collapsible-toggle">▶</span>
                </div>
                <div class="collapsible-content">
                    <div class="collapsible-body">
                        ${Object.entries(criteria).map(([key, value]) => `
                            <div class="summary-item">
                                <div class="summary-label">${key.replace(/_/g, ' ').toUpperCase()}</div>
                                <div class="summary-text">${this.escapeHtml(value)}</div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            </div>
        `;
    }

    generateGroundtruthMetadataSection(metadata, analysis) {
        return `
            <div class="collapsible-section">
                <div class="collapsible-header">
                    <h4>Generation Details</h4>
                    <span class="collapsible-toggle">▶</span>
                </div>
                <div class="collapsible-content">
                    <div class="collapsible-body">
                        <div class="detail-grid">
                            <div><strong>Generated:</strong> ${metadata.timestamp}</div>
                            <div><strong>Source File:</strong> ${metadata.source_file}</div>
                            <div><strong>Model:</strong> ${metadata.model}</div>
                            <div><strong>Use Case:</strong> ${metadata.use_case}</div>
                            <div><strong>Input Tokens:</strong> ${metadata.usage?.input_tokens || 'N/A'}</div>
                            <div><strong>Output Tokens:</strong> ${metadata.usage?.output_tokens || 'N/A'}</div>
                            <div><strong>Input Cost:</strong> $${metadata.cost?.input_cost?.toFixed(4) || 'N/A'}</div>
                            <div><strong>Output Cost:</strong> $${metadata.cost?.output_cost?.toFixed(4) || 'N/A'}</div>
                        </div>
                        ${analysis?.transcript_metadata ? `
                            <h5 style="margin-top: 15px;">Content Metadata</h5>
                            <div class="detail-grid">
                                ${Object.entries(analysis.transcript_metadata).map(([key, value]) => `
                                    <div><strong>${key.replace(/_/g, ' ')}:</strong> ${value}</div>
                                `).join('')}
                            </div>
                        ` : ''}
                    </div>
                </div>
            </div>
        `;
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    async viewSourceFile(sourcePath) {
        try {
            // Convert Windows-style path to API path
            // Example: "test_data\meetings\all_hands_meeting_1.txt" → "meetings/all_hands_meeting_1.txt"
            const pathParts = sourcePath.replace(/\\/g, '/').split('/');
            let apiPath = '';
            let fileName = '';
            
            // Find the test_data or meetings part
            const testDataIndex = pathParts.findIndex(part => part === 'test_data');
            if (testDataIndex !== -1 && testDataIndex < pathParts.length - 2) {
                // Format: test_data/meetings/filename.txt → API: /api/test-data/meetings/filename.txt
                const type = pathParts[testDataIndex + 1]; // e.g., "meetings"
                fileName = pathParts[testDataIndex + 2]; // e.g., "all_hands_meeting_1.txt"
                apiPath = `/api/test-data/${type}/${fileName}`;
            } else {
                // Fallback: try to extract last two parts (type/filename)
                if (pathParts.length >= 2) {
                    const type = pathParts[pathParts.length - 2];
                    fileName = pathParts[pathParts.length - 1];
                    apiPath = `/api/test-data/${type}/${fileName}`;
                } else {
                    throw new Error('Invalid source path format');
                }
            }

            // Fetch the source file content
            const response = await fetch(apiPath);
            if (!response.ok) {
                throw new Error(`Failed to load source file: ${response.status}`);
            }

            const data = await response.json();
            this.showSourceModal(data.content, fileName, sourcePath);
        } catch (error) {
            console.error('Error loading source file:', error);
            this.showError(`Failed to load source file: ${error.message}`);
        }
    }

    showSourceModal(content, fileName, fullPath) {
        // Remove any existing modal
        const existingModal = document.getElementById('sourceModal');
        if (existingModal) {
            existingModal.remove();
        }

        // Create modal HTML
        const modal = document.createElement('div');
        modal.id = 'sourceModal';
        modal.className = 'source-modal';
        modal.innerHTML = `
            <div class="source-modal-content">
                <div class="source-modal-header">
                    <h3>📄 Source File: ${fileName}</h3>
                    <span class="source-modal-path">${fullPath}</span>
                    <button class="source-modal-close">&times;</button>
                </div>
                <div class="source-modal-body">
                    <pre class="source-content">${this.escapeHtml(content)}</pre>
                </div>
                <div class="source-modal-footer">
                    <div class="source-stats">
                        ${content.split('\n').length} lines | ${content.length} characters
                    </div>
                    <button class="source-modal-copy">📋 Copy to Clipboard</button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        // Add event listeners
        const closeBtn = modal.querySelector('.source-modal-close');
        const copyBtn = modal.querySelector('.source-modal-copy');

        closeBtn.addEventListener('click', () => {
            modal.remove();
        });

        copyBtn.addEventListener('click', () => {
            navigator.clipboard.writeText(content).then(() => {
                copyBtn.textContent = '✓ Copied!';
                setTimeout(() => {
                    copyBtn.textContent = '📋 Copy to Clipboard';
                }, 2000);
            }).catch(err => {
                console.error('Failed to copy:', err);
                this.showError('Failed to copy to clipboard');
            });
        });

        // Close modal when clicking outside
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.remove();
            }
        });

        // Close modal with Escape key
        const escapeHandler = (e) => {
            if (e.key === 'Escape') {
                modal.remove();
                document.removeEventListener('keydown', escapeHandler);
            }
        };
        document.addEventListener('keydown', escapeHandler);
    }

    compareSelected() {
        // For now, this just scrolls to show all reports
        // In a more advanced version, this could create a dedicated comparison view
        const reportsContainer = document.querySelector('.reports-container');
        reportsContainer.scrollIntoView({ behavior: 'smooth' });

        if (this.loadedReports.size < 2) {
            alert('Load at least 2 reports to compare');
            return;
        }

        // Show success message
        this.showMessage(`Comparing ${this.loadedReports.size} reports side-by-side`);
    }

    removeReport(reportId) {
        this.loadedReports.delete(reportId);
        this.updateDisplay();
    }

    clearAllReports() {
        this.loadedReports.clear();
        this.updateDisplay();
    }

    showError(message) {
        // Simple error display - could be enhanced with better UI
        console.error(message);
        alert(`Error: ${message}`);
    }

    generateConsolidatedReportCard(reportId, data, filename) {
        const metadata = data.metadata || {};
        const evaluations = data.evaluations || [];
        const fullPath = filename || 'consolidated_evaluations_report.json';



        // Group evaluations by model to combine results from different test sets
        const modelGroups = {};
        evaluations.forEach(evalData => {
            // For consolidated reports, only process main evaluation entries to avoid double counting
            if (!this.isMainEvaluationEntry(evalData)) {
                return; // Skip individual meeting files
            }

            // Extract base model name (remove test set prefix like "standup_meeting.")
            let modelName = evalData.experiment_name.replace('.experiment', '');
            modelName = modelName.replace(/^[^.]+\./, ''); // Remove any prefix before first dot
            if (modelName.includes('.')) {
                // If still has dots, it's likely the original name with prefix
                modelName = evalData.experiment_name.replace('.experiment', '').replace('standup_meeting.', '');
            }

            if (!modelGroups[modelName]) {
                modelGroups[modelName] = {
                    modelName: modelName,
                    evaluations: [],
                    totalScore: 0,
                    totalCost: 0,
                    totalTokens: 0,
                    excellentCount: 0,
                    goodCount: 0,
                    fairCount: 0,
                    poorCount: 0,
                    // Q&A specific metrics
                    totalAccuracy: 0,
                    totalPassRate: 0,
                    totalQuestions: 0,
                    totalPassed: 0,
                    totalFailed: 0,
                    isQAEvaluation: false,
                    testSets: [],
                    totalInferenceInputTokens: 0,
                    totalInferenceOutputTokens: 0,
                    totalInferenceTokens: 0
                };
            }

            const group = modelGroups[modelName];
            group.evaluations.push(evalData);

            // Track which test set this is from
            const testSetName = evalData.experiment_name.includes('standup_meeting') ? 'meetings' : 'general';
            if (!group.testSets.includes(testSetName)) {
                group.testSets.push(testSetName);
            }

            // Accumulate metrics - check if this is a Q&A or summarization evaluation
            const metrics = evalData.overall_rating?.metrics || {};
            
            if (metrics.accuracy_percentage !== undefined) {
                // Q&A evaluation
                group.isQAEvaluation = true;
                group.totalAccuracy += metrics.accuracy_percentage || 0;
                group.totalPassRate += metrics.pass_rate || 0;
                group.totalQuestions += metrics.num_questions || 0;
                group.totalPassed += metrics.num_passed || 0;
                group.totalFailed += metrics.num_failed || 0;
            } else {
                // Summarization evaluation
                group.totalScore += metrics.quality_score || 0;
                group.excellentCount += metrics.excellent_count || 0;
                group.goodCount += metrics.good_count || 0;
                group.fairCount += metrics.fair_count || 0;
                group.poorCount += metrics.poor_count || 0;
            }

            // Use inference cost from consolidated report (actual model cost, not evaluation cost)
            let experimentCost = 0;
            if (evalData.inference_cost) {
                experimentCost = evalData.inference_cost.total_cost || 0;
            }
            group.totalCost += experimentCost;

            group.totalTokens += evalData.usage?.total_tokens || 0;
            
            // Accumulate inference token usage
            if (evalData.inference_usage) {
                group.totalInferenceInputTokens += evalData.inference_usage.input_tokens || 0;
                group.totalInferenceOutputTokens += evalData.inference_usage.output_tokens || 0;
                group.totalInferenceTokens += evalData.inference_usage.total_tokens || 0;
            }
        });

        // Convert groups to consolidated evaluations
        const consolidatedEvaluations = Object.values(modelGroups).map(group => {
            let avgScore = 0;
            let overallRating = 'poor';
            let metricsData = {};
            
            if (group.isQAEvaluation) {
                // Q&A evaluation - use accuracy as score
                const numEvals = group.evaluations.length;
                avgScore = numEvals > 0 ? group.totalAccuracy / numEvals : 0;
                const avgPassRate = numEvals > 0 ? group.totalPassRate / numEvals : 0;
                
                // Determine rating based on accuracy
                if (avgScore >= 90) overallRating = 'excellent';
                else if (avgScore >= 75) overallRating = 'good';
                else if (avgScore >= 60) overallRating = 'fair';
                
                metricsData = {
                    accuracy_percentage: avgScore,
                    pass_rate: avgPassRate,
                    num_questions: group.totalQuestions,
                    num_passed: group.totalPassed,
                    num_failed: group.totalFailed
                };
            } else {
                // Summarization evaluation
                const totalRatings = group.excellentCount + group.goodCount + group.fairCount + group.poorCount;
                
                // Properly calculate quality score from aggregated rating counts using the same formula as Python
                if (totalRatings > 0) {
                    avgScore = ((group.excellentCount * 4 + group.goodCount * 3 + group.fairCount * 2 + group.poorCount * 1) / totalRatings - 1) * 100 / 3;
                }

                // Determine overall rating based on average score
                if (avgScore >= 85) overallRating = 'excellent';
                else if (avgScore >= 70) overallRating = 'good';
                else if (avgScore >= 50) overallRating = 'fair';
                
                metricsData = {
                    quality_score: avgScore,
                    excellent_count: group.excellentCount,
                    good_count: group.goodCount,
                    fair_count: group.fairCount,
                    poor_count: group.poorCount,
                    total_summaries: totalRatings
                };
            }

            // Calculate average latency across all evaluations for this model
            // Use avg_processing_time_seconds from evaluation data
            // Filter out failed experiments (those with very low processing times and no tokens)
            let avgLatency = 0;
            let latencyCount = 0;
            group.evaluations.forEach(evalData => {
                if (evalData.avg_processing_time_seconds) {
                    // Filter out failed experiments that have:
                    // - Very low processing time (< 1 second) AND
                    // - No inference tokens (indicating failed inference) AND
                    // - Unknown inference type
                    const isFailedExperiment = (
                        evalData.avg_processing_time_seconds < 1.0 &&
                        (!evalData.inference_usage || evalData.inference_usage.total_tokens === 0) &&
                        evalData.inference_type === 'unknown'
                    );
                    
                    if (!isFailedExperiment) {
                        avgLatency += evalData.avg_processing_time_seconds;
                        latencyCount++;
                    }
                }
            });
            if (latencyCount > 0) {
                avgLatency = avgLatency / latencyCount;
            }

            return {
                experiment_name: group.modelName,
                test_sets: group.testSets.join(', '),
                num_evaluations: group.evaluations.length,
                overall_rating: {
                    rating: overallRating,
                    metrics: metricsData
                },
                cost: { total_cost: group.totalCost },
                usage: { total_tokens: group.totalTokens },
                // Pass through inference cost for display
                inference_cost: { total_cost: group.totalCost },
                // Pass through aggregated inference usage for token chart
                inference_usage: {
                    input_tokens: group.totalInferenceInputTokens,
                    output_tokens: group.totalInferenceOutputTokens,
                    total_tokens: group.totalInferenceTokens
                },
                // Pass through average processing time in seconds
                avg_processing_time_seconds: avgLatency,
                // Aggregate aspect summaries from all evaluations
                aspect_summary: group.evaluations[0]?.aspect_summary || {}
            };
        });

        // Sort consolidated evaluations by score (quality_score for summarization, accuracy_percentage for Q&A)
        const sortedEvaluations = consolidatedEvaluations.sort((a, b) => {
            const scoreA = a.overall_rating?.metrics?.quality_score || a.overall_rating?.metrics?.accuracy_percentage || 0;
            const scoreB = b.overall_rating?.metrics?.quality_score || b.overall_rating?.metrics?.accuracy_percentage || 0;
            return scoreB - scoreA;
        });

        // Generate comparison table
        const tableHtml = this.generateComparisonTable(sortedEvaluations);

        // Generate charts
        const chartsHtml = this.generateComparisonCharts(sortedEvaluations);

        // Generate summary statistics with consolidated data
        // Pass the original evaluations array to get meeting types from individual meeting files
        const summaryHtml = this.generateConsolidatedSummary(metadata, evaluations);

        // Generate aspect breakdown with consolidated data
        const aspectBreakdownHtml = this.generateAspectBreakdown(consolidatedEvaluations);

        return `
            <div class="report-card consolidated-report" data-report-id="${reportId}">
                <div class="report-header">
                    <h3 title="${fullPath}">📊 Consolidated Evaluation Report</h3>
                    <div class="meta">
                        ${consolidatedEvaluations.length} Unique Models | ${metadata.total_evaluations} Total Evaluations |
                        ${metadata.timestamp || 'N/A'}
                    </div>
                    <div class="report-actions">
                        <div class="export-dropdown">
                            <button class="export-btn" title="Export report">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                    <polyline points="7 10 12 15 17 10"/>
                                    <line x1="12" y1="15" x2="12" y2="3"/>
                                </svg>
                            </button>
                            <div class="export-menu">
                                <button class="export-option" data-format="png" data-report-id="${reportId}">📷 Export as PNG</button>
                                <button class="export-option" data-format="pdf" data-report-id="${reportId}">📄 Export as PDF</button>
                            </div>
                        </div>
                        <button class="report-close" data-report-id="${reportId}">×</button>
                    </div>
                </div>
                <div class="report-content">
                    ${summaryHtml}
                    ${chartsHtml}
                    ${tableHtml}
                    ${aspectBreakdownHtml}
                </div>
            </div>
        `;
    }

    generateConsolidatedSummary(metadata, evaluations) {
        // Use pre-calculated metadata values from the consolidated report
        const totalCost = metadata.total_cost?.total_cost || 0;
        const totalTokens = metadata.total_usage?.total_tokens || 0;
        const inputTokens = metadata.total_usage?.input_tokens || 0;
        const outputTokens = metadata.total_usage?.output_tokens || 0;

        // Calculate aggregate statistics from main evaluation entries only (to avoid double counting)
        let excellentCount = 0, goodCount = 0, fairCount = 0, poorCount = 0;
        let cloudCount = 0, localCount = 0;
        const meetingTypes = new Set();
        const uniqueModelNames = new Set();

        evaluations.forEach(evalData => {
            // Only process main evaluation entries for overall quality metrics to avoid double counting
            if (this.isMainEvaluationEntry(evalData)) {
                const metrics = evalData.overall_rating?.metrics || {};
                excellentCount += metrics.excellent_count || 0;
                goodCount += metrics.good_count || 0;
                fairCount += metrics.fair_count || 0;
                poorCount += metrics.poor_count || 0;

                // Extract and count unique models
                const expName = evalData.experiment_name;
                let modelName = expName.replace('.experiment', '');
                uniqueModelNames.add(modelName);

                // Count cloud vs local models (for subcaption display)
                if (expName.includes('Claude')) {
                    cloudCount++;
                } else {
                    localCount++;
                }
            }

            // Extract meeting types from ALL evaluation entries (including individual meeting files)
            const expName = evalData.experiment_name || '';
            if (expName.includes('.')) {
                const meetingName = expName.split('.')[0];
                // Clean up meeting type name - only count actual meetings, not metadata
                if (meetingName.includes('_meeting_')) {
                    // Extract base meeting type (e.g., "all_hands_meeting")
                    const parts = meetingName.split('_meeting_');
                    meetingTypes.add(parts[0] + '_meeting');
                }
                // Note: transcript_metadata is excluded as it's not a meeting type
            }
        });

        const totalRatings = excellentCount + goodCount + fairCount + poorCount;
        const uniqueModels = uniqueModelNames.size;
        const costPerSummary = totalRatings > 0 ? (totalCost / totalRatings).toFixed(3) : 0;

        return `
            <div class="consolidated-summary enhanced">
                <h4>📈 Overall Summary</h4>
                
                <!-- Primary Statistics -->
                <div class="summary-grid primary-stats">
                    <div class="summary-card">
                        <div class="summary-value">${uniqueModels || 0}</div>
                        <div class="summary-label" data-tooltip="Number of unique AI models tested">Models Evaluated</div>
                        <div class="summary-subcaption">${cloudCount} Cloud, ${localCount} Local</div>
                    </div>
                    <div class="summary-card">
                        <div class="summary-value">${totalRatings}</div>
                        <div class="summary-label" data-tooltip="Total summaries evaluated">Total Summaries</div>
                        <div class="summary-subcaption">Across ${meetingTypes.size} meeting types</div>
                    </div>
                    <div class="summary-card">
                        <div class="summary-value">$${totalCost.toFixed(4)}</div>
                        <div class="summary-label" data-tooltip="Claude evaluation cost">Evaluation Cost</div>
                        <div class="summary-subcaption">$${costPerSummary}/summary</div>
                    </div>
                    <div class="summary-card">
                        <div class="summary-value">${(totalTokens / 1000).toFixed(1)}K</div>
                        <div class="summary-label" data-tooltip="Total tokens processed">Tokens Used</div>
                        <div class="summary-subcaption">${(inputTokens/1000).toFixed(0)}K in, ${(outputTokens/1000).toFixed(0)}K out</div>
                    </div>
                </div>
            </div>
        `;
    }

    generateComparisonTable(evaluations) {

        let tableRows = '';

        evaluations.forEach((evalData, index) => {
            // For consolidated reports, only process main evaluation entries to avoid double counting
            if (!this.isMainEvaluationEntry(evalData)) {
                return; // Skip individual meeting files
            }

            const rating = evalData.overall_rating || {};
            const metrics = rating.metrics || {};
            
            // Check if this is Q&A (has accuracy_percentage) or summarization (has quality_score)
            const isQA = metrics.accuracy_percentage !== undefined;
            const score = isQA ? metrics.accuracy_percentage : (metrics.quality_score || 0);
            const cost = evalData.cost?.total_cost || 0;
            const tokens = evalData.usage?.total_tokens || 0;

            // Extract model name from experiment name
            let fullModelName = evalData.experiment_name.replace('.experiment', '');
            fullModelName = fullModelName.replace('standup_meeting.', '');

            // Create display name (truncated if needed)
            let displayName = fullModelName;
            if (displayName.length > 50) {
                displayName = displayName.substring(0, 47) + '...';
            }

            // Determine if it's a local or cloud model
            // Check inference_cost to determine if it's local (cost = 0) or cloud
            const inferenceType = evalData.inference_type || '';
            const inferenceCost = evalData.inference_cost?.total_cost || 0;
            const isLocal = inferenceType === 'local' ||
                           fullModelName.toLowerCase().includes('lemonade') ||
                           (inferenceCost === 0 && !fullModelName.toLowerCase().includes('claude'));

            // Add test sets indicator if this is combined data
            const testSetsIndicator = evalData.test_sets ? ` (${evalData.test_sets})` : '';
            const numEvals = evalData.num_evaluations || 1;

            tableRows += `
                <tr class="quality-row-${rating.rating}">
                    <td class="rank-cell">${index + 1}</td>
                    <td class="model-cell">
                        <div class="model-name-wrapper" title="${fullModelName}${testSetsIndicator}">
                            <span class="model-name">${displayName}</span>
                            ${isLocal ? '<span class="badge-local" data-tooltip="Model runs locally on your machine - no API costs">LOCAL</span>' : '<span class="badge-cloud" data-tooltip="Model runs on cloud servers - usage-based pricing">CLOUD</span>'}
                            ${numEvals > 1 ? `<span class="badge-count" title="Combined from ${numEvals} evaluations">${numEvals}×</span>` : ''}
                        </div>
                    </td>
                    <td class="score-cell">
                        <div class="score-bar-container">
                            <div class="score-bar" style="width: ${score}%"></div>
                            <span class="score-text">${Math.round(score)}%</span>
                        </div>
                    </td>
                    <td class="rating-cell">
                        <span class="quality-rating rating-${rating.rating}">${rating.rating}</span>
                    </td>
                    <td class="distribution-cell">
                        <div class="mini-distribution">
                            ${isQA ? `
                                <span class="mini-count excellent" data-tooltip="Pass Rate: ${((metrics.pass_rate || 0) * 100).toFixed(1)}%">✓ ${metrics.num_passed || 0}</span>
                                <span class="mini-count poor" data-tooltip="Failed Questions">✗ ${metrics.num_failed || 0}</span>
                                <span class="mini-count" data-tooltip="Total Questions">Σ ${metrics.num_questions || 0}</span>
                            ` : `
                                <span class="mini-count excellent" data-tooltip="${metrics.excellent_count || 0} summaries rated Excellent (comprehensive & accurate)">${metrics.excellent_count || 0}</span>
                                <span class="mini-count good" data-tooltip="${metrics.good_count || 0} summaries rated Good (mostly accurate, minor issues)">${metrics.good_count || 0}</span>
                                <span class="mini-count fair" data-tooltip="${metrics.fair_count || 0} summaries rated Fair (acceptable but missing details)">${metrics.fair_count || 0}</span>
                                <span class="mini-count poor" data-tooltip="${metrics.poor_count || 0} summaries rated Poor (significant errors or omissions)">${metrics.poor_count || 0}</span>
                            `}
                        </div>
                    </td>
                    <td class="cost-cell">
                        ${isLocal ? '<span style="color: #28a745;">FREE</span>' :
                          (cost > 0 ? '$' + cost.toFixed(4) : '<span style="color: #28a745;">FREE</span>')}
                    </td>
                    <td class="tokens-cell">${(tokens / 1000).toFixed(1)}K</td>
                </tr>
            `;
        });

        return `
            <div class="comparison-table-section">
                <h4>🏆 Model Performance Comparison</h4>
                <div class="table-container">
                    <table class="comparison-table">
                        <thead>
                            <tr>
                                <th class="rank-header" data-tooltip="Model ranking based on Grade">Rank</th>
                                <th class="model-header" data-tooltip="AI model name and type (LOCAL runs on your machine, CLOUD runs remotely)">Model</th>
                                <th class="score-header" data-tooltip="Score: accuracy % for Q&A, quality % for summarization">Score</th>
                                <th class="rating-header" data-tooltip="Overall rating (Excellent/Good/Fair/Poor)">Rating</th>
                                <th class="distribution-header" data-tooltip="Performance breakdown across evaluations">Performance</th>
                                <th class="cost-header" data-tooltip="Inference cost (FREE for local models)">Cost</th>
                                <th class="tokens-header" data-tooltip="Number of tokens processed (1K = 1,000 tokens)">Tokens</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${tableRows}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    generateComparisonCharts(evaluations) {

        // Prepare data for charts
        const labels = [];
        const scores = [];
        const costs = [];
        const tokens = [];
        const latencies = [];
        const inputTokens = [];
        const outputTokens = [];
        const totalTokens = [];

        evaluations.forEach(evalData => {
            // For consolidated reports, only process main evaluation entries to avoid double counting
            if (!this.isMainEvaluationEntry(evalData)) {
                return; // Skip individual meeting files
            }

            let fullModelName = evalData.experiment_name.replace('.experiment', '');
            fullModelName = fullModelName.replace('standup_meeting.', '');

            // Create a shorter label for display
            let shortName = fullModelName;
            if (fullModelName.includes('Lemonade-')) {
                shortName = fullModelName.replace('Lemonade-', '');
            }
            if (fullModelName.includes('Claude-')) {
                shortName = fullModelName.replace('Claude-', 'Claude ');
            }
            if (shortName.includes('-Basic-Summary')) {
                shortName = shortName.replace('-Basic-Summary', '');
            }
            // Handle Llama model names
            if (shortName.includes('Llama-3.2-3B-Instruct-Hybrid')) {
                shortName = 'Llama-3.2-3B-Instruct';
            }
            if (shortName.includes('LFM2-1.2B-Basic-Summary')) {
                shortName = 'LFM2-1.2B';
            }

            // Further shorten if still too long
            if (shortName.length > 30) {
                shortName = shortName.substring(0, 27) + '...';
            }

            // Use inference cost from consolidated report (actual model cost, not evaluation cost)
            let experimentCost = 0;
            if (evalData.inference_cost) {
                experimentCost = evalData.inference_cost.total_cost || 0;
            }

            // Extract latency data from avg_processing_time_seconds in consolidated report
            let avgLatency = evalData.avg_processing_time_seconds || 0;

            // Extract token usage data (use inference_usage for actual model usage)
            const inferenceUsage = evalData.inference_usage || {};
            const inputToks = inferenceUsage.input_tokens || 0;
            const outputToks = inferenceUsage.output_tokens || 0;
            const totalToks = inferenceUsage.total_tokens || 0;

            labels.push({ short: shortName, full: fullModelName });
            const metrics = evalData.overall_rating?.metrics || {};
            const score = metrics.accuracy_percentage !== undefined ? metrics.accuracy_percentage : (metrics.quality_score || 0);
            scores.push(score);
            costs.push(experimentCost);
            tokens.push((evalData.usage?.total_tokens || 0) / 1000); // in K
            latencies.push(avgLatency);
            inputTokens.push(inputToks);
            outputTokens.push(outputToks);
            totalTokens.push(totalToks);
        });

        // Find max values for scaling
        const maxScore = 100;
        const maxCost = Math.max(...costs) * 1.1 || 1;
        const maxTokens = Math.max(...totalTokens) * 1.1 || 1;
        const maxLatency = Math.max(...latencies) * 1.1 || 1;

        // Generate bar charts using CSS
        const scoreChartBars = labels.map((labelObj, i) => {
            const height = (scores[i] / maxScore) * 100;
            const rating = evaluations[i].overall_rating?.rating || 'fair';
            return `
                <div class="chart-bar-group">
                    <div class="chart-bar-container">
                        <div class="chart-bar quality-${rating}" style="height: ${height}%; position: relative;"
                             data-tooltip="Grade: ${Math.round(scores[i])}%">
                            <span class="bar-value-top percentage-value">${Math.round(scores[i])}%</span>
                        </div>
                    </div>
                    <div class="chart-label" title="${labelObj.full}">${labelObj.short}</div>
                </div>
            `;
        }).join('');

        const costChartBars = labels.map((labelObj, i) => {
            const isFree = costs[i] === 0;
            const height = isFree ? 5 : (costs[i] / maxCost) * 100; // Min height for free models
            const costDisplay = isFree ? 'FREE' : '$' + costs[i].toFixed(4);
            return `
                <div class="chart-bar-group">
                    <div class="chart-bar-container">
                        <div class="chart-bar cost-bar ${isFree ? 'free-model' : ''}" style="height: ${height}%; position: relative;"
                             data-tooltip="Inference Cost: ${costDisplay}${isFree ? ' (Local Model)' : ' (Cloud API)'}">
                            <span class="bar-value-top" style="${isFree ? 'color: #28a745;' : ''}">${costDisplay}</span>
                        </div>
                    </div>
                    <div class="chart-label" title="${labelObj.full}">${labelObj.short}</div>
                </div>
            `;
        }).join('');

        const latencyChartBars = labels.map((labelObj, i) => {
            const latency = latencies[i];
            const height = latency > 0 ? (latency / maxLatency) * 100 : 0;
            
            // Format latency display
            let latencyDisplay = 'N/A';
            if (latency > 0) {
                if (latency < 1) {
                    latencyDisplay = `${(latency * 1000).toFixed(0)}ms`;
                } else if (latency < 10) {
                    latencyDisplay = `${latency.toFixed(2)}s`;
                } else {
                    latencyDisplay = `${latency.toFixed(1)}s`;
                }
            }
            
            return `
                <div class="chart-bar-group">
                    <div class="chart-bar-container">
                        <div class="chart-bar latency-bar" style="height: ${height}%; position: relative;"
                             data-tooltip="Average inference time: ${latencyDisplay}">
                            <span class="bar-value-top">${latencyDisplay}</span>
                        </div>
                    </div>
                    <div class="chart-label" title="${labelObj.full}">${labelObj.short}</div>
                </div>
            `;
        }).join('');

        // Generate stacked token usage chart
        const tokenChartBars = labels.map((labelObj, i) => {
            const inputHeight = (inputTokens[i] / maxTokens) * 100;
            const outputHeight = (outputTokens[i] / maxTokens) * 100;
            const totalHeight = (totalTokens[i] / maxTokens) * 100;
            
            // Format token display
            const formatTokens = (num) => {
                if (num >= 1000) {
                    return `${(num / 1000).toFixed(1)}K`;
                }
                return num.toString();
            };
            
            const inputPercentage = totalTokens[i] > 0 ? ((inputTokens[i] / totalTokens[i]) * 100).toFixed(1) : 0;
            const outputPercentage = totalTokens[i] > 0 ? ((outputTokens[i] / totalTokens[i]) * 100).toFixed(1) : 0;
            
            return `
                <div class="chart-bar-group">
                    <div class="chart-bar-container">
                        <div class="stacked-bar-wrapper" style="height: ${totalHeight}%; position: relative;">
                            <span class="bar-value-top">${formatTokens(totalTokens[i])}</span>
                            <div class="stacked-bar input-tokens" 
                                 style="height: ${(inputHeight / totalHeight) * 100}%"
                                 data-tooltip="Input Tokens: ${formatTokens(inputTokens[i])} (${inputPercentage}% of total)">
                            </div>
                            <div class="stacked-bar output-tokens" 
                                 style="height: ${(outputHeight / totalHeight) * 100}%"
                                 data-tooltip="Output Tokens: ${formatTokens(outputTokens[i])} (${outputPercentage}% of total)">
                            </div>
                        </div>
                    </div>
                    <div class="chart-label" title="${labelObj.full}">${labelObj.short}</div>
                </div>
            `;
        }).join('');

        return `
            <div class="charts-section-vertical">
                <div class="chart-container-full">
                    <h5>📊 Grade Comparison</h5>
                    <div class="bar-chart quality-chart">
                        ${scoreChartBars}
                    </div>
                </div>
                <div class="chart-container-full">
                    <h5>⏱️ Latency Comparison</h5>
                    <div class="bar-chart latency-chart">
                        ${latencyChartBars}
                    </div>
                </div>
                <div class="chart-container-full">
                    <h5>📈 Token Usage Comparison</h5>
                    <div class="token-legend">
                        <span class="legend-item"><span class="legend-color input-color"></span> Input Tokens</span>
                        <span class="legend-item"><span class="legend-color output-color"></span> Output Tokens</span>
                    </div>
                    <div class="bar-chart token-chart">
                        ${tokenChartBars}
                    </div>
                </div>
                <div class="chart-container-full">
                    <h5>💰 Cost Comparison</h5>
                    <div class="bar-chart cost-chart">
                        ${costChartBars}
                    </div>
                </div>
            </div>
        `;
    }

    generateAspectBreakdown(evaluations) {
        // Define the quality aspects we're tracking
        const aspects = [
            { key: 'executive_summary_quality', label: 'Executive Summary', icon: '📋', tooltip: 'Quality of high-level summary and key takeaways' },
            { key: 'detail_completeness', label: 'Detail Completeness', icon: '📝', tooltip: 'How well important details are captured and preserved' },
            { key: 'action_items_structure', label: 'Action Items', icon: '✅', tooltip: 'Identification and clarity of action items and next steps' },
            { key: 'key_decisions_clarity', label: 'Key Decisions', icon: '🎯', tooltip: 'Recognition and documentation of key decisions made' },
            { key: 'participant_information', label: 'Participant Info', icon: '👥', tooltip: 'Quality of identifying participants and their contributions' },
            { key: 'topic_organization', label: 'Topic Organization', icon: '📂', tooltip: 'Logical structure and organization of topics and themes' }
        ];

        // Collect aspect data from evaluations
        const aspectData = {};
        const modelScores = {};
        const modelGrades = {}; // Store grade scores for each model

        evaluations.forEach(evalData => {
            // For consolidated reports, only process main evaluation entries to avoid double counting
            if (!this.isMainEvaluationEntry(evalData)) {
                return; // Skip individual meeting files
            }

            // For consolidated reports, the actual evaluation data might be nested
            let modelName = evalData.experiment_name || evalData.model || 'Unknown';
            modelName = modelName.replace('.experiment', '').replace('standup_meeting.', '');

            // Get shortened display name
            let displayName = modelName;
            if (displayName.includes('Lemonade-')) {
                displayName = displayName.replace('Lemonade-', '');
            }
            if (displayName.includes('Claude-')) {
                displayName = displayName.replace('Claude-', 'Claude ');
            }
            if (displayName.includes('-Basic-Summary')) {
                displayName = displayName.replace('-Basic-Summary', '');
            }

            modelScores[displayName] = {};

            // Store the score (accuracy for Q&A, quality score for summarization) for this model
            if (evalData.overall_rating && evalData.overall_rating.metrics) {
                const metrics = evalData.overall_rating.metrics;
                const score = metrics.accuracy_percentage !== undefined ? metrics.accuracy_percentage : (metrics.quality_score || 0);
                modelGrades[displayName] = score;
            }

            // Check if this evaluation has aspect_summary data (from consolidated report)
            if (evalData.aspect_summary) {
                aspects.forEach(aspect => {
                    const aspectSummary = evalData.aspect_summary[aspect.key];
                    if (aspectSummary && aspectSummary.most_common_rating) {
                        const modeRating = aspectSummary.most_common_rating;
                        modelScores[displayName][aspect.key] = modeRating;

                        // Track overall aspect performance using distribution
                        if (!aspectData[aspect.key]) {
                            aspectData[aspect.key] = { excellent: 0, good: 0, fair: 0, poor: 0 };
                        }

                        // Add all ratings from the distribution
                        const distribution = aspectSummary.rating_distribution || {};
                        Object.entries(distribution).forEach(([rating, count]) => {
                            if (aspectData[aspect.key][rating] !== undefined) {
                                aspectData[aspect.key][rating] += count;
                            }
                        });
                    }
                });
            }
            // Fallback: Check if this evaluation has per_question data (individual reports)
            else if (evalData.per_question && evalData.per_question.length > 0) {
                // Aggregate scores across all questions for this model
                const questionScores = {};

                evalData.per_question.forEach(question => {
                    if (question.analysis) {
                        aspects.forEach(aspect => {
                            const aspectResult = question.analysis[aspect.key];
                            if (aspectResult && aspectResult.rating) {
                                if (!questionScores[aspect.key]) {
                                    questionScores[aspect.key] = [];
                                }
                                questionScores[aspect.key].push(aspectResult.rating);
                            }
                        });
                    }
                });

                // Calculate mode (most common rating) for each aspect
                aspects.forEach(aspect => {
                    if (questionScores[aspect.key] && questionScores[aspect.key].length > 0) {
                        const ratings = questionScores[aspect.key];
                        const ratingCounts = {};
                        ratings.forEach(r => {
                            ratingCounts[r] = (ratingCounts[r] || 0) + 1;
                        });
                        // Find most common rating
                        let maxCount = 0;
                        let modeRating = 'fair';
                        Object.entries(ratingCounts).forEach(([rating, count]) => {
                            if (count > maxCount) {
                                maxCount = count;
                                modeRating = rating;
                            }
                        });
                        modelScores[displayName][aspect.key] = modeRating;

                        // Track overall aspect performance
                        if (!aspectData[aspect.key]) {
                            aspectData[aspect.key] = { excellent: 0, good: 0, fair: 0, poor: 0 };
                        }
                        aspectData[aspect.key][modeRating]++;
                    }
                });
            }
        });

        // Generate the aspect breakdown visualization
        let aspectRows = '';
        aspects.forEach(aspect => {
            const data = aspectData[aspect.key] || { excellent: 0, good: 0, fair: 0, poor: 0 };
            const total = data.excellent + data.good + data.fair + data.poor;

            if (total > 0) {
                // Group models by their rating for this aspect
                const modelsByRating = {
                    excellent: [],
                    good: [],
                    fair: [],
                    poor: []
                };
                
                Object.entries(modelScores).forEach(([model, scores]) => {
                    const rating = scores[aspect.key];
                    if (rating && modelsByRating[rating]) {
                        modelsByRating[rating].push(model);
                    }
                });

                // Create detailed tooltips for each rating level
                const excellentTooltip = data.excellent > 0 ? 
                    `Excellent (${data.excellent} ${data.excellent === 1 ? 'summary' : 'summaries'}):

Models with excellent ${aspect.label.toLowerCase()}:
• ${modelsByRating.excellent.join('\n• ')}

These models excel at ${aspect.tooltip.toLowerCase()}` : '';
                
                const goodTooltip = data.good > 0 ?
                    `Good (${data.good} ${data.good === 1 ? 'summary' : 'summaries'}):

Models with good ${aspect.label.toLowerCase()}:
• ${modelsByRating.good.join('\n• ')}

These models perform well at ${aspect.tooltip.toLowerCase()}` : '';
                
                const fairTooltip = data.fair > 0 ?
                    `Fair (${data.fair} ${data.fair === 1 ? 'summary' : 'summaries'}):

Models with fair ${aspect.label.toLowerCase()}:
• ${modelsByRating.fair.join('\n• ')}

These models need improvement at ${aspect.tooltip.toLowerCase()}` : '';
                
                const poorTooltip = data.poor > 0 ?
                    `Poor (${data.poor} ${data.poor === 1 ? 'summary' : 'summaries'}):

Models with poor ${aspect.label.toLowerCase()}:
• ${modelsByRating.poor.join('\n• ')}

These models struggle with ${aspect.tooltip.toLowerCase()}` : '';

                aspectRows += `
                    <div class="aspect-row">
                        <div class="aspect-header">
                            <span class="aspect-icon">${aspect.icon}</span>
                            <span class="aspect-label" data-tooltip="${aspect.tooltip}">${aspect.label}</span>
                        </div>
                        <div class="aspect-distribution">
                            ${data.excellent > 0 ? `<div class="aspect-bar excellent" 
                                style="width: ${(data.excellent/total*100)}%" 
                                data-tooltip="${excellentTooltip}">
                                ${data.excellent}
                            </div>` : ''}
                            ${data.good > 0 ? `<div class="aspect-bar good" 
                                style="width: ${(data.good/total*100)}%" 
                                data-tooltip="${goodTooltip}">
                                ${data.good}
                            </div>` : ''}
                            ${data.fair > 0 ? `<div class="aspect-bar fair" 
                                style="width: ${(data.fair/total*100)}%" 
                                data-tooltip="${fairTooltip}">
                                ${data.fair}
                            </div>` : ''}
                            ${data.poor > 0 ? `<div class="aspect-bar poor" 
                                style="width: ${(data.poor/total*100)}%" 
                                data-tooltip="${poorTooltip}">
                                ${data.poor}
                            </div>` : ''}
                        </div>
                    </div>
                `;
            }
        });

        // Create model-aspect matrix with clean table format
        let matrixHtml = '';
        if (Object.keys(modelScores).length > 0) {
            // Create table header with Grade column
            let headerRow = '<tr><th class="model-header">Model</th>';
            headerRow += '<th class="grade-header">Grade</th>'; // Add Grade column
            aspects.forEach(aspect => {
                headerRow += `<th class="aspect-header" data-tooltip="${aspect.tooltip}">${aspect.label.replace(' Quality', '').replace(' Structure', '').replace(' Information', '')}</th>`;
            });
            headerRow += '</tr>';

            // Create table rows
            let tableRows = '';
            Object.entries(modelScores).forEach(([model, scores]) => {
                tableRows += `<tr><td class="model-name">${model}</td>`;

                // Add grade cell
                const grade = modelGrades[model] || 0;
                const gradeClass = grade >= 85 ? 'cell-excellent' :
                                   grade >= 70 ? 'cell-good' :
                                   grade >= 50 ? 'cell-fair' : 'cell-poor';
                tableRows += `<td class="${gradeClass} grade-cell" title="Grade">${Math.round(grade)}%</td>`;

                // Add aspect rating cells
                aspects.forEach(aspect => {
                    const rating = scores[aspect.key] || 'unknown';
                    const ratingClass = `cell-${rating}`;
                    tableRows += `<td class="${ratingClass}" title="${aspect.label}: ${rating}">${rating}</td>`;
                });
                tableRows += '</tr>';
            });

            matrixHtml = `
                <div class="clean-matrix-container">
                    <h5>🎯 Model Performance Summary</h5>
                    <table class="clean-performance-matrix">
                        <thead>${headerRow}</thead>
                        <tbody>${tableRows}</tbody>
                    </table>
                </div>
            `;
        }

        return aspectRows ? `
            <div class="aspect-breakdown-section">
                <h4>🔍 Quality Aspect Analysis</h4>
                <div class="aspect-breakdown">
                    ${aspectRows}
                </div>
                ${matrixHtml}
            </div>
        ` : '';
    }

    showMessage(message) {
        // Simple message display
        console.log(message);
        const msg = document.createElement('div');
        msg.textContent = message;
        msg.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: #28a745;
            color: white;
            padding: 10px 20px;
            border-radius: 5px;
            z-index: 1000;
        `;
        document.body.appendChild(msg);
        setTimeout(() => msg.remove(), 3000);
    }

    showProgress(message) {
        const progress = document.createElement('div');
        progress.className = 'export-progress';
        progress.innerHTML = `
            <h3>${message}</h3>
            <div class="export-spinner"></div>
        `;
        document.body.appendChild(progress);
        return progress;
    }

    async exportReportAsPNG(reportId) {
        const progress = this.showProgress('Generating PNG...');

        try {
            // Get the specific report card
            const element = document.querySelector(`.report-card[data-report-id="${reportId}"]`);

            if (!element) {
                throw new Error('Report not found');
            }

            // Clone the element to manipulate it without affecting the display
            const clonedElement = element.cloneNode(true);
            
            // Add export-ready class to ensure proper styling
            clonedElement.classList.add('export-ready');
            
            // Create a temporary container off-screen
            const tempContainer = document.createElement('div');
            tempContainer.style.cssText = `
                position: absolute;
                left: -9999px;
                top: 0;
                width: ${Math.max(element.scrollWidth, 1600)}px;
                background: white;
            `;
            document.body.appendChild(tempContainer);
            tempContainer.appendChild(clonedElement);

            // Expand all collapsible sections in the clone
            const collapsibleContents = clonedElement.querySelectorAll('.collapsible-content');
            const collapsibleToggles = clonedElement.querySelectorAll('.collapsible-toggle');
            
            collapsibleContents.forEach(content => {
                content.classList.add('expanded');
                content.style.maxHeight = 'none';
                content.style.overflow = 'visible';
            });
            
            collapsibleToggles.forEach(toggle => {
                toggle.classList.add('expanded');
                toggle.textContent = '▼';
            });

            // Ensure all content is visible
            clonedElement.style.overflow = 'visible';
            clonedElement.style.width = '100%';
            clonedElement.style.maxWidth = 'none';
            
            const reportContent = clonedElement.querySelector('.report-content');
            if (reportContent) {
                reportContent.style.overflow = 'visible';
                reportContent.style.maxWidth = 'none';
            }

            // Ensure tables and charts are fully visible
            const tables = clonedElement.querySelectorAll('table');
            tables.forEach(table => {
                table.style.width = '100%';
                table.style.maxWidth = 'none';
            });

            const chartContainers = clonedElement.querySelectorAll('.chart-container, .chart-container-full, .charts-section-vertical');
            chartContainers.forEach(container => {
                container.style.overflow = 'visible';
                container.style.maxWidth = 'none';
            });

            // Wait a bit for any dynamic content to render
            await new Promise(resolve => setTimeout(resolve, 100));

            // Force layout recalculation
            clonedElement.offsetHeight;

            // Get actual dimensions after all styles are applied
            const actualWidth = Math.max(clonedElement.scrollWidth, clonedElement.offsetWidth, 1600);
            const actualHeight = clonedElement.scrollHeight;

            // Use html2canvas to capture the cloned element
            const canvas = await html2canvas(clonedElement, {
                scale: 2, // Higher quality
                logging: false,
                backgroundColor: '#ffffff',
                width: actualWidth,
                height: actualHeight,
                windowWidth: actualWidth,
                windowHeight: actualHeight,
                useCORS: true,
                allowTaint: true,
                scrollX: 0,
                scrollY: 0
            });

            // Clean up the temporary container
            document.body.removeChild(tempContainer);

            // Convert to blob and download
            canvas.toBlob((blob) => {
                const url = URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.download = `gaia-report-${reportId}-${new Date().toISOString().slice(0,10)}.png`;
                link.href = url;
                link.click();
                URL.revokeObjectURL(url);

                progress.remove();
                this.showMessage('PNG exported successfully!');
            });
        } catch (error) {
            console.error('Failed to export PNG:', error);
            progress.remove();
            this.showError(`Failed to export PNG: ${error.message}`);
        }
    }

    async exportReportAsPDF(reportId) {
        const progress = this.showProgress('Generating PDF...');

        try {
            // Get the specific report card
            const element = document.querySelector(`.report-card[data-report-id="${reportId}"]`);

            if (!element) {
                throw new Error('Report not found');
            }

            // Initialize jsPDF
            const { jsPDF } = window.jspdf;
            const pdf = new jsPDF('p', 'mm', 'a4');

            // PDF dimensions
            const pdfWidth = 210; // A4 width in mm
            const pdfHeight = 297; // A4 height in mm
            const margin = 10; // mm margin
            const contentWidth = pdfWidth - (2 * margin);
            const contentHeight = pdfHeight - (2 * margin);

            // Capture the entire report first
            const fullCanvas = await html2canvas(element, {
                scale: 2,
                logging: false,
                backgroundColor: '#ffffff',
                windowWidth: element.scrollWidth,
                windowHeight: element.scrollHeight,
                useCORS: true,
                allowTaint: true
            });

            // Calculate total height needed
            const totalHeight = (fullCanvas.height * contentWidth) / fullCanvas.width;
            const pageCount = Math.ceil(totalHeight / contentHeight);

            // Add pages with smart breaks
            for (let page = 0; page < pageCount; page++) {
                if (page > 0) {
                    pdf.addPage();
                }

                // Calculate the portion of the image to use for this page
                const sourceY = (page * contentHeight * fullCanvas.width) / contentWidth;
                const sourceHeight = Math.min(
                    (contentHeight * fullCanvas.width) / contentWidth,
                    fullCanvas.height - sourceY
                );

                // Create a temporary canvas for this page's content
                const pageCanvas = document.createElement('canvas');
                const pageCtx = pageCanvas.getContext('2d');
                pageCanvas.width = fullCanvas.width;
                pageCanvas.height = sourceHeight;

                // Draw the portion of the full canvas onto the page canvas
                pageCtx.drawImage(
                    fullCanvas,
                    0, sourceY, fullCanvas.width, sourceHeight,
                    0, 0, fullCanvas.width, sourceHeight
                );

                // Convert to image and add to PDF
                const imgData = pageCanvas.toDataURL('image/png');
                const imgHeight = (sourceHeight * contentWidth) / fullCanvas.width;
                pdf.addImage(imgData, 'PNG', margin, margin, contentWidth, imgHeight);
            }

            // Save the PDF
            pdf.save(`gaia-report-${reportId}-${new Date().toISOString().slice(0,10)}.pdf`);

            progress.remove();
            this.showMessage('PDF exported successfully!');
        } catch (error) {
            console.error('Failed to export PDF:', error);
            progress.remove();
            this.showError(`Failed to export PDF: ${error.message}`);
        }
    }

    // Agent Output Helper Methods
    generateAgentSummarySection(summary) {
        const statusClass = summary.status === 'success' ? 'success' : 'error';
        const statusIcon = summary.status === 'success' ? '✅' : '❌';
        
        return `
            <div class="section">
                <h4>📊 Execution Summary</h4>
                <div class="summary-status-banner ${statusClass}">
                    <div class="status-icon">${statusIcon}</div>
                    <div class="status-details">
                        <div class="status-text">${summary.status.toUpperCase()}</div>
                        <div class="status-result">${summary.result}</div>
                    </div>
                </div>
                <div class="metrics-grid">
                    <div class="metric">
                        <span class="metric-label">Steps Taken</span>
                        <span class="metric-value">${summary.steps_taken}</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Total Messages</span>
                        <span class="metric-value">${summary.conversation_length}</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Tool Calls</span>
                        <span class="metric-value">${summary.tool_calls_count}</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Error Count</span>
                        <span class="metric-value ${summary.error_count > 0 ? 'error' : 'success'}">${summary.error_count}</span>
                    </div>
                </div>
            </div>
        `;
    }

    generateConversationFlowSection(conversation) {
        let flowHtml = '';
        
        conversation.forEach((msg, index) => {
            let messageClass = '';
            let roleLabel = '';
            let content = '';
            
            if (msg.role === 'user') {
                messageClass = 'user-message';
                roleLabel = 'User';
                content = `<div class="message-text">${this.escapeHtml(msg.content)}</div>`;
            } else if (msg.role === 'assistant') {
                messageClass = 'assistant-message';
                roleLabel = 'Assistant';
                if (typeof msg.content === 'object') {
                    if (msg.content.thought && msg.content.goal) {
                        content = `<div class="assistant-reasoning">`;
                        content += `<div class="reasoning-item"><span class="reasoning-label">💭 Thought:</span> ${this.escapeHtml(msg.content.thought)}</div>`;
                        content += `<div class="reasoning-item"><span class="reasoning-label">🎯 Goal:</span> ${this.escapeHtml(msg.content.goal)}</div>`;
                        
                        if (msg.content.tool) {
                            content += `<div class="tool-invocation">`;
                            content += `<div class="tool-name-inline">🔧 ${msg.content.tool}</div>`;
                            if (msg.content.tool_args) {
                                content += `<pre class="tool-args-inline">${JSON.stringify(msg.content.tool_args, null, 2)}</pre>`;
                            }
                            content += `</div>`;
                        }
                        if (msg.content.plan) {
                            content += `<details class="plan-details">`;
                            content += `<summary>📋 Execution Plan</summary>`;
                            content += `<pre class="plan-content">${JSON.stringify(msg.content.plan, null, 2)}</pre>`;
                            content += `</details>`;
                        }
                        if (msg.content.answer) {
                            content += `<div class="final-answer">`;
                            content += `<span class="answer-label">✅ Final Answer:</span>`;
                            content += `<div class="answer-text">${this.escapeHtml(msg.content.answer)}</div>`;
                            content += `</div>`;
                        }
                        content += `</div>`;
                    } else {
                        content = `<pre class="json-content">${JSON.stringify(msg.content, null, 2)}</pre>`;
                    }
                } else {
                    content = `<div class="message-text">${this.escapeHtml(msg.content)}</div>`;
                }
            } else if (msg.role === 'system') {
                messageClass = 'system-message';
                roleLabel = 'System';
                if (msg.content?.type === 'stats') {
                    const stats = msg.content.performance_stats;
                    content = `
                        <div class="stats-badge">
                            <div class="stats-header">📊 Performance Metrics (Step ${msg.content.step})</div>
                            <div class="stats-grid">
                                <div class="stat-item">
                                    <span class="stat-label">Input</span>
                                    <span class="stat-value">${stats.input_tokens.toLocaleString()}</span>
                                </div>
                                <div class="stat-item">
                                    <span class="stat-label">Output</span>
                                    <span class="stat-value">${stats.output_tokens.toLocaleString()}</span>
                                </div>
                                <div class="stat-item">
                                    <span class="stat-label">TTFT</span>
                                    <span class="stat-value">${stats.time_to_first_token.toFixed(2)}s</span>
                                </div>
                                <div class="stat-item">
                                    <span class="stat-label">Speed</span>
                                    <span class="stat-value">${stats.tokens_per_second.toFixed(0)} t/s</span>
                                </div>
                            </div>
                        </div>
                    `;
                } else if (msg.content?.issues) {
                    const issues = msg.content.issues || [];
                    content = `
                        <div class="tool-result">
                            <div class="result-header">✅ Jira Search Results (${msg.content.total} found)</div>
                            ${issues.length > 0 ? `
                                <div class="issues-list">
                                    ${issues.map(issue => `
                                        <div class="issue-item">
                                            <span class="issue-key">${issue.key}</span>
                                            <span class="issue-summary">${this.escapeHtml(issue.summary)}</span>
                                            <span class="issue-status ${issue.status.toLowerCase().replace(' ', '-')}">${issue.status}</span>
                                        </div>
                                    `).join('')}
                                </div>
                            ` : '<div class="no-results">No issues found</div>'}
                        </div>
                    `;
                } else if (msg.content?.status === 'success') {
                    content = `
                        <div class="tool-result success">
                            <div class="result-header">✅ Tool Execution Success</div>
                            <pre class="result-data">${JSON.stringify(msg.content, null, 2)}</pre>
                        </div>
                    `;
                } else {
                    content = `<pre class="json-content">${JSON.stringify(msg.content, null, 2)}</pre>`;
                }
            }
            
            flowHtml += `
                <div class="conversation-message ${messageClass}" data-index="${index}">
                    <div class="message-header">
                        <span class="message-role">${roleLabel}</span>
                        <span class="message-number">#${index + 1}</span>
                    </div>
                    <div class="message-body">
                        ${content}
                    </div>
                </div>
            `;
        });
        
        return `
            <div class="section">
                <h4>💬 Conversation Flow</h4>
                <div class="conversation-flow">
                    ${flowHtml}
                </div>
            </div>
        `;
    }

    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    generatePerformanceMetricsSection(performanceStats, totalInputTokens, totalOutputTokens, avgTokensPerSecond, avgTimeToFirstToken) {
        if (performanceStats.length === 0) {
            return `
                <div class="section">
                    <h4>⚡ Performance Metrics</h4>
                    <p style="color: #6c757d; font-style: italic;">No performance statistics available</p>
                </div>
            `;
        }

        // Calculate min, max, and averages
        const inputTokensList = performanceStats.map(s => s.input_tokens);
        const outputTokensList = performanceStats.map(s => s.output_tokens);
        const ttftList = performanceStats.map(s => s.time_to_first_token);
        const speedList = performanceStats.map(s => s.tokens_per_second);
        
        const stats = {
            input: {
                min: Math.min(...inputTokensList).toLocaleString(),
                max: Math.max(...inputTokensList).toLocaleString(),
                avg: Math.round(totalInputTokens / performanceStats.length).toLocaleString()
            },
            output: {
                min: Math.min(...outputTokensList).toLocaleString(),
                max: Math.max(...outputTokensList).toLocaleString(),
                avg: Math.round(totalOutputTokens / performanceStats.length).toLocaleString()
            },
            ttft: {
                min: Math.min(...ttftList).toFixed(3),
                max: Math.max(...ttftList).toFixed(3),
                avg: avgTimeToFirstToken.toFixed(3)
            },
            speed: {
                min: Math.min(...speedList).toFixed(1),
                max: Math.max(...speedList).toFixed(1),
                avg: avgTokensPerSecond.toFixed(1)
            }
        };

        const totalTokens = totalInputTokens + totalOutputTokens;
        const inputPercentage = totalTokens > 0 ? (totalInputTokens / totalTokens * 100).toFixed(1) : 0;
        const outputPercentage = totalTokens > 0 ? (totalOutputTokens / totalTokens * 100).toFixed(1) : 0;

        let stepsTableHtml = '';
        performanceStats.forEach((stats, index) => {
            stepsTableHtml += `
                <tr>
                    <td class="step-number">${index + 1}</td>
                    <td class="tokens-in">${stats.input_tokens.toLocaleString()}</td>
                    <td class="tokens-out">${stats.output_tokens.toLocaleString()}</td>
                    <td class="ttft">${stats.time_to_first_token.toFixed(2)}s</td>
                    <td class="speed">${stats.tokens_per_second.toFixed(0)} t/s</td>
                </tr>
            `;
        });

        return `
            <div class="section">
                <h4>⚡ Performance Metrics</h4>
                <div class="performance-summary">
                    <div class="token-overview">
                        <h5>Token Summary</h5>
                        <div class="metrics-grid">
                            <div class="metric">
                                <span class="metric-label">Total Tokens</span>
                                <span class="metric-value">${totalTokens.toLocaleString()}</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">Total Input</span>
                                <span class="metric-value">${totalInputTokens.toLocaleString()} (${inputPercentage}%)</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">Total Output</span>
                                <span class="metric-value">${totalOutputTokens.toLocaleString()} (${outputPercentage}%)</span>
                            </div>
                        </div>
                    </div>
                    
                    <div class="detailed-stats">
                        <h5>Detailed Statistics (Min / Avg / Max)</h5>
                        <table class="stats-summary-table">
                            <thead>
                                <tr>
                                    <th>Metric</th>
                                    <th>Min</th>
                                    <th>Average</th>
                                    <th>Max</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr class="metric-tokens">
                                    <td><strong>Input Tokens</strong></td>
                                    <td class="stat-min">${stats.input.min}</td>
                                    <td class="stat-avg">${stats.input.avg}</td>
                                    <td class="stat-max">${stats.input.max}</td>
                                </tr>
                                <tr class="metric-tokens">
                                    <td><strong>Output Tokens</strong></td>
                                    <td class="stat-min">${stats.output.min}</td>
                                    <td class="stat-avg">${stats.output.avg}</td>
                                    <td class="stat-max">${stats.output.max}</td>
                                </tr>
                                <tr class="metric-ttft">
                                    <td><strong>Time to First Token</strong></td>
                                    <td class="stat-min">${stats.ttft.min}s</td>
                                    <td class="stat-avg">${stats.ttft.avg}s</td>
                                    <td class="stat-max">${stats.ttft.max}s</td>
                                </tr>
                                <tr class="metric-speed">
                                    <td><strong>Tokens/Second</strong></td>
                                    <td class="stat-min">${stats.speed.min} t/s</td>
                                    <td class="stat-avg">${stats.speed.avg} t/s</td>
                                    <td class="stat-max">${stats.speed.max} t/s</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
                    <div class="steps-table-container">
                        <h5>Step-by-Step Breakdown</h5>
                        <table class="steps-table">
                            <thead>
                                <tr>
                                    <th>Step</th>
                                    <th>Input</th>
                                    <th>Output</th>
                                    <th>TTFT</th>
                                    <th>Speed</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${stepsTableHtml}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        `;
    }

    generateToolExecutionSection(toolCalls) {
        if (toolCalls.length === 0) {
            return `
                <div class="section">
                    <h4>🔧 Tool Executions</h4>
                    <p style="color: #6c757d; font-style: italic;">No tool calls were made during this conversation.</p>
                </div>
            `;
        }

        let toolsHtml = '';
        toolCalls.forEach((toolCall, index) => {
            toolsHtml += `
                <div class="tool-call">
                    <div class="tool-header">
                        <span class="tool-name">🔧 ${toolCall.tool}</span>
                        <span class="tool-index">#${index + 1}</span>
                    </div>
                    <div class="tool-details">
                        <div class="tool-thought"><strong>Thought:</strong> ${toolCall.thought}</div>
                        <div class="tool-goal"><strong>Goal:</strong> ${toolCall.goal}</div>
                        <div class="tool-args"><strong>Arguments:</strong> <code>${JSON.stringify(toolCall.args, null, 2)}</code></div>
                    </div>
                </div>
            `;
        });

        return `
            <div class="section">
                <h4>🔧 Tool Executions (${toolCalls.length})</h4>
                <div class="tool-executions">
                    ${toolsHtml}
                </div>
            </div>
        `;
    }

    generateSystemPromptSection(systemPrompt, systemPromptTokens) {
        if (!systemPrompt) {
            return '';
        }

        // Estimate token count if not provided (rough approximation: ~4 chars per token)
        const estimatedTokens = Math.round(systemPrompt.length / 4);
        const tokenCount = systemPromptTokens || estimatedTokens;
        
        // Note about token counting for local models
        const tokenNote = systemPromptTokens ? '' : 
            '<div class="token-note">Note: System prompt tokens are included in the total input but may not be reflected in per-step metrics for local models.</div>';

        return `
            <div class="section">
                <h4>📋 System Prompt</h4>
                <div class="system-prompt-info">
                    <span class="prompt-tokens">Estimated Token Count: ~${tokenCount.toLocaleString()}</span>
                    <span class="prompt-chars">(${systemPrompt.length.toLocaleString()} characters)</span>
                </div>
                ${tokenNote}
                <pre class="system-prompt">${this.escapeHtml(systemPrompt)}</pre>
            </div>
        `;
    }
}

// Initialize the application when the page loads
document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM Content Loaded, initializing EvaluationVisualizer');
    try {
        new EvaluationVisualizer();
    } catch (error) {
        console.error('Error initializing EvaluationVisualizer:', error);
    }
});