// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Tests for the startup Chromium switches (services/chromium-switches.cjs). */

const fs = require("fs");
const path = require("path");

const {
  chromiumSwitches,
} = require("../../src/gaia/apps/webui/services/chromium-switches.cjs");

const names = (switches) => switches.map(([name]) => name);

describe("chromiumSwitches", () => {
  test("Linux keeps the sandbox and Wayland color-management workarounds", () => {
    expect(chromiumSwitches({ platform: "linux", env: {} })).toEqual([
      ["no-sandbox"],
      ["disable-features", "WaylandColorManagement"],
    ]);
  });

  test.each(["win32", "darwin"])("%s gets no switches by default", (platform) => {
    expect(chromiumSwitches({ platform, env: {} })).toEqual([]);
  });

  test.each(["1", "true", "TRUE", "yes", " 1 "])("GAIA_DISABLE_GPU=%p disables the GPU", (value) => {
    expect(names(chromiumSwitches({ platform: "linux", env: { GAIA_DISABLE_GPU: value } })))
      .toContain("disable-gpu");
  });

  test.each(["", "0", "false", "no"])("GAIA_DISABLE_GPU=%p leaves the GPU on", (value) => {
    expect(names(chromiumSwitches({ platform: "linux", env: { GAIA_DISABLE_GPU: value } })))
      .not.toContain("disable-gpu");
  });

  test("GAIA_DISABLE_GPU works on every platform", () => {
    for (const platform of ["linux", "win32", "darwin"]) {
      expect(names(chromiumSwitches({ platform, env: { GAIA_DISABLE_GPU: "1" } })))
        .toContain("disable-gpu");
    }
  });

  test("never forces an ozone platform, so --ozone-platform=x11 stays the user's call", () => {
    for (const platform of ["linux", "win32", "darwin"]) {
      const all = chromiumSwitches({ platform, env: { GAIA_DISABLE_GPU: "1" } });
      expect(names(all).some((n) => n.startsWith("ozone"))).toBe(false);
    }
  });
});

describe("main.cjs", () => {
  const MAIN = fs.readFileSync(
    path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui", "main.cjs"),
    "utf8"
  );

  test("applies chromiumSwitches before app.whenReady()", () => {
    const apply = MAIN.indexOf("chromiumSwitches({ platform: process.platform, env: process.env })");
    expect(apply).toBeGreaterThan(-1);
    expect(apply).toBeLessThan(MAIN.indexOf("app.whenReady()"));
  });

  test("does not append any ozone switch itself", () => {
    expect(MAIN).not.toMatch(/appendSwitch\(\s*["']ozone/);
  });
});
