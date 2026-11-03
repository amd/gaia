// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Pure helpers ChatView uses to turn the chat stream into messages. */

import type { AgentStep, Message, RenderCardData, StreamEvent } from '../types';

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

/** The cards a reloaded answer drew, from the steps that persisted them. */
function cardsFromSteps(steps: AgentStep[] | undefined): RenderCardData[] | undefined {
    const cards = (steps || [])
        .filter((s) => typeof s.render === 'string' && s.render.length > 0)
        .map((s) => ({ render: s.render as string, data: s.data }));
    return cards.length > 0 ? cards : undefined;
}

/** Messages as the API sends them, with steps and stats under the names the view reads. */
export function fromApiMessages(raw: unknown[] | undefined): Message[] {
    return (raw || []).map((m: any) => {
        const agentSteps = m.agentSteps || m.agent_steps || undefined;
        return {
            ...m,
            agentSteps,
            cards: m.cards || cardsFromSteps(agentSteps),
            stats: m.stats || m.inference_stats || undefined,
            turnSummary: m.turnSummary || m.turn_summary || undefined,
        };
    });
}

/**
 * The server's row for *target*, a question shown in *local*. A question sent
 * from this view keeps its temporary id until the list reloads, so it is matched
 * by its text, in order, among the questions the server saved since the last row
 * both sides know. Null when the server never saved it.
 */
export function persistedQuestion(server: Message[], local: Message[], target: Message): Message | null {
    const byId = server.find((m) => m.id === target.id && m.role === 'user');
    if (byId) return byId;
    const at = local.findIndex((m) => m.id === target.id);
    if (at < 0) throw new Error(`Message ${target.id} is no longer in this chat.`);
    // Pair the questions after the last row both sides share, skipping ones the server never saved.
    const known = new Set(server.map((m) => m.id));
    let anchor = at - 1;
    while (anchor >= 0 && !known.has(local[anchor].id)) anchor--;
    const from = anchor < 0 ? 0 : server.findIndex((m) => m.id === local[anchor].id) + 1;
    const saved = server.slice(from).filter((m) => m.role === 'user');
    let next = 0;
    for (const m of local.slice(anchor + 1, at)) {
        if (m.role === 'user' && saved[next]?.content === m.content) next++;
    }
    return saved[next]?.content === target.content ? saved[next] : null;
}

/** Whether *row* is the server's saved copy of the failure *reply* stands in for. */
export function isSavedFailure(row: Message | undefined, reply: Message): boolean {
    return !!row && row.role === 'assistant' && !!reply.failureText
        && row.content.trim() === reply.failureText.trim();
}

/**
 * Whether Try again may replace *question* on the server: nothing follows it
 * there but the saved copy of *reply*'s failure. Anything else is an answer
 * written since (another window, or a run that finished after the view lost it).
 */
export function onlyFailureAfter(server: Message[], question: Message, reply: Message): boolean {
    const after = server.slice(server.findIndex((m) => m.id === question.id) + 1);
    return after.length === 0 || (after.length === 1 && isSavedFailure(after[0], reply));
}

/** The server's rows, keeping Try again only on the server's copy of a failed reply. */
export function keepFailedReply(local: Message[], server: Message[]): Message[] {
    const last = local[local.length - 1];
    const twin = server[server.length - 1];
    if (!last?.failed || !isSavedFailure(twin, last)) return server;
    const lastQuestion = (rows: Message[]) => [...rows].reverse().find((m) => m.role === 'user')?.content;
    if (lastQuestion(local) !== lastQuestion(server)) return server;
    return [...server.slice(0, -1), { ...twin, failed: true, failureText: last.failureText }];
}

export function policyReceiptAnchor(receiptId: string): string {
    return `policy-receipt-${encodeURIComponent(receiptId)}`;
}
