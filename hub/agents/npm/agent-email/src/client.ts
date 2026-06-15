// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Typed REST client for the GAIA email agent sidecar.
 *
 * Wraps the five HTTP endpoints the frozen sidecar serves:
 *   POST /v1/email/triage   (the frozen #1262 contract)
 *   POST /v1/email/draft     (mint a confirmation token)
 *   POST /v1/email/send      (send — gated on a valid token)
 *   GET  /health             (readiness probe)
 *   GET  /version            (apiVersion / agentVersion)
 *
 * Uses the global `fetch` (Node >= 18). Every non-2xx response raises an
 * `HttpError` carrying the status and body — no silent empty/null fallback.
 */

import { HttpError } from "./errors.js";
import { createLogger } from "./logger.js";
import { stripTrailingSlashes } from "./url.js";
import type {
  EmailDraftRequest,
  EmailDraftResponse,
  EmailSendRequest,
  EmailSendResponse,
  EmailTriageRequest,
  EmailTriageResponse,
  HealthResponse,
  VersionResponse,
} from "./types.js";

const log = createLogger("client");

export interface EmailClientOptions {
  /** Base URL of the running sidecar, e.g. "http://127.0.0.1:8131". */
  baseUrl: string;
  /** Per-request timeout in ms. Default 30000. */
  timeoutMs?: number;
  /** Optional fetch override (for tests). Defaults to the global `fetch`. */
  fetchImpl?: typeof fetch;
}

const DEFAULT_TIMEOUT_MS = 30_000;

export class EmailClient {
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(opts: EmailClientOptions) {
    if (!opts?.baseUrl) {
      throw new TypeError(
        "EmailClient requires a baseUrl, e.g. { baseUrl: 'http://127.0.0.1:8131' }",
      );
    }
    // Normalize: strip trailing slashes so path joins are predictable.
    this.baseUrl = stripTrailingSlashes(opts.baseUrl);
    this.timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.fetchImpl = opts.fetchImpl ?? globalThis.fetch;
    if (typeof this.fetchImpl !== "function") {
      throw new TypeError(
        "global fetch is unavailable — use Node >= 18, or pass fetchImpl in EmailClientOptions",
      );
    }
  }

  /** Triage a single email or a full thread (POST /v1/email/triage). */
  async triage(request: EmailTriageRequest): Promise<EmailTriageResponse> {
    return this.post<EmailTriageResponse>("/v1/email/triage", request);
  }

  /** Propose a reply and mint a confirmation token (POST /v1/email/draft). */
  async draft(request: EmailDraftRequest): Promise<EmailDraftResponse> {
    return this.post<EmailDraftResponse>("/v1/email/draft", request);
  }

  /** Send a reply — requires a valid confirmation token (POST /v1/email/send). */
  async send(request: EmailSendRequest): Promise<EmailSendResponse> {
    return this.post<EmailSendResponse>("/v1/email/send", request);
  }

  /** Readiness probe (GET /health). */
  async health(): Promise<HealthResponse> {
    return this.get<HealthResponse>("/health");
  }

  /** Version probe (GET /version). */
  async version(): Promise<VersionResponse> {
    return this.get<VersionResponse>("/version");
  }

  // -- internals ----------------------------------------------------------

  private async get<T>(path: string): Promise<T> {
    return this.request<T>("GET", path);
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>("POST", path, body);
  }

  private async request<T>(
    method: "GET" | "POST",
    path: string,
    body?: unknown,
  ): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), this.timeoutMs);
    log.debug(`${method} ${url}`);
    let res: Response;
    try {
      res = await this.fetchImpl(url, {
        method,
        signal: ctrl.signal,
        headers:
          body === undefined
            ? { accept: "application/json" }
            : { accept: "application/json", "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      if (e instanceof Error && e.name === "AbortError") {
        throw new HttpError(
          0,
          url,
          `request timed out after ${this.timeoutMs}ms — is the sidecar running at ${this.baseUrl}?`,
        );
      }
      // Network-level failure (connection refused, DNS, etc.) — surface it.
      throw new HttpError(
        0,
        url,
        `network error: ${(e as Error).message} — is the sidecar running at ${this.baseUrl}?`,
      );
    } finally {
      clearTimeout(timer);
    }

    const text = await res.text();
    if (!res.ok) {
      log.debug(`${method} ${url} -> ${res.status}`);
      throw new HttpError(res.status, url, text);
    }
    if (!text) {
      // A 2xx with no body is unexpected for these JSON endpoints — fail loud.
      throw new HttpError(res.status, url, "expected a JSON body but got none");
    }
    try {
      return JSON.parse(text) as T;
    } catch (e) {
      throw new HttpError(
        res.status,
        url,
        `response was not valid JSON: ${(e as Error).message}`,
      );
    }
  }
}
