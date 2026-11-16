// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Jest setup file for Electron tests
 * Initializes test environment and global mocks
 */

// Suppress console output during tests unless debugging
if (!process.env.DEBUG) {
  global.console = {
    ...console,
    log: jest.fn(),
    debug: jest.fn(),
    info: jest.fn(),
    warn: jest.fn(),
    // Keep error for important messages
    error: console.error,
  };
}

// A throwaway home per test file: the services resolve ~/.gaia (tray-config.json,
// crash-log.json, agents/) from os.homedir() when first required, and a test
// must never read or write the developer's real one. Assigned, not spied, so
// jest.restoreAllMocks() in a test can't hand back the real home; Jest's
// sandboxed process.env never reaches os.homedir(), so HOME can't do this.
{
  const fs = require('fs');
  const os = require('os');
  const path = require('path');
  const realHomedir = os.homedir;
  const testHome = fs.mkdtempSync(path.join(os.tmpdir(), 'gaia-electron-home-'));
  os.homedir = () => testHome;
  afterAll(() => {
    os.homedir = realHomedir;
    fs.rmSync(testHome, { recursive: true, force: true });
  });
}

// Set test timeout
jest.setTimeout(10000);

// Global test utilities
global.testUtils = {
  /**
   * Create a mock Electron BrowserWindow
   */
  createMockWindow: () => ({
    webContents: {
      send: jest.fn(),
      on: jest.fn(),
      openDevTools: jest.fn(),
      loadFile: jest.fn(),
      loadURL: jest.fn(),
    },
    on: jest.fn(),
    show: jest.fn(),
    hide: jest.fn(),
    close: jest.fn(),
    isDestroyed: jest.fn(() => false),
    destroy: jest.fn(),
  }),

  /**
   * Create a mock app config
   */
  createMockConfig: (overrides = {}) => ({
    name: 'test-app',
    displayName: 'Test App',
    version: '1.0.0',
    window: {
      width: 1024,
      height: 768,
    },
    ...overrides,
  }),

  /**
   * Wait for async operations
   */
  wait: (ms) => new Promise(resolve => setTimeout(resolve, ms)),

  /**
   * Create a mock MCP message
   */
  createMockMCPMessage: (type, data = {}) => ({
    type,
    data,
    timestamp: Date.now(),
  }),
};
