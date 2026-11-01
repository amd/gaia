// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The AppImage must not ship electron-builder's GPL/LGPL desktop-integration
// libraries. These tests run the patch against electron-builder's real
// appImageUtil.js and against synthetic sources, then execute the patched
// filter to prove what it keeps and drops.

const fs = require("node:fs");
const path = require("node:path");

const WEBUI = path.resolve(__dirname, "../../src/gaia/apps/webui");
const { EXCLUDED_LIBRARY_PREFIXES, MARKER, patchSource } = require(path.join(
  WEBUI,
  "scripts/strip-appimage-copyleft.cjs"
));

const STATIC_CALL = 'await (0, builder_util_1.copyDir)(libraries, path.join(stageDir, "usr", "lib"));';
const LEGACY_CALL = 'await (0, builder_util_1.copyDir)(runtimeLibraries, path.join(stageDir, "usr", "lib"));';
const SYNTHETIC = `async function a() {\n  ${STATIC_CALL}\n}\nasync function b() {\n  ${LEGACY_CALL}\n}\n`;

// Pull the generated filter out of the patched source and evaluate it.
function extractFilter(patched) {
  const start = patched.indexOf(MARKER) + MARKER.length;
  const end = patched.indexOf(" }", start);
  // eslint-disable-next-line no-eval
  return eval(patched.slice(start, end));
}

describe("strip-appimage-copyleft", () => {
  test("excludes exactly the GPL/LGPL libraries", () => {
    expect(EXCLUDED_LIBRARY_PREFIXES.sort()).toEqual(
      ["libappindicator", "libgconf", "libindicator", "libnotify"].sort()
    );
  });

  test("patches both library copy calls", () => {
    const { source, changed } = patchSource(SYNTHETIC);
    expect(changed).toBe(true);
    expect(source.split(MARKER).length - 1).toBe(2);
    expect(source).toContain("copyDir)(libraries, path.join(stageDir, \"usr\", \"lib\"), { filter:");
    expect(source).toContain("copyDir)(runtimeLibraries, path.join(stageDir, \"usr\", \"lib\"), { filter:");
  });

  test("filter drops copyleft libraries and keeps the rest", () => {
    const filter = extractFilter(patchSource(SYNTHETIC).source);
    const dropped = [
      "/x/lib/x64/libindicator.so.7",
      "/x/lib/x64/libappindicator.so.1",
      "/x/lib/x64/libgconf-2.so.4",
      "/x/lib/x64/libnotify.so.4",
    ];
    const kept = ["/x/lib/x64/libXss.so.1", "/x/lib/x64/libXtst.so.6", "/x/lib/x64"];
    for (const file of dropped) expect(filter(file)).toBe(false);
    for (const file of kept) expect(filter(file)).toBe(true);
  });

  test("is idempotent", () => {
    const once = patchSource(SYNTHETIC).source;
    const twice = patchSource(once);
    expect(twice.changed).toBe(false);
    expect(twice.source).toBe(once);
  });

  test("fails loudly when electron-builder's code no longer matches", () => {
    expect(() => patchSource("async function a() { await copyDir(other, dest); }")).toThrow(
      /no longer matches/
    );
  });

  test("fails loudly when only one of the two calls is present", () => {
    expect(() => patchSource(`async function a() {\n  ${STATIC_CALL}\n}\n`)).toThrow(/runtimeLibraries/);
  });

  const INSTALLED = path.join(WEBUI, "node_modules/app-builder-lib/out/targets/appimage/appImageUtil.js");
  // Needs the webui's node_modules (installed by `npm ci` in src/gaia/apps/webui).
  const withInstalled = fs.existsSync(INSTALLED) ? test : test.skip;

  withInstalled("applies to the installed electron-builder", () => {
    const target = INSTALLED;
    const original = fs.readFileSync(target, "utf8");
    const { source } = patchSource(original);
    expect(source.split(MARKER).length - 1).toBe(2);
    // The patched module must still parse.
    expect(() => new Function("require", "module", "exports", "__dirname", "__filename", source)).not.toThrow();
  });
});
