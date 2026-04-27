// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * electron-builder afterPack hook for GAIA Agent UI.
 *
 * Ports the locale-pruning logic from the retired forge.config.cjs
 * `postPackage` hook. Chromium ships ~50 locale .pak files that add
 * ~45 MB to the install size. GAIA is English-only so we strip every
 * locale except en-US.
 *
 * electron-builder calls this after copying the Electron binary and
 * bundled app files to `context.appOutDir`. The locales/ directory
 * lives in different places per-platform:
 *
 *   Windows: <appOutDir>/locales/<lang>.pak
 *   Linux:   <appOutDir>/locales/<lang>.pak
 *   macOS:   <appOutDir>/<productName>.app/Contents/Frameworks/
 *            Electron Framework.framework/Versions/A/Resources/<lang>.lproj/
 *
 * We walk `appOutDir` recursively looking for any directory named
 * "locales" (Windows/Linux) or any directory containing *.lproj
 * subdirectories (macOS), then delete the non-English entries.
 *
 * Reference: desktop-installer.mdx §7 Phase C.
 */

"use strict";

const fs = require("fs");
const path = require("path");

// Windows/Linux Chromium ships one .pak per locale. Keep en-US.pak only.
const KEEP_PAK = new Set(["en-US.pak"]);

// macOS Electron ships .lproj directories (one per locale). Keep en.lproj
// (and the base "Base.lproj" if present — it contains the default layouts).
const KEEP_LPROJ = new Set(["en.lproj", "Base.lproj"]);

/**
 * Recursively locate any directory named "locales" under `root`.
 * Returns an array of absolute paths.
 */
function findLocalesDirs(root) {
  const results = [];
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const abs = path.join(current, entry.name);
      if (entry.name === "locales") {
        results.push(abs);
        // Don't descend into locales/ — nothing else of interest.
        continue;
      }
      stack.push(abs);
    }
  }
  return results;
}

/**
 * Recursively locate any directory that *contains* *.lproj subdirectories
 * (typical for macOS frameworks). Returns an array of absolute paths to
 * the parent directories, not to the individual .lproj dirs themselves.
 */
function findLprojParents(root) {
  const results = new Set();
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      continue;
    }
    let hasLproj = false;
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const abs = path.join(current, entry.name);
      if (entry.name.endsWith(".lproj")) {
        hasLproj = true;
      } else {
        stack.push(abs);
      }
    }
    if (hasLproj) results.add(current);
  }
  return Array.from(results);
}

/**
 * Return the on-disk size of `p` (file or directory) in bytes.
 */
function sizeOf(p) {
  let total = 0;
  try {
    const stat = fs.statSync(p);
    if (stat.isFile()) return stat.size;
    if (stat.isDirectory()) {
      for (const entry of fs.readdirSync(p)) {
        total += sizeOf(path.join(p, entry));
      }
    }
  } catch {
    // missing / inaccessible — ignore
  }
  return total;
}

function prettyBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * Delete every file in `localesDir` whose name is not in KEEP_PAK.
 */
function pruneLocalesDir(localesDir) {
  let saved = 0;
  let removed = 0;
  let entries;
  try {
    entries = fs.readdirSync(localesDir);
  } catch {
    return { saved, removed };
  }
  for (const name of entries) {
    if (KEEP_PAK.has(name)) continue;
    const abs = path.join(localesDir, name);
    saved += sizeOf(abs);
    try {
      fs.rmSync(abs, { recursive: true, force: true });
      removed += 1;
    } catch (err) {
      console.warn(`[after-pack] failed to remove ${abs}: ${err.message}`);
    }
  }
  return { saved, removed };
}

/**
 * Delete every *.lproj directory under `parent` except those in KEEP_LPROJ.
 */
function pruneLprojParent(parent) {
  let saved = 0;
  let removed = 0;
  let entries;
  try {
    entries = fs.readdirSync(parent, { withFileTypes: true });
  } catch {
    return { saved, removed };
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    if (!entry.name.endsWith(".lproj")) continue;
    if (KEEP_LPROJ.has(entry.name)) continue;
    const abs = path.join(parent, entry.name);
    saved += sizeOf(abs);
    try {
      fs.rmSync(abs, { recursive: true, force: true });
      removed += 1;
    } catch (err) {
      console.warn(`[after-pack] failed to remove ${abs}: ${err.message}`);
    }
  }
  return { saved, removed };
}

module.exports = async function afterPack(context) {
  const root = context.appOutDir;
  const platform =
    (context.packager && context.packager.platform && context.packager.platform.name) ||
    process.platform;
  console.log(
    `[after-pack] pruning Chromium locales under ${root} (platform=${platform})`
  );

  let totalSaved = 0;
  let totalRemoved = 0;

  // Windows + Linux: locales/*.pak
  for (const dir of findLocalesDirs(root)) {
    const { saved, removed } = pruneLocalesDir(dir);
    totalSaved += saved;
    totalRemoved += removed;
    if (removed > 0) {
      console.log(
        `[after-pack] pruned ${removed} files (${prettyBytes(saved)}) from ${dir}`
      );
    }
  }

  // macOS: various *.lproj directories in the Electron framework bundle.
  for (const parent of findLprojParents(root)) {
    const { saved, removed } = pruneLprojParent(parent);
    totalSaved += saved;
    totalRemoved += removed;
    if (removed > 0) {
      console.log(
        `[after-pack] pruned ${removed} .lproj dirs (${prettyBytes(saved)}) from ${parent}`
      );
    }
  }

  console.log(
    `[after-pack] done — removed ${totalRemoved} locale entries, saved ${prettyBytes(
      totalSaved
    )}`
  );

  // ─── Delete chrome-sandbox for AppImage (issue #782) ─────────────────
  //
  // Chromium's SUID sandbox helper (`chrome-sandbox`) requires root-owned
  // mode 4755. AppImages extract unprivileged to a FUSE mount and cannot
  // chown, so the helper FATALs Chromium at launch:
  //
  //   FATAL:sandbox/linux/suid/client/setuid_sandbox_host.cc:166]
  //   The SUID sandbox helper binary was found, but is not configured
  //   correctly. Rather than run without sandboxing I'm aborting now.
  //
  // `linux.executableArgs: [--no-sandbox]` in electron-builder.yml covers
  // the `.desktop`-entry launch path, but users who run
  // `./gaia-agent-ui-*.AppImage` from the shell bypass the .desktop Exec=
  // line entirely. Deleting chrome-sandbox plus wrapping the binary (see
  // below) ensures --no-sandbox reaches Electron's native startup checks
  // on every launch path.
  //
  // Combined deb+AppImage builds: electron-builder runs afterPack on the
  // shared `linux-unpacked` appOutDir, so this delete affects the DEB
  // staging tree too. That is intentional and coordinated — the DEB
  // postinst already guards its chmod with `if [ -f chrome-sandbox ]`,
  // so it skips rather than failing, and the DEB .desktop entry also
  // gets --no-sandbox from `linux.executableArgs`. DEB CLI launches on
  // kernels where unprivileged userns is fully disabled (rare on 24.04+)
  // still need manual --no-sandbox; this is documented.
  const isLinux = context.electronPlatformName === "linux";
  if (isLinux) {
    const sandboxPath = path.join(root, "chrome-sandbox");
    try {
      fs.rmSync(sandboxPath, { force: true });
      console.log(
        `[after-pack] deleted chrome-sandbox at ${sandboxPath} (issue #782; Chromium will use userns sandbox)`,
      );
    } catch (err) {
      console.warn(
        `[after-pack] failed to delete chrome-sandbox at ${sandboxPath}: ${err.message}`,
      );
    }

    // ── Wrap binary to inject --no-sandbox into argv (issue #782) ──────────
    //
    // app.commandLine.appendSwitch('no-sandbox') in main.js runs after V8
    // starts, but Electron's sandbox checks (root-without-sandbox in
    // electron_main_delegate.cc, userns-sandbox in zygote_host_impl_linux.cc)
    // happen in native code before V8 — too early to be affected by JS.
    // --no-sandbox must be in the binary's own argv[] at process start.
    //
    // Solution: rename gaia-desktop → .gaia-desktop-bin and write a thin
    // POSIX shell wrapper named gaia-desktop that prepends --no-sandbox.
    // AppRun (AppImage) and the DEB .desktop Exec= both call gaia-desktop,
    // so every launch path gets the flag regardless of how the AppImage is
    // invoked (double-click, shell, .desktop, xdg-open, CI xvfb-run).
    //
    // The DEB .desktop already carries --no-sandbox via executableArgs —
    // passing it twice is harmless (Chromium ignores duplicate switches).
    const execName = "gaia-desktop";
    const binaryPath = path.join(root, execName);
    const realBinaryName = `.${execName}-bin`;
    const realBinaryPath = path.join(root, realBinaryName);
    try {
      if (fs.existsSync(binaryPath)) {
        fs.renameSync(binaryPath, realBinaryPath);
        const wrapper =
          `#!/bin/sh\n` +
          `exec "$(dirname "$(readlink -f "$0")")/${realBinaryName}" --no-sandbox "$@"\n`;
        fs.writeFileSync(binaryPath, wrapper);
        fs.chmodSync(binaryPath, 0o755);
        console.log(
          `[after-pack] wrapped ${execName} → ${realBinaryName} with --no-sandbox in argv`,
        );
      } else {
        console.warn(
          `[after-pack] binary not found at ${binaryPath} — skipping --no-sandbox wrapper`,
        );
      }
    } catch (err) {
      console.warn(
        `[after-pack] failed to create --no-sandbox wrapper: ${err.message}`,
      );
    }
  }
};
