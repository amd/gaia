// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * @amd-gaia/gaia — binary fetcher + process lifecycle for the GAIA flagship
 * agent sidecar and the GAIA terminal UI.
 *
 * The CLI (`npx @amd-gaia/gaia`) is the normal way in. These exports are for
 * integrators who want the pieces:
 *
 *   import { fetchAll, startSidecar, shutdown } from "@amd-gaia/gaia";
 *
 *   const { sidecar, tui } = await fetchAll();          // both, SHA-256 verified
 *   const proc = await startSidecar({ binaryPath: sidecar.binaryPath });
 *   const res  = await fetch(`${proc.baseUrl}/v1/gaia/query`, { ... });
 *   await shutdown(proc);
 */

export {
  fetchAll,
  fetchBinary,
  verifySha256,
  fileSha256,
  binaryExists,
  defaultCacheDir,
  daemonSidecarCacheDir,
} from "./fetch.js";
export type {
  FetchOptions,
  FetchResult,
  FetchAllOptions,
  FetchAllResult,
} from "./fetch.js";

export {
  AGENT_ID,
  API_VERSION,
  DEFAULT_HOST,
  DEFAULT_PORT,
  RESERVED_PORT,
  checkVersion,
  health,
  resolveSidecarPath,
  resolveTuiPath,
  runTui,
  shutdown,
  sidecarExecutableName,
  spawnSidecar,
  startSidecar,
  tuiExecutableName,
  version,
  waitForHealth,
} from "./lifecycle.js";
export type {
  HealthResponse,
  ResolveOptions,
  RunTuiOptions,
  Sidecar,
  SpawnOptions,
  StartOptions,
  VersionCheckOptions,
  VersionResponse,
  WaitForHealthOptions,
} from "./lifecycle.js";

export {
  COMPONENTS,
  SCHEMA_MAJOR,
  SUPPORTED_PLATFORMS,
  SUPPORTED_SIDECAR_PLATFORMS,
  SUPPORTED_TUI_PLATFORMS,
  TUI_ARTIFACT_NAMES,
  componentBaseUrl,
  componentLock,
  currentPlatformKey,
  defaultLockPath,
  isPlaceholderSha,
  loadLock,
  platformsFor,
  resolveEntry,
} from "./platform.js";
export type {
  BinaryLock,
  BinaryLockEntry,
  ComponentLock,
  ComponentName,
} from "./platform.js";

export {
  BinaryNotFoundError,
  GaiaError,
  HealthTimeoutError,
  HttpError,
  IntegrityError,
  PlatformError,
  VersionMismatchError,
} from "./errors.js";
