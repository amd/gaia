// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const { BrowserWindow } = require('electron');
const path = require('path');

class WindowManager {
  constructor(config, appPath) {
    this.config = config;
    this.appPath = appPath;
    this.mainWindow = null;
  }

  createMainWindow() {
    // Default window config, can be overridden by app config
    const windowConfig = {
      width: 1400,
      height: 900,
      minWidth: 1000,
      minHeight: 600,
      title: this.config.displayName || 'GAIA App',
      webPreferences: {
        nodeIntegration: false,
        contextIsolation: true,
        preload: path.join(__dirname, '..', 'preload', 'preload.js')
      },
      ...this.config.window // Allow app to override window settings
    };

    this.mainWindow = new BrowserWindow(windowConfig);

    // Load the app's HTML file from public directory
    const indexPath = path.join(this.appPath, 'public', 'index.html');
    this.mainWindow.loadFile(indexPath);

    // Open DevTools in development
    if (process.env.NODE_ENV === 'development' || process.env.GAIA_APP_MODE === 'development') {
      this.mainWindow.webContents.openDevTools();
    }

    this.mainWindow.on('closed', () => {
      this.mainWindow = null;
    });

    return this.mainWindow;
  }

  sendToRenderer(channel, data) {
    if (this.mainWindow && !this.mainWindow.isDestroyed()) {
      this.mainWindow.webContents.send(channel, data);
    }
  }

  getMainWindow() {
    return this.mainWindow;
  }
}

module.exports = WindowManager;