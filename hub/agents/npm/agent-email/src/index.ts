// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * @amd-gaia/agent-email — thin client + binary fetcher + sidecar lifecycle for
 * the GAIA email agent (frozen, no-Python REST sidecar).
 *
 * Typical build-time + runtime flow:
 *
 *   import { fetchBinary, startSidecar, shutdown } from "@amd-gaia/agent-email";
 *
 *   // build step (or postinstall): pull + verify the binary
 *   const { binaryPath } = await fetchBinary({ outDir: "resources", baseUrl });
 *
 *   // runtime: spawn → health → version-check
 *   const sidecar = await startSidecar({ binaryPath, port: 8131 });
 *   const res = await sidecar.client.triage({ payload: { ... } });
 *   await shutdown(sidecar);
 */

export { EmailClient } from "./client.js";
export type { EmailClientOptions } from "./client.js";

export {
  fetchBinary,
  verifySha256,
  fileSha256,
  binaryExists,
} from "./fetch.js";
export type { FetchOptions, FetchResult } from "./fetch.js";

export {
  resolveBinaryPath,
  spawnSidecar,
  waitForHealth,
  checkVersion,
  shutdown,
  startSidecar,
  executableName,
  generateSessionToken,
} from "./lifecycle.js";
export type {
  ResolveOptions,
  SpawnOptions,
  Sidecar,
  WaitForHealthOptions,
  VersionCheckOptions,
  StartOptions,
} from "./lifecycle.js";

export {
  currentPlatformKey,
  loadLock,
  resolveEntry,
  defaultLockPath,
  isPlaceholderSha,
  SUPPORTED_PLATFORMS,
} from "./platform.js";
export type { BinaryLock, BinaryLockEntry } from "./platform.js";

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

export { SCHEMA_VERSION, MAX_BATCH_SIZE } from "./types.js";
export type * from "./types.js";
