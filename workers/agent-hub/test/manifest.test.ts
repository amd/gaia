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
