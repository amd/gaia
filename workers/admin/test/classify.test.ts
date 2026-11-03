// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from "vitest";
import { classifyAsset, isMainOrRcTag, isRcTag } from "../src/classify";

describe("classifyAsset", () => {
  it.each([
    ["latest.yml"],
    ["latest-linux.yml"],
    ["latest-mac.yml"],
    ["gaia-agent-ui-0.25.1-x64-setup.exe.blockmap"],
    ["amd_gaia-0.25.1-py3-none-any.whl.sigstore.json"],
    ["gaia-tui-SHA256SUMS.txt"],
    ["gaia-installers-SHA256SUMS.txt"],
    ["gaia-agent-release.json"],
    ["gaia-agent.yaml"],
  ])("treats %s as update/metadata, not an install", (name) => {
    expect(classifyAsset(name).kind).toBe("meta");
  });

  it.each([
    ["gaia-agent-ui-0.25.1-x64-setup.exe", "Windows", "Agent UI"],
    ["gaia-agent-ui-0.25.1-amd64.deb", "Linux", "Agent UI"],
    ["gaia-agent-ui-0.25.1-x86_64.AppImage", "Linux", "Agent UI"],
    ["gaia-agent-ui-0.25.1-arm64.dmg", "macOS", "Agent UI"],
    ["gaia-windows-setup.exe", "Windows", "Installer"],
    ["gaia-linux-setup.deb", "Linux", "Installer"],
    ["gaia-0.25.1-win-x64-setup.exe", "Windows", "Installer"],
    ["gaia_0.25.1_amd64.deb", "Linux", "Installer"],
    ["gaia-0.25.1.x86_64.rpm", "Linux", "Installer"],
    ["gaia-0.25.1-darwin-arm64.pkg", "macOS", "Installer"],
    ["GaiaInstaller_0.20.0.zip", "Windows", "Installer"],
    ["gaia-windows-amd64.exe", "Windows", "CLI / TUI"],
    ["gaia-linux-arm64", "Linux", "CLI / TUI"],
    ["gaia-darwin-arm64", "macOS", "CLI / TUI"],
    ["amd_gaia-0.25.1-py3-none-any.whl", "Cross-platform", "Python package"],
    ["amd_gaia-0.25.1.tar.gz", "Cross-platform", "Python package"],
    ["gaia-agent-linux-x64", "Linux", "Agents"],
    ["gaia-agent-win32-x64.exe", "Windows", "Agents"],
    ["gaia_agent_email-0.5.0-py3-none-any.whl", "Cross-platform", "Agents"],
    ["jira-0.1.0-setup.exe", "Windows", "Agents"],
  ])("classifies %s as %s / %s", (name, os, product) => {
    expect(classifyAsset(name)).toEqual({ kind: "install", os, product });
  });

  it("flags an unknown file as Unclassified instead of guessing", () => {
    const c = classifyAsset("mystery.bin");
    expect(c).toEqual({ kind: "install", os: "Unclassified", product: "Unclassified" });
  });
});

describe("tags", () => {
  it("recognises main and RC tags, rejects sub-package tags", () => {
    expect(isMainOrRcTag("v0.25.1")).toBe(true);
    expect(isMainOrRcTag("v0.25.1-rc3")).toBe(true);
    expect(isMainOrRcTag("v0.15.4.1")).toBe(true);
    expect(isMainOrRcTag("agent-pkg-email-v0.5.0")).toBe(false);
    expect(isMainOrRcTag("v0.25.1-beta1")).toBe(false);
    expect(isRcTag("v0.25.1-rc3")).toBe(true);
    expect(isRcTag("v0.25.1")).toBe(false);
  });
});
