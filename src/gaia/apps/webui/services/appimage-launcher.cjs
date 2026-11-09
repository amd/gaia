// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A launcher for GAIA run as an AppImage.
 *
 * An AppImage installs nothing, so the desktop has no `.desktop` file to tie
 * GAIA's window to: GNOME lists it without a name or icon, and the app grid
 * has nothing to reopen it from. The `.deb` installs that file; here GAIA
 * writes its own into the user's applications folder on launch.
 */

const fs = require("fs");
const os = require("os");
const path = require("path");
const { notificationIconPath } = require("./app-icon.cjs");

// The window's app_id / WM_CLASS comes from the same package.json field.
const DESKTOP_FILE = require("../package.json").desktopName;
const APP_ID = DESKTOP_FILE.replace(/\.desktop$/, "");
/** Marks an entry as GAIA's own, so one the user or a tool wrote is never replaced. */
const OWNED = "X-GAIA-AppImage=true";

/**
 * A path as one quoted Exec= argument: the Desktop Entry spec escapes " ` $ \
 * inside the quotes, then escapes every backslash again as a string value.
 */
function desktopExecArg(file) {
  // "%" starts a field code (%f, %u…) anywhere in Exec=, so it is doubled.
  const quoted = `"${file.replace(/%/g, "%%").replace(/["`$\\]/g, (c) => `\\${c}`)}"`;
  return quoted.replace(/\\/g, "\\\\");
}

/** A path as a plain string value (Icon=, TryExec=), where only "\" is special. */
function desktopString(file) {
  return file.replace(/\\/g, "\\\\");
}

function launcherPaths(env, home) {
  const dataHome = env.XDG_DATA_HOME || path.join(home, ".local", "share");
  return {
    entry: path.join(dataHome, "applications", DESKTOP_FILE),
    icon: path.join(dataHome, "icons", "hicolor", "256x256", "apps", `${APP_ID}.png`),
  };
}

function entryText(appImage, icon) {
  return [
    "[Desktop Entry]",
    "Type=Application",
    "Name=GAIA",
    "Comment=Privacy-first agentic AI interface for AMD Ryzen AI",
    `Exec=${desktopExecArg(appImage)}`,
    // Hides the entry once the AppImage is deleted or moved.
    `TryExec=${desktopString(appImage)}`,
    `Icon=${desktopString(icon)}`,
    "Terminal=false",
    "Categories=Development;Utility;",
    `StartupWMClass=${APP_ID}`,
    OWNED,
    "",
  ].join("\n");
}

/**
 * Write or refresh the launcher for this AppImage.
 *
 * @param {{platform?: string, env?: NodeJS.ProcessEnv, home?: string, iconSource?: string}} [deps]
 * @returns {{status: "not-appimage" | "package-installed" | "kept-existing" | "current" | "written", entry?: string}}
 * @throws {Error} When the launcher or its icon can't be written.
 */
function installAppImageLauncher({
  platform = process.platform,
  env = process.env,
  home = os.homedir(),
  iconSource = notificationIconPath(),
} = {}) {
  const appImage = env.APPIMAGE;
  if (platform !== "linux" || !appImage) return { status: "not-appimage" };

  const systemDirs = (env.XDG_DATA_DIRS || "/usr/local/share:/usr/share").split(":").filter(Boolean);
  const packaged = systemDirs.find((dir) => fs.existsSync(path.join(dir, "applications", DESKTOP_FILE)));
  if (packaged) return { status: "package-installed", entry: path.join(packaged, "applications", DESKTOP_FILE) };

  const { entry, icon } = launcherPaths(env, home);
  const wanted = entryText(appImage, icon);
  try {
    const current = fs.existsSync(entry) ? fs.readFileSync(entry, "utf8") : null;
    if (current !== null && !current.split("\n").includes(OWNED)) return { status: "kept-existing", entry };
    if (current === wanted && fs.existsSync(icon)) return { status: "current", entry };
    fs.mkdirSync(path.dirname(icon), { recursive: true });
    fs.writeFileSync(icon, fs.readFileSync(iconSource));
    fs.mkdirSync(path.dirname(entry), { recursive: true });
    fs.writeFileSync(entry, wanted, "utf8");
  } catch (err) {
    throw new Error(
      `Could not write GAIA's launcher ${entry}: ${err.message}. ` +
        "GAIA still runs, but the desktop shows its window without a name or icon. " +
        "Check that the folder is writable."
    );
  }
  return { status: "written", entry };
}

module.exports = { installAppImageLauncher, desktopExecArg, launcherPaths, APP_ID, DESKTOP_FILE, OWNED };
