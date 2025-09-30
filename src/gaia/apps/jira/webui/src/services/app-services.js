// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// JAX app-specific services
// Handles JIRA-specific IPC handlers for the standalone JAX app

class JaxAppServices {
  constructor() {
    // In standalone mode, we don't have an MCP client from the framework
    this.mcpClient = null;
  }

  /**
   * Get the MCP bridge URL from environment or default
   */
  getMCPUrl() {
    return process.env.GAIA_MCP_URL || 'http://localhost:8765';
  }

  /**
   * Execute a JIRA operation via the MCP bridge
   */
  async executeJiraOperation(command) {
    try {
      const response = await fetch(`${this.getMCPUrl()}/jira`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: command })
      });

      if (!response.ok) {
        const error = await response.text();
        return { success: false, error };
      }

      const result = await response.json();
      return { success: true, data: result };
    } catch (error) {
      console.error('JIRA operation error:', error);
      return { success: false, error: error.message };
    }
  }

  /**
   * Set the MCP client instance (kept for compatibility but not used in standalone)
   */
  setMCPClient(mcpClient) {
    this.mcpClient = mcpClient;
  }

  async initialize(mainWindow, mcpClient) {
    console.log('🚀 Initializing JAX services...');
    console.log(`   Using MCP Bridge at: ${this.getMCPUrl()}`);

    // Store the MCP client if provided (though it won't be in standalone mode)
    if (mcpClient) {
      this.mcpClient = mcpClient;
    }

    console.log('✅ JAX ready to handle JIRA requests');
  }

  setupIpcHandlers(ipcMain, mcpClient) {
    // Store the MCP client if provided (for compatibility)
    if (mcpClient && !this.mcpClient) {
      this.mcpClient = mcpClient;
    }

    // Main JIRA command execution handler
    ipcMain.handle('execute-jira-command', async (event, command) => {
      return this.executeJiraOperation(command);
    });

    // Get JIRA projects
    ipcMain.handle('get-jira-projects', async () => {
      return this.executeJiraOperation('list projects');
    });

    // Get user's issues
    ipcMain.handle('get-my-issues', async () => {
      return this.executeJiraOperation('show my open issues');
    });

    // Search JIRA
    ipcMain.handle('search-jira', async (event, query) => {
      return this.executeJiraOperation(query);
    });

    // Create JIRA issue
    ipcMain.handle('create-jira-issue', async (event, issueData) => {
      // Format the create issue command based on the issue data
      let command = 'create issue';
      if (issueData.project) command += ` project:${issueData.project}`;
      if (issueData.type) command += ` type:${issueData.type}`;
      if (issueData.summary) command += ` summary:"${issueData.summary}"`;
      if (issueData.description) command += ` description:"${issueData.description}"`;
      return this.executeJiraOperation(command);
    });

    // Health check (for compatibility)
    ipcMain.handle('jira:checkHealth', async () => {
      try {
        const response = await fetch(`${this.getMCPUrl()}/health`);
        if (response.ok) {
          const health = await response.json();
          return { success: true, data: health };
        }
        return { success: false, error: 'Health check failed' };
      } catch (error) {
        return { success: false, error: error.message };
      }
    });

    console.log('✅ JIRA-specific IPC handlers registered');
  }

  cleanup() {
    // Cleanup if needed
    console.log('JAX services cleanup completed');
  }
}

module.exports = new JaxAppServices();