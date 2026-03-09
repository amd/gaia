// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Installer Readiness Tests for GAIA Agent UI App
 *
 * Validates that the Chat app is properly structured for:
 * - Vite build output for Electron packaging
 * - Windows installer creation
 * - Throwaway app pattern (CI validation)
 * - Security configuration for distribution
 * - Asset and dependency completeness
 *
 * These tests ensure the app can be built and packaged into a
 * distributable installer without errors.
 */

const path = require('path');
const fs = require('fs');
const os = require('os');

const CHAT_APP_PATH = path.join(__dirname, '../../src/gaia/apps/webui');
const FRAMEWORK_PATH = path.join(__dirname, '../../src/gaia/electron');
const BACKEND_PATH = path.join(__dirname, '../../src/gaia/ui');

describe('Chat App Installer Readiness', () => {

  // ── Package Configuration for Distribution ─────────────────────────

  describe('package configuration', () => {
    it('should have valid parseable package.json', () => {
      const packagePath = path.join(CHAT_APP_PATH, 'package.json');
      expect(() => {
        JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      }).not.toThrow();
    });

    it('should have version in semver format', () => {
      const packagePath = path.join(CHAT_APP_PATH, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      expect(pkg.version).toMatch(/^\d+\.\d+\.\d+/);
    });

    it('should have license field for distribution', () => {
      const configPath = path.join(CHAT_APP_PATH, 'app.config.json');
      const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      expect(config.license).toBe('MIT');
    });

    it('should have build script for production bundling', () => {
      const packagePath = path.join(CHAT_APP_PATH, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      expect(pkg.scripts.build).toBeDefined();
      expect(pkg.scripts.build).toContain('vite build');
    });

    it('should have well-formed dependency version strings', () => {
      const packagePath = path.join(CHAT_APP_PATH, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));

      Object.entries(pkg.dependencies || {}).forEach(([name, version]) => {
        expect(version).toMatch(/^[\^~]?\d+/,
          `Invalid version for ${name}: ${version}`);
      });

      Object.entries(pkg.devDependencies || {}).forEach(([name, version]) => {
        expect(version).toMatch(/^[\^~]?\d+/,
          `Invalid version for ${name}: ${version}`);
      });
    });
  });

  // ── Frontend Source Completeness ───────────────────────────────────

  describe('frontend source completeness', () => {
    it('should have all required source files', () => {
      const requiredFiles = [
        'index.html',
        'src/main.tsx',
        'src/App.tsx',
        'src/services/api.ts',
        'src/types/index.ts',
        'src/stores/chatStore.ts',
        'vite.config.ts',
        'tsconfig.json',
      ];

      requiredFiles.forEach(file => {
        const filePath = path.join(CHAT_APP_PATH, file);
        expect(fs.existsSync(filePath)).toBe(true);
      });
    });

    it('should have all required React components', () => {
      const components = [
        'ChatView',
        'Sidebar',
        'WelcomeScreen',
        'MessageBubble',
      ];

      components.forEach(component => {
        const tsxPath = path.join(CHAT_APP_PATH, `src/components/${component}.tsx`);
        const cssPath = path.join(CHAT_APP_PATH, `src/components/${component}.css`);
        expect(fs.existsSync(tsxPath)).toBe(true);
        expect(fs.existsSync(cssPath)).toBe(true);
      });
    });

    it('should have app.config.json for framework loader', () => {
      const configPath = path.join(CHAT_APP_PATH, 'app.config.json');
      expect(fs.existsSync(configPath)).toBe(true);

      const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      expect(config.name).toBe('agent-ui');
      expect(config.displayName).toBeDefined();
    });

    it('should have copyright headers in source files', () => {
      const filesToCheck = [
        'src/services/api.ts',
        'src/types/index.ts',
        'src/stores/chatStore.ts',
        'src/App.tsx',
      ];

      filesToCheck.forEach(file => {
        const filePath = path.join(CHAT_APP_PATH, file);
        if (fs.existsSync(filePath)) {
          const content = fs.readFileSync(filePath, 'utf8');
          expect(content).toContain('Copyright');
          expect(content).toContain('SPDX-License-Identifier');
        }
      });
    });
  });

  // ── Backend Python Completeness ────────────────────────────────────

  describe('backend Python completeness', () => {
    it('should have __init__.py', () => {
      expect(fs.existsSync(path.join(BACKEND_PATH, '__init__.py'))).toBe(true);
    });

    it('should have server.py with FastAPI app', () => {
      const serverPath = path.join(BACKEND_PATH, 'server.py');
      expect(fs.existsSync(serverPath)).toBe(true);

      const content = fs.readFileSync(serverPath, 'utf8');
      expect(content).toContain('FastAPI');
      expect(content).toContain('create_app');
    });

    it('should have database.py with SQLite support', () => {
      const dbPath = path.join(BACKEND_PATH, 'database.py');
      expect(fs.existsSync(dbPath)).toBe(true);

      const content = fs.readFileSync(dbPath, 'utf8');
      expect(content).toContain('sqlite3');
      expect(content).toContain('ChatDatabase');
    });

    it('should have models.py with Pydantic models', () => {
      const modelsPath = path.join(BACKEND_PATH, 'models.py');
      expect(fs.existsSync(modelsPath)).toBe(true);

      const content = fs.readFileSync(modelsPath, 'utf8');
      expect(content).toContain('BaseModel');
      expect(content).toContain('SystemStatus');
      expect(content).toContain('ChatRequest');
    });

    it('should use port 4200 as default (not 4001)', () => {
      const serverPath = path.join(BACKEND_PATH, 'server.py');
      const content = fs.readFileSync(serverPath, 'utf8');
      expect(content).toContain('4200');
      expect(content).not.toContain('4001');
    });
  });

  // ── Vite Build Configuration ───────────────────────────────────────

  describe('Vite build configuration', () => {
    let viteContent;

    beforeAll(() => {
      const vitePath = path.join(CHAT_APP_PATH, 'vite.config.ts');
      viteContent = fs.readFileSync(vitePath, 'utf8');
    });

    it('should output to dist directory', () => {
      expect(viteContent).toContain("outDir: 'dist'");
    });

    it('should clean output directory before build', () => {
      expect(viteContent).toContain('emptyOutDir: true');
    });

    it('should use relative base path for Electron compatibility', () => {
      expect(viteContent).toContain("base: './'");
    });

    it('should proxy API to backend during development', () => {
      expect(viteContent).toContain('proxy');
      expect(viteContent).toContain("'/api'");
      expect(viteContent).toContain('localhost:4200');
    });
  });

  // ── Throwaway App Installer Test ───────────────────────────────────

  describe('throwaway chat app structure validation', () => {
    const TEMP_APP_NAME = 'ci-chat-test';
    let tempAppPath;

    beforeAll(() => {
      tempAppPath = path.join(os.tmpdir(), `gaia-${TEMP_APP_NAME}-${Date.now()}`);
      fs.mkdirSync(path.join(tempAppPath, 'src'), { recursive: true });
    });

    afterAll(() => {
      if (tempAppPath && fs.existsSync(tempAppPath)) {
        fs.rmSync(tempAppPath, { recursive: true, force: true });
      }
    });

    it('should create valid app.config.json matching chat app', () => {
      const realConfig = JSON.parse(
        fs.readFileSync(path.join(CHAT_APP_PATH, 'app.config.json'), 'utf8')
      );

      const testConfig = {
        ...realConfig,
        name: TEMP_APP_NAME,
        displayName: 'CI Chat Test',
      };

      const configPath = path.join(tempAppPath, 'app.config.json');
      fs.writeFileSync(configPath, JSON.stringify(testConfig, null, 2));

      expect(fs.existsSync(configPath)).toBe(true);
      const written = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      expect(written.name).toBe(TEMP_APP_NAME);
      expect(written.window).toBeDefined();
      expect(written.window.width).toBeGreaterThanOrEqual(800);
    });

    it('should create valid package.json with Vite build', () => {
      const pkg = {
        name: `@gaia/${TEMP_APP_NAME}`,
        version: '0.1.0',
        private: true,
        type: 'module',
        scripts: {
          dev: 'vite',
          build: 'tsc && vite build',
          preview: 'vite preview',
        },
        dependencies: {
          react: '^18.2.0',
          'react-dom': '^18.2.0',
        },
        devDependencies: {
          typescript: '^5.3.3',
          vite: '^5.0.12',
        },
      };

      const pkgPath = path.join(tempAppPath, 'package.json');
      fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2));

      const written = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
      expect(written.scripts.build).toContain('vite build');
      expect(written.type).toBe('module');
    });

    it('should create valid index.html for Vite', () => {
      const html = `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>CI Chat Test</title>
</head>
<body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
</body>
</html>`;

      const htmlPath = path.join(tempAppPath, 'index.html');
      fs.writeFileSync(htmlPath, html);

      const content = fs.readFileSync(htmlPath, 'utf8');
      expect(content).toContain('id="root"');
      expect(content).toContain('type="module"');
    });

    it('should create minimal main.tsx entry', () => {
      const mainTsx = `import React from 'react';
import ReactDOM from 'react-dom/client';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <div>CI Chat Test</div>
  </React.StrictMode>,
);`;

      const mainPath = path.join(tempAppPath, 'src', 'main.tsx');
      fs.writeFileSync(mainPath, mainTsx);

      const content = fs.readFileSync(mainPath, 'utf8');
      expect(content).toContain('createRoot');
      expect(content).toContain("getElementById('root')");
    });

    it('should have valid throwaway structure', () => {
      expect(fs.existsSync(path.join(tempAppPath, 'app.config.json'))).toBe(true);
      expect(fs.existsSync(path.join(tempAppPath, 'package.json'))).toBe(true);
      expect(fs.existsSync(path.join(tempAppPath, 'index.html'))).toBe(true);
      expect(fs.existsSync(path.join(tempAppPath, 'src', 'main.tsx'))).toBe(true);
    });
  });

  // ── Security for Distribution ──────────────────────────────────────

  describe('security for distribution', () => {
    it('should not contain hardcoded secrets or API keys', () => {
      const filesToCheck = [
        'app.config.json',
        'package.json',
        'src/services/api.ts',
        'src/stores/chatStore.ts',
      ];

      const secretPatterns = [
        /api[_-]?key\s*[:=]\s*["'][^"']+["']/i,
        /secret\s*[:=]\s*["'][^"']+["']/i,
        /password\s*[:=]\s*["'][^"']+["']/i,
        /token\s*[:=]\s*["'][A-Za-z0-9]{20,}["']/i,
      ];

      filesToCheck.forEach(file => {
        const filePath = path.join(CHAT_APP_PATH, file);
        if (fs.existsSync(filePath)) {
          const content = fs.readFileSync(filePath, 'utf8');
          secretPatterns.forEach(pattern => {
            expect(content).not.toMatch(pattern);
          });
        }
      });
    });

    it('should use relative API paths (no hardcoded external URLs)', () => {
      const apiPath = path.join(CHAT_APP_PATH, 'src/services/api.ts');
      const content = fs.readFileSync(apiPath, 'utf8');

      // API_BASE should be relative /api (proxied by Vite/Electron)
      expect(content).toContain("'/api'");

      // Should not hardcode full external URLs
      const urlMatches = content.match(/https?:\/\/(?!localhost)[^\s'"]+/g) || [];
      expect(urlMatches.length).toBe(0);
    });
  });

  // ── File Size Checks ───────────────────────────────────────────────

  describe('source file size validation', () => {
    it('should have reasonably sized TypeScript files (each < 50KB)', () => {
      const tsFiles = [
        'src/services/api.ts',
        'src/stores/chatStore.ts',
        'src/types/index.ts',
        'src/App.tsx',
      ];

      tsFiles.forEach(file => {
        const filePath = path.join(CHAT_APP_PATH, file);
        if (fs.existsSync(filePath)) {
          const stats = fs.statSync(filePath);
          expect(stats.size).toBeLessThan(50 * 1024);
        }
      });
    });

    it('should have reasonably sized component files (each < 100KB)', () => {
      const componentDir = path.join(CHAT_APP_PATH, 'src/components');
      if (fs.existsSync(componentDir)) {
        const files = fs.readdirSync(componentDir);
        files.forEach(file => {
          const filePath = path.join(componentDir, file);
          if (fs.statSync(filePath).isFile()) {
            const stats = fs.statSync(filePath);
            expect(stats.size).toBeLessThan(100 * 1024);
          }
        });
      }
    });
  });

  // ── Backend API Port Compatibility ─────────────────────────────────

  describe('backend API compatibility', () => {
    it('should proxy to port 4200 during development', () => {
      const vitePath = path.join(CHAT_APP_PATH, 'vite.config.ts');
      const content = fs.readFileSync(vitePath, 'utf8');
      expect(content).toContain('4200');
    });

    it('should not reference port 4001 anywhere (reserved)', () => {
      const filesToCheck = [
        'src/services/api.ts',
        'vite.config.ts',
        'app.config.json',
      ];

      filesToCheck.forEach(file => {
        const filePath = path.join(CHAT_APP_PATH, file);
        if (fs.existsSync(filePath)) {
          const content = fs.readFileSync(filePath, 'utf8');
          expect(content).not.toContain(':4001');
        }
      });
    });

    it('should match backend API endpoints', () => {
      const apiPath = path.join(CHAT_APP_PATH, 'src/services/api.ts');
      const apiContent = fs.readFileSync(apiPath, 'utf8');

      const serverPath = path.join(BACKEND_PATH, 'server.py');
      const serverContent = fs.readFileSync(serverPath, 'utf8');

      // Verify key endpoints exist in both frontend and backend
      const endpoints = [
        '/api/system/status',
        '/api/health',
        '/api/sessions',
        '/api/chat/send',
        '/api/documents',
      ];

      endpoints.forEach(endpoint => {
        // Backend should define the route
        const routePath = endpoint.replace('/api', '');
        expect(serverContent).toContain(routePath);

        // Frontend should call the endpoint
        expect(apiContent).toContain(routePath);
      });
    });
  });

  // ── App Config Backend Section ────────────────────────────────────

  describe('app.config.json backend configuration', () => {
    let config;

    beforeAll(() => {
      const configPath = path.join(CHAT_APP_PATH, 'app.config.json');
      config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    });

    it('should have backend section', () => {
      expect(config.backend).toBeDefined();
    });

    it('should specify backend command', () => {
      expect(config.backend.command).toBeDefined();
      expect(typeof config.backend.command).toBe('string');
    });

    it('should specify backend port matching 4200', () => {
      expect(config.backend.port).toBe(4200);
    });

    it('should have healthCheck endpoint', () => {
      expect(config.backend.healthCheck).toBeDefined();
      expect(config.backend.healthCheck).toContain('/api/health');
    });

    it('should have startup timeout', () => {
      expect(config.backend.startupTimeout).toBeDefined();
      expect(config.backend.startupTimeout).toBeGreaterThan(0);
    });
  });

  // ── App Config Installer Section ──────────────────────────────────

  describe('app.config.json installer configuration', () => {
    let config;

    beforeAll(() => {
      const configPath = path.join(CHAT_APP_PATH, 'app.config.json');
      config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    });

    it('should have installer section', () => {
      expect(config.installer).toBeDefined();
    });

    it('should have appId in reverse-DNS format', () => {
      expect(config.installer.appId).toBeDefined();
      expect(config.installer.appId).toMatch(/^com\.\w+\.\w+/);
    });

    it('should have productName', () => {
      expect(config.installer.productName).toBeDefined();
    });

    it('should have copyright notice', () => {
      expect(config.installer.copyright).toBeDefined();
      expect(config.installer.copyright).toContain('Copyright');
      expect(config.installer.copyright).toContain('Advanced Micro Devices');
    });

    it('should have NSIS configuration for Windows', () => {
      expect(config.installer.nsis).toBeDefined();
      expect(config.installer.nsis.oneClick).toBeDefined();
    });
  });

  // ── Electron Packaging Configuration ──────────────────────────────

  describe('Electron packaging configuration', () => {
    let pkg;
    let forgeConfig;

    beforeAll(() => {
      const packagePath = path.join(CHAT_APP_PATH, 'package.json');
      pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));

      // Forge config can be inline in package.json or in a separate file
      if (typeof pkg.config?.forge === 'string') {
        // External forge config file (e.g. "./forge.config.cjs")
        const forgeConfigPath = path.join(CHAT_APP_PATH, pkg.config.forge);
        expect(fs.existsSync(forgeConfigPath)).toBe(true);
        forgeConfig = require(forgeConfigPath);
      } else {
        forgeConfig = pkg.config?.forge || {};
      }
    });

    it('should have main field pointing to Electron entry', () => {
      expect(pkg.main).toBeDefined();
      // main can be .js or .cjs (CommonJS for Electron compatibility with ESM package)
      expect(pkg.main).toMatch(/main\.(c?js)$/);
    });

    it('should have Electron as devDependency', () => {
      expect(pkg.devDependencies.electron).toBeDefined();
    });

    it('should have Electron Forge CLI', () => {
      expect(pkg.devDependencies['@electron-forge/cli']).toBeDefined();
    });

    it('should have squirrel maker for Windows installer', () => {
      const makers = forgeConfig.makers;
      const squirrel = makers.find(m => m.name.includes('squirrel'));
      expect(squirrel).toBeDefined();
    });

    it('should have package script', () => {
      expect(pkg.scripts.package).toBeDefined();
      expect(pkg.scripts.package).toContain('build');
    });

    it('should have make script', () => {
      expect(pkg.scripts.make).toBeDefined();
      expect(pkg.scripts.make).toContain('build');
    });

    it('should have packager config with app name', () => {
      expect(forgeConfig.packagerConfig.name).toBeDefined();
    });

    it('should include dist in extraResource for packaged app', () => {
      const extraResource = forgeConfig.packagerConfig.extraResource;
      expect(extraResource).toBeDefined();
      expect(extraResource).toContain('./dist');
    });
  });

  // ── npm Package Configuration ──────────────────────────────────────

  describe('npm package configuration', () => {
    let pkg;

    beforeAll(() => {
      const packagePath = path.join(CHAT_APP_PATH, 'package.json');
      pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
    });

    it('should have scoped package name for npm', () => {
      expect(pkg.name).toMatch(/^@[\w-]+\/[\w-]+$/);
    });

    it('should have bin field with gaia-ui CLI entry', () => {
      expect(pkg.bin).toBeDefined();
      expect(pkg.bin['gaia-ui']).toBeDefined();
      expect(pkg.bin['gaia-ui']).toContain('bin/gaia-ui.mjs');
    });

    it('should have files field for npm publish', () => {
      expect(pkg.files).toBeDefined();
      expect(pkg.files).toContain('bin/');
      expect(pkg.files).toContain('dist/');
    });

    it('should have repository metadata', () => {
      expect(pkg.repository).toBeDefined();
      expect(pkg.repository.url).toContain('github.com');
    });

    it('should have homepage and bugs URLs', () => {
      expect(pkg.homepage).toBeDefined();
      expect(pkg.bugs).toBeDefined();
      expect(pkg.bugs.url).toContain('github.com');
    });

    it('should have keywords for discoverability', () => {
      expect(pkg.keywords).toBeDefined();
      expect(pkg.keywords.length).toBeGreaterThanOrEqual(5);
      expect(pkg.keywords).toContain('gaia');
      expect(pkg.keywords).toContain('amd');
    });

    it('should have prepublishOnly script', () => {
      expect(pkg.scripts.prepublishOnly).toBeDefined();
      expect(pkg.scripts.prepublishOnly).toContain('build');
    });

    it('should have CLI entry point file', () => {
      const cliPath = path.join(CHAT_APP_PATH, 'bin', 'gaia-ui.mjs');
      expect(fs.existsSync(cliPath)).toBe(true);
    });

    it('should have valid CLI entry with shebang', () => {
      const cliPath = path.join(CHAT_APP_PATH, 'bin', 'gaia-ui.mjs');
      const content = fs.readFileSync(cliPath, 'utf8');
      expect(content.startsWith('#!/usr/bin/env node')).toBe(true);
    });

    it('should use version from version.py (single source of truth)', () => {
      const versionPyPath = path.join(CHAT_APP_PATH, '..', '..', 'version.py');
      expect(fs.existsSync(versionPyPath)).toBe(true);

      const content = fs.readFileSync(versionPyPath, 'utf8');
      const match = content.match(/__version__\s*=\s*"([^"]+)"/);
      expect(match).not.toBeNull();
      expect(pkg.version).toBe(match[1]);
    });

    it('should have .npmignore for clean publishing', () => {
      const npmignorePath = path.join(CHAT_APP_PATH, '.npmignore');
      expect(fs.existsSync(npmignorePath)).toBe(true);

      const content = fs.readFileSync(npmignorePath, 'utf8');
      expect(content).toContain('src/');
      expect(content).toContain('node_modules/');
    });

    it('should not be marked private (publishable to npm)', () => {
      expect(pkg.private).toBeUndefined();
    });
  });

  // ── TypeScript Configuration ──────────────────────────────────────

  describe('TypeScript configuration', () => {
    it('should have valid parseable tsconfig.json', () => {
      const tsconfigPath = path.join(CHAT_APP_PATH, 'tsconfig.json');
      expect(fs.existsSync(tsconfigPath)).toBe(true);

      // tsconfig may have comments, so just verify it's readable
      const content = fs.readFileSync(tsconfigPath, 'utf8');
      expect(content.length).toBeGreaterThan(10);
      expect(content).toContain('compilerOptions');
    });
  });

  // ── Global Styles ─────────────────────────────────────────────────

  describe('global styles', () => {
    it('should have src/styles/index.css', () => {
      const cssPath = path.join(CHAT_APP_PATH, 'src/styles/index.css');
      expect(fs.existsSync(cssPath)).toBe(true);
    });

    it('should have non-empty index.css', () => {
      const cssPath = path.join(CHAT_APP_PATH, 'src/styles/index.css');
      const stats = fs.statSync(cssPath);
      expect(stats.size).toBeGreaterThan(0);
    });
  });
});
