// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The dialog shown when GAIA's backend exits on its own. Every choice ends
 * somewhere: Restart relaunches the app, Quit quits it, and Save diagnostics
 * writes the bundle and comes back to the same choice.
 */

const RESTART = 0;
const SAVE_DIAGNOSTICS = 1;
const QUIT = 2;

// Colour codes the backend's logger writes; a dialog shows them as junk.
const ANSI_ESCAPE = /\u001b\[[0-9;]*[A-Za-z]/g;

function stripAnsi(text) {
  return String(text).replace(ANSI_ESCAPE, "");
}

/** The dialog's detail text: what happened, what to do, then the log tail. */
function crashDetail(code, signal, tailLines) {
  const how = signal ? `signal ${signal}` : `exit code ${code}`;
  const tail = (tailLines || []).map(stripAnsi).join("\n") || "(nothing was logged)";
  return (
    `GAIA's background service stopped unexpectedly (${how}). Your chats are saved.\n` +
    "Restart GAIA to continue. If it keeps happening, save diagnostics and attach " +
    "them to a bug report.\n\n" +
    `Last log lines:\n${tail}`
  );
}

/**
 * An AppImage runs from a mount that goes away when the app exits, so a
 * relaunch must start the AppImage file itself, not process.execPath.
 */
function relaunchOptions(env = process.env, argv = process.argv) {
  return env.APPIMAGE ? { execPath: env.APPIMAGE, args: argv.slice(1) } : undefined;
}

/**
 * Show the crash dialog until the user restarts or quits.
 *
 * @param {object} deps
 * @param {object} deps.dialog Electron's dialog module
 * @param {object} deps.app Electron's app module
 * @param {() => Promise<string>} deps.writeDiagnostics resolves to the bundle path
 * @param {() => void} deps.beforeQuit called before app.quit(), e.g. to skip the quit guard
 * @param {object} [deps.env] where an AppImage's own path is read from
 * @param {object} [deps.parent] window the dialog is modal to, e.g. mainWindow
 * @returns {Promise<"restart"|"quit">}
 */
async function showBackendCrashDialog({ dialog, app, code, signal, tailLines, writeDiagnostics, beforeQuit, env = process.env, parent }) {
  const detail = crashDetail(code, signal, tailLines);
  // Modal to GAIA's window and in front: unparented, it can open behind other apps.
  const owner = parent && !parent.isDestroyed() ? parent : null;
  if (owner) {
    if (owner.isMinimized()) owner.restore();
    owner.show();
    owner.focus();
  }
  const show = (box) => (owner && !owner.isDestroyed() ? dialog.showMessageBox(owner, box) : dialog.showMessageBox(box));
  for (;;) {
    const { response } = await show({
      type: "error",
      title: "GAIA stopped",
      message: "GAIA's background service stopped",
      detail,
      buttons: ["Restart GAIA", "Save diagnostics", "Quit"],
      defaultId: RESTART,
      cancelId: QUIT,
      noLink: true,
    });

    if (response === SAVE_DIAGNOSTICS) {
      try {
        const bundlePath = await writeDiagnostics();
        await show({
          type: "info",
          title: "Diagnostics saved",
          message: "Diagnostics saved",
          detail: `Attach this file to your bug report:\n${bundlePath}`,
          buttons: ["OK"],
        });
      } catch (err) {
        dialog.showErrorBox(
          "Could not save diagnostics",
          `Writing the diagnostics bundle failed: ${err.message}`
        );
      }
      continue;
    }

    const choice = response === RESTART ? "restart" : "quit";
    if (choice === "restart") app.relaunch(relaunchOptions(env));
    if (beforeQuit) beforeQuit();
    app.quit();
    return choice;
  }
}

module.exports = { showBackendCrashDialog, crashDetail, stripAnsi, relaunchOptions };
