// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Mock electron-updater module for Jest tests.
 * Provides a minimal EventEmitter-based autoUpdater singleton.
 */

const { EventEmitter } = require('events');

class MockAutoUpdater extends EventEmitter {
  constructor() {
    super();
    this.autoDownload = true;
    this.autoInstallOnAppQuit = true;
    this.disableWebInstaller = false;
    this.allowDowngrade = false;
    this.allowPrerelease = false;
    this.logger = null;
    this._feedURL = null;
  }

  setFeedURL(opts) {
    this._feedURL = opts;
  }

  getFeedURL() {
    return this._feedURL;
  }

  async checkForUpdates() {
    return null;
  }

  quitAndInstall() {}
}

const autoUpdater = new MockAutoUpdater();

module.exports = { autoUpdater };
