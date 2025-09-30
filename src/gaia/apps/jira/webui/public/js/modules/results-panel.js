// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

export class ResultsPanel {
    constructor() {
        this.currentResults = null;
        this.activeTab = 'overview';

        // Get tab content elements
        this.overviewContent = document.getElementById('results-content');
        this.detailsContent = document.getElementById('results-details');
        this.rawContent = document.getElementById('results-raw');

        this.initializeTabs();
    }

    initializeTabs() {
        // Set up tab switching
        const tabs = document.querySelectorAll('.results-tab');
        tabs.forEach(tab => {
            tab.addEventListener('click', (e) => {
                const tabName = e.target.dataset.tab;
                this.switchTab(tabName);
            });
        });
    }

    switchTab(tabName) {
        // Update active tab button
        const tabs = document.querySelectorAll('.results-tab');
        tabs.forEach(tab => {
            tab.classList.remove('active');
            if (tab.dataset.tab === tabName) {
                tab.classList.add('active');
            }
        });

        // Update active tab content
        const contents = document.querySelectorAll('.results-tab-content');
        contents.forEach(content => {
            content.classList.remove('active');
        });

        const activeContent = document.getElementById(`results-${tabName === 'overview' ? 'content' : tabName}`);
        if (activeContent) {
            activeContent.classList.add('active');
        }

        this.activeTab = tabName;

        // Refresh content if we have results
        if (this.currentResults) {
            this.renderTabContent(tabName, this.currentResults);
        }
    }

    show(data) {
        console.log('ResultsPanel.show called with:', data);

        // Store the current results including raw data
        this.currentResults = { ...data };

        // Add a timestamp to force refresh
        const timestamp = new Date().getTime();
        console.log(`Rendering ${data.type} at ${timestamp}`);

        // Render content for current tab
        this.renderTabContent(this.activeTab, data);
    }

    renderTabContent(tabName, data) {
        switch (tabName) {
            case 'overview':
                this.renderOverview(data);
                break;
            case 'details':
                this.renderDetails(data);
                break;
            case 'raw':
                this.renderRawData(data);
                break;
        }
    }

    renderOverview(data) {
        // Clear previous content
        if (this.overviewContent) {
            this.overviewContent.innerHTML = '';
        }

        if (!data) {
            this.overviewContent.innerHTML = '<div class="no-results">No results to display</div>';
            return;
        }

        if (data.type === 'issues') {
            this.renderIssuesOverview(data.items || []);
        } else if (data.type === 'projects') {
            this.renderProjectsOverview(data.items || []);
        } else {
            this.renderGenericOverview(data);
        }
    }

    renderIssuesOverview(issues) {
        const container = document.createElement('div');
        container.className = 'issues-overview';

        if (!issues || issues.length === 0) {
            container.innerHTML = `
                <div class="results-summary">
                    <h3>📋 No Issues Found</h3>
                    <p class="no-results-message">No issues match your search criteria.</p>
                </div>
            `;
        } else {
            // Show summary and first 6 issues
            container.innerHTML = `
                <div class="results-summary">
                    <h3>📋 Issues Found</h3>
                    <div class="summary-stats">
                        <div class="stat">
                            <span class="stat-number">${issues.length}</span>
                            <span class="stat-label">Total Issues</span>
                        </div>
                    </div>
                </div>
            `;

            const issuesList = document.createElement('div');
            issuesList.className = 'issues-list';

            issues.slice(0, 6).forEach(issue => {
                const card = this.createIssueCard(issue);
                issuesList.appendChild(card);
            });

            container.appendChild(issuesList);

            if (issues.length > 6) {
                const showMore = document.createElement('div');
                showMore.className = 'show-more';
                showMore.textContent = `Showing 6 of ${issues.length} issues. Switch to Details tab for the complete list.`;
                container.appendChild(showMore);
            }
        }

        this.overviewContent.appendChild(container);
    }

    renderProjectsOverview(projects) {
        const container = document.createElement('div');
        container.className = 'projects-overview';

        container.innerHTML = `
            <div class="results-summary">
                <h3>📁 Projects</h3>
                <div class="summary-stats">
                    <div class="stat">
                        <span class="stat-number">${projects.length}</span>
                        <span class="stat-label">Total Projects</span>
                    </div>
                </div>
            </div>
        `;

        const projectsList = document.createElement('div');
        projectsList.className = 'projects-list';

        projects.forEach(project => {
            const item = this.createProjectCard(project);
            projectsList.appendChild(item);
        });

        container.appendChild(projectsList);
        this.overviewContent.appendChild(container);
    }

    renderGenericOverview(data) {
        const container = document.createElement('div');
        container.className = 'generic-overview';
        container.innerHTML = `
            <div class="results-summary">
                <h3>📄 Response</h3>
                <p>Response received. Check the Raw Data tab for complete details.</p>
            </div>
        `;
        this.overviewContent.appendChild(container);
    }

    renderDetails(data) {
        // Clear previous content
        if (this.detailsContent) {
            this.detailsContent.innerHTML = '';
        }

        if (!data) {
            this.detailsContent.innerHTML = '<div class="no-results">No details to display</div>';
            return;
        }

        if (data.type === 'issues') {
            this.renderIssuesDetails(data.items || []);
        } else if (data.type === 'projects') {
            this.renderProjectsDetails(data.items || []);
        } else {
            this.detailsContent.innerHTML = '<p class="no-details">No detailed view available for this result type.</p>';
        }
    }

    renderIssuesDetails(issues) {
        const container = document.createElement('div');
        container.className = 'issues-details';

        if (!issues || issues.length === 0) {
            container.innerHTML = `
                <div class="details-header">
                    <h3>📋 No Issues Found</h3>
                    <p class="no-results-message">No issues match your search criteria.</p>
                </div>
            `;
        } else {
            container.innerHTML = `
                <div class="details-header">
                    <h3>📋 All Issues (${issues.length})</h3>
                </div>
            `;

            const issuesList = document.createElement('div');
            issuesList.className = 'issues-list detailed';

            issues.forEach(issue => {
                const card = this.createDetailedIssueCard(issue);
                issuesList.appendChild(card);
            });

            container.appendChild(issuesList);
        }

        this.detailsContent.appendChild(container);
    }

    renderProjectsDetails(projects) {
        const container = document.createElement('div');
        container.className = 'projects-details';

        container.innerHTML = `
            <div class="details-header">
                <h3>📁 All Projects (${projects.length})</h3>
            </div>
        `;

        if (projects.length > 0) {
            const projectsList = document.createElement('div');
            projectsList.className = 'projects-list detailed';

            projects.forEach(project => {
                const card = this.createDetailedProjectCard(project);
                projectsList.appendChild(card);
            });

            container.appendChild(projectsList);
        }

        this.detailsContent.appendChild(container);
    }

    renderRawData(data) {
        // Clear previous content
        if (this.rawContent) {
            this.rawContent.innerHTML = '';
        }

        const container = document.createElement('div');
        container.className = 'raw-data-container';
        container.innerHTML = `
            <h3>🔍 Raw Response Data</h3>
            <pre class="raw-data-content">${JSON.stringify(data || {}, null, 2)}</pre>
        `;

        this.rawContent.appendChild(container);
    }

    createIssueCard(issue) {
        const card = document.createElement('div');
        card.className = 'issue-card';

        const priorityClass = this.getPriorityClass(issue.priority);
        const statusClass = this.getStatusClass(issue.status);
        const priorityIcon = this.getPriorityIcon(issue.priority);

        card.innerHTML = `
            <div class="issue-header">
                <span class="issue-key">${issue.key}</span>
                <span class="issue-priority ${priorityClass}">${priorityIcon} ${issue.priority || 'Unknown'}</span>
            </div>
            <div class="issue-title">${this.escapeHtml(issue.summary || 'No summary')}</div>
            <div class="issue-meta">
                <span class="issue-assignee">👤 ${issue.assignee || 'Unassigned'}</span>
                <span class="issue-status status-badge ${statusClass}">${issue.status || 'Unknown'}</span>
            </div>
        `;

        return card;
    }

    createProjectCard(project) {
        const item = document.createElement('div');
        item.className = 'project-item';

        const statusIcon = project.status === 'Active' ? '🟢' : '🟡';

        item.innerHTML = `
            <div class="project-header">
                <span class="project-key">${project.key || 'N/A'}</span>
                <span class="project-status">${statusIcon} ${project.status || 'Unknown'}</span>
            </div>
            <h4>${this.escapeHtml(project.name || 'Unnamed')}</h4>
            <p>${this.escapeHtml(project.description || 'No description')}</p>
            <small>Lead: ${project.lead || 'Unknown'}</small>
        `;

        return item;
    }

    createDetailedIssueCard(issue) {
        const card = document.createElement('div');
        card.className = 'detailed-issue-card';

        const priorityClass = this.getPriorityClass(issue.priority);
        const statusClass = this.getStatusClass(issue.status);
        const priorityIcon = this.getPriorityIcon(issue.priority);

        card.innerHTML = `
            <div class="issue-main">
                <div class="issue-header">
                    <span class="issue-key">${issue.key || 'N/A'}</span>
                    <span class="issue-priority ${priorityClass}">${priorityIcon} ${issue.priority || 'Unknown'}</span>
                    <span class="issue-status status-badge ${statusClass}">${issue.status || 'Unknown'}</span>
                </div>
                <div class="issue-title">${this.escapeHtml(issue.summary || 'No summary')}</div>
                <div class="issue-description">${this.escapeHtml(issue.description || 'No description available')}</div>
            </div>
            <div class="issue-sidebar">
                <div class="issue-field">
                    <label>Assignee:</label>
                    <span>👤 ${issue.assignee || 'Unassigned'}</span>
                </div>
                <div class="issue-field">
                    <label>Type:</label>
                    <span>${issue.issueType || issue.type || 'Unknown'}</span>
                </div>
                <div class="issue-field">
                    <label>Reporter:</label>
                    <span>${issue.reporter || 'Unknown'}</span>
                </div>
            </div>
        `;

        return card;
    }

    createDetailedProjectCard(project) {
        const card = document.createElement('div');
        card.className = 'detailed-project-card';

        const statusIcon = project.status === 'Active' ? '🟢' : '🟡';

        card.innerHTML = `
            <div class="project-main">
                <div class="project-header">
                    <span class="project-key">${project.key || 'N/A'}</span>
                    <span class="project-status">${statusIcon} ${project.status || 'Unknown'}</span>
                </div>
                <div class="project-title">${this.escapeHtml(project.name || 'Unnamed')}</div>
                <div class="project-description">${this.escapeHtml(project.description || 'No description available')}</div>
            </div>
            <div class="project-sidebar">
                <div class="project-field">
                    <label>Lead:</label>
                    <span>👤 ${project.lead || 'Unknown'}</span>
                </div>
                <div class="project-field">
                    <label>Type:</label>
                    <span>${project.projectTypeKey || 'Unknown'}</span>
                </div>
            </div>
        `;

        return card;
    }

    getPriorityClass(priority) {
        switch (priority) {
            case 'High':
            case 'Highest':
                return 'priority-high';
            case 'Medium':
                return 'priority-medium';
            case 'Low':
            case 'Lowest':
                return 'priority-low';
            default:
                return '';
        }
    }

    getPriorityIcon(priority) {
        switch (priority) {
            case 'High':
            case 'Highest':
                return '🔴';
            case 'Medium':
                return '🟠';
            case 'Low':
            case 'Lowest':
                return '🟢';
            default:
                return '⚪';
        }
    }

    getStatusClass(status) {
        switch (status) {
            case 'To Do':
            case 'Open':
                return 'status-todo';
            case 'In Progress':
            case 'In Review':
                return 'status-progress';
            case 'Done':
            case 'Closed':
                return 'status-done';
            default:
                return 'status-todo';
        }
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    hide() {
        // Not needed anymore as results are always visible in middle column
    }
}