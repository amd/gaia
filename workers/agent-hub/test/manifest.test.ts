// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from "vitest";

import { HttpError } from "../src/http";
import { compareSemver, parseManifest } from "../src/manifest";
import { latestVersion } from "../src/catalog";
import { sampleManifest } from "./fake-r2";

describe("parseManifest", () => {
  it("parses a valid manifest and defaults optional fields", () => {
    const m = parseManifest(sampleManifest());
    expect(m.id).toBe("chat");
    expect(m.version).toBe("0.1.0");
    expect(m.security_tier).toBe("verified");
    expect(m.requirements.platforms).toEqual(["win-x64", "linux-x64", "darwin-arm64"]);
    expect(m.interfaces.cli).toBe(true);
    expect(m.deprecated).toBe(false);
  });

  it("defaults security_tier to experimental when unspecified", () => {
    const yaml = [
      "id: mini",
      "name: Mini",
      "version: 1.0.0",
      "description: x",
      "author: AMD",
      "license: MIT",
      "language: python",
    ].join("\n");
    expect(parseManifest(yaml).security_tier).toBe("experimental");
  });

  it("defaults type to agent when unspecified", () => {
    expect(parseManifest(sampleManifest()).type).toBe("agent");
  });

  it.each(["agent", "app", "component"])("accepts type %s", (pkgType) => {
    expect(parseManifest(`${sampleManifest()}type: ${pkgType}\n`).type).toBe(pkgType);
  });

  it("rejects an unknown type with the valid values named", () => {
    expect(() => parseManifest(`${sampleManifest()}type: plugin\n`)).toThrow(
      /agent, app, component/
    );
  });

  it.each([
    ["missing required field", "id: x\nname: y\n"],
    ["invalid id", sampleManifest({ id: "Bad_Id" })],
    ["invalid semver", sampleManifest({ version: "1.0" })],
    ["unknown language", sampleManifest({ language: "rust" })],
    ["unknown security_tier", sampleManifest({ security_tier: "gold" })],
  ])("rejects %s", (_label, yaml) => {
    expect(() => parseManifest(yaml)).toThrow(HttpError);
  });

  it("rejects an unknown platform", () => {
    const yaml = sampleManifest().replace("win-x64, linux-x64, darwin-arm64", "win-x64, sparc");
    expect(() => parseManifest(yaml)).toThrow(/unknown platform/);
  });

  it("carries tools_count and defaults it to 0 when absent", () => {
    expect(parseManifest(sampleManifest({ tools_count: "9" })).tools_count).toBe(9);

    const noToolsCount = sampleManifest()
      .split("\n")
      .filter((l) => !l.startsWith("tools_count:"))
      .join("\n");
    expect(parseManifest(noToolsCount).tools_count).toBe(0);
  });

  it.each([
    ["negative tools_count", "tools_count: -1"],
    ["non-integer tools_count", "tools_count: 1.5"],
    ["non-numeric tools_count", "tools_count: lots"],
  ])("rejects %s", (_label, line) => {
    const yaml = sampleManifest().replace(/^tools_count:.*$/m, line);
    expect(() => parseManifest(yaml)).toThrow(/tools_count must be an integer/);
  });

  it("carries permissions and defaults to [] when absent", () => {
    const yaml = sampleManifest() + "permissions: [filesystem:read, network:none]\n";
    expect(parseManifest(yaml).permissions).toEqual(["filesystem:read", "network:none"]);
    expect(parseManifest(sampleManifest()).permissions).toEqual([]);
  });

  it("rejects non-list permissions", () => {
    const yaml = sampleManifest() + "permissions: everything\n";
    expect(() => parseManifest(yaml)).toThrow(/permissions must be a list of strings/);
  });

  it("carries deprecation_message and leaves it undefined when absent", () => {
    const yaml = sampleManifest() + 'deprecation_message: "Use chat-v2 instead."\n';
    expect(parseManifest(yaml).deprecation_message).toBe("Use chat-v2 instead.");
    expect(parseManifest(sampleManifest()).deprecation_message).toBeUndefined();
  });

  it("fully populates requirements with defaults for omitted fields", () => {
    const m = parseManifest(sampleManifest());
    expect(m.requirements).toEqual({
      platforms: ["win-x64", "linux-x64", "darwin-arm64"],
      min_memory_gb: 8,
      min_disk_gb: 0,
      min_context_size: 0,
      npu: false,
      gpu_vram_gb: 0,
    });
  });

  it("fully populates requirements when the block is absent entirely", () => {
    const yaml = [
      "id: mini",
      "name: Mini",
      "version: 1.0.0",
      "description: x",
      "author: AMD",
      "license: MIT",
      "language: python",
    ].join("\n");
    expect(parseManifest(yaml).requirements).toEqual({
      platforms: [],
      min_memory_gb: 0,
      min_disk_gb: 0,
      min_context_size: 0,
      npu: false,
      gpu_vram_gb: 0,
    });
  });
});

describe("compareSemver", () => {
  it("orders core versions", () => {
    expect(compareSemver("1.0.0", "0.9.9")).toBeGreaterThan(0);
    expect(compareSemver("0.1.0", "0.2.0")).toBeLessThan(0);
    expect(compareSemver("1.2.3", "1.2.3")).toBe(0);
  });

  it("ranks releases above their pre-releases", () => {
    expect(compareSemver("1.0.0", "1.0.0-rc.1")).toBeGreaterThan(0);
    expect(compareSemver("1.0.0-alpha", "1.0.0-beta")).toBeLessThan(0);
    expect(compareSemver("1.0.0-rc.2", "1.0.0-rc.10")).toBeLessThan(0);
  });

  it("latestVersion picks the highest", () => {
    expect(latestVersion(["0.1.0", "1.2.0", "0.9.9", "1.10.0"])).toBe("1.10.0");
    expect(latestVersion(["2.0.0-rc.1", "2.0.0"])).toBe("2.0.0");
  });
});
