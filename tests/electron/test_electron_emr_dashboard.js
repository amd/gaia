// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Integration tests for EMR Dashboard Electron wrapper
 * Tests app structure, configuration, and Electron compatibility
 */

const path = require('path');
const fs = require('fs');

describe('EMR Dashboard Integration', () => {
  const emrAppPath = path.join(__dirname, '../../src/gaia/agents/emr/dashboard/electron');

  describe('app configuration', () => {
    it('should have valid package.json', () => {
      const packagePath = path.join(emrAppPath, 'package.json');
      expect(fs.existsSync(packagePath)).toBe(true);

      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
      expect(pkg).toHaveProperty('name');
      expect(pkg).toHaveProperty('version');
      expect(pkg).toHaveProperty('main');
      expect(pkg.name).toBe('emr-dashboard-electron');
    });

    it('should have required dependencies', () => {
      const packagePath = path.join(emrAppPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));

      // EMR Dashboard uses electron as a direct dependency
      expect(pkg.dependencies).toHaveProperty('electron');
    });

    it('should have start script', () => {
      const packagePath = path.join(emrAppPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));

      expect(pkg.scripts).toHaveProperty('start');
      expect(pkg.scripts.start).toContain('electron');
    });
  });

  describe('app structure', () => {
    it('should have main entry point', () => {
      const mainPath = path.join(emrAppPath, 'main.js');
      expect(fs.existsSync(mainPath)).toBe(true);
    });

    it('should have valid main.js content', () => {
      const mainPath = path.join(emrAppPath, 'main.js');
      const content = fs.readFileSync(mainPath, 'utf8');

      // Check for required Electron imports
      expect(content).toContain('app');
      expect(content).toContain('BrowserWindow');

      // Check for proper security settings
      expect(content).toContain('contextIsolation');
      expect(content).toContain('nodeIntegration');
    });

    it('should have AMD logo asset', () => {
      const logoPath = path.join(emrAppPath, 'amd.png');
      expect(fs.existsSync(logoPath)).toBe(true);
    });
  });

  describe('security configuration', () => {
    it('should disable node integration in renderer', () => {
      const mainPath = path.join(emrAppPath, 'main.js');
      const content = fs.readFileSync(mainPath, 'utf8');

      // Node integration should be false for security
      expect(content).toMatch(/nodeIntegration:\s*false/);
    });

    it('should enable context isolation', () => {
      const mainPath = path.join(emrAppPath, 'main.js');
      const content = fs.readFileSync(mainPath, 'utf8');

      // Context isolation should be true for security
      expect(content).toMatch(/contextIsolation:\s*true/);
    });
  });

  describe('dependency versions', () => {
    it('should use valid Electron version format', () => {
      const packagePath = path.join(emrAppPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));

      const electronVersion = pkg.dependencies.electron;
      expect(electronVersion).toMatch(/^\^?\d+\.\d+\.\d+$/);
    });

    it('should use Electron 35+ (latest stable)', () => {
      const packagePath = path.join(emrAppPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));

      const electronVersion = pkg.dependencies.electron;
      const majorVersion = parseInt(electronVersion.match(/(\d+)/)[1]);

      // EMR Dashboard should use Electron 35+ for latest features
      expect(majorVersion).toBeGreaterThanOrEqual(35);
    });
  });

  describe('npm scripts', () => {
    it('should have start script for development', () => {
      const packagePath = path.join(emrAppPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));

      expect(pkg.scripts.start).toBeDefined();
    });

    it('should have start:dev script for development mode', () => {
      const packagePath = path.join(emrAppPath, 'package.json');
      const pkg = JSON.parse(fs.readFileSync(packagePath, 'utf8'));

      expect(pkg.scripts['start:dev']).toBeDefined();
      expect(pkg.scripts['start:dev']).toContain('development');
    });
  });
});
