// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Without a system library the Linux binary dies before it can draw anything.
// These run the real wrapper script against stand-in `ldd` / `ldconfig` tools.

const { spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const { launchWrapper, X11_OPENED_BY_NAME, HELP_URL } = require("../../installer/scripts/linux-launch-wrapper.cjs");

const REAL = ".gaia-desktop-bin";
const onPosix = process.platform !== "win32";
const suite = onPosix ? describe : describe.skip;

let dir;
let tools;

const script = (file, body) => {
  fs.writeFileSync(file, `#!/bin/sh\n${body}\n`);
  fs.chmodSync(file, 0o755);
};

const LINKED_OK = "\tlibgtk-3.so.0 => /lib64/libgtk-3.so.0 (0x00007f0000000000)";
const cacheWith = (...libs) => libs.map((lib) => `\t${lib} (libc6,x86-64) => /lib64/${lib}`).join("\n");

/** Stand-ins for the system tools, first on PATH. */
function tool(name, output) {
  script(path.join(tools, name), `cat <<'EOF'\n${output}\nEOF`);
}

function run(args = [], env = {}) {
  const result = spawnSync(path.join(dir, "gaia-desktop"), args, {
    env: { PATH: `${tools}:/usr/bin:/bin`, ...env },
    encoding: "utf8",
  });
  const shown = path.join(dir, "shown");
  return { ...result, shown: fs.existsSync(shown) ? fs.readFileSync(shown, "utf8") : "" };
}

suite("the Linux launch wrapper", () => {
  beforeEach(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-wrapper-"));
    tools = path.join(dir, "tools");
    fs.mkdirSync(tools);
    script(path.join(dir, "gaia-desktop"), launchWrapper(REAL).replace(/^#!\/bin\/sh\n/, ""));
    script(path.join(dir, REAL), 'echo "STARTED $*"');
    // Records what the user would have been shown.
    script(path.join(tools, "zenity"), `echo "$*" > "${path.join(dir, "shown")}"`);
    tool("ldd", LINKED_OK);
    tool("ldconfig", cacheWith(...X11_OPENED_BY_NAME, "libgtk-3.so.0"));
  });

  afterEach(() => fs.rmSync(dir, { recursive: true, force: true }));

  test("with every library present GAIA starts, with --no-sandbox ahead of its arguments", () => {
    const result = run(["gaia://open", "--flag"]);
    expect(result.status).toBe(0);
    expect(result.stdout.trim()).toBe("STARTED --no-sandbox gaia://open --flag");
    expect(result.stderr).toBe("");
    expect(result.shown).toBe("");
  });

  test("on X11, a library Chromium opens by name is reported instead of a silent abort", () => {
    tool("ldconfig", cacheWith("libgtk-3.so.0", "libX11.so.6"));
    const result = run();
    expect(result.status).toBe(1);
    expect(result.stdout).toBe("");
    expect(result.stderr).toContain("GAIA can't start");
    expect(result.stderr).toContain(`needs to start: ${X11_OPENED_BY_NAME.join(", ")}.`);
    expect(result.stderr).toContain("Install it with your software manager");
    expect(result.stderr).toContain(HELP_URL);
    // The same words reach the desktop, where nobody sees stderr.
    expect(result.shown).toContain(X11_OPENED_BY_NAME[0]);
    expect(result.shown).toContain(HELP_URL);
  });

  test("on Wayland the X11-only library isn't needed, so GAIA starts without it", () => {
    tool("ldconfig", cacheWith("libgtk-3.so.0"));
    expect(run([], { XDG_SESSION_TYPE: "wayland" }).stdout).toContain("STARTED");
    expect(run(["--ozone-platform=wayland"], { XDG_SESSION_TYPE: "x11" }).stdout).toContain("STARTED");
    // Told to use X11 inside a Wayland session, it is needed again.
    const forced = run(["--ozone-platform=x11"], { XDG_SESSION_TYPE: "wayland" });
    expect(forced.status).toBe(1);
    expect(forced.stderr).toContain(X11_OPENED_BY_NAME[0]);
    // A library the binary links against is needed everywhere.
    tool("ldd", "	libnss3.so => not found");
    expect(run([], { XDG_SESSION_TYPE: "wayland" }).stderr).toContain("needs to start: libnss3.so.");
  });

  test("libraries the binary links against are named, all of them", () => {
    tool("ldd", [LINKED_OK, "\tlibnss3.so => not found", "\tlibgbm.so.1 => not found"].join("\n"));
    const result = run();
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("needs to start: libnss3.so, libgbm.so.1.");
  });

  test("a library the AppImage bundles on LD_LIBRARY_PATH counts as present", () => {
    tool("ldconfig", cacheWith("libgtk-3.so.0"));
    const bundled = path.join(dir, "usr", "lib");
    fs.mkdirSync(bundled, { recursive: true });
    for (const lib of X11_OPENED_BY_NAME) fs.writeFileSync(path.join(bundled, lib), "");
    const result = run([], { LD_LIBRARY_PATH: `${path.join(dir, "none")}:${bundled}` });
    expect(result.status).toBe(0);
    expect(result.stdout).toContain("STARTED");
  });

  test("with no library list to read, GAIA starts instead of guessing", () => {
    tool("ldconfig", "");
    const result = run();
    expect(result.status).toBe(0);
    expect(result.stdout).toContain("STARTED");
  });

  test("with no dialog tool that works, the message still reaches stderr", () => {
    for (const name of ["zenity", "kdialog", "notify-send"]) script(path.join(tools, name), "exit 1");
    tool("ldconfig", cacheWith("libgtk-3.so.0"));
    const result = run();
    expect(result.status).toBe(1);
    expect(result.stderr).toContain(X11_OPENED_BY_NAME[0]);
  });
});

test("after-pack.cjs writes this wrapper", () => {
  const afterPack = fs.readFileSync(path.join(__dirname, "../../installer/scripts/after-pack.cjs"), "utf8");
  expect(afterPack).toContain("launchWrapper(realBinaryName)");
  expect(launchWrapper(REAL)).toMatch(/^#!\/bin\/sh\n/);
  expect(launchWrapper(REAL)).toContain(`exec "$bin" --no-sandbox "$@"`);
});
