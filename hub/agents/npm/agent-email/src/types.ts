// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * TypeScript mirror of the GAIA email agent REST contract.
 *
 * These types are **hand-written** to mirror two Python sources:
 *  - `hub/agents/python/email/gaia_agent_email/contract.py` — the frozen #1262
 *    triage request/response contract (single source of truth).
 *  - `hub/agents/python/email/gaia_agent_email/api_routes.py` — the local
 *    (non-frozen) draft/send handshake models.
 *
 * Why hand-written and not generated from `/openapi.json`: the contract is
 * small, stable (SCHEMA_VERSION is frozen at "1.0"), and hand-writing keeps the
 * published package free of an openapi-typegen build step and its dependency
 * tree. If the contract grows, regenerate from the live `/openapi.json` (the
 * server exposes it) and replace this file. The `apiVersion` check in
 * `lifecycle.ts` guards against silent drift at runtime.
 *
 * Wire note: `EmailMessage.from` is the JSON key on the wire (Python aliases its
 * `from_` field to `from`), so this interface uses `from` directly.
 */

/** Frozen contract version echoed by the server's `/version` endpoint. */
export const SCHEMA_VERSION = "1.0" as const;

/** The frozen four-bucket triage taxonomy (contract.py: EmailCategory). */
export type EmailCategory =
  | "urgent"
  | "actionable"
  | "informational"
  | "low priority";

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

/** Top-level triage request envelope (contract.py: EmailTriageRequest). */
export interface EmailTriageRequest {
  /** Contract version. Defaults to SCHEMA_VERSION; mismatch fails loudly. */
  schema_version?: string;
  /** The single-email or full-thread input. */
  payload: EmailInput;
}

/** A single extracted action (contract.py: ActionItem). */
export interface ActionItem {
  /** Imperative action, e.g. "Reply to Bob". */
  description: string;
  /** Free-text due hint as written ("Friday", "EOD"); not parsed. */
  due_hint?: string | null;
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

/** The structured analysis of one email or thread (contract.py: EmailTriageResult). */
export interface EmailTriageResult {
  /** One of the four buckets. */
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
  /** Echoes the provider message-id / thread-id from the request. */
  message_id?: string | null;
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
// Draft / send handshake (api_routes.py — LOCAL, not part of the frozen #1262
// contract; the send-confirmation gate per #1264).
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
