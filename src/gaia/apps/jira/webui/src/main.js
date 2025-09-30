// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const { app, BrowserWindow } = require('electron');
const path = require('path');
const AppController = require('./app-controller');

// Load environment variables from .env file (this will not override existing env vars)
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });

// Debug: Log application startup info
console.log('🔍 Application startup:');
console.log(`   Current working directory: ${process.cwd()}`);
console.log(`   App directory: ${__dirname}`);

// Handle creating/removing shortcuts on Windows when installing/uninstalling.
if (require('electron-squirrel-startup')) {
  app.quit();
}

// App event handlers
const appController = new AppController();

app.whenReady().then(() => {
  appController.initialize();
  
  app.on('activate', () => {
    // On macOS it's common to re-create a window in the app when the
    // dock icon is clicked and there are no other windows open.
    if (BrowserWindow.getAllWindows().length === 0) {
      appController.initialize();
    }
  });
});

app.on('window-all-closed', () => {
  // On macOS it is common for applications and their menu bar
  // to stay active until the user quits explicitly with Cmd + Q
  if (process.platform !== 'darwin') {
    appController.cleanup();
    app.quit();
  }
});

app.on('before-quit', () => {
  appController.cleanup();
});