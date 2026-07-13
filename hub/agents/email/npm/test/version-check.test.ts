// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/** checkVersion rejects an incompatible apiVersion MAJOR; accepts compatible. */

import { describe, expect, it } from "vitest";

import { EmailClient } from "../src/client.js";
import { checkVersion } from "../src/lifecycle.js";
import { VersionMismatchError } from "../src/errors.js";

function clientReturning(apiVersion: string, agentVersion = "0.2.0"): EmailClient {
  const fetchImpl = (async () =>
    new Response(JSON.stringify({ apiVersion, agentVersion }), {
      status: 200,
      headers: { "content-type": "application/json" },
    })) as unknown as typeof fetch;
  return new EmailClient({ baseUrl: "http://x", fetchImpl });
}

describe("checkVersion", () => {
  it("accepts apiVersion 2.0 against the current 2.1 default (same MAJOR 2)", async () => {
    // checkVersion enforces MAJOR only, so a 2.0 sidecar stays compatible with
    // this client's default-expected SCHEMA_VERSION (now 2.1).
    const info = await checkVersion(clientReturning("2.0"));
    expect(info.apiVersion).toBe("2.0");
  });

  it("accepts a higher MINOR within the same MAJOR 2 (backward-compatible)", async () => {
    const info = await checkVersion(clientReturning("2.5"), { expectedApiVersion: "2.0" });
    expect(info.apiVersion).toBe("2.5");
  });

  it("rejects a major-1 server (old sidecar — breaking)", async () => {
    await expect(
      checkVersion(clientReturning("1.0"), { expectedApiVersion: "2.0" }),
    ).rejects.toBeInstanceOf(VersionMismatchError);
  });

  it("rejects a major-3 server (future incompatible major)", async () => {
    await expect(
      checkVersion(clientReturning("3.0"), { expectedApiVersion: "2.0" }),
    ).rejects.toBeInstanceOf(VersionMismatchError);
  });
});
