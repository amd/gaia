// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

"use strict";

/**
 * Resolve with `child` once the OS has started it, or reject with why it
 * could not be started (e.g. ENOENT, EACCES). No Electron imports, so it unit-tests.
 *
 * @param {import("child_process").ChildProcess} child
 * @param {object} opts
 * @param {string} opts.command the program that was spawned, for the error
 * @param {string} opts.logPath where the user should look next
 * @returns {Promise<import("child_process").ChildProcess>}
 */
function waitForSpawn(child, { command, logPath }) {
  const failure = (reason, cause) =>
    new Error(
      `GAIA's backend could not be started (${command}): ${reason}. ` +
        `Reinstall GAIA if the program is missing or blocked. Logs: ${logPath}`,
      cause ? { cause } : undefined
    );

  return new Promise((resolve, reject) => {
    const onSpawn = () => {
      child.removeListener("error", onError);
      if (Number.isInteger(child.pid) && child.pid > 0) {
        resolve(child);
      } else {
        reject(failure(`the operating system returned no process id (pid=${child.pid})`));
      }
    };
    const onError = (err) => {
      child.removeListener("spawn", onSpawn);
      reject(failure(err && err.message ? err.message : String(err), err));
    };
    child.once("spawn", onSpawn);
    child.once("error", onError);
  });
}

module.exports = { waitForSpawn };
