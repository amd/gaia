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
  return quoted.replace(/\\/g, "\\\\").replace(/\n/g, "\\n");
}

/** A path as a plain string value (Icon=, TryExec=): "\" and a newline are escaped. */
function desktopString(file) {
  return file.replace(/\\/g, "\\\\").replace(/\n/g, "\\n");
}

/** Whether `dir` is `root` or inside it. */
function isInside(dir, root) {
  const rel = path.relative(path.resolve(root), path.resolve(dir));
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

/** A Desktop Entry string value with its escapes (\s \n \t \r \\) undone. */
function unescapeString(value) {
  const map = { s: " ", n: "\n", t: "\t", r: "\r", "\\": "\\" };
  return value.replace(/\\([sntr\\])/g, (_m, c) => map[c]);
}

/** The program an Exec= value runs: its first argument, unquoted. */
function execProgram(value) {
  const exec = unescapeString(value).trim();
  if (!exec.startsWith('"')) return exec.split(/\s+/)[0].replace(/%%/g, "%");
  let out = "";
  for (let i = 1; i < exec.length; i += 1) {
    if (exec[i] === "\\" && i + 1 < exec.length) out += exec[(i += 1)];
    else if (exec[i] === '"') break;
    else out += exec[i];
  }
  return out.replace(/%%/g, "%");
}

function samePath(a, b) {
  if (path.resolve(a) === path.resolve(b)) return true;
  try {
    return fs.realpathSync(a) === fs.realpathSync(b);
  } catch (err) {
    if (err.code === "ENOENT" || err.code === "ENOTDIR") return false;
    throw err;
  }
}

/**
 * Another entry in `dir` that launches this AppImage, written by an AppImage
 * integration tool or by hand. GAIA's own `DESKTOP_FILE` is not counted.
 */
function otherLauncherFor(dir, appImage) {
  if (!fs.existsSync(dir)) return null;
  for (const name of fs.readdirSync(dir).sort()) {
    if (!name.endsWith(".desktop") || name === DESKTOP_FILE) continue;
    const file = path.join(dir, name);
    if (!fs.statSync(file, { throwIfNoEntry: false })?.isFile()) continue;
    const lines = fs.readFileSync(file, "utf8").split("\n");
    if (lines.includes(OWNED)) continue;
    const targets = [];
    for (const line of lines) {
      if (line.startsWith("Exec=")) targets.push(execProgram(line.slice(5)));
      else if (line.startsWith("TryExec=")) targets.push(unescapeString(line.slice(8)).trim());
    }
    if (targets.some((target) => target && samePath(target, appImage))) return file;
  }
  return null;
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

  // An AppImage's own mount can be on XDG_DATA_DIRS; its bundled entry isn't an install.
  const systemDirs = (env.XDG_DATA_DIRS || "/usr/local/share:/usr/share")
    .split(":")
    .filter((dir) => dir && !(env.APPDIR && isInside(dir, env.APPDIR)));
  const packaged = systemDirs.find((dir) => fs.existsSync(path.join(dir, "applications", DESKTOP_FILE)));
  if (packaged) return { status: "package-installed", entry: path.join(packaged, "applications", DESKTOP_FILE) };

  const { entry, icon } = launcherPaths(env, home);
  const wanted = entryText(appImage, icon);
  try {
    const current = fs.existsSync(entry) ? fs.readFileSync(entry, "utf8") : null;
    if (current !== null && !current.split("\n").includes(OWNED)) return { status: "kept-existing", entry };
    // A second entry for the same AppImage shows GAIA twice in the app grid.
    const other = otherLauncherFor(path.dirname(entry), appImage);
    if (other) {
      if (current !== null) fs.rmSync(entry);
      return { status: "kept-existing", entry: other };
    }
    const image = fs.readFileSync(iconSource);
    const iconCurrent = fs.existsSync(icon) && fs.readFileSync(icon).equals(image);
    if (current === wanted && iconCurrent) return { status: "current", entry };
    fs.mkdirSync(path.dirname(icon), { recursive: true });
    fs.writeFileSync(icon, image);
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
