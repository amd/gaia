// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * EMR Dashboard Electron Wrapper
 *
 * A minimal Electron wrapper that loads the EMR dashboard from a running server.
 * This provides a native app experience without requiring full Electron integration.
 */

const { app, BrowserWindow, shell } = require('electron');
const path = require('path');

// Get configuration from environment or command line
const DASHBOARD_URL = process.env.EMR_DASHBOARD_URL || process.argv[2] || 'http://localhost:8080';
const WINDOW_TITLE = 'EMR Dashboard - Medical Intake Agent';

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1920,
    height: 1080,
    minWidth: 1000,
    minHeight: 600,
    title: WINDOW_TITLE,
    icon: path.join(__dirname, 'amd.png'),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
    // Modern window styling - AMD black theme
    backgroundColor: '#000000',
    show: false, // Don't show until ready
    center: true, // Center on screen
  });

  // Load the dashboard URL
  mainWindow.loadURL(DASHBOARD_URL);

  // Show window centered when ready
  mainWindow.once('ready-to-show', () => {
    mainWindow.center();
    mainWindow.show();
    mainWindow.focus();
  });

  // Handle external links - open in default browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // Handle navigation - keep dashboard URLs internal
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const dashboardHost = new URL(DASHBOARD_URL).host;
    const targetHost = new URL(url).host;

    if (targetHost !== dashboardHost) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  // Handle connection errors gracefully
  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.error(`Failed to load dashboard: ${errorDescription} (${errorCode})`);

    // Show error page
    mainWindow.loadURL(`data:text/html,
      <html>
        <head>
          <style>
            @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;600&display=swap');
            body {
              background: #000000;
              color: #ffffff;
              font-family: 'Poppins', -apple-system, BlinkMacSystemFont, sans-serif;
              display: flex;
              align-items: center;
              justify-content: center;
              height: 100vh;
              margin: 0;
              flex-direction: column;
            }
            h1 { color: #ED1C24; margin-bottom: 1rem; }
            p { color: #a0a0a0; max-width: 400px; text-align: center; line-height: 1.6; }
            button {
              margin-top: 2rem;
              padding: 0.75rem 2rem;
              background: #ED1C24;
              color: white;
              border: none;
              border-radius: 8px;
              cursor: pointer;
              font-size: 1rem;
              font-family: 'Poppins', sans-serif;
            }
            button:hover { background: #C41922; }
          </style>
        </head>
        <body>
          <h1>Connection Error</h1>
          <p>Could not connect to the EMR Dashboard server at ${DASHBOARD_URL}.</p>
          <p>Make sure the dashboard server is running.</p>
          <button onclick="location.reload()">Retry</button>
        </body>
      </html>
    `);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// Handle creating/removing shortcuts on Windows when installing/uninstalling
try {
  if (require('electron-squirrel-startup')) {
    app.quit();
  }
} catch (error) {
  // electron-squirrel-startup not available, continue normally
}

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

console.log(`🏥 EMR Dashboard Electron wrapper starting...`);
console.log(`📍 Dashboard URL: ${DASHBOARD_URL}`);
