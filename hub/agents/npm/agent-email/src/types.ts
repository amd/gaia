// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * TypeScript mirror of the GAIA email agent REST contract.
 *
 * These types are **hand-written** to mirror two Python sources:
 *  - `hub/agents/python/email/gaia_agent_email/contract.py` — the frozen triage
 *    request/response contract (single source of truth).
 *  - `hub/agents/python/email/gaia_agent_email/api_routes.py` — the local
 *    (non-frozen) draft/send handshake models.
 *
 * Why hand-written and not generated from `/openapi.json`: the contract is
 * small, stable, and hand-writing keeps the published package free of an
 * openapi-typegen build step and its dependency tree. If the contract grows,
 * regenerate from the live `/openapi.json` (the server exposes it) and replace
 * this file. The `apiVersion` check in `lifecycle.ts` guards against silent
 * drift at runtime.
 *
 * Wire note: `EmailMessage.from` is the JSON key on the wire (Python aliases its
 * `from_` field to `from`), so this interface uses `from` directly.
 *
 * Schema 2.0: five-bucket EmailCategory, suggested_action, TriageUsage, typed
 * ActionItem (type/url discriminator).
 * Schema 2.1: calendar surface (view / create / respond) — additive.
 */

/** Frozen contract version echoed by the server's `/version` endpoint. */
export const SCHEMA_VERSION = "2.1" as const;

/**
 * The five-bucket triage taxonomy (schema 2.0 — contract.py: EmailCategory).
 * Values are the exact JSON wire strings the server emits.
 */
export type EmailCategory =
  | "URGENT"
  | "NEEDS_RESPONSE"
  | "FYI"
  | "PROMOTIONAL"
  | "PERSONAL";

/** A single email participant (contract.py: EmailAddress). */
export interface EmailAddress {
  /** Display name, e.g. "Alice Example". Optional. */
  name?: string | null;
  /** Bare email address, e.g. "a@b.com". Required. */
  email: string;
}

/** One email message (contract.py: EmailMessage). */
export interface EmailMessage {
  /** Provider message id (opaque). */
  message_id: string;
  /** Provider thread id this message belongs to. */
  thread_id?: string | null;
  /** Sender. JSON key is `from` on the wire. */
  from: EmailAddress;
  /** Primary recipients (the "To" line). */
  to?: EmailAddress[];
  /** Carbon-copy recipients. */
  cc?: EmailAddress[];
  /** Blind-carbon-copy recipients. */
  bcc?: EmailAddress[];
  /** ISO-8601 timestamp of the message. */
  date?: string | null;
  /** Subject line. */
  subject?: string;
  /** Plain-text message body to analyze. */
  body: string;
}

/** A single email to triage (contract.py: SingleEmailInput). */
export interface SingleEmailInput {
  kind: "single";
  /** The recipient the agent acts on behalf of — whose inbox this is. */
  principal: EmailAddress;
  /** The one message to analyze. */
  message: EmailMessage;
}

/** A full conversation thread to triage (contract.py: ThreadInput). */
export interface ThreadInput {
  kind: "thread";
  /** The recipient the agent acts on behalf of — whose inbox this is. */
  principal: EmailAddress;
  /** Provider thread id for the conversation. */
  thread_id: string;
  /** Every message in the thread, oldest-first. Non-empty. */
  messages: EmailMessage[];
}

/** Discriminated union on `kind` (contract.py: EmailInput). */
export type EmailInput = SingleEmailInput | ThreadInput;

/** Optional caller-supplied context that biases categorization (contract.py: TriageContext). */
export interface TriageContext {
  /** Important people whose mail should weigh higher. */
  people?: string[];
  /** Active projects the principal cares about. */
  projects?: string[];
  /** Preferred summary tone, e.g. "concise". */
  tone?: string | null;
  /** The principal's own address, so the model knows who "I" is. */
  self_email?: string | null;
}

/** Top-level triage request envelope (contract.py: EmailTriageRequest). */
export interface EmailTriageRequest {
  /** Contract version. Defaults to SCHEMA_VERSION; mismatch fails loudly. */
  schema_version?: string;
  /** The single-email or full-thread input. */
  payload: EmailInput;
  /** Optional context that biases categorization and summary. */
  context?: TriageContext | null;
}

/**
 * A single extracted action (contract.py: ActionItem — schema 2.0).
 * `type` discriminates plain-text actions from link actions.
 * When `type` is "link", `url` is required and non-empty.
 */
export interface ActionItem {
  /** Imperative action, e.g. "Reply to Bob". */
  description: string;
  /** Free-text due hint as written ("Friday", "EOD"); not parsed. */
  due_hint?: string | null;
  /** "text" for a plain action; "link" when the action involves a URL. Default "text". */
  type?: "text" | "link";
  /** URL for a "link" action item; absent/null for "text". */
  url?: string | null;
}

/** A drafted reply the agent proposes (contract.py: DraftReply). */
export interface DraftReply {
  /** Proposed recipients (non-empty). */
  to: EmailAddress[];
  /** Proposed subject line. */
  subject: string;
  /** Proposed reply body. */
  body: string;
}

/**
 * LLM usage metrics for a triage (contract.py: TriageUsage — schema 2.0).
 * Null on the heuristic-only path where no LLM call was made.
 */
export interface TriageUsage {
  /** Sum of input tokens across the LLM calls. */
  prompt_tokens: number;
  /** Sum of output (completion) tokens across the LLM calls. */
  completion_tokens: number;
  /** Sum of input + output tokens across the LLM calls. */
  total_tokens: number;
  /** Aggregate decode throughput (total output tokens / total decode time). */
  tokens_per_second: number;
}

/** The structured analysis of one email or thread (contract.py: EmailTriageResult). */
export interface EmailTriageResult {
  /** One of the five taxonomy buckets (schema 2.0). */
  category: EmailCategory;
  /** Spam signal (independent). */
  is_spam: boolean;
  /** Phishing signal (independent of spam). */
  is_phishing: boolean;
  /** Plain-text summary of the email/thread. */
  summary: string;
  /** Extracted actions (may be empty). */
  action_items: ActionItem[];
  /** Proposed reply, or null when none is suggested. */
  draft?: DraftReply | null;
  /**
   * Suggested next action (schema 2.0): "reply" for URGENT/NEEDS_RESPONSE,
   * "archive" for PROMOTIONAL, "none" for FYI/PERSONAL. Default "none".
   */
  suggested_action?: "reply" | "none" | "archive";
  /** Echoes the provider message-id / thread-id from the request. */
  message_id?: string | null;
  /** LLM usage metrics; null on the heuristic-only path. */
  usage?: TriageUsage | null;
}

/** Top-level triage response envelope (contract.py: EmailTriageResponse). */
export interface EmailTriageResponse {
  /** Echoes the contract version. */
  schema_version: string;
  /** Which input shape produced this result. */
  request_kind: "single" | "thread";
  /** The structured analysis. */
  result: EmailTriageResult;
}

// ---------------------------------------------------------------------------
// Draft / send handshake (api_routes.py — LOCAL, not part of the frozen triage
// contract; the send-confirmation gate).
// ---------------------------------------------------------------------------

/** Propose a reply and obtain a confirmation token (api_routes.py: EmailDraftRequest). */
export interface EmailDraftRequest {
  /** Proposed recipients (non-empty). */
  to: EmailAddress[];
  /** Proposed subject line. */
  subject: string;
  /** Proposed reply body. */
  body: string;
  /** Optional provider binding ("google" or "microsoft"). */
  provider?: string | null;
}

/** The proposed reply plus a single-use confirmation token (api_routes.py: EmailDraftResponse). */
export interface EmailDraftResponse {
  /** The proposed reply (to / subject / body). */
  draft: DraftReply;
  /** Echo to POST /v1/email/send to authorize this exact payload. Single-use. */
  confirmation_token: string;
}

/** Send a reply — requires a valid confirmation token (api_routes.py: EmailSendRequest). */
export interface EmailSendRequest {
  /** Recipients (non-empty). */
  to: EmailAddress[];
  /** Subject line. */
  subject: string;
  /** Reply body. */
  body: string;
  /** Confirmation token from POST /v1/email/draft. Required for a real send. */
  confirmation_token?: string | null;
  /** Optional provider ("google" or "microsoft") fallback. */
  provider?: string | null;
}

/** Result of a send (api_routes.py: EmailSendResponse). */
export interface EmailSendResponse {
  /** Provider message id of the sent email. */
  sent_id: string;
  /** Recipients the message was sent to. */
  to: EmailAddress[];
  /** Subject of the sent message. */
  subject: string;
  /** Always true on success. */
  sent: boolean;
}

// ---------------------------------------------------------------------------
// Calendar surface (contract.py — schema 2.1, #1780). View / create / respond
// reach either the Google or Microsoft calendar backend through one contract.
// ---------------------------------------------------------------------------

/**
 * One endpoint (start or end) of a calendar event (contract.py: CalendarEventDateTime).
 * Provide EXACTLY ONE of `date_time` (timed, RFC 3339) or `date` (all-day, YYYY-MM-DD).
 * `time_zone` is optional; the Outlook backend defaults a missing zone to "UTC"
 * for timed events (Google: include a UTC offset in `date_time` or set `time_zone`).
 */
export interface CalendarEventDateTime {
  /** Timed-event instant, RFC 3339. Mutually exclusive with `date`. */
  date_time?: string | null;
  /** All-day date, "YYYY-MM-DD". Mutually exclusive with `date_time`. */
  date?: string | null;
  /** IANA time zone, e.g. "America/Los_Angeles". Optional. */
  time_zone?: string | null;
}

/** A calendar event returned by the view endpoint (contract.py: CalendarEvent). */
export interface CalendarEvent {
  /** Provider event id (opaque). */
  id?: string | null;
  /** Event title / summary. */
  summary: string;
  /** Start instant ("dateTime") or all-day "date" string. */
  start?: string | null;
  /** End instant ("dateTime") or all-day "date" string. */
  end?: string | null;
  /** Free-text location, or null. */
  location?: string | null;
  /** Organizer email, or null. */
  organizer?: string | null;
}

/** Result of `GET /v1/email/calendar/events` (contract.py: CalendarEventsResponse). */
export interface CalendarEventsResponse {
  /** Echoes the contract version. */
  schema_version: string;
  /** Matching events, ordered by start time. */
  events: CalendarEvent[];
}

/**
 * Create a calendar event (contract.py: CalendarCreateEventRequest). Shared by
 * the preview (token-mint) and create (token-consume) endpoints. Creating is a
 * confirmation-gated mutation — see `previewCalendarEvent` / `createCalendarEvent`.
 */
export interface CalendarCreateEventRequest {
  /** Contract version. Defaults to SCHEMA_VERSION server-side. */
  schema_version?: string;
  /** Event title / summary (non-empty). */
  summary: string;
  /** Event start. */
  start: CalendarEventDateTime;
  /** Event end (after start). */
  end: CalendarEventDateTime;
  /** Attendee email addresses to invite (may be empty). */
  attendees?: string[];
  /** Optional free-text location. */
  location?: string | null;
  /** Optional event description / body. */
  description?: string | null;
  /** Optional provider binding ("google" or "microsoft"). */
  provider?: string | null;
  /**
   * Confirmation token from POST /v1/email/calendar/events/preview. Ignored by
   * preview; required by create — a create without a valid token bound to this
   * exact event is rejected (403).
   */
  confirmation_token?: string | null;
}

/**
 * The normalized event echo plus a single-use confirmation token bound to it
 * (contract.py: CalendarEventPreviewResponse).
 */
export interface CalendarEventPreviewResponse {
  /** Echoes the contract version. */
  schema_version: string;
  /** The event title to be created. */
  summary: string;
  /** Event start. */
  start: CalendarEventDateTime;
  /** Event end. */
  end: CalendarEventDateTime;
  /** Attendees to invite. */
  attendees: string[];
  /** Optional location. */
  location?: string | null;
  /** Optional description. */
  description?: string | null;
  /** Echo to POST /v1/email/calendar/events to authorize this exact event. Single-use. */
  confirmation_token: string;
}

/** Result of `POST /v1/email/calendar/events` (contract.py: CalendarEventResponse). */
export interface CalendarEventResponse {
  /** Echoes the contract version. */
  schema_version: string;
  /** Provider id of the created event. */
  event_id: string;
  /** Title of the created event. */
  summary: string;
  /** Always true on success. */
  created: boolean;
}

/** RSVP status verb (contract.py: CalendarRespondRequest.status). */
export type CalendarRsvpStatus = "accepted" | "declined" | "tentative";

/** RSVP to an existing invite (contract.py: CalendarRespondRequest). */
export interface CalendarRespondRequest {
  /** Contract version. Defaults to SCHEMA_VERSION server-side. */
  schema_version?: string;
  /** Provider event id to RSVP to. */
  event_id: string;
  /** RSVP response: accept, decline, or tentatively accept. */
  status: CalendarRsvpStatus;
  /**
   * The principal's own email (the attendee responding). Used by the Google
   * backend; ignored by Outlook (RSVPs on /me).
   */
  attendee_email: string;
  /** Optional provider binding ("google" or "microsoft"). */
  provider?: string | null;
}

/** Result of `POST /v1/email/calendar/events/respond` (contract.py: CalendarRespondResponse). */
export interface CalendarRespondResponse {
  /** Echoes the contract version. */
  schema_version: string;
  /** The event that was responded to. */
  event_id: string;
  /** The RSVP response that was recorded. */
  status: CalendarRsvpStatus;
  /** Always true on success. */
  responded: boolean;
}

// ---------------------------------------------------------------------------
// Probe endpoints (server.py)
// ---------------------------------------------------------------------------

/** Response of `GET /health` (server.py). */
export interface HealthResponse {
  status: string;
  service: string;
}

/** Response of `GET /version` (server.py). */
export interface VersionResponse {
  /** Host-facing REST contract version (frozen request/response schema). */
  apiVersion: string;
  /** Package build version. */
  agentVersion: string;
}

/**
 * The OpenAPI 3.x document served at `GET /openapi.json`. Loosely typed — it is
 * the sidecar's own machine schema, not re-modeled here. Use it to drive codegen
 * or contract checks rather than reaching for hand-written types.
 */
export type OpenApiDocument = {
  openapi: string;
  info: { title: string; version: string };
  paths: Record<string, unknown>;
} & Record<string, unknown>;
