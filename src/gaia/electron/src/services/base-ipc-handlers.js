// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Base IPC Handlers for GAIA Electron Apps
 *
 * Provides common IPC handlers that all GAIA apps can use,
 * particularly for MCP Bridge communication and system operations.
 *
 * Apps can extend this class to add their own specific handlers.
 */

const MCPClient = require('./mcp-client');

class BaseIpcHandlers {
  constructor(appName = 'GAIA App', appVersion = '1.0.0') {
    this.appName = appName;
    this.appVersion = appVersion;
    this.mcpClient = new MCPClient({
      debug: process.env.NODE_ENV === 'development',
      verbose: process.env.VERBOSE === 'true'
    });
    this.mcpReady = false;
  }

  /**
   * Set MCP ready status
   */
  setMCPReady(ready) {
    this.mcpReady = ready;
  }

  /**
   * Setup base IPC handlers that all apps get
   */
  setupHandlers(ipcMain) {
    // System status handlers
    ipcMain.handle('get-system-status', async () => {
      const mcpAvailable = await this.mcpClient.isAvailable();
      return {
        gaiaReady: this.mcpReady,
        mcpBridge: mcpAvailable,
        mcpUrl: this.mcpClient.baseUrl,
        appName: this.appName,
        appVersion: this.appVersion
      };
    });

    // MCP Bridge management
    ipcMain.handle('check-mcp-environment', async () => {
      return this.checkMCPEnvironment();
    });

    ipcMain.handle('check-mcp-health', async () => {
      try {
        const health = await this.mcpClient.checkHealth();
        return { success: true, data: health };
      } catch (error) {
        return { success: false, error: error.message };
      }
    });

    // Generic MCP operations
    ipcMain.handle('mcp-get-tools', async () => {
      try {
        const tools = await this.mcpClient.getTools();
        return { success: true, data: tools };
      } catch (error) {
        return { success: false, error: error.message };
      }
    });

    ipcMain.handle('mcp-query-chat', async (event, query, options) => {
      try {
        const result = await this.mcpClient.queryChat(query, options);
        return { success: true, data: result };
      } catch (error) {
        return { success: false, error: error.message };
      }
    });

    ipcMain.handle('mcp-query-llm', async (event, query, options) => {
      try {
        const result = await this.mcpClient.queryLLM(query, options);
        return { success: true, data: result };
      } catch (error) {
        return { success: false, error: error.message };
      }
    });

    // Application management
    ipcMain.handle('open-external-link', async (event, url) => {
      const { shell } = require('electron');
      shell.openExternal(url);
    });

    ipcMain.handle('show-save-dialog', async (event, options) => {
      const { dialog } = require('electron');
      const result = await dialog.showSaveDialog(null, options);
      return result;
    });

    ipcMain.handle('show-open-dialog', async (event, options) => {
      const { dialog } = require('electron');
      const result = await dialog.showOpenDialog(null, options);
      return result;
    });

    // Allow derived classes to add their own handlers
    this.setupAppSpecificHandlers(ipcMain);
  }

  /**
   * Override this method in derived classes to add app-specific handlers
   */
  setupAppSpecificHandlers(ipcMain) {
    // To be overridden by derived classes
  }

  /**
   * Check MCP environment and availability
   */
  async checkMCPEnvironment() {
    try {
      const isAvailable = await this.mcpClient.isAvailable();
      if (isAvailable) {
        const health = await this.mcpClient.checkHealth();
        this.mcpReady = true;
        return {
          success: true,
          message: `MCP Bridge connected (${health.agents || 0} agents, ${health.tools || 0} tools available)`
        };
      } else {
        this.mcpReady = false;
        return {
          success: false,
          message: 'MCP Bridge not available. Please run: gaia mcp start'
        };
      }
    } catch (error) {
      this.mcpReady = false;
      return {
        success: false,
        message: `MCP connection check failed: ${error.message}`
      };
    }
  }

  /**
   * Initialize MCP connection
   */
  async initializeMCP() {
    try {
      console.log('🔍 Checking MCP Bridge availability...');

      const isAvailable = await this.mcpClient.waitForBridge(5, 2000);

      if (isAvailable) {
        const health = await this.mcpClient.checkHealth();
        console.log('✅ MCP Bridge connected:', health);
        this.mcpReady = true;

        // Initialize session
        await this.mcpClient.initialize(this.appName, this.appVersion);

        return {
          success: true,
          health: health
        };
      } else {
        console.warn('⚠️ MCP Bridge not available');
        this.mcpReady = false;
        return {
          success: false,
          message: 'MCP Bridge not available. Start with: gaia mcp start'
        };
      }
    } catch (error) {
      console.error('Failed to initialize MCP:', error);
      this.mcpReady = false;
      return {
        success: false,
        error: error.message
      };
    }
  }

  /**
   * Generic method to execute queries through MCP
   */
  async executeMCPQuery(endpoint, query, options = {}) {
    try {
      console.log(`\n🎯 === EXECUTING MCP QUERY ===`);
      console.log(`Endpoint: ${endpoint}`);
      console.log(`Query: "${query}"`);
      console.log(`MCP URL: ${this.mcpClient.baseUrl}/${endpoint}`);

      let result;
      switch (endpoint) {
        case 'chat':
          result = await this.mcpClient.queryChat(query, options);
          break;
        case 'llm':
          result = await this.mcpClient.queryLLM(query, options);
          break;
        case 'jira':
          result = await this.mcpClient.queryJira(query);
          break;
        default:
          throw new Error(`Unknown endpoint: ${endpoint}`);
      }

      console.log(`\n📊 === QUERY RESULT ===`);
      console.log(`Success: ${result.success !== false}`);

      if (result.data) {
        const dataKeys = Object.keys(result.data);
        if (dataKeys.length > 0) {
          console.log(`Data keys: ${dataKeys.join(', ')}`);
        }
      }

      if (result.metadata) {
        console.log(`Metadata: steps=${result.metadata.steps_taken}, duration=${result.metadata.duration}s`);
      }

      console.log(`=== END RESULT ===\n`);

      return result;
    } catch (error) {
      console.error(`\n💥 === MCP QUERY ERROR ===`);
      console.error(`Error: ${error.message}`);
      console.error(`MCP Bridge URL: ${this.mcpClient.baseUrl}`);
      console.error(`=== END ERROR ===\n`);

      return {
        success: false,
        error: error.message,
        data: {}
      };
    }
  }

  /**
   * Cleanup resources
   */
  cleanup() {
    // Base cleanup - can be extended by derived classes
    console.log(`${this.appName} base handlers cleanup completed`);
  }
}

module.exports = BaseIpcHandlers;