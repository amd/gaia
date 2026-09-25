// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tests for the external-link policy (services/link-policy.cjs).
 *
 * The renderer is loaded from file://, so Chromium resolves a scheme-less
 * markdown link against that document BEFORE main.cjs sees it. The cases
 * below are the resolved forms an LLM-rendered link produces.
 */

const {
  isAllowedExternalUrl,
  isInAppNavigation,
  describeBlockedUrl,
} = require("../../src/gaia/apps/webui/services/link-policy.cjs");

const APP_URL =
  "file:///C:/app/resources/app.asar/dist/index.html?api=http%3A%2F%2Flocalhost%3A4200";

describe("isAllowedExternalUrl", () => {
  test.each([
    "https://amd-gaia.ai/docs",
    "http://localhost:4200/api/health",
    "HTTPS://AMD-GAIA.AI",
    "mailto:someone@example.com",
  ])("allows web/mail URL %s", (url) => {
    expect(isAllowedExternalUrl(url)).toBe(true);
  });

  test.each([
    // `/Windows/System32/calc.exe` in markdown resolves to this.
    "file:///C:/Windows/System32/calc.exe",
    // `//attacker/share/x.exe` resolves to a UNC fetch.
    "file://attacker/share/x.exe",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "ms-msdt:/id",
    "smb://attacker/share/x.exe",
    "gaia://hub/install/evil",
  ])("refuses %s", (url) => {
    expect(isAllowedExternalUrl(url)).toBe(false);
  });

  test.each([
    [""],
    [null],
    [undefined],
    [42],
    ["/Windows/System32/calc.exe"],
    ["//host/share/x.exe"],
  ])("refuses unresolvable input %p", (url) => {
    expect(isAllowedExternalUrl(url)).toBe(false);
  });
});

describe("isInAppNavigation", () => {
  test("a fragment on the loaded document is in-app", () => {
    expect(isInAppNavigation(`${APP_URL}#section`, APP_URL)).toBe(true);
  });

  test("a different path in the app bundle is NOT in-app", () => {
    expect(
      isInAppNavigation("file:///C:/app/resources/app.asar/dist/other.html", APP_URL)
    ).toBe(false);
  });

  test("a local binary is never in-app", () => {
    expect(isInAppNavigation("file:///C:/Windows/System32/calc.exe", APP_URL)).toBe(false);
  });

  test("a web URL is never in-app", () => {
    expect(isInAppNavigation("https://amd-gaia.ai", APP_URL)).toBe(false);
  });
});

describe("describeBlockedUrl", () => {
  test("names the URL and its scheme so the refusal is actionable", () => {
    const msg = describeBlockedUrl("file:///C:/Windows/System32/calc.exe");
    expect(msg).toContain("calc.exe");
    expect(msg).toContain("file");
    expect(msg).toMatch(/http, https and mailto/);
  });
});
