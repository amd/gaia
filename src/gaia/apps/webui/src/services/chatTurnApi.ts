// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Controls for a chat's running or latest turn; re-exported from `api`. */

import type { ChatPerformance, ContextUsageResult, RetryChoice, RetryResult } from '../types';
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

export async function getChatPerformance(sessionId: string): Promise<ChatPerformance> {
    return apiFetch('GET', `/chat/performance/${encodeURIComponent(sessionId)}`);
}

export async function retryWith(sessionId: string, messageId: number, model: string): Promise<RetryResult> {
    return apiFetch('POST', '/chat/retry-with', { session_id: sessionId, message_id: messageId, model });
}

export async function chooseRetry(sessionId: string, messageId: number, keep: 'original' | 'retry'): Promise<RetryChoice> {
    return apiFetch('POST', '/chat/retry-with/choose', { session_id: sessionId, message_id: messageId, keep });
}
