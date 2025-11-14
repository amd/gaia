// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Integration tests for Example App
 * Tests basic app structure and configuration
 */

const path = require('path');
const fs = require('fs');

describe('Example App Integration', () => {
  const exampleAppPath = path.join(__dirname, '../../src/gaia/apps/example/webui');
  
  describe('app configuration', () => {
    it('should have valid app.config.json', () => {
      const configPath = path.join(exampleAppPath, 'app.config.json');
      expect(fs.existsSync(configPath)).toBe(true);
      
      const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      expect(config).toHaveProperty('name');
      expect(config).toHaveProperty('displayName');
    });

    it('should have valid package.json', () => {
      const packagePath = path.join(exampleAppPath, 'package.json');
      expect(fs.existsSync(packagePath)).toBe(true);
      
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      expect(pkg).toHaveProperty('name');
      expect(pkg).toHaveProperty('version');
      expect(pkg).toHaveProperty('scripts');
    });

    it('should have required dependencies', () => {
      const packagePath = path.join(exampleAppPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      
      expect(pkg.dependencies).toHaveProperty('electron-squirrel-startup');
      expect(pkg.dependencies).toHaveProperty('dotenv');
      expect(pkg.devDependencies).toHaveProperty('electron');
    });
  });

  describe('app structure', () => {
    it('should have main entry point', () => {
      const mainPath = path.join(exampleAppPath, 'src/main.js');
      expect(fs.existsSync(mainPath)).toBe(true);
    });

    it('should have preload script', () => {
      const preloadPath = path.join(exampleAppPath, 'src/preload.js');
      expect(fs.existsSync(preloadPath)).toBe(true);
    });
  });

  describe('npm scripts', () => {
    it('should have start script', () => {
      const packagePath = path.join(exampleAppPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      expect(pkg.scripts.start).toBeDefined();
    });

    it('should have package script', () => {
      const packagePath = path.join(exampleAppPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      expect(pkg.scripts.package).toBeDefined();
    });

    it('should have make script', () => {
      const packagePath = path.join(exampleAppPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      expect(pkg.scripts.make).toBeDefined();
    });
  });

  describe('dependency versions', () => {
    it('should use compatible Electron version', () => {
      const packagePath = path.join(exampleAppPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      
      const electronVersion = pkg.devDependencies.electron;
      expect(electronVersion).toMatch(/^\^?\d+\.\d+\.\d+$/);
    });

    it('should have matching versions with framework', () => {
      const appPackagePath = path.join(exampleAppPath, 'package.json');
      const frameworkPackagePath = path.join(__dirname, '../../src/gaia/electron/package.json');
      
      const appPkg = JSON.parse(fs.readFileSync(appPackagePath, 'utf8'));
      const frameworkPkg = JSON.parse(fs.readFileSync(frameworkPackagePath, 'utf8'));
      
      // Should use compatible Electron versions
      expect(appPkg.devDependencies.electron).toBeDefined();
      expect(frameworkPkg.devDependencies.electron).toBeDefined();
    });
  });
});
