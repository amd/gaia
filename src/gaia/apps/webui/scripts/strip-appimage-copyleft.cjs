// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// electron-builder copies a fixed set of desktop-integration libraries into
// every AppImage's usr/lib. Four of them are GPL/LGPL (libindicator is
// GPL-3.0; libappindicator, libgconf-2 and libnotify are LGPL), which GAIA
// must not ship. No electron-builder option turns the copy off, so this
// patches its copy step to skip them. Electron still dlopen()s the system's
// copies when a distro provides them.
//
// Run before every `electron-builder` invocation (see package.json). Fails
// loudly if electron-builder's code no longer matches, so an upgrade cannot
// silently start shipping the libraries again.

"use strict";

const fs = require("node:fs");
const path = require("node:path");

const EXCLUDED_LIBRARY_PREFIXES = ["libindicator", "libappindicator", "libgconf", "libnotify"];

const MARKER = "/* gaia: strip copyleft AppImage libraries */";

// The two places electron-builder copies its bundled libraries into usr/lib:
// the static-runtime toolset and the legacy FUSE2 toolset.
const COPY_CALLS = [
  'copyDir)(libraries, path.join(stageDir, "usr", "lib"))',
  'copyDir)(runtimeLibraries, path.join(stageDir, "usr", "lib"))',
];

const FILTER =
  `{ filter: ${MARKER} (file) => !${JSON.stringify(EXCLUDED_LIBRARY_PREFIXES)}` +
  ".some((prefix) => require(\"node:path\").basename(file).startsWith(prefix)) }";

function appImageUtilPath(webuiDir) {
  return path.join(webuiDir, "node_modules", "app-builder-lib", "out", "targets", "appimage", "appImageUtil.js");
}

function patchSource(source) {
  if (source.includes(MARKER)) {
    return { source, changed: false };
  }
  let patched = source;
  for (const call of COPY_CALLS) {
    if (!patched.includes(call)) {
      throw new Error(
        `strip-appimage-copyleft: electron-builder's AppImage library copy no longer matches \`${call}\`. ` +
          "The copyleft-library filter cannot be applied, so the AppImage would ship GPL/LGPL libraries. " +
          "Update COPY_CALLS in src/gaia/apps/webui/scripts/strip-appimage-copyleft.cjs for this electron-builder version."
      );
    }
    patched = patched.replace(call, call.slice(0, -1) + `, ${FILTER})`);
  }
  return { source: patched, changed: true };
}

function main() {
  const target = appImageUtilPath(path.resolve(__dirname, ".."));
  if (!fs.existsSync(target)) {
    throw new Error(
      `strip-appimage-copyleft: ${target} not found. Run \`npm ci\` in src/gaia/apps/webui before packaging.`
    );
  }
  const { source, changed } = patchSource(fs.readFileSync(target, "utf8"));
  if (changed) {
    fs.writeFileSync(target, source);
    console.log(`strip-appimage-copyleft: patched ${target}`);
  } else {
    console.log("strip-appimage-copyleft: already patched");
  }
}

module.exports = { EXCLUDED_LIBRARY_PREFIXES, MARKER, patchSource };

if (require.main === module) {
  main();
}
