// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The dialog shown when GAIA's window stops working (its page crashed, or the
 * PC ran out of memory) while GAIA itself runs on. The details stay in the
 * log; the user gets plain words and a way back to the window.
 */

const RELOAD = 0;

/** What the user reads, for Electron's render-process-gone `reason`. */
function rendererGoneDetail(reason) {
  const why =
    reason === "oom"
      ? "The window ran out of memory. "
      : reason === "killed"
        ? "Something on this PC closed the window's process. "
        : "";
  return (
    `${why}GAIA itself is still running, so your chats and any work in progress are safe.\n\n` +
    "Press Reload window to carry on. If this keeps happening, close other apps to free memory, then restart GAIA."
  );
}

/**
 * Ask whether to reload the window or quit.
 *
 * @param {object} deps
 * @param {object} deps.dialog Electron's dialog module
 * @param {object|null} deps.parent the window whose page died, if it still exists
 * @param {string} deps.reason Electron's render-process-gone reason
 * @returns {Promise<"reload"|"quit">}
 */
async function showRendererGoneDialog({ dialog, parent, reason }) {
  const owner = parent && !parent.isDestroyed() ? parent : null;
  if (owner) {
    if (owner.isMinimized()) owner.restore();
    owner.show();
    owner.focus();
  }
  const box = {
    type: "warning",
    title: "GAIA",
    message: "GAIA's window stopped working",
    detail: rendererGoneDetail(reason),
    buttons: ["Reload window", "Quit GAIA"],
    defaultId: RELOAD,
    // Closing the box must not quit GAIA.
    cancelId: RELOAD,
    noLink: true,
  };
  const { response } = owner ? await dialog.showMessageBox(owner, box) : await dialog.showMessageBox(box);
  return response === RELOAD ? "reload" : "quit";
}

module.exports = { showRendererGoneDialog, rendererGoneDetail };
