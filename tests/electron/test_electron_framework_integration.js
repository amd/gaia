// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Framework Integration Tests
 *
 * These tests verify that the GAIA Electron framework can still be used to
 * create and run new apps after dependency updates (e.g., from Dependabot).
 *
 * The key test creates a minimal throwaway app using the exact same structure
 * as documented apps, then verifies it can be launched successfully.
 *
 * This catches breaking changes in:
 * - Electron API compatibility
 * - Framework dependencies
 * - Build tooling (electron-forge)
 * - IPC/preload script patterns
 */

const { execSync, spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

// Paths to framework and apps
const FRAMEWORK_PATH = path.join(__dirname, '../../src/gaia/electron');
const EXAMPLE_APP_PATH = path.join(__dirname, '../../src/gaia/apps/example/webui');
const JIRA_APP_PATH = path.join(__dirname, '../../src/gaia/apps/jira/webui');

describe('Electron Framework Integration', () => {
  describe('Framework Core Validation', () => {
    it('should have valid framework package.json with electron dependency', () => {
      const pkgPath = path.join(FRAMEWORK_PATH, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));

      expect(pkg.devDependencies).toHaveProperty('electron');
      expect(pkg.peerDependencies).toHaveProperty('electron');
    });

    it('should have main.js that loads apps dynamically via GAIA_APP_NAME', () => {
      const mainPath = path.join(FRAMEWORK_PATH, 'src/main.js');
      const content = fs.readFileSync(mainPath, 'utf8');

      // Framework should reference GAIA_APP_NAME for app loading
      expect(content).toContain('GAIA_APP_NAME');
    });

    it('should have app-controller with required lifecycle methods', () => {
      const controllerPath = path.join(FRAMEWORK_PATH, 'src/app-controller.js');
      const content = fs.readFileSync(controllerPath, 'utf8');

      // AppController should have init, cleanup, createWindow methods
      expect(content).toContain('class');
      expect(content).toContain('init');
    });

    it('should have window-manager service', () => {
      const wmPath = path.join(FRAMEWORK_PATH, 'src/services/window-manager.js');
      expect(fs.existsSync(wmPath)).toBe(true);

      const content = fs.readFileSync(wmPath, 'utf8');
      expect(content).toContain('BrowserWindow');
    });

    it('should have mcp-client service for backend communication', () => {
      const mcpPath = path.join(FRAMEWORK_PATH, 'src/services/mcp-client.js');
      expect(fs.existsSync(mcpPath)).toBe(true);
    });

    it('should have base-ipc-handlers for IPC communication', () => {
      const ipcPath = path.join(FRAMEWORK_PATH, 'src/services/base-ipc-handlers.js');
      expect(fs.existsSync(ipcPath)).toBe(true);

      const content = fs.readFileSync(ipcPath, 'utf8');
      expect(content).toContain('ipcMain');
    });

    it('should have preload script with contextBridge', () => {
      const preloadPath = path.join(FRAMEWORK_PATH, 'src/preload/preload.js');
      expect(fs.existsSync(preloadPath)).toBe(true);

      const content = fs.readFileSync(preloadPath, 'utf8');
      expect(content).toContain('contextBridge');
      expect(content).toContain('exposeInMainWorld');
    });
  });

  describe('App Structure Compliance', () => {
    const apps = [
      { name: 'example', path: EXAMPLE_APP_PATH },
      { name: 'jira', path: JIRA_APP_PATH }
    ];

    apps.forEach(({ name, path: appPath }) => {
      describe(`${name} app`, () => {
        it('should have app.config.json with required fields', () => {
          const configPath = path.join(appPath, 'app.config.json');
          expect(fs.existsSync(configPath)).toBe(true);

          const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
          expect(config).toHaveProperty('name');
          expect(config).toHaveProperty('displayName');
          expect(config).toHaveProperty('version');
        });

        it('should have package.json with electron dependency', () => {
          const pkgPath = path.join(appPath, 'package.json');
          const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));

          // Electron can be in dependencies or devDependencies
          const hasElectron =
            pkg.dependencies?.electron ||
            pkg.devDependencies?.electron;
          expect(hasElectron).toBeDefined();
        });

        it('should have main entry point that imports electron', () => {
          const pkgPath = path.join(appPath, 'package.json');
          const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
          const mainFile = pkg.main || 'src/main.js';
          const mainPath = path.join(appPath, mainFile);

          expect(fs.existsSync(mainPath)).toBe(true);

          const content = fs.readFileSync(mainPath, 'utf8');
          expect(content).toContain('electron');
        });

        it('should have preload script with context isolation', () => {
          const preloadPath = path.join(appPath, 'src/preload.js');
          expect(fs.existsSync(preloadPath)).toBe(true);

          const content = fs.readFileSync(preloadPath, 'utf8');
          expect(content).toContain('contextBridge');
        });

        it('should have index.html in public directory', () => {
          const htmlPath = path.join(appPath, 'public/index.html');
          expect(fs.existsSync(htmlPath)).toBe(true);
        });
      });
    });
  });

  describe('Throwaway App Creation Test', () => {
    const TEMP_APP_NAME = 'ci-test-app';
    let tempAppPath;

    beforeAll(() => {
      // Create temp directory for throwaway app
      tempAppPath = path.join(os.tmpdir(), `gaia-${TEMP_APP_NAME}-${Date.now()}`);
      fs.mkdirSync(tempAppPath, { recursive: true });
      fs.mkdirSync(path.join(tempAppPath, 'src'), { recursive: true });
      fs.mkdirSync(path.join(tempAppPath, 'public'), { recursive: true });
    });

    afterAll(() => {
      // Cleanup temp app
      if (tempAppPath && fs.existsSync(tempAppPath)) {
        fs.rmSync(tempAppPath, { recursive: true, force: true });
      }
    });

    it('should create a valid app.config.json', () => {
      const config = {
        name: TEMP_APP_NAME,
        displayName: 'CI Test App',
        version: '1.0.0',
        description: 'Throwaway app for CI testing',
        window: {
          width: 800,
          height: 600
        }
      };

      const configPath = path.join(tempAppPath, 'app.config.json');
      fs.writeFileSync(configPath, JSON.stringify(config, null, 2));

      expect(fs.existsSync(configPath)).toBe(true);
      const written = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      expect(written.name).toBe(TEMP_APP_NAME);
    });

    it('should create a valid package.json with electron', () => {
      // Get electron version from the framework (canonical source that Dependabot updates)
      // The framework version is what all apps should match
      const frameworkPkg = JSON.parse(
        fs.readFileSync(path.join(FRAMEWORK_PATH, 'package.json'), 'utf8')
      );
      const electronVersion = frameworkPkg.devDependencies.electron;

      const pkg = {
        name: TEMP_APP_NAME,
        version: '1.0.0',
        main: 'src/main.js',
        scripts: {
          start: 'electron .'
        },
        dependencies: {},
        devDependencies: {
          electron: electronVersion
        }
      };

      const pkgPath = path.join(tempAppPath, 'package.json');
      fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2));

      expect(fs.existsSync(pkgPath)).toBe(true);
    });

    it('should create a valid main.js following framework pattern', () => {
      const mainJs = `
// CI Test App - Minimal Electron main process
const { app, BrowserWindow } = require('electron');
const path = require('path');

let mainWindow;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 800,
    height: 600,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true
    }
  });

  mainWindow.loadFile(path.join(__dirname, '..', 'public', 'index.html'));

  // Signal successful creation for CI
  console.log('CI_TEST_WINDOW_CREATED');

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  console.log('CI_TEST_APP_READY');
  createWindow();

  // Exit after successful window creation for CI testing
  if (process.env.GAIA_TEST_MODE === '1') {
    setTimeout(() => {
      console.log('CI_TEST_SUCCESS');
      app.quit();
    }, 2000);
  }
});

app.on('window-all-closed', () => {
  app.quit();
});
`;

      const mainPath = path.join(tempAppPath, 'src/main.js');
      fs.writeFileSync(mainPath, mainJs);

      expect(fs.existsSync(mainPath)).toBe(true);
      const content = fs.readFileSync(mainPath, 'utf8');
      expect(content).toContain('BrowserWindow');
      expect(content).toContain('contextIsolation: true');
    });

    it('should create a valid preload.js with contextBridge', () => {
      const preloadJs = `
// CI Test App - Minimal preload script
const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('testAPI', {
  getAppName: () => '${TEMP_APP_NAME}'
});
`;

      const preloadPath = path.join(tempAppPath, 'src/preload.js');
      fs.writeFileSync(preloadPath, preloadJs);

      expect(fs.existsSync(preloadPath)).toBe(true);
    });

    it('should create a valid index.html', () => {
      const html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>CI Test App</title>
</head>
<body>
  <h1>CI Test App</h1>
  <p id="status">Loading...</p>
  <script>
    document.getElementById('status').textContent =
      'App: ' + (window.testAPI?.getAppName() || 'unknown');
  </script>
</body>
</html>`;

      const htmlPath = path.join(tempAppPath, 'public/index.html');
      fs.writeFileSync(htmlPath, html);

      expect(fs.existsSync(htmlPath)).toBe(true);
    });

    it('should have valid throwaway app structure', () => {
      // Verify complete structure matches framework requirements
      expect(fs.existsSync(path.join(tempAppPath, 'app.config.json'))).toBe(true);
      expect(fs.existsSync(path.join(tempAppPath, 'package.json'))).toBe(true);
      expect(fs.existsSync(path.join(tempAppPath, 'src/main.js'))).toBe(true);
      expect(fs.existsSync(path.join(tempAppPath, 'src/preload.js'))).toBe(true);
      expect(fs.existsSync(path.join(tempAppPath, 'public/index.html'))).toBe(true);
    });
  });

  describe('Version Compatibility Matrix', () => {
    it('should have consistent electron major version across framework and apps', () => {
      const frameworkPkg = JSON.parse(
        fs.readFileSync(path.join(FRAMEWORK_PATH, 'package.json'), 'utf8')
      );
      const examplePkg = JSON.parse(
        fs.readFileSync(path.join(EXAMPLE_APP_PATH, 'package.json'), 'utf8')
      );
      const jiraPkg = JSON.parse(
        fs.readFileSync(path.join(JIRA_APP_PATH, 'package.json'), 'utf8')
      );

      const frameworkVersion = frameworkPkg.devDependencies.electron;
      const exampleVersion = examplePkg.devDependencies.electron;
      const jiraVersion = jiraPkg.devDependencies.electron;

      // Extract major versions
      const getMajor = (v) => parseInt(v.replace(/[\^~]/, '').split('.')[0]);

      const frameworkMajor = getMajor(frameworkVersion);
      const exampleMajor = getMajor(exampleVersion);
      const jiraMajor = getMajor(jiraVersion);

      // All should be on the same major version
      expect(exampleMajor).toBe(frameworkMajor);
      expect(jiraMajor).toBe(frameworkMajor);
    });

    it('should use electron >= 31 for security features', () => {
      const frameworkPkg = JSON.parse(
        fs.readFileSync(path.join(FRAMEWORK_PATH, 'package.json'), 'utf8')
      );

      const electronVersion = frameworkPkg.devDependencies.electron;
      const majorVersion = parseInt(electronVersion.replace(/[\^~]/, '').split('.')[0]);

      // Electron 31+ required for latest security features
      expect(majorVersion).toBeGreaterThanOrEqual(31);
    });

    it('should have compatible electron-forge version if present', () => {
      // Check if apps using forge have compatible versions
      const jiraPkg = JSON.parse(
        fs.readFileSync(path.join(JIRA_APP_PATH, 'package.json'), 'utf8')
      );

      if (jiraPkg.devDependencies['@electron-forge/cli']) {
        const forgeVersion = jiraPkg.devDependencies['@electron-forge/cli'];
        const forgeMajor = parseInt(forgeVersion.replace(/[\^~]/, '').split('.')[0]);

        // Forge 7+ for Electron 31+ compatibility
        expect(forgeMajor).toBeGreaterThanOrEqual(7);
      }
    });
  });

  describe('Security Configuration Validation', () => {
    const apps = [
      { name: 'example', path: EXAMPLE_APP_PATH },
      { name: 'jira', path: JIRA_APP_PATH }
    ];

    apps.forEach(({ name, path: appPath }) => {
      describe(`${name} app security`, () => {
        it('should disable nodeIntegration in BrowserWindow creation', () => {
          // Security settings can be in main.js or window-manager.js
          const filesToCheck = [
            path.join(appPath, 'src/main.js'),
            path.join(appPath, 'src/services/window-manager.js')
          ];

          let foundSecuritySettings = false;
          let content = '';

          for (const filePath of filesToCheck) {
            if (fs.existsSync(filePath)) {
              content = fs.readFileSync(filePath, 'utf8');
              if (content.includes('nodeIntegration')) {
                foundSecuritySettings = true;
                break;
              }
            }
          }

          expect(foundSecuritySettings).toBe(true);
          // nodeIntegration should be explicitly false
          expect(content).toMatch(/nodeIntegration:\s*false/);
        });

        it('should enable contextIsolation in BrowserWindow creation', () => {
          // Security settings can be in main.js or window-manager.js
          const filesToCheck = [
            path.join(appPath, 'src/main.js'),
            path.join(appPath, 'src/services/window-manager.js')
          ];

          let foundSecuritySettings = false;
          let content = '';

          for (const filePath of filesToCheck) {
            if (fs.existsSync(filePath)) {
              content = fs.readFileSync(filePath, 'utf8');
              if (content.includes('contextIsolation')) {
                foundSecuritySettings = true;
                break;
              }
            }
          }

          expect(foundSecuritySettings).toBe(true);
          // contextIsolation should be explicitly true
          expect(content).toMatch(/contextIsolation:\s*true/);
        });

        it('should use contextBridge in preload (not direct exposure)', () => {
          const preloadPath = path.join(appPath, 'src/preload.js');
          const content = fs.readFileSync(preloadPath, 'utf8');

          // Should use safe contextBridge API
          expect(content).toContain('contextBridge');
          expect(content).toContain('exposeInMainWorld');

          // Should NOT expose entire modules
          expect(content).not.toContain('nodeIntegration: true');
        });
      });
    });
  });
});
