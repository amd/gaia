// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Results Panel Component  
// Dynamic results display with smart rendering

import DomHelpers from '../services/dom-helpers.js';
import resultParser from '../services/result-parser.js';

class ResultsPanel {
  constructor(containerId) {
    this.containerId = containerId;
    this.currentResults = null;
    this.activeTab = 'overview';
    
    this.initializeComponent();
    this.setupEventListeners();
    this.showWelcome();
  }

  initializeComponent() {
    const container = DomHelpers.getElementById(this.containerId);
    if (!container) {
      console.error(`Results container not found: ${this.containerId}`);
      return;
    }

    container.innerHTML = `
      <div class="results-header">
        <h2>📊 Results</h2>
        <div class="results-tabs">
          <button class="results-tab active" data-tab="overview">Overview</button>
          <button class="results-tab" data-tab="details">Details</button>
          <button class="results-tab" data-tab="raw">Raw Data</button>
        </div>
      </div>
      
      <div class="results-content">
        <div id="results-overview" class="results-tab-content active">
          <!-- Overview content -->
        </div>
        <div id="results-details" class="results-tab-content">
          <!-- Details content -->
        </div>
        <div id="results-raw" class="results-tab-content">
          <!-- Raw data content -->
        </div>
      </div>
    `;
  }

  setupEventListeners() {
    // Tab switching
    const tabs = DomHelpers.querySelectorAll('.results-tab');
    tabs.forEach(tab => {
      DomHelpers.addEventListener(tab, 'click', (e) => {
        const tabName = e.target.dataset.tab;
        this.switchTab(tabName);
      });
    });
  }

  switchTab(tabName) {
    // Update active tab button
    const tabs = DomHelpers.querySelectorAll('.results-tab');
    tabs.forEach(tab => {
      DomHelpers.removeClass(tab, 'active');
      if (tab.dataset.tab === tabName) {
        DomHelpers.addClass(tab, 'active');
      }
    });

    // Update active tab content
    const contents = DomHelpers.querySelectorAll('.results-tab-content');
    contents.forEach(content => {
      DomHelpers.removeClass(content, 'active');
    });
    
    const activeContent = DomHelpers.getElementById(`results-${tabName}`);
    if (activeContent) {
      DomHelpers.addClass(activeContent, 'active');
    }

    this.activeTab = tabName;
    
    // Refresh content if we have results
    if (this.currentResults) {
      this.renderTabContent(tabName, this.currentResults);
    }
  }

  showWelcome() {
    const overview = DomHelpers.getElementById('results-overview');
    DomHelpers.setHTML(overview, `
      <div class="welcome-message">
        <div class="welcome-icon">👋</div>
        <h3>Welcome to JAX</h3>
        <p>Start a conversation with the AI assistant to see your JIRA data here.</p>
        <div class="welcome-suggestions">
          <h4>Try asking:</h4>
          <ul>
            <li>"Show me my assigned issues"</li>
            <li>"What projects are available?"</li>
            <li>"Find high priority bugs"</li>
            <li>"Create a new task for user testing"</li>
          </ul>
        </div>
      </div>
    `);
  }

  updateResults(parsedResult, query) {
    console.log('📊 Results Panel - updateResults called with:', parsedResult);
    this.currentResults = { ...parsedResult, query };
    this.renderCurrentTab();
  }

  renderCurrentTab() {
    if (!this.currentResults) return;
    this.renderTabContent(this.activeTab, this.currentResults);
  }

  renderTabContent(tabName, results) {
    const content = DomHelpers.getElementById(`results-${tabName}`);
    if (!content) return;

    switch (tabName) {
      case 'overview':
        this.renderOverview(content, results);
        break;
      case 'details':
        this.renderDetails(content, results);
        break;
      case 'raw':
        this.renderRawData(content, results);
        break;
    }
  }

  renderOverview(container, results) {
    let html = '';
    
    switch (results.type) {
      case resultParser.resultTypes.ISSUES:
        html = this.renderIssuesOverview(results);
        break;
      case resultParser.resultTypes.PROJECTS:
        html = this.renderProjectsOverview(results);
        break;
      case resultParser.resultTypes.CREATED_ISSUE:
        html = this.renderCreatedIssueOverview(results);
        break;
      case resultParser.resultTypes.CHAT_RESPONSE:
        html = this.renderChatResponseOverview(results);
        break;
      case resultParser.resultTypes.ERROR:
        html = this.renderErrorOverview(results);
        break;
      default:
        html = this.renderUnknownOverview(results);
    }

    DomHelpers.setHTML(container, html);
  }

  renderIssuesOverview(results) {
    const { issues = [], total = 0, jql, query } = results;
    
    // If no issues found, show simple message
    if (!issues || issues.length === 0) {
      return `
        <div class="results-summary">
          <h3>📋 No Issues Found</h3>
          ${query ? `<p class="query-info">Query: "${query}"</p>` : ''}
          <p class="no-results-message">No issues match your search criteria.</p>
        </div>
      `;
    }
    
    let html = `
      <div class="results-summary">
        <h3>📋 Issues Found</h3>
        <div class="summary-stats">
          <div class="stat">
            <span class="stat-number">${total}</span>
            <span class="stat-label">Total Issues</span>
          </div>
        </div>
        ${query ? `<p class="query-info">Query: "${query}"</p>` : ''}
        ${jql ? `<p class="jql-info">JQL: <code>${jql}</code></p>` : ''}
      </div>
      
      <div class="issues-grid">
        ${issues.slice(0, 6).map(issue => this.createIssueCard(issue)).join('')}
      </div>
      
      ${issues.length > 6 ? `<div class="show-more">Showing 6 of ${total} issues. Switch to Details tab for the complete list.</div>` : ''}
    `;
    
    return html;
  }

  renderProjectsOverview(results) {
    const { projects = [], total = 0, query } = results;
    
    let html = `
      <div class="results-summary">
        <h3>📁 Projects</h3>
        <div class="summary-stats">
          <div class="stat">
            <span class="stat-number">${total}</span>
            <span class="stat-label">Total Projects</span>
          </div>
        </div>
        ${query ? `<p class="query-info">Query: "${query}"</p>` : ''}
      </div>
    `;
    
    if (projects && projects.length > 0) {
      html += `
        <div class="projects-grid">
          ${projects.map(project => this.createProjectCard(project)).join('')}
        </div>
      `;
    }
    
    return html;
  }

  renderCreatedIssueOverview(results) {
    const { issue, query } = results;
    
    return `
      <div class="results-summary success">
        <h3>✅ Issue Created Successfully</h3>
        ${query ? `<p class="query-info">Request: "${query}"</p>` : ''}
      </div>
      
      <div class="created-issue-card">
        <div class="issue-key">${issue.key}</div>
        ${issue.url ? `<a href="#" onclick="window.electronAPI.openExternalLink('${issue.url}')" class="issue-link">🔗 Open in JIRA</a>` : ''}
        <div class="issue-details">
          ${Object.entries(issue).filter(([key]) => !['key', 'url'].includes(key)).map(([key, value]) => 
            `<div class="detail-row">
              <span class="detail-label">${key}:</span>
              <span class="detail-value">${value}</span>
            </div>`
          ).join('')}
        </div>
      </div>
    `;
  }

  renderChatResponseOverview(results) {
    return `
      <div class="results-summary">
        <h3>💬 AI Response</h3>
        ${results.query ? `<p class="query-info">Query: "${results.query}"</p>` : ''}
      </div>
      
      <div class="chat-response-card">
        <div class="response-content">${this.formatMessageContent(results.message)}</div>
      </div>
    `;
  }

  renderErrorOverview(results) {
    return `
      <div class="results-summary error">
        <h3>❌ Error</h3>
        ${results.query ? `<p class="query-info">Query: "${results.query}"</p>` : ''}
      </div>
      
      <div class="error-card">
        <div class="error-message">${DomHelpers.escapeHtml(results.error)}</div>
      </div>
    `;
  }

  renderUnknownOverview(results) {
    return `
      <div class="results-summary">
        <h3>📄 Response</h3>
        ${results.query ? `<p class="query-info">Query: "${results.query}"</p>` : ''}
      </div>
      
      <div class="unknown-response-card">
        <p>Response received. Check the Raw Data tab for complete details.</p>
      </div>
    `;
  }

  renderDetails(container, results) {
    let html = '';

    switch (results.type) {
      case resultParser.resultTypes.ISSUES:
        const issues = results.issues || [];
        const total = results.total || 0;
        
        // Handle case where no issues found
        if (issues.length === 0) {
          html = `
            <div class="details-header">
              <h3>📋 No Issues Found</h3>
              <p class="no-results-message">No issues match your search criteria.</p>
            </div>
          `;
        } else {
          html = `
            <div class="details-header">
              <h3>📋 All Issues (${total})</h3>
              ${results.jql ? `<p class="jql-info">JQL: <code>${results.jql}</code></p>` : ''}
            </div>
            <div class="issues-list">
              ${issues.map(issue => this.createDetailedIssueCard(issue)).join('')}
            </div>
          `;
        }
        break;
      case resultParser.resultTypes.PROJECTS:
        const projects = results.projects || [];
        const projectTotal = results.total || 0;
        
        html = `
          <div class="details-header">
            <h3>📁 All Projects (${projectTotal})</h3>
          </div>
        `;
        
        if (projects.length > 0) {
          html += `
            <div class="projects-list">
              ${projects.map(project => this.createDetailedProjectCard(project)).join('')}
            </div>
          `;
        }
        break;
      default:
        html = '<p class="no-details">No detailed view available for this result type.</p>';
    }

    // Add performance stats if available
    if (results.performanceStats && results.performanceStats.length > 0) {
      html += this.renderPerformanceStats(results.performanceStats);
    }

    DomHelpers.setHTML(container, html);
  }

  renderRawData(container, results) {
    // Use rawData if available (original API response), otherwise show the parsed results
    const dataToShow = results.rawData || results;
    
    const html = `
      <div class="raw-data-container">
        <h3>🔍 Raw Response Data</h3>
        <pre class="raw-data-content">${JSON.stringify(dataToShow, null, 2)}</pre>
      </div>
    `;
    
    DomHelpers.setHTML(container, html);
  }

  createIssueCard(issue) {
    const priorityClass = this.getPriorityClass(issue.priority);
    const statusClass = this.getStatusClass(issue.status);
    const priorityIcon = this.getPriorityIcon(issue.priority);
    
    return `
      <div class="issue-card">
        <div class="issue-header">
          <div class="issue-key">${issue.key || 'N/A'}</div>
          <div class="issue-priority ${priorityClass}">${priorityIcon} ${issue.priority || 'Unknown'}</div>
        </div>
        <div class="issue-title">${DomHelpers.escapeHtml(issue.summary || 'No summary')}</div>
        <div class="issue-meta">
          <span class="issue-assignee">👤 ${issue.assignee || 'Unassigned'}</span>
          <span class="issue-status status-badge ${statusClass}">${issue.status || 'Unknown'}</span>
        </div>
      </div>
    `;
  }

  createProjectCard(project) {
    const statusIcon = project.status === 'Active' ? '🟢' : '🟡';
    
    return `
      <div class="project-card">
        <div class="project-header">
          <div class="project-key">${project.key || 'N/A'}</div>
          <div class="project-status">${statusIcon} ${project.status || 'Unknown'}</div>
        </div>
        <div class="project-title">${DomHelpers.escapeHtml(project.name || 'Unnamed')}</div>
        <div class="project-meta">
          <span class="project-lead">👤 ${project.lead || 'Unknown'}</span>
          <span class="project-issues">📋 ${project.issueCount || 0} issues</span>
        </div>
      </div>
    `;
  }

  createDetailedIssueCard(issue) {
    const priorityClass = this.getPriorityClass(issue.priority);
    const statusClass = this.getStatusClass(issue.status);
    const priorityIcon = this.getPriorityIcon(issue.priority);
    
    return `
      <div class="detailed-issue-card">
        <div class="issue-main">
          <div class="issue-header">
            <div class="issue-key">${issue.key || 'N/A'}</div>
            <div class="issue-priority ${priorityClass}">${priorityIcon} ${issue.priority || 'Unknown'}</div>
            <span class="issue-status status-badge ${statusClass}">${issue.status || 'Unknown'}</span>
          </div>
          <div class="issue-title">${DomHelpers.escapeHtml(issue.summary || 'No summary')}</div>
          <div class="issue-description">${DomHelpers.escapeHtml(issue.description || 'No description available')}</div>
        </div>
        <div class="issue-sidebar">
          <div class="issue-field">
            <label>Assignee:</label>
            <span>👤 ${issue.assignee || 'Unassigned'}</span>
          </div>
          <div class="issue-field">
            <label>Type:</label>
            <span>${issue.issueType || 'Unknown'}</span>
          </div>
          <div class="issue-field">
            <label>Reporter:</label>
            <span>${issue.reporter || 'Unknown'}</span>
          </div>
        </div>
      </div>
    `;
  }

  createDetailedProjectCard(project) {
    const statusIcon = project.status === 'Active' ? '🟢' : '🟡';
    
    return `
      <div class="detailed-project-card">
        <div class="project-main">
          <div class="project-header">
            <div class="project-key">${project.key || 'N/A'}</div>
            <div class="project-status">${statusIcon} ${project.status || 'Unknown'}</div>
          </div>
          <div class="project-title">${DomHelpers.escapeHtml(project.name || 'Unnamed')}</div>
          <div class="project-description">${DomHelpers.escapeHtml(project.description || 'No description available')}</div>
        </div>
        <div class="project-sidebar">
          <div class="project-field">
            <label>Lead:</label>
            <span>👤 ${project.lead || 'Unknown'}</span>
          </div>
          <div class="project-field">
            <label>Issues:</label>
            <span>📋 ${project.issueCount || 0}</span>
          </div>
        </div>
      </div>
    `;
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

  renderPerformanceStats(performanceStats) {
    if (!performanceStats || performanceStats.length === 0) {
      return '';
    }
    
    let html = `
      <div class="performance-stats-container">
        <h3>⚡ Performance Metrics</h3>
        <div class="stats-grid">
    `;
    
    performanceStats.forEach(stat => {
      const { step, stats } = stat;
      html += `
        <div class="stat-card">
          <h4>Step ${step}</h4>
          <div class="stat-details">
            <div class="stat-row">
              <span class="stat-label">Input Tokens:</span>
              <span class="stat-value">${stats.input_tokens || 'N/A'}</span>
            </div>
            <div class="stat-row">
              <span class="stat-label">Output Tokens:</span>
              <span class="stat-value">${stats.output_tokens || 'N/A'}</span>
            </div>
            <div class="stat-row">
              <span class="stat-label">Time to First Token:</span>
              <span class="stat-value">${stats.time_to_first_token ? stats.time_to_first_token.toFixed(3) + 's' : 'N/A'}</span>
            </div>
            <div class="stat-row">
              <span class="stat-label">Tokens/Second:</span>
              <span class="stat-value">${stats.tokens_per_second ? stats.tokens_per_second.toFixed(2) : 'N/A'}</span>
            </div>
          </div>
        </div>
      `;
    });
    
    html += `
        </div>
      </div>
    `;
    
    return html;
  }

  formatMessageContent(content) {
    // Convert newlines to <br> and preserve formatting
    return DomHelpers.escapeHtml(content)
      .replace(/\n/g, '<br>')
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.*?)\*/g, '<em>$1</em>');
  }

  clearResults() {
    this.currentResults = null;
    this.showWelcome();
    
    const details = DomHelpers.getElementById('results-details');
    const raw = DomHelpers.getElementById('results-raw');
    
    DomHelpers.clearContent(details);
    DomHelpers.clearContent(raw);
  }
}

// Export for use in main app
window.ResultsPanel = ResultsPanel;
export default ResultsPanel;