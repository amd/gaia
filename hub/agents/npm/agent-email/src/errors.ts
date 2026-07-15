// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Typed error classes. Per the GAIA no-silent-fallback rule, every failure path
 * raises one of these with an actionable message — never a swallowed error or a
 * silently-degraded result.
 */

/** Base class so callers can `instanceof AgentEmailError` to catch any of ours. */
export class AgentEmailError extends Error {
  constructor(message: string) {
    super(message);
    this.name = new.target.name;
  }
}

/** An HTTP request to the sidecar returned a non-2xx status. */
export class HttpError extends AgentEmailError {
  constructor(
    public readonly status: number,
    public readonly url: string,
    public readonly bodyText: string,
  ) {
    super(`HTTP ${status} from ${url}: ${bodyText || "(empty body)"}`);
  }
}

/** A downloaded binary's SHA-256 did not match `binaries.lock.json`. */
export class IntegrityError extends AgentEmailError {}

/** No binary entry / unsupported platform-arch. */
export class PlatformError extends AgentEmailError {}

/** The sidecar did not become healthy within the timeout. */
export class HealthTimeoutError extends AgentEmailError {}

/** The sidecar's apiVersion is incompatible with what the client expects. */
export class VersionMismatchError extends AgentEmailError {}

/** A binary could not be located on disk for spawning. */
export class BinaryNotFoundError extends AgentEmailError {}

/**
 * The `/query` SSE stream violated the frozen contract — a malformed event
 * payload, a non-SSE response, or a stream that closed without the mandated
 * terminal `final`/`error` event.
 */
export class QueryStreamError extends AgentEmailError {}
