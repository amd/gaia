// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// App Controller for JAX (Jira Agent Experience)
// Manages application lifecycle and coordinates services

const { ipcMain } = require('electron');
const WindowManager = require('./services/window-manager');
const appServices = require('./services/app-services');

class AppController {
  constructor() {
    this.windowManager = new WindowManager();
    this.mcpReady = false;
  }

  // Helper method to get MCP URL from environment or default
  getMCPUrl() {
    return process.env.GAIA_MCP_URL || 'http://localhost:8765';
  }

  async initialize() {
    // Create main window
    const mainWindow = this.windowManager.createMainWindow();

    // Setup IPC handlers
    this.setupIpcHandlers();

    // Initialize app services (JIRA-specific functionality)
    try {
      await appServices.initialize(mainWindow, null);
      console.log('✅ JAX services initialized');
    } catch (error) {
      console.error('⚠️ Failed to initialize JAX services:', error.message);
    }

    // Check MCP bridge availability in background
    this.checkMCPBridgeInBackground();

    return mainWindow;
  }

  setupIpcHandlers() {
    // Basic app info handlers
    ipcMain.handle('app:getInfo', () => ({
      name: 'gaia-jax-webui',
      displayName: 'JAX - Jira Agent Experience',
      version: '1.0.0',
      description: 'AI-powered JIRA management using GAIA MCP integration'
    }));

    // MCP bridge status handler
    ipcMain.handle('mcp:status', async () => {
      try {
        const response = await fetch(`${this.getMCPUrl()}/status`);
        if (response.ok) {
          const data = await response.json();
          this.mcpReady = true;
          return { success: true, status: 'connected', data };
        }
        this.mcpReady = false;
        return { success: false, status: 'disconnected' };
      } catch (error) {
        this.mcpReady = false;
        return { success: false, status: 'error', error: error.message };
      }
    });

    // System status handler (for preload compatibility)
    ipcMain.handle('get-system-status', async () => {
      try {
        const response = await fetch(`${this.getMCPUrl()}/status`);
        if (response.ok) {
          const data = await response.json();
          this.mcpReady = true;
          return { success: true, status: 'connected', data };
        }
        this.mcpReady = false;
        return { success: false, status: 'disconnected' };
      } catch (error) {
        this.mcpReady = false;
        return { success: false, status: 'error', error: error.message };
      }
    });

    // Application utility handlers
    const { shell, dialog } = require('electron');
    ipcMain.handle('open-external-link', async (event, url) => {
      await shell.openExternal(url);
      return { success: true };
    });

    ipcMain.handle('show-save-dialog', async (event, options) => {
      return await dialog.showSaveDialog(this.windowManager.getMainWindow(), options);
    });

    ipcMain.handle('show-open-dialog', async (event, options) => {
      return await dialog.showOpenDialog(this.windowManager.getMainWindow(), options);
    });

    // Stub handlers for Python/MCP management (not needed in standalone mode)
    ipcMain.handle('start-gaia-python', async () => {
      console.log('start-gaia-python not implemented in JAX standalone mode');
      return { success: false, error: 'Not available in standalone mode' };
    });

    ipcMain.handle('stop-gaia-python', async () => {
      console.log('stop-gaia-python not implemented in JAX standalone mode');
      return { success: false, error: 'Not available in standalone mode' };
    });

    ipcMain.handle('start-mcp-bridge', async () => {
      console.log('MCP Bridge should be started externally with: gaia mcp start');
      return { success: false, error: 'Please start MCP Bridge externally with: gaia mcp start' };
    });

    ipcMain.handle('stop-mcp-bridge', async () => {
      console.log('MCP Bridge should be stopped externally with: gaia mcp stop');
      return { success: false, error: 'Please stop MCP Bridge externally with: gaia mcp stop' };
    });

    // Setup additional JIRA-specific handlers via app-services
    if (typeof appServices.setupIpcHandlers === 'function') {
      appServices.setupIpcHandlers(ipcMain, null);
      console.log('✅ JAX IPC handlers registered');
    }
  }

  async checkMCPBridgeInBackground() {
    try {
      console.log('🔍 Checking MCP Bridge availability...');

      // Try to connect to MCP bridge
      const maxRetries = 5;
      let connected = false;

      for (let i = 0; i < maxRetries; i++) {
        try {
          const response = await fetch(`${this.getMCPUrl()}/status`);
          if (response.ok) {
            connected = true;
            break;
          }
        } catch (error) {
          // Retry after delay
          await new Promise(resolve => setTimeout(resolve, 2000));
        }
      }

      if (connected) {
        console.log('✅ MCP Bridge connected');
        this.mcpReady = true;
        this.windowManager.sendStatusUpdate('✅ Connected to GAIA MCP Bridge');

        // Check for JIRA credentials
        await this.checkJiraCredentials();
      } else {
        console.warn('⚠️ MCP Bridge not available');
        this.mcpReady = false;
        this.windowManager.sendStatusUpdate(
          '⚠️ MCP Bridge not running. Start with: gaia mcp start'
        );
      }
    } catch (error) {
      console.error('Failed to check MCP bridge:', error);
      this.mcpReady = false;
      this.windowManager.sendStatusUpdate(
        `Failed to connect to MCP Bridge: ${error.message}`
      );
    }
  }

  async checkJiraCredentials() {
    try {
      // Try a simple JIRA query to check if credentials are configured
      const response = await fetch(`${this.getMCPUrl()}/jira`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: 'show my issues limit 1' })
      });

      if (response.ok) {
        console.log('✅ JIRA credentials configured');
        this.windowManager.sendStatusUpdate('✅ JIRA credentials configured');
      } else {
        const error = await response.text();
        if (error.includes('credentials') || error.includes('authentication')) {
          console.warn('⚠️ JIRA credentials not configured');
          this.windowManager.sendStatusUpdate(
            '⚠️ JIRA credentials not configured. Please set environment variables.'
          );
        }
      }
    } catch (error) {
      // Credentials check is optional, don't fail if it doesn't work
      console.log('Could not verify JIRA credentials:', error.message);
    }
  }

  cleanup() {
    if (typeof appServices.cleanup === 'function') {
      appServices.cleanup();
    }
    console.log('JAX cleanup completed');
  }

  getMainWindow() {
    return this.windowManager.getMainWindow();
  }
}

module.exports = AppController;