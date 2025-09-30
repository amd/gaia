// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');

// Get app name from command line or environment
const APP_NAME = process.env.GAIA_APP_NAME || process.argv[2];
const APP_MODE = process.env.GAIA_APP_MODE || 'production';

if (!APP_NAME) {
  console.error('❌ No app name provided. Use GAIA_APP_NAME environment variable or pass as argument.');
  console.error('   Example: GAIA_APP_NAME=jira electron main.js');
  process.exit(1);
}

// Try multiple paths to find the app
function findAppPath(appName) {
  const possiblePaths = [
    path.resolve(__dirname, '..', '..', 'apps', appName, 'webui'),
    path.resolve(process.cwd(), 'src', 'gaia', 'apps', appName, 'webui'),
    path.resolve(__dirname, '..', '..', '..', 'apps', appName, 'webui')
  ];

  for (const appPath of possiblePaths) {
    const configPath = path.join(appPath, 'app.config.json');
    if (fs.existsSync(configPath)) {
      return appPath;
    }
  }
  return null;
}

const appPath = findAppPath(APP_NAME);
if (!appPath) {
  console.error(`❌ App '${APP_NAME}' not found in any of the expected locations.`);
  console.error('   Make sure the app exists in src/gaia/apps/' + APP_NAME + '/webui/');
  process.exit(1);
}

const appConfigPath = path.join(appPath, 'app.config.json');

let appConfig;
try {
  const configContent = fs.readFileSync(appConfigPath, 'utf8');
  appConfig = JSON.parse(configContent);
} catch (error) {
  console.error(`❌ Failed to load app configuration from ${appConfigPath}:`);
  console.error('  ', error.message);
  process.exit(1);
}

// Validate required config fields
if (!appConfig.name || !appConfig.displayName) {
  console.error('❌ Invalid app.config.json: missing required fields (name, displayName)');
  process.exit(1);
}

console.log(`🚀 Starting ${appConfig.displayName} (${APP_NAME}) in ${APP_MODE} mode...`);

// Handle creating/removing shortcuts on Windows when installing/uninstalling
try {
  if (require('electron-squirrel-startup')) {
    app.quit();
  }
} catch (error) {
  // electron-squirrel-startup not available, continue normally
}

// Load AppController with error handling
let AppController;
try {
  AppController = require('./app-controller');
} catch (error) {
  console.error('❌ Failed to load AppController:', error.message);
  process.exit(1);
}

// Initialize app controller with config
const appController = new AppController(appConfig, appPath);

app.whenReady().then(() => {
  try {
    appController.initialize();
  } catch (error) {
    console.error('❌ Failed to initialize app:', error.message);
    app.quit();
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      try {
        appController.initialize();
      } catch (error) {
        console.error('❌ Failed to re-initialize app:', error.message);
      }
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    try {
      appController.cleanup();
    } catch (error) {
      console.error('⚠️ Error during cleanup:', error.message);
    }
    app.quit();
  }
});

app.on('before-quit', () => {
  try {
    appController.cleanup();
  } catch (error) {
    console.error('⚠️ Error during cleanup:', error.message);
  }
});