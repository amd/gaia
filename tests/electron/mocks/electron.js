// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Mock Electron module for testing
 * Provides minimal mock implementations of Electron APIs
 */

const { EventEmitter } = require('events');

class MockBrowserWindow extends EventEmitter {
  constructor(options = {}) {
    super();
    this.options = options;
    this._isDestroyed = false;
    
    this.webContents = {
      send: jest.fn(),
      on: jest.fn(),
      once: jest.fn(),
      removeListener: jest.fn(),
      loadFile: jest.fn(() => Promise.resolve()),
      loadURL: jest.fn(() => Promise.resolve()),
      openDevTools: jest.fn(),
      closeDevTools: jest.fn(),
      isDevToolsOpened: jest.fn(() => false),
      session: {
        clearCache: jest.fn((callback) => callback()),
      },
    };
  }

  loadFile(path) {
    return Promise.resolve();
  }

  loadURL(url) {
    return Promise.resolve();
  }

  show() {
    this.emit('show');
  }

  hide() {
    this.emit('hide');
  }

  close() {
    this._isDestroyed = true;
    this.emit('close');
  }

  destroy() {
    this._isDestroyed = true;
    this.emit('closed');
  }

  isDestroyed() {
    return this._isDestroyed;
  }

  focus() {
    this.emit('focus');
  }

  blur() {
    this.emit('blur');
  }

  static getAllWindows() {
    return [];
  }

  static getFocusedWindow() {
    return null;
  }
}

class MockApp extends EventEmitter {
  constructor() {
    super();
    this._isReady = false;
    this._isQuitting = false;
  }

  whenReady() {
    return Promise.resolve();
  }

  quit() {
    this._isQuitting = true;
    this.emit('before-quit');
    this.emit('will-quit');
    this.emit('quit');
  }

  exit(code = 0) {
    process.exit(code);
  }

  getPath(name) {
    const paths = {
      home: '/mock/home',
      appData: '/mock/appData',
      userData: '/mock/userData',
      temp: '/mock/temp',
      exe: '/mock/exe',
      module: '/mock/module',
      desktop: '/mock/desktop',
      documents: '/mock/documents',
      downloads: '/mock/downloads',
      music: '/mock/music',
      pictures: '/mock/pictures',
      videos: '/mock/videos',
      logs: '/mock/logs',
    };
    return paths[name] || `/mock/${name}`;
  }

  getName() {
    return 'MockApp';
  }

  getVersion() {
    return '1.0.0';
  }

  isReady() {
    return this._isReady;
  }

  focus() {
    this.emit('browser-window-focus');
  }
}

class MockIpcMain extends EventEmitter {
  constructor() {
    super();
    this._handlers = new Map();
  }

  handle(channel, handler) {
    this._handlers.set(channel, handler);
  }

  handleOnce(channel, handler) {
    this.once(channel, handler);
  }

  removeHandler(channel) {
    this._handlers.delete(channel);
  }

  // Simulate IPC invoke from renderer
  async simulateInvoke(channel, ...args) {
    const handler = this._handlers.get(channel);
    if (handler) {
      return await handler({ sender: { send: jest.fn() } }, ...args);
    }
    throw new Error(`No handler registered for channel: ${channel}`);
  }
}

class MockIpcRenderer extends EventEmitter {
  constructor() {
    super();
  }

  send(channel, ...args) {
    this.emit('send', channel, ...args);
  }

  invoke(channel, ...args) {
    return Promise.resolve({ channel, args });
  }

  sendSync(channel, ...args) {
    return { channel, args };
  }
}

const mockApp = new MockApp();
const mockIpcMain = new MockIpcMain();
const mockIpcRenderer = new MockIpcRenderer();

module.exports = {
  BrowserWindow: MockBrowserWindow,
  app: mockApp,
  ipcMain: mockIpcMain,
  ipcRenderer: mockIpcRenderer,
  
  // Additional Electron modules commonly used
  dialog: {
    showOpenDialog: jest.fn(() => Promise.resolve({ canceled: false, filePaths: [] })),
    showSaveDialog: jest.fn(() => Promise.resolve({ canceled: false, filePath: '' })),
    showMessageBox: jest.fn(() => Promise.resolve({ response: 0 })),
    showErrorBox: jest.fn(),
  },
  
  Menu: {
    setApplicationMenu: jest.fn(),
    buildFromTemplate: jest.fn(() => ({})),
  },
  
  Tray: jest.fn(),
  
  nativeTheme: {
    shouldUseDarkColors: false,
    themeSource: 'system',
  },
  
  shell: {
    openExternal: jest.fn(() => Promise.resolve()),
    openPath: jest.fn(() => Promise.resolve('')),
  },
  
  clipboard: {
    writeText: jest.fn(),
    readText: jest.fn(() => ''),
  },
  
  screen: {
    getPrimaryDisplay: jest.fn(() => ({
      bounds: { x: 0, y: 0, width: 1920, height: 1080 },
      workArea: { x: 0, y: 0, width: 1920, height: 1040 },
    })),
    getAllDisplays: jest.fn(() => []),
  },
};
