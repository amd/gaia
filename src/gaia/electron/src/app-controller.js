// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const { ipcMain } = require('electron');
const WindowManager = require('./services/window-manager');
const MCPClient = require('./services/mcp-client');
const BaseIpcHandlers = require('./services/base-ipc-handlers');
const path = require('path');
const fs = require('fs');

class AppController {
  constructor(config, appPath) {
    this.config = config;
    this.appPath = appPath;
    this.windowManager = new WindowManager(config, appPath);

    // Create shared MCP client
    this.mcpClient = new MCPClient({
      debug: process.env.NODE_ENV === 'development',
      verbose: process.env.VERBOSE === 'true'
    });

    // Create base IPC handlers
    this.baseIpcHandlers = new BaseIpcHandlers(config.displayName, config.version);

    // Load app-specific services if they exist
    this.appServices = this.loadAppServices();
  }

  async initialize() {
    try {
      // Setup IPC handlers
      this.setupIpcHandlers();

      // Create main window
      const mainWindow = this.windowManager.createMainWindow();

      // Initialize MCP connection
      const mcpStatus = await this.baseIpcHandlers.initializeMCP();
      if (mcpStatus.success) {
        mainWindow.webContents.send('app:status', {
          type: 'success',
          message: '✅ Connected to GAIA MCP Bridge'
        });
      } else {
        mainWindow.webContents.send('app:status', {
          type: 'warning',
          message: '⚠️ ' + mcpStatus.message
        });
      }

      // Initialize app-specific services
      if (this.appServices) {
        // Pass the MCP client to app services if they have an initialize method
        if (typeof this.appServices.initialize === 'function') {
          try {
            await this.appServices.initialize(mainWindow, this.mcpClient);
          } catch (error) {
            console.error('⚠️ App services initialization error:', error.message);
            // Continue even if app services fail
          }
        }

        // If app services have a setMCPClient method, provide the client
        if (typeof this.appServices.setMCPClient === 'function') {
          this.appServices.setMCPClient(this.mcpClient);
        }
      }

      return mainWindow;
    } catch (error) {
      console.error('❌ Failed to initialize app controller:', error.message);
      throw error;
    }
  }

  loadAppServices() {
    // Try to load app-specific services
    const possiblePaths = [
      path.join(this.appPath, 'src', 'services', 'app-services.js'),
      path.join(this.appPath, 'services', 'app-services.js'),
      path.join(this.appPath, 'app-services.js')
    ];

    for (const servicesPath of possiblePaths) {
      if (fs.existsSync(servicesPath)) {
        try {
          const services = require(servicesPath);
          console.log('✅ Loaded app-specific services from:', servicesPath);
          return services;
        } catch (error) {
          console.error('⚠️ Failed to load app services from', servicesPath + ':', error.message);
        }
      }
    }

    console.log('ℹ️ No app-specific services found (optional)');
    return null;
  }

  setupIpcHandlers() {
    // Basic IPC handlers that all apps get
    ipcMain.handle('app:getConfig', () => this.config);
    ipcMain.handle('app:getVersion', () => this.config.version);
    ipcMain.handle('app:getInfo', () => ({
      name: this.config.name,
      displayName: this.config.displayName,
      version: this.config.version,
      description: this.config.description
    }));

    // Setup base MCP IPC handlers that all apps get
    this.baseIpcHandlers.setupHandlers(ipcMain);
    console.log('✅ Base IPC handlers registered');

    // Setup app-specific IPC handlers if they exist
    if (this.appServices && typeof this.appServices.setupIpcHandlers === 'function') {
      try {
        // Pass both ipcMain and mcpClient so apps can use them
        this.appServices.setupIpcHandlers(ipcMain, this.mcpClient);
        console.log('✅ App-specific IPC handlers registered');
      } catch (error) {
        console.error('⚠️ Failed to setup app IPC handlers:', error.message);
      }
    }
  }

  cleanup() {
    try {
      if (this.appServices && typeof this.appServices.cleanup === 'function') {
        this.appServices.cleanup();
      }
    } catch (error) {
      console.error('⚠️ Error during app services cleanup:', error.message);
    }
    console.log(`✅ ${this.config.displayName} cleanup completed`);
  }

  getMainWindow() {
    return this.windowManager.getMainWindow();
  }
}

module.exports = AppController;