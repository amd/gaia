// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The electron-builder hook that records Flathub as the .flatpak's runtime
 * repo (installer/scripts/flatpak-runtime-repo.cjs). Without it the bundle
 * fails to install on a PC that has not added Flathub.
 */

const fs = require("fs");
const os = require("os");
const path = require("path");

const hook = require("../../installer/scripts/flatpak-runtime-repo.cjs");
const { recordRuntimeRepo, RUNTIME_REPO } = hook;

let dir;
beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-flatpak-test-"));
});
afterEach(() => {
  fs.rmSync(dir, { recursive: true, force: true });
});

/** Stands in for ostree and flatpak: build-bundle writes its output file. */
function fakeTools(calls) {
  return (cmd, args) => {
    calls.push([cmd, ...args]);
    if (cmd === "flatpak" && args[0] === "build-bundle") {
      fs.writeFileSync(args[args.length - 2], "rebundled");
    }
    return "";
  };
}

test("rewrites the bundle in place with Flathub as its runtime repo", () => {
  const bundle = path.join(dir, "gaia-agent-ui-1.2.3-x86_64.flatpak");
  fs.writeFileSync(bundle, "original");
  const calls = [];
  recordRuntimeRepo(bundle, "ai.amd.gaia", { exec: fakeTools(calls) });

  expect(calls.map(([cmd, sub]) => `${cmd} ${sub}`)).toEqual([
    "ostree init",
    "flatpak build-import-bundle",
    "flatpak build-bundle",
  ]);
  const [, , repo, imported] = calls[1];
  expect(imported).toBe(bundle);
  const rebundle = calls[2];
  expect(rebundle).toContain(`--runtime-repo=${RUNTIME_REPO}`);
  expect(RUNTIME_REPO).toBe("https://dl.flathub.org/repo/flathub.flatpakrepo");
  expect(rebundle.slice(-3)).toEqual([repo, expect.stringMatching(/\.flatpak$/), "ai.amd.gaia"]);
  expect(fs.readFileSync(bundle, "utf8")).toBe("rebundled");
  // The scratch repo is removed whether or not the rewrite worked.
  expect(fs.existsSync(repo)).toBe(false);
});

test("a failed rewrite leaves the bundle untouched and cleans up", () => {
  const bundle = path.join(dir, "app.flatpak");
  fs.writeFileSync(bundle, "original");
  let repo;
  const exec = (cmd, args) => {
    if (cmd === "ostree") repo = args[2].replace("--repo=", "");
    if (args[0] === "build-import-bundle") throw new Error("import failed");
    return "";
  };
  expect(() => recordRuntimeRepo(bundle, "ai.amd.gaia", { exec })).toThrow("import failed");
  expect(fs.readFileSync(bundle, "utf8")).toBe("original");
  expect(fs.existsSync(repo)).toBe(false);
});

test("a missing tool fails the build, naming the tool and where the build installs it", () => {
  const bundle = path.join(dir, "app.flatpak");
  fs.writeFileSync(bundle, "original");
  const realPath = process.env.PATH;
  process.env.PATH = dir;
  try {
    expect(() => recordRuntimeRepo(bundle, "ai.amd.gaia")).toThrow(
      /ostree is not installed.*Install the Flatpak build tools/s
    );
  } finally {
    process.env.PATH = realPath;
  }
});

test("the hook passes every other artifact through untouched", async () => {
  const deb = path.join(dir, "gaia-agent-ui-1.2.3-amd64.deb");
  fs.writeFileSync(deb, "deb");
  await expect(hook({ file: deb, packager: { appInfo: { id: "ai.amd.gaia" } } })).resolves.toBeUndefined();
  await expect(hook({ file: undefined })).resolves.toBeUndefined();
  expect(fs.readFileSync(deb, "utf8")).toBe("deb");
});

test("a .flatpak with no app ID fails instead of shipping without a runtime repo", async () => {
  await expect(hook({ file: path.join(dir, "app.flatpak"), packager: {} })).rejects.toThrow(/gave no app ID/);
});
