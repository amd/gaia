// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Source-level contracts for main.cjs, which cannot be required without
 * launching Electron.
 */

const fs = require("fs");
const path = require("path");

const MAIN = fs.readFileSync(
  path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui", "main.cjs"),
  "utf8"
);

function whenReadyBody() {
  const start = MAIN.indexOf("app.whenReady().then(async () => {");
  expect(start).toBeGreaterThan(-1);
  return MAIN.slice(start);
}

function functionBody(name) {
  const start = MAIN.indexOf(`function ${name}(`);
  expect(start).toBeGreaterThan(-1);
  const ends = [MAIN.indexOf("\nfunction ", start + 1), MAIN.indexOf("\nasync function ", start + 1)]
    .filter((i) => i > -1);
  return MAIN.slice(start, ends.length ? Math.min(...ends) : undefined);
}

describe("external links go through the scheme allow-list", () => {
  test("setWindowOpenHandler checks the resolved URL before opening it", () => {
    const start = MAIN.indexOf("setWindowOpenHandler(");
    const handler = MAIN.slice(start, MAIN.indexOf("});", start));
    expect(handler.indexOf("isAllowedExternalUrl(url)")).toBeGreaterThan(-1);
    expect(handler.indexOf("isAllowedExternalUrl(url)")).toBeLessThan(
      handler.indexOf("openAllowedLink(url)")
    );
    expect(handler).toContain('return { action: "deny" }');
  });

  test("a will-navigate guard prevents navigation away from the app", () => {
    const start = MAIN.indexOf('webContents.on("will-navigate"');
    expect(start).toBeGreaterThan(-1);
    const handler = MAIN.slice(start, MAIN.indexOf("\n  });", start));
    expect(handler).toContain("event.preventDefault()");
    expect(handler.indexOf("isAllowedExternalUrl(url)")).toBeLessThan(
      handler.indexOf("openAllowedLink(url)")
    );
  });

  test("every openAllowedLink in main.cjs is behind the allow-list", () => {
    const calls = MAIN.match(/openAllowedLink\(url\);/g) || [];
    const guarded =
      MAIN.match(/if \(isAllowedExternalUrl\(url\)\) \{\s*openAllowedLink\(url\)/g) || [];
    expect(calls.length).toBeGreaterThan(0);
    expect(calls.length).toBe(guarded.length);
  });

  // An uncaught openExternal rejection is fatal (main-safety-net.cjs).
  test("main.cjs never calls shell.openExternal directly", () => {
    expect(MAIN).not.toMatch(/shell\.openExternal\(/);
    expect(functionBody("openAllowedLink")).toContain("openExternalLink(shell, url,");
  });
});

describe("backend port selection fails loudly", () => {
  test("a findFreePort failure is not papered over with the default port", () => {
    const body = functionBody("startBackend");
    expect(body).toContain("await portManager.findFreePort()");
    expect(body).not.toMatch(/backendPort\s*=\s*DEFAULT_BACKEND_PORT/);
    expect(body).toMatch(/catch \(err\) \{\s*throw new Error\(/);
  });

  test("a rejected startup routes to the fatal error dialog", () => {
    const body = whenReadyBody();
    expect(body).toContain("backendStarting = startBackend();");
    expect(body).toContain("backendProcess = await backendStarting;");
    expect(body).toMatch(/\}\)\.catch\(\(err\) => \{[^}]*_fatalHandler\(err\);/);
  });
});

describe("crash-loop safe mode (issue #938)", () => {
  test("safe mode is decided from the counter at the threshold", () => {
    expect(MAIN).toContain("const _crashCount = readCount(() => os.homedir());");
    expect(MAIN).toContain("const _inSafeMode = _crashCount >= SAFE_MODE_THRESHOLD;");
  });

  test("the counter is read after the safety net is installed and before the log tee", () => {
    const net = MAIN.indexOf("installSafetyNet({");
    const decide = MAIN.indexOf("const _inSafeMode =");
    const tee = MAIN.indexOf("if (!_inSafeMode) installMainLogTee();");
    expect(net).toBeGreaterThan(-1);
    expect(decide).toBeGreaterThan(net);
    expect(tee).toBeGreaterThan(decide);
  });

  test("the log tee runs only through the safe-mode guard", () => {
    expect(MAIN).not.toContain("(function installMainLogTee()");
    expect(MAIN.match(/installMainLogTee\(\)/g)).toHaveLength(2);
  });

  test("safe mode prompts first thing on ready and returns before any start-up work", () => {
    const body = whenReadyBody();
    const guard = body.indexOf("if (_inSafeMode) {");
    const ret = body.indexOf("return;", guard);
    const seed = body.indexOf("agentSeeder.seedBundledAgents()");
    expect(guard).toBeGreaterThan(-1);
    expect(body.slice(guard, ret)).toContain("await runSafeModePrompt({");
    expect(ret).toBeLessThan(seed);
    expect(ret).toBeLessThan(body.indexOf("cleanupOrphanedBackend()"));
  });
});

describe("the window opens before anything slow", () => {
  const position = (needle) => {
    const at = whenReadyBody().indexOf(needle);
    expect(at).toBeGreaterThan(-1);
    return at;
  };

  test("the window and tray exist before seeding, orphan cleanup, install or backend", () => {
    const window = position("createWindow();");
    expect(window).toBeLessThan(position("initializeServices();"));
    for (const later of [
      "agentSeeder.seedBundledAgents()",
      "await cleanupOrphanedBackend()",
      "engineInstall.run()",
      "backendStarting = startBackend();",
    ]) {
      expect(position("initializeServices();")).toBeLessThan(position(later));
    }
  });

  test("an install puts its screen up before the slow steps, and only then runs", () => {
    const decided = position("showingInstallScreen = !backendInstaller.isBackendCurrent();");
    const screen = position("if (showingInstallScreen) await loadApp();");
    expect(decided).toBeLessThan(screen);
    expect(screen).toBeLessThan(position("agentSeeder.seedBundledAgents()"));
    expect(position("await cleanupOrphanedBackend()")).toBeLessThan(position("engineInstall.run()"));
  });

  test("a current backend loads the app once, with no install page first", () => {
    expect(position("if (!showingInstallScreen) await loadApp();")).toBeGreaterThan(
      position("backendProcess = await backendStarting;")
    );
  });

  test("the install screen waits for the backend to answer, then loads the app", () => {
    const answered = position("backendReady = await waitForBackend(");
    const app = position("showingInstallScreen = false;");
    expect(answered).toBeLessThan(app);
    expect(app).toBeLessThan(whenReadyBody().indexOf("await loadApp();", app));
  });

  test("the app page carries the real port once the backend has one", () => {
    const body = functionBody("loadApp");
    expect(body).toContain(
      "showingInstallScreen ? INSTALL_SCREEN_QUERY : buildIndexQuery(backendPort)"
    );
    expect(position("trayManager.setBackendPort(backendPort)")).toBeGreaterThan(
      position("backendProcess = await backendStarting;")
    );
  });

  test("a reopened window on the macOS dock is wired before a long install can need it", () => {
    expect(position('app.on("activate"')).toBeLessThan(position("engineInstall.run()"));
  });

  test("the separate progress window and its native failure boxes are gone", () => {
    expect(MAIN).not.toMatch(/createProgressWindow|showFailureDialog|isBootstrapping/);
  });

  test("a quit stops the install, or releases one waiting on Try again, and waits for it", () => {
    const body = functionBody("cleanup");
    expect(body).toContain("const installStopped = engineInstall ? engineInstall.stop() : null;");
    expect(body).toContain("await Promise.all([installStopped, ");
  });

  test("SIGTERM is wired to quit past the work-in-progress question", () => {
    expect(MAIN).toContain("quitOnTermSignal({ proc: process, app, guard: quitGuard });");
  });

  test("a failed install is shown even after a start in the tray", () => {
    const body = functionBody("showInstallFailure");
    expect(body).toContain("win.show()");
    expect(body).toContain("_fatalHandler(err)");
  });
});
