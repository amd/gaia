// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Typed error classes. Per the GAIA no-silent-fallback rule, every failure path
 * raises one of these with an actionable message — never a swallowed error or a
 * silently-degraded result.
 */

/** Base class so callers can `instanceof GaiaError` to catch any of ours. */
export class GaiaError extends Error {
  constructor(message: string) {
    super(message);
    this.name = new.target.name;
  }
}

/** A downloaded binary's SHA-256 did not match `binaries.lock.json`. */
export class IntegrityError extends GaiaError {}

/** Unsupported platform-arch, or no lock entry for a component on this host. */
export class PlatformError extends GaiaError {}

/** The sidecar did not become healthy within the timeout. */
export class HealthTimeoutError extends GaiaError {}

/** The sidecar's apiVersion is incompatible with what this package expects. */
export class VersionMismatchError extends GaiaError {}

/** A binary could not be located on disk for spawning. */
export class BinaryNotFoundError extends GaiaError {}

/** An HTTP request to the sidecar returned a non-2xx status. */
export class HttpError extends GaiaError {
  constructor(
    public readonly status: number,
    public readonly url: string,
    public readonly bodyText: string,
  ) {
    super(`HTTP ${status} from ${url}: ${bodyText || "(empty body)"}`);
  }
}
