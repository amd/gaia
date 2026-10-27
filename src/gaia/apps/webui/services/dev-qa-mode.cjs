// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Dev QA mode: run an unpackaged build against a backend binary named by
 * GAIA_BIN without touching the user's installed GAIA. In this mode main
 * skips the installer, its state file, the gaia:// registration, the pidfile
 * and the orphan cleanup that would stop the installed app's backend.
 * A packaged build never enters it.
 */

"use strict";

const fs = require("fs");

/**
 * @param {{ isPackaged: boolean, env: Record<string, string | undefined>, exists?: (p: string) => boolean }} opts
 * @returns {{ gaiaBin: string } | null}
 */
function resolveDevQaMode({ isPackaged, env, exists = fs.existsSync }) {
  const gaiaBin = env.GAIA_BIN;
  if (isPackaged || !gaiaBin) return null;
  if (!exists(gaiaBin)) {
    throw new Error(
      `GAIA_BIN is set to "${gaiaBin}", which does not exist. Point it at a gaia executable ` +
        "(for example <venv>/bin/gaia), or unset it to run the normal install flow."
    );
  }
  return { gaiaBin };
}

module.exports = { resolveDevQaMode };
