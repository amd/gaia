// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tests for the gaia:// deep-link parser (issue #1725).
 *
 * deep-link.cjs is pure (no Electron), so these run without a running app.
 */

"use strict";

const {
  parseDeepLink,
  extractDeepLinkFromArgv,
  dispatchDeepLink,
  buildInstallPrompt,
} = require("../../src/gaia/apps/webui/services/deep-link.cjs");

describe("parseDeepLink()", () => {
  test("parses a well-formed install link", () => {
    expect(parseDeepLink("gaia://hub/install/summarize")).toEqual({
      action: "install",
      agentId: "summarize",
    });
  });

  test("accepts ids with dots, dashes and underscores", () => {
    expect(parseDeepLink("gaia://hub/install/my-agent_v2.1").agentId).toBe(
      "my-agent_v2.1"
    );
  });

  test("decodes a percent-encoded id", () => {
    expect(parseDeepLink("gaia://hub/install/my%2Dagent").agentId).toBe(
      "my-agent"
    );
  });

  test("rejects a non-gaia scheme", () => {
    expect(() => parseDeepLink("https://hub/install/x")).toThrow(
      /must start with "gaia:\/\/"/
    );
  });

  test("rejects an empty / non-string input", () => {
    expect(() => parseDeepLink("")).toThrow(/Not a GAIA deep link/);
    expect(() => parseDeepLink(null)).toThrow(/Not a GAIA deep link/);
    expect(() => parseDeepLink(undefined)).toThrow(/Not a GAIA deep link/);
  });

  test("rejects an install link with no id", () => {
    expect(() => parseDeepLink("gaia://hub/install/")).toThrow(
      /missing an agent id/
    );
    expect(() => parseDeepLink("gaia://hub/install")).toThrow(
      /missing an agent id/
    );
  });

  test("rejects an id with illegal characters", () => {
    // URL normalization collapses the "../" (popping "install"), so this is
    // rejected as unrecognized rather than reaching id validation — either way
    // it is loudly refused, never silently installing "etc".
    expect(() => parseDeepLink("gaia://hub/install/../etc")).toThrow();
    expect(() => parseDeepLink("gaia://hub/install/a b")).toThrow(
      /invalid agent id/
    );
  });

  test("rejects extra path segments", () => {
    expect(() => parseDeepLink("gaia://hub/install/foo/bar")).toThrow(
      /extra path segments/
    );
  });

  test("rejects an unrecognized host/action", () => {
    expect(() => parseDeepLink("gaia://hub/uninstall/foo")).toThrow(
      /only gaia:\/\/hub\/install/
    );
    expect(() => parseDeepLink("gaia://settings/open")).toThrow(
      /only gaia:\/\/hub\/install/
    );
  });
});

describe("extractDeepLinkFromArgv()", () => {
  test("finds a gaia:// arg among other argv entries", () => {
    const argv = [
      "/path/to/electron",
      "main.cjs",
      "gaia://hub/install/summarize",
    ];
    expect(extractDeepLinkFromArgv(argv)).toBe("gaia://hub/install/summarize");
  });

  test("returns null when no gaia:// arg is present", () => {
    expect(extractDeepLinkFromArgv(["electron", "main.cjs"])).toBeNull();
  });

  test("returns null for a non-array input", () => {
    expect(extractDeepLinkFromArgv(undefined)).toBeNull();
    expect(extractDeepLinkFromArgv(null)).toBeNull();
  });
});

describe("dispatchDeepLink() — install confirmation gate (security)", () => {
  const command = { action: "install", agentId: "summarize" };

  test("installs only AFTER the user confirms the specific agent", async () => {
    const confirm = jest.fn().mockResolvedValue(true);
    const installAgent = jest.fn().mockResolvedValue({ status: "completed" });
    const focusWindow = jest.fn();

    const result = await dispatchDeepLink(command, {
      confirm,
      installAgent,
      focusWindow,
    });

    // Confirmation happened before install, and for THIS agent. Once
    // confirmed, the trust override is always sent — this out-of-band flow
    // already required its own explicit per-agent confirmation, and the
    // backend's non-verified gate now covers every non-verified agent, not
    // just native ones.
    expect(confirm).toHaveBeenCalledWith(command);
    expect(installAgent).toHaveBeenCalledWith("summarize", { trustNative: true });
    expect(confirm.mock.invocationCallOrder[0]).toBeLessThan(
      installAgent.mock.invocationCallOrder[0]
    );
    expect(result).toEqual({ installed: true });
  });

  test("does NOT install when the user declines — the core security guarantee", async () => {
    const confirm = jest.fn().mockResolvedValue(false);
    const installAgent = jest.fn();

    const result = await dispatchDeepLink(command, { confirm, installAgent });

    expect(installAgent).not.toHaveBeenCalled();
    expect(result).toEqual({ installed: false, reason: "declined" });
  });

  test("treats a non-true confirm result as a decline (no install)", async () => {
    const installAgent = jest.fn();
    // A dialog that resolves undefined/null (e.g. dismissed) must not install.
    await dispatchDeepLink(command, {
      confirm: jest.fn().mockResolvedValue(undefined),
      installAgent,
    });
    expect(installAgent).not.toHaveBeenCalled();
  });

  test("focuses the window before prompting", async () => {
    const calls = [];
    const focusWindow = jest.fn(() => calls.push("focus"));
    const confirm = jest.fn(() => {
      calls.push("confirm");
      return Promise.resolve(false);
    });

    await dispatchDeepLink(command, {
      confirm,
      installAgent: jest.fn(),
      focusWindow,
    });

    expect(calls).toEqual(["focus", "confirm"]);
  });

  test("propagates an install failure so the caller can surface it", async () => {
    const confirm = jest.fn().mockResolvedValue(true);
    const installAgent = jest
      .fn()
      .mockRejectedValue(new Error("checksum mismatch"));

    await expect(
      dispatchDeepLink(command, { confirm, installAgent })
    ).rejects.toThrow(/checksum mismatch/);
  });

  test("rejects an unsupported action without installing", async () => {
    const installAgent = jest.fn();
    await expect(
      dispatchDeepLink(
        { action: "uninstall", agentId: "x" },
        { confirm: jest.fn().mockResolvedValue(true), installAgent }
      )
    ).rejects.toThrow(/Unsupported deep-link action/);
    expect(installAgent).not.toHaveBeenCalled();
  });
});

describe("buildInstallPrompt() — informed trust prompt (security fix: gate ALL non-verified agents)", () => {
  test("a non-verified agent's prompt requires trust and says so", () => {
    const prompt = buildInstallPrompt({
      id: "sketchy",
      name: "Sketchy Agent",
      security_tier: "community",
      permissions: ["fs:read"],
    });
    expect(prompt.requiresTrust).toBe(true);
    expect(prompt.detail).toMatch(/not amd-verified/i);
    expect(prompt.detail).toMatch(/fs:read/);
  });

  test("a verified agent's prompt does not demand extra trust", () => {
    const prompt = buildInstallPrompt({
      id: "safe",
      name: "Safe Agent",
      security_tier: "verified",
      permissions: [],
    });
    expect(prompt.requiresTrust).toBe(false);
    expect(prompt.detail).not.toMatch(/not amd-verified/i);
  });

  test("an unknown/missing tier defaults to the least-trusted posture", () => {
    const prompt = buildInstallPrompt({ id: "unknown-tier", name: "Mystery" });
    expect(prompt.requiresTrust).toBe(true);
  });

  test("refuses to build a prompt (throws) rather than prompting blind when no entry is given", () => {
    expect(() => buildInstallPrompt(null)).toThrow();
    expect(() => buildInstallPrompt(undefined)).toThrow();
  });
});

describe("dispatchDeepLink() + buildInstallPrompt() — end-to-end trust posture (security fix)", () => {
  const command = { action: "install", agentId: "summarize" };

  test("(i) a non-verified agent's deep link requires the trust ack and installs with the trust option on consent", async () => {
    const entry = { id: "summarize", security_tier: "community", permissions: [] };
    expect(buildInstallPrompt(entry).requiresTrust).toBe(true);

    const installAgent = jest.fn().mockResolvedValue({ status: "completed" });
    const result = await dispatchDeepLink(command, {
      confirm: jest.fn().mockResolvedValue(true),
      installAgent,
    });

    expect(installAgent).toHaveBeenCalledWith("summarize", { trustNative: true });
    expect(result).toEqual({ installed: true });
  });

  test("(ii) declining a non-verified agent's trust ack does not install", async () => {
    const entry = { id: "summarize", security_tier: "experimental", permissions: [] };
    expect(buildInstallPrompt(entry).requiresTrust).toBe(true);

    const installAgent = jest.fn();
    const result = await dispatchDeepLink(command, {
      confirm: jest.fn().mockResolvedValue(false),
      installAgent,
    });

    expect(installAgent).not.toHaveBeenCalled();
    expect(result).toEqual({ installed: false, reason: "declined" });
  });

  test("(iii) a verified agent's prompt does not demand extra trust, and still installs with the trust option once confirmed", async () => {
    const entry = { id: "summarize", security_tier: "verified", permissions: [] };
    expect(buildInstallPrompt(entry).requiresTrust).toBe(false);

    const installAgent = jest.fn().mockResolvedValue({ status: "completed" });
    const result = await dispatchDeepLink(command, {
      confirm: jest.fn().mockResolvedValue(true),
      installAgent,
    });

    expect(installAgent).toHaveBeenCalledWith("summarize", { trustNative: true });
    expect(result).toEqual({ installed: true });
  });
});
