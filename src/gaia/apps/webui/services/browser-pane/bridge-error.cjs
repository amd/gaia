// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

"use strict";

/** An agent op's failure, with a code from BridgeErrorCode and a message written for the model. */
class BridgeError extends Error {
  /**
   * @param {import("./contract").BridgeErrorCode} code
   * @param {string} message
   * @param {Record<string, unknown>} [data]
   */
  constructor(code, message, data = {}) {
    super(message);
    this.code = code;
    this.data = data;
  }
}

module.exports = { BridgeError };
