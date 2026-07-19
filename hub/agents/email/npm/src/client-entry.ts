// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Browser-safe client-only entry for @amd-gaia/agent-email.
 *
 * Import via the "./client" subpath export:
 *
 *   import { EmailClient } from "@amd-gaia/agent-email/client";
 *
 * This module re-exports ONLY symbols that have zero dependency on Node.js
 * built-ins (no node:fs, node:crypto, node:child_process, node:path, etc.):
 *   - EmailClient            — typed REST client (uses globalThis.fetch)
 *   - EmailClientOptions     — constructor options type
 *   - Error classes          — AgentEmailError and subclasses
 *   - SCHEMA_VERSION         — frozen contract version constant
 *   - All request/response types (TypeScript-only, erased at runtime)
 *
 * Node-only symbols (spawnSidecar, fetchBinary, platform helpers) remain
 * on the "." entry and are intentionally absent here.
 */

export { EmailClient } from "./client.js";
export type { EmailClientOptions } from "./client.js";

export {
  AgentEmailError,
  HttpError,
  IntegrityError,
  PlatformError,
  HealthTimeoutError,
  VersionMismatchError,
  BinaryNotFoundError,
  QueryStreamError,
} from "./errors.js";

export { SCHEMA_VERSION } from "./types.js";
export type * from "./types.js";
