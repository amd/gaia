// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * What changes when GAIA runs as a Flatpak.
 *
 * `flatpak run` sets FLATPAK_ID inside the sandbox. Each launch is a new
 * sandbox with its own /tmp, so anything two launches must share goes under
 * $XDG_RUNTIME_DIR/app/<id>, which Flatpak shares between them.
 */

const path = require("path");

/** `appId` in electron-builder.yml, which is the Flatpak's ID. */
const APP_ID = "ai.amd.gaia";

/** The bus name xdg-desktop-portal owns while it keeps GNOME's Background Apps list. */
const BACKGROUND_MONITOR = "org.freedesktop.background.Monitor";

/** Where shareTmpDirAcrossLaunches() keeps the TMPDIR it replaced ("" if unset). */
const TMPDIR_BEFORE = "GAIA_FLATPAK_TMPDIR_BEFORE";

/**
 * Whether this process is GAIA's own Flatpak. Another app's sandbox (a
 * terminal inside a Flatpak IDE) sets FLATPAK_ID too, and is not it.
 * @returns {boolean}
 */
function isFlatpak(env = process.env) {
  return env.FLATPAK_ID === APP_ID;
}

/**
 * Whether the desktop is one whose Background Apps can list a hidden GAIA.
 * GNOME before 44 has no such list; tray-host.cjs asks the bus for
 * BACKGROUND_MONITOR to tell them apart.
 * @returns {boolean}
 */
function isGnomeFlatpak(env = process.env) {
  if (!isFlatpak(env)) return false;
  return String(env.XDG_CURRENT_DESKTOP || "")
    .split(":")
    .some((name) => name.toLowerCase() === "gnome");
}

/**
 * Chromium keeps its single-instance socket under TMPDIR. Point it at the
 * directory every launch of this Flatpak shares, or a second launch can't find
 * the first and starts a second GAIA. Call before requestSingleInstanceLock().
 * @returns {string | null} The shared directory, or null outside the Flatpak.
 */
function shareTmpDirAcrossLaunches(env = process.env) {
  if (!isFlatpak(env)) return null;
  if (!env.XDG_RUNTIME_DIR) {
    throw new Error(
      "GAIA runs as a Flatpak but XDG_RUNTIME_DIR is not set, so a second launch can't find the running one. " +
        `Start GAIA from your desktop's app launcher, or with \`flatpak run ${APP_ID}\`.`
    );
  }
  const shared = path.join(env.XDG_RUNTIME_DIR, "app", APP_ID);
  if (!(TMPDIR_BEFORE in env)) env[TMPDIR_BEFORE] = env.TMPDIR ?? "";
  env.TMPDIR = shared;
  return shared;
}

/**
 * Put TMPDIR back once the single-instance lock is held. The shared directory
 * is on /run/user, a small tmpfs, and the backend and its installers would
 * otherwise inherit it for downloads and archive extraction.
 */
function restoreTmpDir(env = process.env) {
  if (!(TMPDIR_BEFORE in env)) return;
  const before = env[TMPDIR_BEFORE];
  delete env[TMPDIR_BEFORE];
  if (before) env.TMPDIR = before;
  else delete env.TMPDIR;
}

module.exports = {
  APP_ID,
  BACKGROUND_MONITOR,
  isFlatpak,
  isGnomeFlatpak,
  restoreTmpDir,
  shareTmpDirAcrossLaunches,
};
