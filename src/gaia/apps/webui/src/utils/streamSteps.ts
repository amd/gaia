// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { StreamEvent, AgentStep } from '../types';

/** The backend's periodic "Model is processing... (Ns)" tick; the live line beside GAIA's name already times that wait. */
export function isProgressTick(event: StreamEvent): boolean {
    return event.type === 'status' && event.status === 'working' && !event.phase
        && (event.message ?? '').startsWith('Model is processing');
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
