// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Typed REST client for the GAIA email agent sidecar.
 *
 * Wraps every HTTP endpoint the frozen sidecar serves:
 *   POST /v1/email/triage    (the frozen triage contract)
 *   POST /v1/email/search     (read-only inbox search)
 *   POST /v1/email/prescan    (read-only inbox pre-scan → triage-card envelope)
 *   POST /v1/email/draft      (mint a confirmation token)
 *   POST /v1/email/send       (send — gated on a valid token)
 *   POST /v1/email/confirm    (mint an action confirmation token)
 *   POST /v1/email/archive    (archive — gated on a valid token)
 *   POST /v1/email/unarchive  (reverse an archive — ungated, within 30s)
 *   POST /v1/email/quarantine (quarantine phishing — gated on a valid token)
 *   POST /v1/email/unquarantine (reverse a quarantine — ungated, within 30s)
 *   GET  /v1/email/calendar/events            (view events — read-only)
 *   POST /v1/email/calendar/events/preview     (mint a calendar confirmation token)
 *   POST /v1/email/calendar/events             (create — gated on a valid token)
 *   POST /v1/email/calendar/events/respond     (RSVP accept/decline/tentative)
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
  BatchTriageRequest,
  BatchTriageResponse,
  CalendarCreateEventRequest,
  CalendarEventPreviewResponse,
  CalendarEventResponse,
  CalendarEventsResponse,
  CalendarRespondRequest,
  CalendarRespondResponse,
  EmailActionConfirmRequest,
  EmailActionConfirmResponse,
  EmailArchiveRequest,
  EmailArchiveResponse,
  EmailDraftRequest,
  EmailDraftResponse,
  EmailPreScanRequest,
  EmailPreScanResponse,
  EmailQuarantineRequest,
  EmailQuarantineResponse,
  EmailSearchRequest,
  EmailSearchResponse,
  EmailSendRequest,
  EmailSendResponse,
  EmailTriageRequest,
  EmailTriageResponse,
  EmailUnarchiveRequest,
  EmailUnarchiveResponse,
  EmailUnquarantineRequest,
  EmailUnquarantineResponse,
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
  /**
   * Per-session caller-auth bearer token (#1706). When set, it is sent as
   * `Authorization: Bearer <token>` on every request. The sidecar requires it on
   * `/v1/email/*` calls (401 otherwise); `spawnSidecar` generates one and binds
   * it to the sidecar's client automatically.
   */
  authToken?: string;
}

const DEFAULT_TIMEOUT_MS = 30_000;

export class EmailClient {
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;
  private readonly authToken?: string;

  constructor(opts: EmailClientOptions) {
    if (!opts?.baseUrl) {
      throw new TypeError(
        "EmailClient requires a baseUrl, e.g. { baseUrl: 'http://127.0.0.1:8131' }",
      );
    }
    // Normalize: strip trailing slashes so path joins are predictable.
    this.baseUrl = stripTrailingSlashes(opts.baseUrl);
    this.timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.authToken = opts.authToken;
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
   * Triage a batch of emails/threads in one request (POST /v1/email/triage/batch).
   * Returns one `results[]` entry per item, order-preserved. A 200 with every
   * item errored is valid — inspect each `results[].error`, not just the status.
   */
  async triageBatch(
    request: BatchTriageRequest,
  ): Promise<BatchTriageResponse> {
    return this.post<BatchTriageResponse>("/v1/email/triage/batch", request);
  }

  /**
   * Search the connected mailbox, read-only (POST /v1/email/search). Returns
   * inbox-list metadata (id, thread, subject, from, snippet, labels) for
   * messages matching the query/labels — not the message body. Requires a
   * mailbox connected on the host; no mailbox → 503, two+ → 400.
   */
  async search(request: EmailSearchRequest): Promise<EmailSearchResponse> {
    return this.post<EmailSearchResponse>("/v1/email/search", request);
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
   * Mint a single-use confirmation token for a destructive mailbox action
   * (POST /v1/email/confirm). The token authorizes exactly one
   * `(action, message_id)` — echo it to `archive`/`quarantine`. This is the
   * action analogue of `draft` for sends; nothing mutates here.
   */
  async confirmAction(
    request: EmailActionConfirmRequest,
  ): Promise<EmailActionConfirmResponse> {
    return this.post<EmailActionConfirmResponse>("/v1/email/confirm", request);
  }

  /**
   * Archive a message — requires a valid confirmation token (POST /v1/email/archive).
   * Returns a `batch_id` undo handle; pass it to `unarchive` within
   * `undo_window_seconds` to restore the message to the inbox.
   */
  async archive(request: EmailArchiveRequest): Promise<EmailArchiveResponse> {
    return this.post<EmailArchiveResponse>("/v1/email/archive", request);
  }

  /**
   * Reverse an archive within the undo window (POST /v1/email/unarchive).
   * NOT gated — it restores. Routes by the mailbox recorded at archive time and
   * uses the post-archive id so Outlook can find the moved message.
   */
  async unarchive(
    request: EmailUnarchiveRequest,
  ): Promise<EmailUnarchiveResponse> {
    return this.post<EmailUnarchiveResponse>("/v1/email/unarchive", request);
  }

  /**
   * Quarantine a phishing message — requires a valid confirmation token
   * (POST /v1/email/quarantine). Applies the GAIA_PHISHING_QUARANTINE label and
   * archives. Refuses `is_phishing: false`. Reverse with `unquarantine`.
   */
  async quarantine(
    request: EmailQuarantineRequest,
  ): Promise<EmailQuarantineResponse> {
    return this.post<EmailQuarantineResponse>("/v1/email/quarantine", request);
  }

  /**
   * Reverse a quarantine within the undo window (POST /v1/email/unquarantine).
   * NOT gated — it restores the message's prior labels.
   */
  async unquarantine(
    request: EmailUnquarantineRequest,
  ): Promise<EmailUnquarantineResponse> {
    return this.post<EmailUnquarantineResponse>(
      "/v1/email/unquarantine",
      request,
    );
  }

  /**
   * View calendar events on the primary calendar (GET /v1/email/calendar/events).
   * Read-only. `timeMin`/`timeMax` are optional RFC 3339 bounds; `provider`
   * (google|microsoft) is required only when more than one account is connected.
   */
  async listCalendarEvents(opts?: {
    timeMin?: string;
    timeMax?: string;
    provider?: string;
  }): Promise<CalendarEventsResponse> {
    const params = new URLSearchParams();
    if (opts?.timeMin) params.set("time_min", opts.timeMin);
    if (opts?.timeMax) params.set("time_max", opts.timeMax);
    if (opts?.provider) params.set("provider", opts.provider);
    const qs = params.toString();
    return this.get<CalendarEventsResponse>(
      `/v1/email/calendar/events${qs ? `?${qs}` : ""}`,
    );
  }

  /**
   * Mint a single-use confirmation token bound to a proposed event
   * (POST /v1/email/calendar/events/preview). The calendar analogue of `draft`:
   * creates nothing; echo the returned `confirmation_token` to `createCalendarEvent`.
   */
  async previewCalendarEvent(
    request: CalendarCreateEventRequest,
  ): Promise<CalendarEventPreviewResponse> {
    return this.post<CalendarEventPreviewResponse>(
      "/v1/email/calendar/events/preview",
      request,
    );
  }

  /**
   * Create a calendar event — requires a valid confirmation token from
   * `previewCalendarEvent` (POST /v1/email/calendar/events). Without a
   * payload-bound token the create is rejected (403); events are never created
   * without explicit confirmation.
   */
  async createCalendarEvent(
    request: CalendarCreateEventRequest,
  ): Promise<CalendarEventResponse> {
    return this.post<CalendarEventResponse>(
      "/v1/email/calendar/events",
      request,
    );
  }

  /**
   * RSVP accept / decline / tentative to an existing calendar invite
   * (POST /v1/email/calendar/events/respond).
   */
  async respondToCalendarEvent(
    request: CalendarRespondRequest,
  ): Promise<CalendarRespondResponse> {
    return this.post<CalendarRespondResponse>(
      "/v1/email/calendar/events/respond",
      request,
    );
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
    // Per-session caller-auth token (#1706), when the client was given one.
    const authHeaders: Record<string, string> = this.authToken
      ? { authorization: `Bearer ${this.authToken}` }
      : {};
    let res: Response;
    try {
      res = await this.fetchImpl(url, {
        method,
        signal: ctrl.signal,
        headers: hasBody
          ? { accept, "content-type": "application/json", ...authHeaders }
          : { accept, ...authHeaders },
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
