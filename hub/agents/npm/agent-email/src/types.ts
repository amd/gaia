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
 * Schema 2.1 (additive; triage shapes unchanged) bundles several new surfaces:
 *   - read-only inbox search (POST /v1/email/search, #1781)
 *   - mailbox actions: archive / phishing-quarantine + their reversal and the
 *     confirm-token handshake (#1779)
 *   - calendar view/create/respond (#1780)
 *   - inbox pre-scan (POST /v1/email/prescan, #1778)
 * Schema 2.2 (additive over 2.1, #1542): attachment handling — AttachmentMeta
 * on EmailMessage / EmailTriageResult / DraftReply / EmailSendResponse, and
 * OutgoingAttachment accepted by draft/send.
 * Schema 2.3 (BREAKING triage-shape change): EmailTriageResult.draft is now a
 * DraftScaffold (recipient + subject only) instead of a DraftReply — triage
 * never composed a body, so the always-empty draft.body is dropped. DraftReply
 * (with body) is unchanged and remains the draft()/send() shape.
 * Schema 2.4 (additive over 2.3, #2016/#2097): the canonical agent-loop query —
 * POST /v1/email/query (SSE stream of the seven frozen event types, spec
 * docs/spec/agent-ui-query-sse-contract.md) + POST /v1/email/query/{run_id}/cancel.
 * Mirrored from `query_routes.py` (QueryRequest / QueryCancelResponse) and the
 * frozen #2057 event vocabulary.
 */

/** Frozen contract version echoed by the server's `/version` endpoint. */
export const SCHEMA_VERSION = "2.4" as const;

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

/** Attachment metadata — no content (contract.py: AttachmentMeta, schema 2.2, #1542). */
export interface AttachmentMeta {
  /** Attachment filename, e.g. "report.pdf". */
  filename: string;
  /** MIME type as reported by the provider. */
  mime_type: string;
  /** Attachment size in bytes (decoded). */
  size_bytes: number;
  /** Provider handle for fetching the content (Gmail body.attachmentId), or null. */
  attachment_id?: string | null;
}

/**
 * One attachment to include on a draft/send (contract.py: OutgoingAttachment,
 * schema 2.2, #1542). Content travels as standard base64, ≤ 25 MB decoded;
 * invalid base64 / MIME / oversize is a 422, never a silent drop.
 */
export interface OutgoingAttachment {
  /** Filename shown to the recipient. */
  filename: string;
  /** MIME type of the content, e.g. "application/pdf". */
  mime_type: string;
  /** File content, standard base64 (RFC 4648). */
  content_base64: string;
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
  /** Attachment metadata on this message (schema 2.2; metadata only). */
  attachments?: AttachmentMeta[];
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

/**
 * A reply scaffold the triage path proposes (contract.py: DraftScaffold —
 * schema 2.3). Recipient + subject only, no body: triage never composes reply
 * prose. To send a reply, compose the body yourself and call `draft()`, which
 * returns a full `DraftReply` and a single-use confirmation token.
 */
export interface DraftScaffold {
  /** Proposed recipients (non-empty). */
  to: EmailAddress[];
  /** Proposed subject line (Re:-prefixed). */
  subject: string;
}

/** A drafted reply the agent proposes (contract.py: DraftReply). */
export interface DraftReply {
  /** Proposed recipients (non-empty). */
  to: EmailAddress[];
  /** Proposed subject line. */
  subject: string;
  /** Proposed reply body. */
  body: string;
  /** Metadata of the attachments the draft proposes to send (schema 2.2). */
  attachments?: AttachmentMeta[];
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
  /**
   * Proposed reply SCAFFOLD (recipient + subject only, no body), or null when
   * none is suggested (schema 2.3). Triage never composes reply prose — compose
   * the body and call `draft()` to get a full DraftReply + confirmation token.
   */
  draft?: DraftScaffold | null;
  /**
   * Suggested next action (schema 2.0): "reply" for URGENT/NEEDS_RESPONSE,
   * "archive" for PROMOTIONAL, "none" for FYI/PERSONAL. Default "none".
   */
  suggested_action?: "reply" | "none" | "archive";
  /** Echoes the provider message-id / thread-id from the request. */
  message_id?: string | null;
  /** LLM usage metrics; null on the heuristic-only path. */
  usage?: TriageUsage | null;
  /** Attachment metadata of the analyzed message/thread, echoed (schema 2.2). */
  attachments?: AttachmentMeta[];
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
// Inbox search (contract.py — read-only mailbox search, schema 2.1, #1781).
// ---------------------------------------------------------------------------

/** Search the connected mailbox (contract.py: EmailSearchRequest). */
export interface EmailSearchRequest {
  /** Contract version. Defaults to SCHEMA_VERSION; mismatch fails loudly. */
  schema_version?: string;
  /** Gmail-style query (e.g. "from:alice is:unread"). Omit to list the inbox. */
  query?: string | null;
  /** Label ids to filter by (e.g. ["INBOX", "UNREAD"]). Omit → INBOX. */
  labels?: string[] | null;
  /** Max messages to return (1–100). Default 25. */
  max_results?: number;
  /** Opaque pagination cursor from a prior response's `next_page_token`. */
  page_token?: string | null;
}

/**
 * One message in a search result — inbox-list metadata, not the full body
 * (contract.py: EmailSearchResultItem). `from`/`to`/`date` are raw header
 * strings (the wire key for the sender is `from`, mirroring EmailMessage).
 */
export interface EmailSearchResultItem {
  /** Provider message id (opaque). */
  id: string;
  /** Provider thread id this message belongs to. */
  thread_id?: string | null;
  /** Subject line. */
  subject: string;
  /** Raw "From" header string. JSON key is `from` on the wire. */
  from: string;
  /** Raw "To" header string. */
  to: string;
  /** Raw "Date" header string. */
  date: string;
  /** Provider-supplied short preview of the body. */
  snippet: string;
  /** Label ids on the message. */
  label_ids: string[];
}

/** Top-level inbox-search response (contract.py: EmailSearchResponse). */
export interface EmailSearchResponse {
  /** Echoes the contract version. */
  schema_version: string;
  /** Echoes the request query (null when unset). */
  query?: string | null;
  /** Number of messages returned. */
  count: number;
  /** Matching messages (newest-first). */
  messages: EmailSearchResultItem[];
  /** Opaque token to fetch the next page, or null when no more. */
  next_page_token?: string | null;
}

// ---------------------------------------------------------------------------
// Inbox pre-scan (contract.py — schema 2.1, #1778). A read-only, lightweight
// triage over recent inbox messages, reshaped into the card envelope the Agent
// UI renders (`kind: "email_pre_scan"`).
// ---------------------------------------------------------------------------

/** One surfaced inbox message in a pre-scan section (contract.py: PreScanItem). */
export interface PreScanItem {
  /** Provider message id (opaque). */
  message_id: string;
  /** Provider thread id this message belongs to. */
  thread_id?: string | null;
  /** Raw "From" header of the message (display + address). */
  sender: string;
  /** Subject line. */
  subject: string;
  /** Rationale for an urgent/actionable row. */
  why?: string | null;
  /** Rationale for a suggested-archive row. */
  reason?: string | null;
}

/** Session preferences that shaped a pre-scan (contract.py: PreScanPreferencesApplied). */
export interface PreScanPreferencesApplied {
  /** Senders always treated as urgent. */
  priority_senders: string[];
  /** Senders always treated as low-priority. */
  low_priority_senders: string[];
  /** Per-category default action (e.g. { FYI: "archive" }). */
  category_defaults: Record<string, string>;
}

/** Pre-cap counts per bucket (contract.py: PreScanTotals). */
export interface PreScanTotals {
  urgent: number;
  actionable: number;
  informational: number;
  suggested_archives: number;
}

/** Request envelope for an inbox pre-scan (contract.py: EmailPreScanRequest). */
export interface EmailPreScanRequest {
  /** Contract version. Defaults to SCHEMA_VERSION; mismatch fails loudly. */
  schema_version?: string;
  /** How many recent inbox messages to scan (1–100). Default 25. */
  max_messages?: number;
}

/** The aggregate pre-scan envelope the card renders (contract.py: EmailPreScanResult). */
export interface EmailPreScanResult {
  /** Discriminator the chat surface detects to render the card. */
  kind: "email_pre_scan";
  /** Top urgent messages (capped). */
  urgent: PreScanItem[];
  /** Top messages needing a response (capped). */
  actionable: PreScanItem[];
  /** Count of informational (FYI/PERSONAL) messages — not listed. */
  informational_count: number;
  /** Promotional / low-priority messages suggested for archive (capped). */
  suggested_archives: PreScanItem[];
  /** Reserved for future LLM-driven draft generation; empty today. */
  suggested_drafts: unknown[];
  /** Session preferences that shaped this pre-scan. */
  preferences_applied?: PreScanPreferencesApplied | null;
  /** Pre-cap totals per bucket. */
  totals?: PreScanTotals | null;
}

/** Top-level pre-scan response envelope (contract.py: EmailPreScanResponse). */
export interface EmailPreScanResponse {
  /** Echoes the contract version. */
  schema_version: string;
  /** The pre-scan envelope. */
  result: EmailPreScanResult;
}

/**
 * Response of `GET /v1/email/briefing` (api_routes.py: EmailBriefingResponse,
 * #1608). The latest scheduled daily inbox briefing — the same `email_pre_scan`
 * envelope as `prescan`, produced by the sidecar's daily timer without a prompt,
 * plus a `generated_at` stamp. `404` until the first scheduled run has happened.
 */
export interface EmailBriefingResponse {
  /** Echoes the contract version. */
  schema_version: string;
  /** UTC ISO-8601 timestamp of the scheduled run that produced this briefing. */
  generated_at: string;
  /** The pre-scan envelope the scheduled run produced. */
  briefing: EmailPreScanResult;
}

// ---------------------------------------------------------------------------
// Batch triage (#1887) — ADDITIVE beside the single-email triage above.
// POST /v1/email/triage/batch: an items array in, a results array out. The
// single triage() / EmailTriageRequest / EmailTriageResponse are unchanged.
// ---------------------------------------------------------------------------

/** Maximum number of items in one batch request (contract.py: MAX_BATCH_SIZE). */
export const MAX_BATCH_SIZE = 100 as const;

/** Why one batch item could not be triaged (contract.py: BatchItemError). */
export interface BatchItemError {
  /** Actionable failure reason for this item. */
  message: string;
}

/**
 * One item's outcome in a batch triage (contract.py: BatchItemResult).
 * Exactly one of `result` or `error` is set, correlated by 0-based `index`.
 */
export interface BatchItemResult {
  /** 0-based position in the request `items` array. */
  index: number;
  /** Set when the item succeeded. */
  result?: EmailTriageResult | null;
  /** Set when the item failed. */
  error?: BatchItemError | null;
}

/** Top-level batch triage request envelope (contract.py: BatchTriageRequest). */
export interface BatchTriageRequest {
  /** Contract version. Defaults to SCHEMA_VERSION; mismatch fails loudly. */
  schema_version?: string;
  /** 1..MAX_BATCH_SIZE single-email or thread inputs to triage. */
  items: EmailInput[];
  /** Optional context applied to ALL items. */
  context?: TriageContext | null;
}

/** Top-level batch triage response envelope (contract.py: BatchTriageResponse). */
export interface BatchTriageResponse {
  /** Echoes the contract version. */
  schema_version: string;
  /**
   * One entry per request item, order-preserved, 1:1 with `items`. HTTP 200
   * with every item errored is valid — inspect each `results[].error`.
   */
  results: BatchItemResult[];
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
  /** Attachments to send (schema 2.2); the token binds their content digests. */
  attachments?: OutgoingAttachment[];
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
  /** Attachments to send (schema 2.2) — must match the confirmed draft's exactly. */
  attachments?: OutgoingAttachment[];
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
  /** Metadata of the attachments that went out (schema 2.2). */
  attachments?: AttachmentMeta[];
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
// Canonical agent-loop query (query_routes.py — schema 2.4, #2016/#2097). A
// natural-language request in, the seven frozen SSE event types out (the #2057
// wire contract, docs/spec/agent-ui-query-sse-contract.md), terminated by
// exactly one `final` or `error`. The HOST mints `run_id` (spec §2.3) so the
// run is cancellable from the instant the request is sent; the transcript
// slice is PUSHED in `context` (spec §2.4) so the sidecar stays stateless.
// ---------------------------------------------------------------------------

/** Transcript role of one pushed context turn (query_routes.py: QueryContextItem). */
export type QueryRole = "user" | "assistant" | "system" | "tool";

/** One prior turn pushed in the request body (query_routes.py: QueryContextItem). */
export interface QueryContextItem {
  /** Transcript role for this turn. */
  role: QueryRole;
  /** The message text for this turn. */
  content: string;
}

/** `POST /v1/email/query` request body (query_routes.py: QueryRequest, spec §2.2). */
export interface EmailQueryRequest {
  /** The natural-language request driving the agent loop. Non-empty. */
  query: string;
  /**
   * Host-minted streaming-run handle (UUIDv4) — mint it yourself (e.g.
   * `crypto.randomUUID()`) and keep it: cancellation (`cancelQuery(runId)`)
   * keys off it, so the run is cancellable from the instant the request is sent.
   */
  run_id: string;
  /**
   * The relevant transcript slice, pushed in the body. May be an empty array
   * for a fresh conversation, but the field must be present (spec §2.4).
   */
  context: QueryContextItem[];
  /** Model id override. Omitted → the sidecar's default. */
  model?: string;
  /**
   * LLM provider override. The email agent runs local inference only, so only
   * "lemonade" is accepted; any other value is rejected (400).
   */
  provider?: string;
  /** Agent-loop step ceiling (≥ 1). Omitted → the agent's configured default. */
  max_steps?: number;
}

/** Progress narration (also carries folded step/thinking/plan lines). */
export interface QueryStatusEvent {
  type: "status";
  /** The progress line to show. */
  message: string;
}

/** An incremental chunk of assistant answer text. */
export interface QueryTokenEvent {
  type: "token";
  /** The next chunk of assistant text to append. */
  delta: string;
}

/** The agent is invoking a tool. */
export interface QueryToolCallEvent {
  type: "tool_call";
  /** Tool name, e.g. "triage_inbox". */
  tool: string;
  /** Tool arguments; `{}` when the tool takes none. */
  args: Record<string, unknown>;
}

/** A tool returned a result. */
export interface QueryToolResultEvent {
  type: "tool_result";
  /** Tool name the result belongs to. */
  tool: string;
  /** Optional typed-card key (e.g. "email_pre_scan"); unknown keys degrade to a generic card. */
  render?: string;
  /** Structured result; shape is render-specific. */
  data: unknown;
}

/**
 * A gated (destructive/external) step is awaiting approval. Under the current
 * stateless confirmation model (epic decision D1) the run then ends with a
 * `final` refusal pointing at the fixed-function route (draft() → send());
 * `confirm_url` is omitted until server-side resume is wired.
 */
export interface QueryNeedsConfirmationEvent {
  type: "needs_confirmation";
  /** The run this pause belongs to (correlate with your minted run_id). */
  run_id: string;
  /** The gated action, e.g. "send", "archive", "input". */
  action: string;
  /** The literal text the user would approve. */
  summary: string;
  /** Resume endpoint — only present under the (not yet wired) resume model. */
  confirm_url?: string;
}

/** Run usage metrics on the terminal `final` event (all fields optional). */
export interface QueryUsage {
  /** Agent-loop steps taken. */
  steps?: number;
  /** Tools invoked during the run. */
  tools_used?: number;
  /** Wall-clock seconds for the run. */
  elapsed?: number;
  /** Token counts, when the backend reports them. */
  tokens?: number;
  [key: string]: unknown;
}

/** Terminal: the assistant's final answer. Exactly one `final` OR `error` ends a run. */
export interface QueryFinalEvent {
  type: "final";
  /** The assistant's answer text. */
  answer: string;
  /** Optional usage metrics for the run. */
  usage?: QueryUsage;
}

/** Terminal: an actionable failure — surface `detail` verbatim. */
export interface QueryErrorEvent {
  type: "error";
  /** Actionable message, surfaced verbatim. */
  detail: string;
  /** HTTP-style status code for the failure class. */
  status: number;
}

/**
 * A wire event whose `type` is outside the frozen seven. Per the contract's
 * evolution rule (spec §7) a newer sidecar may add event types additively; the
 * client surfaces them as this placeholder — visibly, never silently dropped.
 * Render an "unsupported event" affordance (or log it) in your default branch.
 */
export interface QueryUnknownEvent {
  type: "unknown";
  /** The unrecognized wire `type` value. */
  eventType: string;
  /** The full raw event object as received. */
  raw: Record<string, unknown>;
}

/**
 * One canonical `/query` SSE event, discriminated on `type`. The seven frozen
 * types (spec §4) plus the `unknown` placeholder for additive future types.
 */
export type QueryEvent =
  | QueryStatusEvent
  | QueryTokenEvent
  | QueryToolCallEvent
  | QueryToolResultEvent
  | QueryNeedsConfirmationEvent
  | QueryFinalEvent
  | QueryErrorEvent
  | QueryUnknownEvent;

/** Result of `POST /v1/email/query/{run_id}/cancel` (query_routes.py: QueryCancelResponse). */
export interface QueryCancelResponse {
  /** The run that was signalled to cancel. */
  run_id: string;
  /** True once the cancel was delivered to the run. */
  cancelled: boolean;
  /** Always "ok" on success. */
  status: string;
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

// ---------------------------------------------------------------------------
// Readiness preflight (api_routes.py — GET /v1/email/init, #1795). Unlike the
// liveness-only /health, this probes the whole triage stack: Lemonade reachable
// AND at a compatible version AND the triage model downloaded. Returns HTTP 200
// when ready, 503 when not — with the same envelope either way, plus a `hint`.
// ---------------------------------------------------------------------------

/** Lemonade reachability + version compatibility (api_routes.py: InitLemonadeStatus). */
export interface InitLemonadeStatus {
  /** True when Lemonade answered the /health probe. */
  reachable: boolean;
  /** The /api/v1 base URL that was probed. */
  base_url: string;
  /** Lemonade's self-reported version, or null when it advertises none. */
  version?: string | null;
  /** Minimum Lemonade version the triage stack requires. */
  min_version: string;
  /** version >= min_version; null when the version could not be determined. */
  compatible?: boolean | null;
}

/** Triage-model presence (api_routes.py: InitModelStatus). */
export interface InitModelStatus {
  /** Resolved Lemonade model id for triage. */
  id: string;
  /** True when the model is downloaded on the server. */
  present: boolean;
  /** Whether it actually loads — not probed in v1 (heavy), so null; `present` is the signal. */
  loadable?: boolean | null;
  /**
   * The context window the model is CURRENTLY loaded at, as reported by the
   * server's /health (recipe_options.ctx_size). Null whenever the model is
   * not loaded right now or the server does not report a ctx — never a
   * config echo or a guess (#1892). Compare against the envelope (16384
   * target / 32768 max) to see what window a run would actually measure.
   */
  ctx_size?: number | null;
}

/**
 * Response of `GET /v1/email/init` (api_routes.py: InitResponse, #1795). The
 * route returns HTTP 200 when `ready` and 503 when not (same shape); `hint`
 * names the fix when not ready. Read-only — no model pull is triggered.
 */
export interface InitResponse {
  /** True only when Lemonade is reachable/compatible AND the triage model is present. */
  ready: boolean;
  /** Lemonade Server reachability + version compatibility. */
  lemonade: InitLemonadeStatus;
  /** Triage-model status. */
  model: InitModelStatus;
  /** Actionable next step when not ready; null when ready. */
  hint?: string | null;
}

// ---------------------------------------------------------------------------
// Mailbox actions — archive / quarantine + reversal (contract.py, schema 2.1,
// #1779). MUTATING actions gate on a single-use confirmation token minted by
// POST /v1/email/confirm; their reversal endpoints are ungated (they restore).
// ---------------------------------------------------------------------------

/** The destructive actions a confirmation token can authorize (contract.py: EmailActionType). */
export type EmailActionType = "archive" | "quarantine";

/** Request a confirmation token for a destructive action (contract.py: EmailActionConfirmRequest). */
export interface EmailActionConfirmRequest {
  /** The action to authorize: "archive" or "quarantine". */
  action: EmailActionType;
  /** Provider message id the action will mutate. */
  message_id: string;
  /** Optional provider binding ("google" / "microsoft"). */
  provider?: string | null;
}

/** A single-use token bound to (action, message_id) (contract.py: EmailActionConfirmResponse). */
export interface EmailActionConfirmResponse {
  /** Echoes the contract version. */
  schema_version: string;
  /** Echo to /archive or /quarantine to authorize this exact action. Single-use. */
  confirmation_token: string;
  /** The authorized action. */
  action: EmailActionType;
  /** The message the token authorizes. */
  message_id: string;
}

/** Archive a message — requires a confirmation token (contract.py: EmailArchiveRequest). */
export interface EmailArchiveRequest {
  /** Provider message id to archive. */
  message_id: string;
  /** Token from POST /v1/email/confirm (action="archive"). Required for a real archive. */
  confirmation_token?: string | null;
  /** Optional provider fallback ("google" / "microsoft"). */
  provider?: string | null;
}

/** Result of an archive — carries the undo handle (contract.py: EmailArchiveResponse). */
export interface EmailArchiveResponse {
  /** Echoes the contract version. */
  schema_version: string;
  /** The message that was archived. */
  message_id: string;
  /** Action-log id for this archive. */
  action_id: string;
  /** Undo handle: pass to POST /v1/email/unarchive within the window. */
  batch_id: string;
  /**
   * The id valid AFTER the archive. Folder backends (Outlook) mint a new id on
   * the move; Gmail keeps the request id. Surfaced to track the message after.
   */
  post_archive_id: string;
  /** Seconds the unarchive handle stays valid. */
  undo_window_seconds: number;
  /** Always true on success. */
  archived: boolean;
}

/** Reverse an archive within the undo window — ungated (contract.py: EmailUnarchiveRequest). */
export interface EmailUnarchiveRequest {
  /** The undo handle returned by POST /v1/email/archive. */
  batch_id: string;
  /** Optional provider; omit to route by the mailbox recorded at archive time. */
  provider?: string | null;
}

/** One message restored to the inbox (contract.py: UnarchivedMessage). */
export interface UnarchivedMessage {
  message_id: string;
  action_id: string;
}

/** One message that failed to restore (contract.py: UnarchiveFailure). */
export interface UnarchiveFailure {
  message_id: string;
  error: string;
}

/** Result of an unarchive — partial success reported, never silent (contract.py: EmailUnarchiveResponse). */
export interface EmailUnarchiveResponse {
  /** Echoes the contract version. */
  schema_version: string;
  /** The undo handle that was processed. */
  batch_id: string;
  /** Count of messages restored to inbox. */
  restored: number;
  /** Each restored message. */
  messages: UnarchivedMessage[];
  /** Messages that could not be restored (with reasons). */
  failed: UnarchiveFailure[];
  /** True when at least one message was restored. */
  undone: boolean;
}

/** Quarantine a phishing message — requires a confirmation token (contract.py: EmailQuarantineRequest). */
export interface EmailQuarantineRequest {
  /** Provider message id to quarantine. */
  message_id: string;
  /** Must be true — the action refuses non-phishing mail (safety gate). */
  is_phishing: boolean;
  /** Token from POST /v1/email/confirm (action="quarantine"). Required for a real quarantine. */
  confirmation_token?: string | null;
  /** Optional provider fallback ("google" / "microsoft"). */
  provider?: string | null;
}

/** Result of a quarantine — carries the undo handle (contract.py: EmailQuarantineResponse). */
export interface EmailQuarantineResponse {
  /** Echoes the contract version. */
  schema_version: string;
  /** The message that was quarantined. */
  message_id: string;
  /** Undo handle: pass to POST /v1/email/unquarantine within the window. */
  action_id: string;
  /** Id of the GAIA_PHISHING_QUARANTINE label that was applied. */
  quarantine_label_id: string;
  /** The label set restored on undo (recorded pre-quarantine). */
  prior_labels: string[];
  /** Seconds the unquarantine handle stays valid. */
  undo_window_seconds: number;
  /** Always true on success. */
  quarantined: boolean;
}

/** Reverse a quarantine within the undo window — ungated (contract.py: EmailUnquarantineRequest). */
export interface EmailUnquarantineRequest {
  /** The action id returned by POST /v1/email/quarantine. */
  action_id: string;
  /** Optional provider; omit to route by the mailbox recorded at quarantine time. */
  provider?: string | null;
}

/** Result of an unquarantine (contract.py: EmailUnquarantineResponse). */
export interface EmailUnquarantineResponse {
  /** Echoes the contract version. */
  schema_version: string;
  /** The action id that was undone. */
  action_id: string;
  /** The restored message id. */
  message_id: string;
  /** Always true on success. */
  restored: boolean;
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
