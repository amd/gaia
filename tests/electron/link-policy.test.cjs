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
  describeOpenFailure,
  openExternalSafely,
  linkNoticeText,
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

describe("describeBlockedUrl in the OS language", () => {
  test("an Arabic OS gets the reason in Arabic with the URL kept left-to-right", () => {
    const msg = describeBlockedUrl("file:///C:/Windows/System32/calc.exe", "ar-EG");
    expect(msg).toContain("رفض GAIA فتح");
    expect(msg).toContain("\u2068file:///C:/Windows/System32/calc.exe\u2069");
    expect(msg).toContain("\u2068file\u2069");
    expect(msg).not.toMatch(/refused/);
  });

  test("a URL with no scheme is labelled in the user's language", () => {
    expect(describeBlockedUrl("not a url")).toContain("(no scheme)");
    expect(describeBlockedUrl("not a url", "ar")).toContain("بلا بروتوكول");
  });

  test("a language without a translation falls back to English", () => {
    expect(describeBlockedUrl("file:///x.exe", "fr-FR")).toMatch(/http, https and mailto/);
  });
});

describe("linkNoticeText", () => {
  test.each([
    ["ar", "تم حظر الرابط", "لم يُفتح الرابط", "حسنًا"],
    ["AR_sa", "تم حظر الرابط", "لم يُفتح الرابط", "حسنًا"],
    ["en-US", "Link blocked", "Link not opened", "OK"],
    ["de", "Link blocked", "Link not opened", "OK"],
    ["", "Link blocked", "Link not opened", "OK"],
    [undefined, "Link blocked", "Link not opened", "OK"],
  ])("%s → %s / %s / %s", (locale, blocked, notOpened, ok) => {
    const text = linkNoticeText(locale);
    expect(text.blockedTitle).toBe(blocked);
    expect(text.notOpenedTitle).toBe(notOpened);
    expect(text.ok).toBe(ok);
  });

  test("every language has every string the English table has", () => {
    const en = linkNoticeText("en");
    const ar = linkNoticeText("ar");
    expect(Object.keys(ar).sort()).toEqual(Object.keys(en).sort());
    for (const key of Object.keys(en)) expect(typeof ar[key]).toBe(typeof en[key]);
  });
});

describe("describeOpenFailure in the OS language", () => {
  test("an Arabic OS gets the reason in Arabic with the link and OS error isolated", () => {
    const msg = describeOpenFailure("mailto:a@b.c", new Error("No application."), "ar-EG");
    expect(msg).toContain("تعذّر على GAIA فتح");
    expect(msg).toContain("\u2068mailto:a@b.c\u2069");
    expect(msg).toContain("\u2068No application\u2069");
    expect(msg).not.toContain("..");
    expect(msg).not.toMatch(/Check that/);
  });

  test("defaults to English", () => {
    expect(describeOpenFailure("mailto:a@b.c", new Error("x"), "fr")).toMatch(/Check that this PC/);
  });
});

test("main.cjs shows both link notices in the OS language and logs them in English", () => {
  const main = require("fs")
    .readFileSync(require("path").join(__dirname, "..", "..", "src", "gaia", "apps", "webui", "main.cjs"), "utf8")
    .replace(/\r\n/g, "\n");
  expect(main).not.toMatch(/warnAboutLink\("Link (blocked|not opened)"/);
  expect(main).toMatch(/linkNoticeText\(locale\)\.blockedTitle/);
  expect(main).toMatch(/linkNoticeText\(locale\)\.notOpenedTitle/);
  expect(main).toMatch(/buttons: \[linkNoticeText\(locale\)\.ok\]/);
  expect(main).toMatch(/console\.error\(`\[main\] \$\{logMessage\}`\)/);
});

describe("openExternalSafely", () => {
  test("a mailto link on a PC with no mail app reports the failure instead of rejecting", async () => {
    const shell = { openExternal: jest.fn().mockRejectedValue(new Error("No application knows how to open the URL")) };
    const onFailure = jest.fn();
    await expect(openExternalSafely(shell, "mailto:someone@example.com", onFailure)).resolves.toBeUndefined();
    expect(onFailure).toHaveBeenCalledTimes(1);
    expect(onFailure.mock.calls[0][0].message).toMatch(/No application/);
  });

  test("a synchronous throw is reported the same way", async () => {
    const shell = { openExternal: jest.fn(() => { throw new Error("boom"); }) };
    const onFailure = jest.fn();
    await openExternalSafely(shell, "https://amd-gaia.ai", onFailure);
    expect(onFailure).toHaveBeenCalledTimes(1);
  });

  test("a link that opens does not report anything", async () => {
    const shell = { openExternal: jest.fn().mockResolvedValue(undefined) };
    const onFailure = jest.fn();
    await openExternalSafely(shell, "https://amd-gaia.ai", onFailure);
    expect(shell.openExternal).toHaveBeenCalledWith("https://amd-gaia.ai");
    expect(onFailure).not.toHaveBeenCalled();
  });
});

describe("describeOpenFailure", () => {
  test("names the link and the reason", () => {
    const msg = describeOpenFailure("mailto:someone@example.com", new Error("No application"));
    expect(msg).toContain("mailto:someone@example.com");
    expect(msg).toContain("No application");
  });

  test("does not double the period when the OS error already ends in one", () => {
    const msg = describeOpenFailure("mailto:a@b.c", new Error("No application."));
    expect(msg).toContain("No application. Check");
    expect(msg).not.toContain("..");
  });
});

test("main.cjs never calls shell.openExternal or the data: loadURL without handling a rejection", () => {
  const main = require("fs")
    .readFileSync(require("path").join(__dirname, "..", "..", "src", "gaia", "apps", "webui", "main.cjs"), "utf8")
    .replace(/\r\n/g, "\n");
  expect(main).not.toMatch(/shell\.openExternal\(/);
  expect(main).toMatch(/\.loadURL\("data:text\/html;charset=utf-8," \+ encodeURIComponent\(waitingHtml\)\)\s*\.catch\(/);
});
