// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * electron-builder `artifactBuildCompleted` hook: rewrite the .flatpak so it
 * names where its runtime comes from.
 *
 * electron-builder's bundle carries the app only. Installing it on a PC that
 * has not added Flathub fails with "runtime not found"; with the runtime repo
 * recorded in the bundle, Flatpak and GNOME Software offer to add it.
 */

const { execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const RUNTIME_REPO = "https://dl.flathub.org/repo/flathub.flatpakrepo";

function run(cmd, args) {
  try {
    return execFileSync(cmd, args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  } catch (err) {
    const why = err.code === "ENOENT" ? `${cmd} is not installed` : (err.stderr || err.message).trim();
    throw new Error(
      `Could not record the runtime repo in the Flatpak: \`${cmd} ${args.join(" ")}\` failed: ${why}. ` +
        "The Flatpak build needs flatpak and ostree on the build machine; see the " +
        "'Install the Flatpak build tools' step in .github/workflows/build-installers.yml."
    );
  }
}

/**
 * @param {string} bundle Path to a .flatpak file; rewritten in place.
 * @param {string} appId The Flatpak application ID inside it.
 */
function recordRuntimeRepo(bundle, appId, { exec = run } = {}) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-flatpak-"));
  try {
    const repo = path.join(work, "repo");
    const out = path.join(work, path.basename(bundle));
    exec("ostree", ["init", "--mode=archive-z2", `--repo=${repo}`]);
    exec("flatpak", ["build-import-bundle", repo, bundle]);
    exec("flatpak", ["build-bundle", `--runtime-repo=${RUNTIME_REPO}`, repo, out, appId]);
    fs.copyFileSync(out, bundle);
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
}

/** @param {{file?: string, packager?: {appInfo?: {id?: string}}}} context */
async function artifactBuildCompleted(context) {
  if (!context.file || !context.file.endsWith(".flatpak")) return;
  const appId = context.packager && context.packager.appInfo && context.packager.appInfo.id;
  if (!appId) {
    throw new Error(`Could not record the runtime repo in ${context.file}: electron-builder gave no app ID.`);
  }
  recordRuntimeRepo(context.file, appId);
  console.log(`[flatpak] recorded ${RUNTIME_REPO} as the runtime repo in ${path.basename(context.file)}`);
}

module.exports = artifactBuildCompleted;
module.exports.default = artifactBuildCompleted;
module.exports.recordRuntimeRepo = recordRuntimeRepo;
module.exports.RUNTIME_REPO = RUNTIME_REPO;
