// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * @amd-gaia/agent-email/client — browser-safe subpath entry.
 *
 * Exports ONLY the HTTP client, request/response types, and error classes.
 * Imports ZERO `node:*` modules so a Vite/browser bundler can consume this
 * without polyfills or resolution errors.
 *
 * Usage (React/Vite renderer):
 *
 *   import { EmailClient, type EmailTriageRequest } from "@amd-gaia/agent-email/client";
 *
 *   const client = new EmailClient({ baseUrl: "http://127.0.0.1:8131" });
 *   const res = await client.triage({ payload: { ... } });
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
} from "./errors.js";

export { SCHEMA_VERSION } from "./types.js";
export type * from "./types.js";
