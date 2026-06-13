// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/** checkVersion rejects an incompatible apiVersion MAJOR; accepts compatible. */

import { describe, expect, it } from "vitest";

import { EmailClient } from "../src/client.js";
import { checkVersion } from "../src/lifecycle.js";
import { VersionMismatchError } from "../src/errors.js";

function clientReturning(apiVersion: string, agentVersion = "0.1.0"): EmailClient {
  const fetchImpl = (async () =>
    new Response(JSON.stringify({ apiVersion, agentVersion }), {
      status: 200,
      headers: { "content-type": "application/json" },
    })) as unknown as typeof fetch;
  return new EmailClient({ baseUrl: "http://x", fetchImpl });
}

describe("checkVersion", () => {
  it("accepts a matching apiVersion", async () => {
    const info = await checkVersion(clientReturning("1.0"));
    expect(info.apiVersion).toBe("1.0");
  });

  it("accepts a higher MINOR within the same MAJOR (backward-compatible)", async () => {
    const info = await checkVersion(clientReturning("1.5"), { expectedApiVersion: "1.0" });
    expect(info.apiVersion).toBe("1.5");
  });

  it("rejects a higher MAJOR (breaking)", async () => {
    await expect(
      checkVersion(clientReturning("2.0"), { expectedApiVersion: "1.0" }),
    ).rejects.toBeInstanceOf(VersionMismatchError);
  });

  it("rejects a lower MAJOR", async () => {
    await expect(
      checkVersion(clientReturning("1.0"), { expectedApiVersion: "2.0" }),
    ).rejects.toBeInstanceOf(VersionMismatchError);
  });
});
