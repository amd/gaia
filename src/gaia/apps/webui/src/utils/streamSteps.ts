// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { getT } from '../i18n/useT';
import type { StreamEvent, AgentStep } from '../types';

/** The backend's periodic "Model is processing... (Ns)" tick; the live line beside GAIA's name already times that wait. */
export function isProgressTick(event: StreamEvent): boolean {
    return event.type === 'status' && event.status === 'working' && !event.phase
        && (event.message ?? '').startsWith('Model is processing');
}

/** A status event's line for the user; the tool-list re-read is said in their language. */
export function statusText(event: StreamEvent): string {
    if (event.phase === 'loading_tools') return getT()('chat.loadingTools');
    if (event.phase === 'model_queue' && event.queue) return queueText(event.queue);
    return (event.message || '').trim();
}

function queueText(queue: NonNullable<StreamEvent['queue']>): string {
    const t = getT();
    const seconds = String(Math.floor(queue.waited_s ?? 0));
    if (queue.event === 'granted') return t('chat.modelQueue.granted', { seconds });
    const length = queue.queue_length ?? 1;
    if (length > 1 && queue.position != null) {
        return t('chat.modelQueue.waitingInLine', { position: String(queue.position), length: String(length), seconds });
    }
    if (!queue.holder && queue.scope !== 'process') return t('chat.modelQueue.priority', { seconds });
    return t('chat.modelQueue.waiting', { seconds });
}

/** Map an SSE agent event to an AgentStep for the UI. */
export function agentEventToStep(event: StreamEvent, stepIdRef: { current: number }): AgentStep | null {
    const id = ++stepIdRef.current;
    const ts = Date.now();

    switch (event.type) {
        case 'thinking':
            return {
                id, type: 'thinking', label: 'Thinking',
                detail: event.content, active: true, timestamp: ts,
            };
        case 'commentary':
            return {
                id, type: 'commentary', label: 'Commentary',
                detail: event.content, active: false, timestamp: ts,
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
                mcpServerLabel: event.mcp_server_label,
                mcpTool: event.mcp_tool,
            };
        case 'skill_use':
            return {
                id, type: 'tool', label: 'Using tool', tool: 'use_skill',
                detail: event.skill, narration: `Using skill: ${event.skill}`,
                active: false, success: true, timestamp: ts,
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
                ...(event.status === 'warning' ? { success: false } : {}),
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
