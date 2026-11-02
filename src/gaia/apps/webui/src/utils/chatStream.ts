// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Pure helpers ChatView uses to turn the chat stream into messages. */

import type { AgentStep, Message, StreamEvent } from '../types';

/**
 * Safety-net regex to strip raw tool-call JSON from streaming content.
 *
 * Primary filtering happens server-side in sse_handler.py (see _TOOL_CALL_JSON_RE).
 * This frontend regex is a secondary safety net in case any tool-call JSON leaks
 * through the SSE stream. The canonical pattern is defined in sse_handler.py;
 * keep this in sync if the server-side pattern changes.
 */
export const TOOL_CALL_JSON_SAFETY_RE = /\s*\{\s*"?(?:tool|thought|goal)"?\s*:\s*"[^"]*"[^}]*(?:"?tool_args"?\s*:\s*\{[^}]*\})?\s*\}/g;

/**
 * Strip the LLM JSON envelope from streamed/accumulated content.
 * Handles responses like {"thought":"...", "goal":"...", "answer":"<content>"}
 * where the entire response is wrapped in a structured JSON object.
 * During streaming, progressively reveals the answer content as it arrives.
 */
export function stripStreamingEnvelope(text: string): string {
    const trimmed = text.trim();
    if (!trimmed.startsWith('{')) return text;
    if (!trimmed.includes('"thought"') && !trimmed.includes('"answer"') && !trimmed.includes('"goal"')) return text;

    // Try full JSON parse first (works when message is complete and well-formed)
    try {
        const parsed = JSON.parse(trimmed);
        if (parsed.answer !== undefined) return String(parsed.answer);
    } catch {
        // Incomplete or malformed JSON (literal newlines in values) — fall through
    }

    // Find "answer" field and extract content progressively
    const answerIdx = trimmed.indexOf('"answer"');
    if (answerIdx === -1) return ''; // Still in thought/goal — suppress during streaming

    const colonIdx = trimmed.indexOf(':', answerIdx + 8);
    if (colonIdx === -1) return '';
    let start = colonIdx + 1;
    while (start < trimmed.length && /\s/.test(trimmed[start])) start++;
    if (start >= trimmed.length || trimmed[start] !== '"') return '';

    let content = trimmed.slice(start + 1);
    // Strip closing "} if the JSON envelope is complete
    if (content.endsWith('"}')) content = content.slice(0, -2);
    else if (content.endsWith('"')) content = content.slice(0, -1);

    return content.replace(/\\"/g, '"').replace(/\\n/g, '\n').replace(/\\\\/g, '\\');
}

/**
 * A recoverable error right after a failed tool repeats that tool's error, which
 * its card already shows; the agent retries, so it isn't a turn-level error.
 * A tool that raises or times out reports the error before its result lands, so a
 * still-running tool counts too — its result always follows as failed.
 */
export function isEchoOfFailedTool(event: StreamEvent, steps: AgentStep[]): boolean {
    if (event.type !== 'agent_error' || event.recoverable !== true) return false;
    const last = steps[steps.length - 1];
    if (!last || last.type !== 'tool') return false;
    return last.success === false || (!!last.active && last.success == null);
}

/** Messages as the API sends them, with steps and stats under the names the view reads. */
export function fromApiMessages(raw: unknown[] | undefined): Message[] {
    return (raw || []).map((m: any) => ({
        ...m,
        agentSteps: m.agentSteps || m.agent_steps || undefined,
        stats: m.stats || m.inference_stats || undefined,
        turnSummary: m.turnSummary || m.turn_summary || undefined,
    }));
}

/**
 * The server's row for *target*, a question shown in *local*. A question sent
 * from this view keeps its temporary id until the list reloads, so it is matched
 * by its place among the questions. Null when the server never saved it.
 */
export function persistedQuestion(server: Message[], local: Message[], target: Message): Message | null {
    const byId = server.find((m) => m.id === target.id && m.role === 'user');
    if (byId) return byId;
    const place = local.filter((m) => m.role === 'user').findIndex((m) => m.id === target.id);
    if (place < 0) throw new Error(`Message ${target.id} is no longer in this chat.`);
    const row = server.filter((m) => m.role === 'user')[place];
    if (!row) return null;
    if (row.content !== target.content) {
        throw new Error(`The server holds a different question in place of message ${target.id}.`);
    }
    return row;
}

/** The server's rows, keeping Try again on a failed reply they stand in for. */
export function keepFailedReply(local: Message[], server: Message[]): Message[] {
    const last = local[local.length - 1];
    const twin = server[server.length - 1];
    if (!last?.failed || server.length !== local.length || twin?.role !== 'assistant') return server;
    return [...server.slice(0, -1), { ...twin, failed: true }];
}

export function policyReceiptAnchor(receiptId: string): string {
    return `policy-receipt-${encodeURIComponent(receiptId)}`;
}
