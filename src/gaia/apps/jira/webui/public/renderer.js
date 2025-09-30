// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// JAX Web Application
class JaxWebUIRenderer {
  constructor() {
    this.currentSection = 'webui';
    this.systemStatus = {
      gaiaReady: false,
      mcpReady: false
    };

    // Use environment variable or fallback
    this.mcpBaseUrl = window.ENV?.GAIA_MCP_URL || 'http://localhost:8765';

    this.initializeEventListeners();
    this.checkSystemStatus();
  }

  initializeEventListeners() {
    // Navigation
    document.querySelectorAll('.nav-item').forEach(item => {
      item.addEventListener('click', (e) => {
        const section = e.target.closest('.nav-item').dataset.section;
        this.switchToSection(section);
      });
    });

    // Search input enter key
    const searchInput = document.getElementById('search-input');
    if (searchInput) {
      searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
          this.performSearch();
        }
      });
    }

    // Chat input enter key
    const chatInput = document.getElementById('chat-input');
    if (chatInput) {
      chatInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          this.sendChatMessage();
        }
      });
    }

    // Auto-refresh system status
    setInterval(() => {
      this.checkSystemStatus();
    }, 30000); // Check every 30 seconds
  }

  switchToSection(sectionName) {
    // Update navigation
    document.querySelectorAll('.nav-item').forEach(item => {
      item.classList.remove('active');
    });
    document.querySelector(`[data-section="${sectionName}"]`).classList.add('active');

    // Update content
    document.querySelectorAll('.content-section').forEach(section => {
      section.classList.remove('active');
    });
    document.getElementById(`${sectionName}-section`).classList.add('active');

    this.currentSection = sectionName;

    // Load section-specific data
    this.loadSectionData(sectionName);
  }

  loadSectionData(section) {
    switch (section) {
      case 'webui':
        this.loadWebUIData();
        break;
      case 'projects':
        // Projects will be loaded when refresh button is clicked
        break;
      case 'issues':
        // Issues will be loaded when refresh button is clicked
        break;
    }
  }

  async loadWebUIData() {
    try {
      // Update system status display
      this.checkSystemStatus();

      // Load recent activity placeholder
      const activityElement = document.getElementById('recent-activity');
      if (activityElement) {
        activityElement.innerHTML = `
          <div class="activity-item">
            <div class="activity-icon">📋</div>
            <div class="activity-text">System initialized</div>
            <div class="activity-time">Just now</div>
          </div>
        `;
      }
    } catch (error) {
      console.error('Error loading webui:', error);
    }
  }

  async checkSystemStatus() {
    try {
      // Check MCP Bridge health using proxy endpoint
      const response = await fetch('/api/health');
      const isConnected = response.ok;

      this.systemStatus.mcpReady = isConnected;
      this.updateConnectionStatus(isConnected ? 'connected' : 'disconnected');

      // Update system status display
      const mcpStatusEl = document.getElementById('mcp-status');
      const gaiaStatusEl = document.getElementById('gaia-status');

      if (mcpStatusEl) {
        mcpStatusEl.textContent = isConnected ? 'Ready' : 'Not Connected';
        mcpStatusEl.className = isConnected ? 'status-value ready' : 'status-value error';
      }

      if (gaiaStatusEl) {
        gaiaStatusEl.textContent = 'Ready';
        gaiaStatusEl.className = 'status-value ready';
      }
    } catch (error) {
      console.error('Error checking system status:', error);
      this.updateConnectionStatus('disconnected');

      const mcpStatusEl = document.getElementById('mcp-status');
      if (mcpStatusEl) {
        mcpStatusEl.textContent = 'Error';
        mcpStatusEl.className = 'status-value error';
      }
    }
  }

  updateConnectionStatus(status) {
    const statusElement = document.getElementById('connection-status');
    if (!statusElement) return;

    const statusDot = statusElement.querySelector('.status-dot');
    const statusText = statusElement.querySelector('.status-text');

    if (status === 'connected') {
      statusDot.classList.add('connected');
      statusDot.classList.remove('error');
      statusText.textContent = 'Connected';
    } else if (status === 'error' || status === 'disconnected') {
      statusDot.classList.add('error');
      statusDot.classList.remove('connected');
      statusText.textContent = 'Disconnected';
    } else {
      statusDot.classList.remove('connected', 'error');
      statusText.textContent = 'Initializing...';
    }
  }

  showLoading() {
    const overlay = document.getElementById('loading-overlay');
    if (overlay) {
      overlay.classList.add('visible');
    }
  }

  hideLoading() {
    const overlay = document.getElementById('loading-overlay');
    if (overlay) {
      overlay.classList.remove('visible');
    }
  }

  async sendMcpRequest(method, params = {}) {
    const requestBody = {
      jsonrpc: "2.0",
      id: Date.now().toString(),
      method: method,
      params: params
    };

    try {
      const response = await fetch(`${this.mcpBaseUrl}/jira`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(requestBody)
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();
      return data;
    } catch (error) {
      console.error('MCP Request Error:', error);
      throw error;
    }
  }

  async loadProjects() {
    this.showLoading();
    try {
      const response = await this.sendMcpRequest('jira_get_projects');
      const projectsContent = document.getElementById('projects-content');

      if (response.result && response.result.projects) {
        const projects = response.result.projects;
        projectsContent.innerHTML = projects.map(project => `
          <div class="project-card">
            <div class="project-title">${project.name}</div>
            <div class="project-meta">
              <span>Key: ${project.key}</span>
              <span>Type: ${project.projectTypeKey}</span>
            </div>
            ${project.description ? `<div class="project-description">${project.description}</div>` : ''}
          </div>
        `).join('');
      } else {
        projectsContent.innerHTML = '<p class="placeholder-text">No projects found or unable to load projects.</p>';
      }
    } catch (error) {
      const projectsContent = document.getElementById('projects-content');
      projectsContent.innerHTML = `<p class="placeholder-text">Error loading projects: ${error.message}</p>`;
    } finally {
      this.hideLoading();
    }
  }

  async loadMyIssues() {
    this.showLoading();
    try {
      const response = await this.sendMcpRequest('jira_search_issues', {
        jql: 'assignee = currentUser() AND resolution = Unresolved ORDER BY priority DESC'
      });

      const issuesContent = document.getElementById('issues-content');

      if (response.result && response.result.issues && response.result.issues.length > 0) {
        const issues = response.result.issues;
        issuesContent.innerHTML = issues.map(issue => `
          <div class="issue-card">
            <div class="issue-title">
              <span class="issue-key">${issue.key}</span>
              ${issue.fields.summary}
            </div>
            <div class="issue-meta">
              <span class="priority-${issue.fields.priority?.name?.toLowerCase() || 'low'}">
                ${issue.fields.priority?.name || 'No Priority'}
              </span>
              <span class="status-badge status-${issue.fields.status?.name?.toLowerCase().replace(' ', '-') || 'todo'}">
                ${issue.fields.status?.name || 'Unknown'}
              </span>
              <span>Type: ${issue.fields.issuetype?.name || 'Unknown'}</span>
            </div>
            ${issue.fields.description ? `<div class="issue-description">${issue.fields.description}</div>` : ''}
          </div>
        `).join('');
      } else {
        issuesContent.innerHTML = '<p class="placeholder-text">No issues assigned to you.</p>';
      }
    } catch (error) {
      const issuesContent = document.getElementById('issues-content');
      issuesContent.innerHTML = `<p class="placeholder-text">Error loading issues: ${error.message}</p>`;
    } finally {
      this.hideLoading();
    }
  }

  async performSearch() {
    const searchInput = document.getElementById('search-input');
    const query = searchInput.value.trim();

    if (!query) return;

    this.showLoading();
    const resultsElement = document.getElementById('search-results');

    try {
      // Use natural language to JQL conversion
      const response = await this.sendMcpRequest('jira_search_issues', {
        query: query
      });

      if (response.result && response.result.issues && response.result.issues.length > 0) {
        const issues = response.result.issues;
        resultsElement.innerHTML = `
          <div class="search-results-header">
            <h3>Found ${issues.length} issue(s)</h3>
          </div>
          <div class="content-grid">
            ${issues.map(issue => `
              <div class="issue-card">
                <div class="issue-title">
                  <span class="issue-key">${issue.key}</span>
                  ${issue.fields.summary}
                </div>
                <div class="issue-meta">
                  <span>Status: ${issue.fields.status?.name || 'Unknown'}</span>
                  <span>Priority: ${issue.fields.priority?.name || 'None'}</span>
                </div>
              </div>
            `).join('')}
          </div>
        `;
      } else {
        resultsElement.innerHTML = '<p class="placeholder-text">No issues found matching your search.</p>';
      }
    } catch (error) {
      resultsElement.innerHTML = `<p class="placeholder-text">Error searching: ${error.message}</p>`;
    } finally {
      this.hideLoading();
    }
  }

  async createIssue(event) {
    event.preventDefault();

    const summary = document.getElementById('issue-summary').value;
    const description = document.getElementById('issue-description').value;
    const issueType = document.getElementById('issue-type').value;
    const project = document.getElementById('issue-project').value;

    if (!summary) {
      alert('Please provide a summary for the issue');
      return;
    }

    this.showLoading();

    try {
      const response = await this.sendMcpRequest('jira_create_issue', {
        summary: summary,
        description: description || '',
        issueType: issueType,
        project: project || undefined
      });

      if (response.result && response.result.key) {
        alert(`Issue created successfully: ${response.result.key}`);
        // Clear form
        document.getElementById('issue-summary').value = '';
        document.getElementById('issue-description').value = '';
        document.getElementById('issue-project').value = '';
      } else {
        alert('Failed to create issue. Please check your input and try again.');
      }
    } catch (error) {
      alert(`Error creating issue: ${error.message}`);
    } finally {
      this.hideLoading();
    }
  }

  async sendChatMessage(event) {
    if (event) event.preventDefault();

    const chatInput = document.getElementById('chat-input');
    const message = chatInput.value.trim();

    if (!message) return;

    // Add user message to chat
    const chatMessages = document.getElementById('chat-messages');
    chatMessages.innerHTML += `
      <div class="chat-message user-message">
        <div class="message-avatar">👤</div>
        <div class="message-content">${message}</div>
      </div>
    `;

    chatInput.value = '';
    chatMessages.scrollTop = chatMessages.scrollHeight;

    // Show typing indicator
    const typingIndicator = `
      <div class="chat-message ai-message typing" id="typing-indicator">
        <div class="message-avatar">🤖</div>
        <div class="message-content">Thinking...</div>
      </div>
    `;
    chatMessages.innerHTML += typingIndicator;
    chatMessages.scrollTop = chatMessages.scrollHeight;

    try {
      // Send message to MCP
      const response = await this.sendMcpRequest('jira_chat', {
        message: message
      });

      // Remove typing indicator
      const indicator = document.getElementById('typing-indicator');
      if (indicator) indicator.remove();

      // Add AI response
      const aiResponse = response.result?.response || 'I encountered an error processing your request.';
      chatMessages.innerHTML += `
        <div class="chat-message ai-message">
          <div class="message-avatar">🤖</div>
          <div class="message-content">${aiResponse}</div>
        </div>
      `;

      chatMessages.scrollTop = chatMessages.scrollHeight;
    } catch (error) {
      // Remove typing indicator
      const indicator = document.getElementById('typing-indicator');
      if (indicator) indicator.remove();

      chatMessages.innerHTML += `
        <div class="chat-message ai-message">
          <div class="message-avatar">🤖</div>
          <div class="message-content">Error: ${error.message}</div>
        </div>
      `;
      chatMessages.scrollTop = chatMessages.scrollHeight;
    }
  }

  async refreshMyIssues() {
    await this.loadMyIssues();
  }
}

// Make functions available globally
window.switchToSection = function(section) {
  window.webuiApp.switchToSection(section);
};

window.loadProjects = function() {
  window.webuiApp.loadProjects();
};

window.loadMyIssues = function() {
  window.webuiApp.loadMyIssues();
};

window.refreshMyIssues = function() {
  window.webuiApp.refreshMyIssues();
};

window.performSearch = function() {
  window.webuiApp.performSearch();
};

window.createIssue = function(event) {
  window.webuiApp.createIssue(event);
};

window.sendChatMessage = function(event) {
  window.webuiApp.sendChatMessage(event);
};

// Initialize the app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  window.webuiApp = new JaxWebUIRenderer();
});