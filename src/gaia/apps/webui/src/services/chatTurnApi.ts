// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Controls for a chat's turns and its tool permissions; re-exported from `api`. */

import type { ActiveRuns, ContextUsageResult, PermissionMode, RetryChoice, RetryResult, SessionPermissions } from '../types';
import { apiFetch } from './api';

/** Cancel an active streaming chat session (sets SSE handler cancelled flag). */
export async function cancelStream(sessionId: string): Promise<{ cancelled: boolean }> {
    return apiFetch('POST', '/chat/cancel', { session_id: sessionId });
}

/** Rejects when the running turn could not take the message. */
export async function sendFollowUp(sessionId: string, text: string): Promise<{ delivered: boolean }> {
    return apiFetch('POST', '/chat/followup', { session_id: sessionId, text });
}

export async function getContextUsage(sessionId: string): Promise<ContextUsageResult> {
    return apiFetch('GET', `/chat/context/${encodeURIComponent(sessionId)}`);
}

export async function retryWith(sessionId: string, messageId: number, model: string): Promise<RetryResult> {
    return apiFetch('POST', '/chat/retry-with', { session_id: sessionId, message_id: messageId, model });
}

export async function chooseRetry(sessionId: string, messageId: number, keep: 'original' | 'retry'): Promise<RetryChoice> {
    return apiFetch('POST', '/chat/retry-with/choose', { session_id: sessionId, message_id: messageId, keep });
}

/** List session ids that currently have a running chat turn (#1580). */
export async function getActiveRuns(): Promise<ActiveRuns> {
    return apiFetch('GET', '/chat/active');
}

/** Answer a tool confirmation: allow once, always allow this call in this chat, or deny. */
export async function confirmTool(
    sessionId: string,
    approved: boolean,
    options: { always?: boolean; confirmId?: string } = {},
): Promise<{ status: string; approved: boolean; granted: string | null }> {
    return apiFetch('POST', '/chat/confirm-tool', {
        session_id: sessionId,
        approved,
        always: options.always ?? false,
        ...(options.confirmId ? { confirm_id: options.confirmId } : {}),
    });
}

/** Answer a pending mid-run `needs_input` question (#2595). The agent
 *  blocks server-side until this call lands, so the run continues once it
 *  resolves. */
export async function respondToInput(
    sessionId: string,
    requestId: string,
    value: string,
): Promise<{ status: string; request_id: string }> {
    return apiFetch('POST', '/chat/user-input', { session_id: sessionId, request_id: requestId, value });
}

export async function getPermissions(sessionId: string): Promise<SessionPermissions> {
    return apiFetch('GET', `/chat/permissions/${encodeURIComponent(sessionId)}`);
}

export async function listAllPermissions(): Promise<{ default_mode: PermissionMode; sessions: SessionPermissions[] }> {
    return apiFetch('GET', '/chat/permissions');
}

export async function setPermissionMode(sessionId: string, mode: PermissionMode): Promise<SessionPermissions> {
    return apiFetch('PUT', `/chat/permissions/${encodeURIComponent(sessionId)}`, { mode });
}

/** Revoke one "always allow" grant, or all of them when `key` is omitted. */
export async function revokeGrants(sessionId: string, key?: string): Promise<SessionPermissions> {
    const q = key ? `?key=${encodeURIComponent(key)}` : '';
    return apiFetch('DELETE', `/chat/permissions/${encodeURIComponent(sessionId)}/grants${q}`);
}
