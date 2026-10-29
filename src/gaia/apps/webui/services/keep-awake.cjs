// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Keep the PC from idle-sleeping while GAIA works (#5226).
 *
 * The renderer decides when (a turn is running, the setting is on, and the
 * battery policy allows it) and calls `power:keep-awake`; this service owns
 * the one powerSaveBlocker. The display may still turn off — only system
 * sleep is held off.
 */

const { ipcMain, powerSaveBlocker } = require("electron");

class KeepAwake {
  constructor() {
    /** @type {number | null} */
    this._blockerId = null;
    ipcMain.handle("power:keep-awake", (_event, on) => this.set(on));
  }

  /**
   * @param {boolean} on
   * @returns {boolean} whether sleep is now being held off.
   */
  set(on) {
    if (typeof on !== "boolean") {
      throw new TypeError(`power:keep-awake expects a boolean, got ${typeof on}`);
    }
    if (on && this._blockerId === null) {
      this._blockerId = powerSaveBlocker.start("prevent-app-suspension");
      console.log("[power] Holding off idle sleep while GAIA works");
    } else if (!on && this._blockerId !== null) {
      powerSaveBlocker.stop(this._blockerId);
      this._blockerId = null;
      console.log("[power] Idle sleep allowed again");
    }
    return this.active;
  }

  /** @returns {boolean} */
  get active() {
    return this._blockerId !== null && powerSaveBlocker.isStarted(this._blockerId);
  }

  destroy() {
    this.set(false);
    ipcMain.removeHandler("power:keep-awake");
  }
}

module.exports = KeepAwake;
