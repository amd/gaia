// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Verifies the ./client browser-safe entry:
 *   1. Bundles for platform:browser with esbuild — no unresolved node: imports.
 *   2. The Node "." entry still exposes the lifecycle symbols (spawnSidecar etc.).
 */

import { describe, expect, it } from "vitest";
import * as esbuild from "esbuild";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const pkgRoot = path.resolve(__dirname, "..");

describe("browser entry (./client)", () => {
  it("bundles for platform:browser with no unresolved node: imports", async () => {
    // esbuild with platform:browser marks node: imports as external by default
    // and logs them as errors if bundle:true is set and they are actually referenced.
    // We capture the result and assert no errors / warnings about node: builtins.
    const result = await esbuild.build({
      entryPoints: [path.join(pkgRoot, "src", "client-entry.ts")],
      bundle: true,
      platform: "browser",
      format: "esm",
      write: false,
      // Treat node: imports as errors (they won't resolve in a browser bundle).
      // Any reference to a node: builtin will appear in result.errors.
      external: [],
      // logLevel: silent so esbuild doesn't print to console during test
      logLevel: "silent",
    });

    // Zero errors means no unresolvable node: builtins pulled in transitively.
    expect(result.errors).toHaveLength(0);
  });

  it("client-entry exports EmailClient", async () => {
    // Import via the TypeScript source to avoid needing the dist/ to exist.
    // vitest handles the TS transform automatically.
    const mod = await import("../src/client-entry.js");
    expect(typeof mod.EmailClient).toBe("function");
  });

  it("client-entry exports error classes", async () => {
    const mod = await import("../src/client-entry.js");
    expect(typeof mod.AgentEmailError).toBe("function");
    expect(typeof mod.HttpError).toBe("function");
    expect(typeof mod.HealthTimeoutError).toBe("function");
    expect(typeof mod.VersionMismatchError).toBe("function");
    expect(typeof mod.BinaryNotFoundError).toBe("function");
    expect(typeof mod.IntegrityError).toBe("function");
    expect(typeof mod.PlatformError).toBe("function");
  });

  it("client-entry exports SCHEMA_VERSION and request/response types (runtime const)", async () => {
    const mod = await import("../src/client-entry.js");
    expect(mod.SCHEMA_VERSION).toBe("2.0");
  });

  it("client-entry does NOT export spawnSidecar (Node-only)", async () => {
    const mod = await import("../src/client-entry.js");
    expect((mod as Record<string, unknown>).spawnSidecar).toBeUndefined();
  });

  it("client-entry does NOT export fetchBinary (Node-only)", async () => {
    const mod = await import("../src/client-entry.js");
    expect((mod as Record<string, unknown>).fetchBinary).toBeUndefined();
  });
});

describe("Node entry (.) lifecycle exports", () => {
  it("exposes spawnSidecar from the Node barrel", async () => {
    const mod = await import("../src/index.js");
    expect(typeof mod.spawnSidecar).toBe("function");
  });

  it("exposes startSidecar from the Node barrel", async () => {
    const mod = await import("../src/index.js");
    expect(typeof mod.startSidecar).toBe("function");
  });

  it("exposes resolveBinaryPath from the Node barrel", async () => {
    const mod = await import("../src/index.js");
    expect(typeof mod.resolveBinaryPath).toBe("function");
  });
});
