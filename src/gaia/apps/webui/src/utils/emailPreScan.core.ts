// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The `email_pre_scan` card's payload, row actions and sender text. Pure, so the phone app can import it. */

export interface PreScanItem {
    message_id: string;
    thread_id?: string;
    sender: string;
    subject: string;
    /** Heuristic / preference rationale for urgent + actionable rows. */
    why?: string;
    /** Same field, named ``reason`` on suggested-archive rows. */
    reason?: string;
}

export interface PreScanPayload {
    kind: 'email_pre_scan';
    urgent: PreScanItem[];
    actionable: PreScanItem[];
    informational_count: number;
    suggested_archives: PreScanItem[];
    suggested_drafts: unknown[];
    preferences_applied?: {
        priority_senders?: string[];
        low_priority_senders?: string[];
        category_defaults?: Record<string, string>;
    };
    totals?: {
        urgent: number;
        actionable: number;
        informational: number;
        suggested_archives: number;
    };
}

/** Anything claiming ``kind === "email_pre_scan"`` with the right shape; a wrong shape shows no card. */
export function isPreScanPayload(value: unknown): value is PreScanPayload {
    if (!value || typeof value !== 'object') return false;
    const v = value as Record<string, unknown>;
    if (v.kind !== 'email_pre_scan') return false;
    return (
        Array.isArray(v.urgent) &&
        Array.isArray(v.actionable) &&
        Array.isArray(v.suggested_archives) &&
        Array.isArray(v.suggested_drafts) &&
        typeof v.informational_count === 'number'
    );
}

/** Unwraps the `{ok, data}` envelope form, if present; otherwise passes the payload through unchanged. */
export function resolvePreScanPayload(data: unknown): unknown {
    if (data && typeof data === 'object' && (data as Record<string, unknown>).kind === 'email_pre_scan') {
        return data;
    }
    const enveloped = (data as { data?: unknown } | null | undefined)?.data;
    if (enveloped && typeof enveloped === 'object' && (enveloped as Record<string, unknown>).kind === 'email_pre_scan') {
        return enveloped;
    }
    return data;
}

// SECURITY: row actions pass the opaque message id only. Sender and subject come from email headers and are
// untrusted; putting them in a chat message would let a sender inject instructions into the model.
export function archiveCommand(item: PreScanItem): string {
    return `Archive message id ${item.message_id}.`;
}

export function replyCommand(item: PreScanItem): string {
    return `Draft a reply to message id ${item.message_id}.`;
}

/** The Gmail link for a row; null when it has no id. */
export function gmailUrl(item: PreScanItem): string | null {
    const id = item.thread_id || item.message_id;
    return id ? `https://mail.google.com/mail/u/0/#inbox/${encodeURIComponent(id)}` : null;
}

/** The sender's display name without the angle-bracketed address. */
export function formatSender(raw: string): string {
    if (!raw) return '(unknown)';
    const trimmed = raw.trim();
    const lt = trimmed.indexOf('<');
    if (lt > 0) {
        return trimmed.slice(0, lt).trim().replace(/^"|"$/g, '');
    }
    return trimmed;
}
