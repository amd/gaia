// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Integration tests for Jira App
 * Tests app initialization, configuration loading, and MCP integration
 */

const path = require('path');
const fs = require('fs');

describe('Jira App Integration', () => {
  const jiraAppPath = path.join(__dirname, '../../src/gaia/apps/jira/webui');
  
  describe('app configuration', () => {
    it('should have valid app.config.json', () => {
      const configPath = path.join(jiraAppPath, 'app.config.json');
      expect(fs.existsSync(configPath)).toBe(true);
      
      const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      expect(config).toHaveProperty('name');
      expect(config).toHaveProperty('displayName');
      expect(config).toHaveProperty('version');
    });

    it('should have valid package.json', () => {
      const packagePath = path.join(jiraAppPath, 'package.json');
      expect(fs.existsSync(packagePath)).toBe(true);
      
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      expect(pkg).toHaveProperty('name');
      expect(pkg).toHaveProperty('version');
      expect(pkg).toHaveProperty('scripts');
      expect(pkg.scripts).toHaveProperty('start');
    });

    it('should have required dependencies', () => {
      const packagePath = path.join(jiraAppPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      
      // Check for critical dependencies
      expect(pkg.dependencies).toHaveProperty('electron-squirrel-startup');
      expect(pkg.dependencies).toHaveProperty('dotenv');
      expect(pkg.devDependencies).toHaveProperty('electron');
    });
  });

  describe('app structure', () => {
    it('should have main entry point', () => {
      const mainPath = path.join(jiraAppPath, 'src/main.js');
      expect(fs.existsSync(mainPath)).toBe(true);
    });

    it('should have app-controller', () => {
      const controllerPath = path.join(jiraAppPath, 'src/app-controller.js');
      expect(fs.existsSync(controllerPath)).toBe(true);
    });

    it('should have preload script', () => {
      const preloadPath = path.join(jiraAppPath, 'src/preload.js');
      expect(fs.existsSync(preloadPath)).toBe(true);
    });

    it('should have renderer directory', () => {
      const rendererPath = path.join(jiraAppPath, 'src/renderer');
      expect(fs.existsSync(rendererPath)).toBe(true);
    });

    it('should have services directory', () => {
      const servicesPath = path.join(jiraAppPath, 'src/services');
      expect(fs.existsSync(servicesPath)).toBe(true);
    });
  });

  describe('app services', () => {
    it('should have app-services.js', () => {
      const servicesPath = path.join(jiraAppPath, 'src/services/app-services.js');
      expect(fs.existsSync(servicesPath)).toBe(true);
    });

    it('should have window-manager.js', () => {
      const windowManagerPath = path.join(jiraAppPath, 'src/services/window-manager.js');
      expect(fs.existsSync(windowManagerPath)).toBe(true);
    });
  });

  describe('renderer components', () => {
    it('should have renderer.js', () => {
      const rendererPath = path.join(jiraAppPath, 'src/renderer/renderer.js');
      expect(fs.existsSync(rendererPath)).toBe(true);
    });

    it('should have components directory', () => {
      const componentsPath = path.join(jiraAppPath, 'src/renderer/components');
      expect(fs.existsSync(componentsPath)).toBe(true);
    });
  });

  describe('build configuration', () => {
    it('should have forge.config.js', () => {
      const forgeConfigPath = path.join(jiraAppPath, 'forge.config.js');
      expect(fs.existsSync(forgeConfigPath)).toBe(true);
    });

    it('should have valid forge configuration', () => {
      const forgeConfigPath = path.join(jiraAppPath, 'forge.config.js');
      // Just verify it's loadable JavaScript
      expect(() => require(forgeConfigPath)).not.toThrow();
    });
  });

  describe('npm scripts', () => {
    it('should have start script', () => {
      const packagePath = path.join(jiraAppPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      expect(pkg.scripts.start).toBeDefined();
    });

    it('should have package script', () => {
      const packagePath = path.join(jiraAppPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      expect(pkg.scripts.package).toBeDefined();
    });

    it('should have make script', () => {
      const packagePath = path.join(jiraAppPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      expect(pkg.scripts.make).toBeDefined();
    });
  });

  describe('dependency versions', () => {
    it('should use compatible Electron version', () => {
      const packagePath = path.join(jiraAppPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      
      const electronVersion = pkg.devDependencies.electron;
      expect(electronVersion).toMatch(/^\^?\d+\.\d+\.\d+$/);
    });

    it('should use compatible Electron Forge version', () => {
      const packagePath = path.join(jiraAppPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      
      const forgeCliVersion = pkg.devDependencies['@electron-forge/cli'];
      expect(forgeCliVersion).toMatch(/^\^?\d+\.\d+\.\d+$/);
    });
  });
});
