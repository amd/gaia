// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Pure helpers ChatView uses to turn the chat stream into messages and steps. */

import type { MutableRefObject } from 'react';
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

/** Map an SSE agent event to an AgentStep for the UI. */
export function agentEventToStep(event: StreamEvent, stepIdRef: MutableRefObject<number>): AgentStep | null {
    const id = ++stepIdRef.current;
    const ts = Date.now();

    switch (event.type) {
        case 'thinking':
            return {
                id, type: 'thinking', label: 'Thinking',
                detail: event.content, active: true, timestamp: ts,
            };
        case 'tool_start':
            return {
                id, type: 'tool',
                // Label is determined by AgentActivity based on tool name
                label: 'Using tool',
                tool: event.tool,
                detail: event.detail,
                narration: event.narration,
                active: true, timestamp: ts,
                mcpServer: event.mcp_server,
            };
        case 'plan':
            return {
                id, type: 'plan', label: 'Created plan',
                planSteps: event.steps, planNarrations: event.step_narrations,
                currentStep: event.current_step, active: false,
                success: true, timestamp: ts,
            };
        case 'step':
            return {
                id, type: 'status',
                label: `Step ${event.step}${event.total ? ` of ${event.total}` : ''}`,
                active: true, timestamp: ts,
            };
        case 'status':
            if (event.message?.startsWith('Agent: ')) return null;
            return {
                id, type: 'status',
                label: event.message || event.status || 'Working',
                active: event.status === 'working',
                timestamp: ts,
            };
        case 'agent_error':
            return {
                id, type: 'error', label: 'Error',
                detail: event.content, success: false,
                active: false, timestamp: ts,
            };
        case 'policy_alert': {
            const toolName = event.tool || 'unknown tool';
            const reason =
                event.reason ||
                event.message ||
                event.content ||
                'Tool execution was blocked by governance policy.';
            return {
                id,
                type: 'policy_alert',
                label: `Policy blocked ${toolName}`,
                detail: reason,
                tool: toolName,
                decision: event.decision || 'BLOCK',
                reason,
                ruleIds: event.rule_ids ?? [],
                policyVersion: event.policy_version,
                receiptId: event.receipt_id,
                success: false,
                active: false,
                timestamp: ts,
            };
        }
        default:
            return null;
    }
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

export function policyReceiptAnchor(receiptId: string): string {
    return `policy-receipt-${encodeURIComponent(receiptId)}`;
}
