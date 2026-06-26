// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Typed REST client for the GAIA email agent sidecar.
 *
 * Wraps every HTTP endpoint the frozen sidecar serves:
 *   POST /v1/email/triage    (the frozen triage contract)
 *   POST /v1/email/prescan    (read-only inbox pre-scan → triage-card envelope)
 *   POST /v1/email/draft      (mint a confirmation token)
 *   POST /v1/email/send       (send — gated on a valid token)
 *   GET  /health              (root liveness — what the standalone sidecar serves)
 *   GET  /version             (root apiVersion / agentVersion)
 *   GET  /v1/email/health     (router-scoped liveness — for the mounted-on-app case)
 *   GET  /v1/email/version    (router-scoped version)
 *   GET  /v1/email/spec       (human-readable HTML endpoint spec)
 *   GET  /openapi.json        (machine-readable OpenAPI document)
 *
 * NOTE: `/health` is liveness-only — it does NOT check Lemonade or the model, so a
 * green health probe does not guarantee `triage` will succeed (a cold/unprovisioned
 * host returns 502 on the first triage). A real readiness endpoint is tracked
 * separately. The interactive `/docs` and `/redoc` UIs are intentionally not
 * wrapped — they are browser pages, not a programmatic surface.
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
  EmailPreScanRequest,
  EmailPreScanResponse,
  EmailSendRequest,
  EmailSendResponse,
  EmailTriageRequest,
  EmailTriageResponse,
  HealthResponse,
  OpenApiDocument,
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
    const rawFetch = opts.fetchImpl ?? globalThis.fetch;
    if (typeof rawFetch !== "function") {
      throw new TypeError(
        "global fetch is unavailable — use Node >= 18, or pass fetchImpl in EmailClientOptions",
      );
    }
    // Bind to globalThis: the browser's `fetch` throws "Illegal invocation" if
    // invoked as a method on any object other than window/globalThis.
    this.fetchImpl = rawFetch.bind(globalThis);
  }

  /** Triage a single email or a full thread (POST /v1/email/triage). */
  async triage(request: EmailTriageRequest): Promise<EmailTriageResponse> {
    return this.post<EmailTriageResponse>("/v1/email/triage", request);
  }

  /**
   * Pre-scan the connected inbox into the triage-card envelope (POST
   * /v1/email/prescan). Read-only: lists recent inbox messages and returns the
   * aggregate summary (urgent / actionable / suggested archives + counts) the
   * Agent UI's pre-scan card renders. The request defaults `max_messages` to 25
   * server-side, so `{}` is a valid body.
   */
  async prescan(
    request: EmailPreScanRequest = {},
  ): Promise<EmailPreScanResponse> {
    return this.post<EmailPreScanResponse>("/v1/email/prescan", request);
  }

  /** Propose a reply and mint a confirmation token (POST /v1/email/draft). */
  async draft(request: EmailDraftRequest): Promise<EmailDraftResponse> {
    return this.post<EmailDraftResponse>("/v1/email/draft", request);
  }

  /** Send a reply — requires a valid confirmation token (POST /v1/email/send). */
  async send(request: EmailSendRequest): Promise<EmailSendResponse> {
    return this.post<EmailSendResponse>("/v1/email/send", request);
  }

  /**
   * Root liveness probe (GET /health). LIVENESS ONLY — it does not check
   * Lemonade or the model, so a green result does not guarantee `triage` works.
   */
  async health(): Promise<HealthResponse> {
    return this.get<HealthResponse>("/health");
  }

  /** Root version probe (GET /version). */
  async version(): Promise<VersionResponse> {
    return this.get<VersionResponse>("/version");
  }

  /**
   * Router-scoped liveness probe (GET /v1/email/health). Use this when the
   * email router is mounted on a product app, where root `/health` is the
   * host's and this one reports the email surface specifically.
   */
  async emailHealth(): Promise<HealthResponse> {
    return this.get<HealthResponse>("/v1/email/health");
  }

  /** Router-scoped version probe (GET /v1/email/version). */
  async emailVersion(): Promise<VersionResponse> {
    return this.get<VersionResponse>("/v1/email/version");
  }

  /**
   * Human-readable HTML endpoint spec (GET /v1/email/spec). Returns the raw
   * HTML page — a convenience for opening in a browser, not a JSON contract.
   */
  async spec(): Promise<string> {
    const { text } = await this.requestRaw("GET", "/v1/email/spec", {
      accept: "text/html",
    });
    return text;
  }

  /** The machine-readable OpenAPI document (GET /openapi.json). */
  async openapi(): Promise<OpenApiDocument> {
    return this.get<OpenApiDocument>("/openapi.json");
  }

  // -- internals ----------------------------------------------------------

  private async get<T>(path: string): Promise<T> {
    return this.request<T>("GET", path);
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>("POST", path, body);
  }

  /** JSON request: fetch raw text, then parse + fail loud on empty/invalid JSON. */
  private async request<T>(
    method: "GET" | "POST",
    path: string,
    body?: unknown,
  ): Promise<T> {
    const { status, text } = await this.requestRaw(method, path, { body });
    const url = `${this.baseUrl}${path}`;
    if (!text) {
      // A 2xx with no body is unexpected for these JSON endpoints — fail loud.
      throw new HttpError(status, url, "expected a JSON body but got none");
    }
    try {
      return JSON.parse(text) as T;
    } catch (e) {
      throw new HttpError(
        status,
        url,
        `response was not valid JSON: ${(e as Error).message}`,
      );
    }
  }

  /**
   * Transport core: send the request, surface network/timeout/non-2xx as
   * `HttpError`, and return the raw `{ status, text }` for the caller to
   * interpret (JSON for most endpoints, HTML for `/v1/email/spec`).
   */
  private async requestRaw(
    method: "GET" | "POST",
    path: string,
    opts?: { body?: unknown; accept?: string },
  ): Promise<{ status: number; text: string }> {
    const url = `${this.baseUrl}${path}`;
    const accept = opts?.accept ?? "application/json";
    const hasBody = opts?.body !== undefined;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), this.timeoutMs);
    log.debug(`${method} ${url}`);
    let res: Response;
    try {
      res = await this.fetchImpl(url, {
        method,
        signal: ctrl.signal,
        headers: hasBody
          ? { accept, "content-type": "application/json" }
          : { accept },
        body: hasBody ? JSON.stringify(opts!.body) : undefined,
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
    return { status: res.status, text };
  }
}
