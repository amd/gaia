// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Window Manager
// Handles Electron window creation and management

const { BrowserWindow } = require('electron');
const path = require('path');

class WindowManager {
  constructor() {
    this.mainWindow = null;
  }

  createMainWindow() {
    // Create the browser window
    this.mainWindow = new BrowserWindow({
      width: 1600,
      height: 1000,
      icon: path.join(__dirname, '..', 'assets', 'icons', 'icon.png'),
      webPreferences: {
        nodeIntegration: false,
        contextIsolation: true,
        enableRemoteModule: false,
        preload: path.join(__dirname, '..', 'preload.js'),
      },
      show: false, // Don't show until ready
      titleBarStyle: 'default',
      title: 'JAX - Jira Agent Experience',
      autoHideMenuBar: true  // Hide menu bar
    });

    // Load the index.html of the app
    this.mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));

    // Show window when ready
    this.mainWindow.once('ready-to-show', () => {
      this.mainWindow.show();
    });

    // Handle window closed
    this.mainWindow.on('closed', () => {
      this.mainWindow = null;
      if (this.onWindowClosed) {
        this.onWindowClosed();
      }
    });

    // Open DevTools in development
    if (process.env.NODE_ENV === 'development') {
      this.mainWindow.webContents.openDevTools();
    }

    return this.mainWindow;
  }

  getMainWindow() {
    return this.mainWindow;
  }

  sendStatusUpdate(message) {
    if (this.mainWindow) {
      this.mainWindow.webContents.send('status-update', message);
    }
  }

  setWindowClosedCallback(callback) {
    this.onWindowClosed = callback;
  }

  isWindowCreated() {
    return this.mainWindow !== null;
  }

  closeWindow() {
    if (this.mainWindow) {
      this.mainWindow.close();
    }
  }

  focusWindow() {
    if (this.mainWindow) {
      if (this.mainWindow.isMinimized()) {
        this.mainWindow.restore();
      }
      this.mainWindow.focus();
    }
  }
}

module.exports = WindowManager;