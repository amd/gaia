// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A packaged app's backend finds the `gaia` CLI it ships on PATH, so skills
// that run `gaia` (automations) load even when no installer added it (#7968).

"use strict";

const fs = require("fs");
const path = require("path");

const { envWithGaiaOnPath } = require("../../src/gaia/apps/webui/services/backend-installer.cjs");

const WEBUI = path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui");
const main = fs.readFileSync(path.join(WEBUI, "main.cjs"), "utf8");

describe("envWithGaiaOnPath", () => {
  test("Windows: prepends the core dir to the existing Path key", () => {
    const env = envWithGaiaOnPath(
      { Path: "C:\\Windows\\system32", OTHER: "1" },
      "C:\\Program Files\\GAIA\\resources\\core\\gaia.exe",
      "win32"
    );
    expect(env.Path).toBe("C:\\Program Files\\GAIA\\resources\\core;C:\\Windows\\system32");
    expect(env).not.toHaveProperty("PATH");
    expect(env.OTHER).toBe("1");
  });

  test("POSIX: prepends the core dir to PATH", () => {
    const env = envWithGaiaOnPath({ PATH: "/usr/bin:/bin" }, "/opt/GAIA/resources/core/gaia", "linux");
    expect(env.PATH).toBe("/opt/GAIA/resources/core:/usr/bin:/bin");
  });

  test("an empty PATH becomes just the core dir", () => {
    expect(envWithGaiaOnPath({}, "/opt/GAIA/resources/core/gaia", "linux").PATH).toBe(
      "/opt/GAIA/resources/core"
    );
  });

  test("does not mutate the env it was given", () => {
    const given = { PATH: "/usr/bin" };
    envWithGaiaOnPath(given, "/opt/core/gaia", "linux");
    expect(given.PATH).toBe("/usr/bin");
  });

  test("refuses a bare command name instead of guessing a directory", () => {
    expect(() => envWithGaiaOnPath({ PATH: "/usr/bin" }, "gaia", "linux")).toThrow(/absolute/);
  });
});

describe("the packaged backend gets the shipped gaia on PATH", () => {
  test("main.cjs builds the backend env through envWithGaiaOnPath when packaged", () => {
    const start = main.indexOf('["chat", "--ui", "--ui-port", String(backendPort)]');
    const end = main.indexOf("detached: false", start);
    expect(start).toBeGreaterThan(-1);
    expect(main.slice(start, end)).toContain(
      "...(app.isPackaged ? backendInstaller.envWithGaiaOnPath(process.env, gaiaCmd) : process.env)"
    );
  });
});
