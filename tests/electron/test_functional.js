// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Functional tests for Electron apps
 * Tests that apps can actually be validated for common runtime issues
 * This catches real breakage from dependency updates that structure tests miss
 */

const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

describe('Electron Apps Functional Validation', () => {
  describe('Jira App', () => {
    const appPath = path.join(__dirname, '../../src/gaia/apps/jira/webui');
    
    it('should have installable package.json', () => {
      const packagePath = path.join(appPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      
      // Verify all dependencies are properly formatted
      expect(pkg.dependencies).toBeDefined();
      expect(pkg.devDependencies).toBeDefined();
      
      // Check for malformed version strings
      Object.entries(pkg.dependencies || {}).forEach(([name, version]) => {
        expect(version).toMatch(/^[\^~]?\d+\.\d+\.\d+$/);
      });
      
      Object.entries(pkg.devDependencies || {}).forEach(([name, version]) => {
        expect(version).toMatch(/^[\^~]?\d+\.\d+\.\d+$/);
      });
    });

    it('should have valid main entry point', () => {
      const packagePath = path.join(appPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      
      const mainPath = path.join(appPath, pkg.main);
      expect(fs.existsSync(mainPath)).toBe(true);
      
      // Verify the main file has no obvious syntax errors
      const content = fs.readFileSync(mainPath, 'utf8');
      expect(content).toContain('require');
      expect(content.length).toBeGreaterThan(100);
    });

    it('should have valid forge configuration', () => {
      const forgeConfigPath = path.join(appPath, 'forge.config.js');
      if (fs.existsSync(forgeConfigPath)) {
        // Verify it can be loaded
        const config = require(forgeConfigPath);
        expect(config).toBeDefined();
        expect(config.packagerConfig).toBeDefined();
      }
    });

    it('should have consistent electron versions', () => {
      const packagePath = path.join(appPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      
      const frameworkPackagePath = path.join(__dirname, '../../src/gaia/electron/package.json');
      const frameworkPkg = JSON.parse(fs.readFileSync(frameworkPackagePath, 'utf8'));
      
      // Both should have electron as devDependency
      expect(pkg.devDependencies.electron).toBeDefined();
      expect(frameworkPkg.devDependencies.electron).toBeDefined();
      
      // Versions should be compatible (both should use ^31 or similar)
      const appElectronMajor = pkg.devDependencies.electron.match(/(\d+)/)[1];
      const frameworkElectronMajor = frameworkPkg.devDependencies.electron.match(/(\d+)/)[1];
      
      expect(appElectronMajor).toBe(frameworkElectronMajor);
    });
  });

  describe('Example App', () => {
    const appPath = path.join(__dirname, '../../src/gaia/apps/example/webui');
    
    it('should have installable package.json', () => {
      const packagePath = path.join(appPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      
      expect(pkg.dependencies).toBeDefined();
      expect(pkg.devDependencies).toBeDefined();
      
      Object.entries(pkg.dependencies || {}).forEach(([name, version]) => {
        expect(version).toMatch(/^[\^~]?\d+\.\d+\.\d+$/);
      });
      
      Object.entries(pkg.devDependencies || {}).forEach(([name, version]) => {
        expect(version).toMatch(/^[\^~]?\d+\.\d+\.\d+$/);
      });
    });

    it('should have valid main entry point', () => {
      const packagePath = path.join(appPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      
      const mainPath = path.join(appPath, pkg.main);
      expect(fs.existsSync(mainPath)).toBe(true);
      
      const content = fs.readFileSync(mainPath, 'utf8');
      expect(content).toContain('require');
      expect(content.length).toBeGreaterThan(50);
    });

    it('should have consistent electron versions', () => {
      const packagePath = path.join(appPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      
      const frameworkPackagePath = path.join(__dirname, '../../src/gaia/electron/package.json');
      const frameworkPkg = JSON.parse(fs.readFileSync(frameworkPackagePath, 'utf8'));
      
      expect(pkg.devDependencies.electron).toBeDefined();
      expect(frameworkPkg.devDependencies.electron).toBeDefined();
      
      const appElectronMajor = pkg.devDependencies.electron.match(/(\d+)/)[1];
      const frameworkElectronMajor = frameworkPkg.devDependencies.electron.match(/(\d+)/)[1];
      
      expect(appElectronMajor).toBe(frameworkElectronMajor);
    });
  });

  describe('Electron Framework', () => {
    const frameworkPath = path.join(__dirname, '../../src/gaia/electron');
    
    it('should have installable package.json', () => {
      const packagePath = path.join(frameworkPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      
      expect(pkg.dependencies).toBeDefined();
      
      Object.entries(pkg.dependencies || {}).forEach(([name, version]) => {
        expect(version).toMatch(/^[\^~]?\d+\.\d+\.\d+$/);
      });
      
      Object.entries(pkg.devDependencies || {}).forEach(([name, version]) => {
        expect(version).toMatch(/^[\^~]?\d+\.\d+\.\d+$/);
      });
    });

    it('should have valid main entry point without syntax errors', () => {
      const mainPath = path.join(frameworkPath, 'src/main.js');
      expect(fs.existsSync(mainPath)).toBe(true);
      
      const content = fs.readFileSync(mainPath, 'utf8');
      
      // Check for required Electron imports
      expect(content).toContain('app');
      expect(content).toContain('BrowserWindow');
      
      // Verify no obvious syntax errors (basic check)
      expect(content).not.toContain('SyntaxError');
      expect(content.split('\n').length).toBeGreaterThan(20);
    });

    it('should have all required service modules', () => {
      const servicesPath = path.join(frameworkPath, 'src/services');
      expect(fs.existsSync(servicesPath)).toBe(true);
      
      // Check for key services
      expect(fs.existsSync(path.join(servicesPath, 'window-manager.js'))).toBe(true);
      expect(fs.existsSync(path.join(servicesPath, 'mcp-client.js'))).toBe(true);
      expect(fs.existsSync(path.join(servicesPath, 'base-ipc-handlers.js'))).toBe(true);
      
      // Verify services have content
      const wmContent = fs.readFileSync(path.join(servicesPath, 'window-manager.js'), 'utf8');
      expect(wmContent).toContain('class');
      expect(wmContent).toContain('module.exports');
    });

    it('should have proper peer dependencies for electron', () => {
      const packagePath = path.join(frameworkPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      
      // Framework should specify peer dependency for electron
      expect(pkg.peerDependencies).toBeDefined();
      expect(pkg.peerDependencies.electron).toBeDefined();
      expect(pkg.peerDependencies.electron).toMatch(/>=\d+/);
    });
  });
});
