// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Source-level contracts for main.cjs, which cannot be required without
 * launching Electron. The deep-link queue's behaviour is tested in
 * deep-link-queue.test.cjs; these pin that main.cjs actually uses it, and
 * uses it in the right order.
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

describe("main.cjs routes gaia:// links through the startup queue", () => {
  test("startup releases the queue only after waitForBackend resolves", () => {
    const body = whenReadyBody();
    const wait = body.indexOf("backendReady = await waitForBackend(");
    const drain = body.indexOf("processStartupDeepLinks(backendReady)");
    expect(wait).toBeGreaterThan(-1);
    expect(drain).toBeGreaterThan(wait);
  });

  test("the startup release delegates to the queue drain", () => {
    expect(functionBody("processStartupDeepLinks")).toContain("startupDeepLinks.drain(");
  });

  test("every inbound link goes through the queue, never straight to dispatch", () => {
    const body = functionBody("handleDeepLink");
    expect(body).toContain("startupDeepLinks.accept(rawUrl)");
    expect(body).not.toContain("runDeepLink(");
    // The old gate released links as soon as services existed — before the backend listened.
    expect(body).not.toMatch(/agentProcessManager/);
  });

  test("the queue dispatches through the confirm-gated install path", () => {
    const start = MAIN.indexOf("createStartupDeepLinkQueue({");
    const block = MAIN.slice(start, MAIN.indexOf("});", start));
    expect(block).toContain("runDeepLink(parseDeepLink(rawUrl))");
    expect(block).toContain("reportNotReady: reportDeepLinksNotReady");
  });

  test("a backend that never came up is reported loudly", () => {
    expect(functionBody("reportDeepLinksNotReady")).toMatch(
      /dialog\.showErrorBox\("GAIA is not ready yet"/
    );
  });
});

describe("external links go through the scheme allow-list", () => {
  test("setWindowOpenHandler checks the resolved URL before openExternal", () => {
    const start = MAIN.indexOf("setWindowOpenHandler(");
    const handler = MAIN.slice(start, MAIN.indexOf("});", start));
    expect(handler.indexOf("isAllowedExternalUrl(url)")).toBeGreaterThan(-1);
    expect(handler.indexOf("isAllowedExternalUrl(url)")).toBeLessThan(
      handler.indexOf("shell.openExternal(url)")
    );
    expect(handler).toContain('return { action: "deny" }');
  });

  test("a will-navigate guard prevents navigation away from the app", () => {
    const start = MAIN.indexOf('webContents.on("will-navigate"');
    expect(start).toBeGreaterThan(-1);
    const handler = MAIN.slice(start, MAIN.indexOf("\n  });", start));
    expect(handler).toContain("event.preventDefault()");
    expect(handler.indexOf("isAllowedExternalUrl(url)")).toBeLessThan(
      handler.indexOf("shell.openExternal(url)")
    );
  });

  test("every openExternal in main.cjs is behind the allow-list", () => {
    const calls = MAIN.match(/shell\.openExternal\(/g) || [];
    const guarded =
      MAIN.match(/if \(isAllowedExternalUrl\(url\)\) \{\s*shell\.openExternal\(url\)/g) || [];
    expect(calls.length).toBe(guarded.length);
  });
});
