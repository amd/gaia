// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Whether this desktop can show GAIA's tray icon at all.
 *
 * Windows and macOS always can. On Linux, Electron's tray is a
 * StatusNotifierItem, which shows only when a host owns the
 * `org.kde.StatusNotifierWatcher` name on the session bus. Stock GNOME has
 * none (its AppIndicator extension adds one), so hiding the window there
 * would leave GAIA running with no way back to it; GAIA minimizes instead.
 */

const { execFile } = require("child_process");

const WATCHER = "org.kde.StatusNotifierWatcher";
const PROBE_TIMEOUT_MS = 2000;

/** The two bus clients a Linux desktop ships, and how each reports NameHasOwner. */
const PROBES = [
  {
    cmd: "gdbus",
    args: [
      "call", "--session",
      "--dest", "org.freedesktop.DBus",
      "--object-path", "/org/freedesktop/DBus",
      "--method", "org.freedesktop.DBus.NameHasOwner", WATCHER,
    ],
    parse: (out) => /\(\s*true\s*,?\s*\)/.test(out) ? true : /\(\s*false\s*,?\s*\)/.test(out) ? false : null,
  },
  {
    cmd: "dbus-send",
    args: [
      "--session", "--print-reply", "--dest=org.freedesktop.DBus",
      "/org/freedesktop/DBus", "org.freedesktop.DBus.NameHasOwner", `string:${WATCHER}`,
    ],
    parse: (out) => /boolean\s+true/.test(out) ? true : /boolean\s+false/.test(out) ? false : null,
  },
];

function runCommand(cmd, args) {
  return new Promise((resolve, reject) => {
    execFile(cmd, args, { timeout: PROBE_TIMEOUT_MS, encoding: "utf8" }, (err, stdout, stderr) => {
      if (err) {
        err.stderr = stderr;
        reject(err);
        return;
      }
      resolve(stdout);
    });
  });
}

/**
 * @typedef {{available: boolean, reason: null | "no-host" | "check-failed", detail?: string}} TrayHost
 * `no-host`: the bus answered and nothing hosts tray icons.
 * `check-failed`: the bus couldn't be asked; treated as no tray, so closing never strands the app.
 */

/**
 * @param {{platform?: string, run?: (cmd: string, args: string[]) => Promise<string>}} [deps]
 * @returns {Promise<TrayHost>}
 */
async function probeTrayHost({ platform = process.platform, run = runCommand } = {}) {
  if (platform !== "linux") return { available: true, reason: null };
  const failures = [];
  for (const probe of PROBES) {
    let out;
    try {
      out = await run(probe.cmd, probe.args);
    } catch (err) {
      const why = err.code === "ENOENT" ? "not installed" : (err.stderr || err.message || String(err)).trim();
      failures.push(`${probe.cmd}: ${why}`);
      continue;
    }
    const owned = probe.parse(out);
    if (owned === true) return { available: true, reason: null };
    if (owned === false) return { available: false, reason: "no-host", detail: `nothing owns ${WATCHER}` };
    failures.push(`${probe.cmd}: unexpected reply ${JSON.stringify(out.trim())}`);
  }
  return { available: false, reason: "check-failed", detail: failures.join("; ") };
}

module.exports = { probeTrayHost, WATCHER, PROBE_TIMEOUT_MS };
