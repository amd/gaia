// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useNotificationStore, PATH_ACCESS_TOOL, PROJECT_TRUST_TOOL, pathAccessQuestion, pathAccessTitle, isPathAccessFollowUp } from '../stores/notificationStore';
import type { GaiaNotification } from '../types/agent';
import type { StreamEvent, AgentStep } from '../types';

/** Raise the prompt for a `permission_request`, or bring back the one a re-attach replays. */
export function showPermissionRequest(event: StreamEvent, sessionId: string): void {
    const toolName = event.tool || '';
    const toolLabel = event.tool_label || toolName;
    const { addNotification: addNotif, notifications } = useNotificationStore.getState();
    // A re-attach replays the run: keep an answered prompt as it is, and
    // bring back one still waiting that earlier replayed steps hid.
    const known = event.confirm_id ? notifications.find((n) => n.id === event.confirm_id) : undefined;
    if (known) {
        if (!known.response && known.dismissed) {
            useNotificationStore.setState((st) => ({
                notifications: st.notifications.map((n) => (n.id === known.id ? { ...n, dismissed: false } : n)),
            }));
        }
        return;
    }
    const isPathAccess = toolName === PATH_ACCESS_TOOL;
    const followUp = isPathAccess && isPathAccessFollowUp(notifications, sessionId, event.args);
    addNotif({
        id: event.confirm_id ?? `perm-${Date.now()}`,
        type: 'permission_request',
        agentId: sessionId,
        sessionId,
        agentName: 'GAIA',
        title: isPathAccess
            ? pathAccessTitle(event.args, followUp)
            : toolName === PROJECT_TRUST_TOOL ? "Trust this folder's instructions and skills?" : `Allow ${toolLabel}?`,
        message: isPathAccess
            ? pathAccessQuestion(event.args, followUp)
            : toolName === PROJECT_TRUST_TOOL
                ? "GAIA would put this folder's CLAUDE.md, AGENTS.md and skills in the model's prompt, now and every time you work in it."
                : `GAIA needs your OK before ${toolLabel}.`,
        timestamp: Date.now(),
        read: false,
        dismissed: false,
        priority: 'high',
        tool: toolName,
        toolArgs: event.args as Record<string, unknown> | undefined,
        confirmId: event.confirm_id,
        alwaysScope: typeof event.always_scope === 'string' ? event.always_scope : undefined,
        alwaysLabel: typeof event.always_label === 'string' && event.always_label ? event.always_label : undefined,
        toolLabel: event.tool_label || undefined,
        risk: typeof event.risk === 'string' ? event.risk : undefined,
        // A relayed agent sends a summary of the call instead of its args.
        summary: typeof event.summary === 'string' && event.summary ? event.summary : undefined,
        pathFollowUp: followUp || undefined,
        // No timeout from the backend means it waits; don't invent one.
        timeoutSeconds: event.timeout_seconds,
    });
}

/** The notification for a tool call that governance policy blocked. */
export function policyAlertNotification(
    event: StreamEvent,
    sessionId: string,
    fallbackId: string,
): GaiaNotification & { tool: string } {
    const toolName = event.tool || 'unknown tool';
    const reason =
        event.reason ||
        event.message ||
        event.content ||
        'Tool execution was blocked by governance policy.';
    return {
        id: event.receipt_id ?? fallbackId,
        type: 'policy_alert',
        agentId: sessionId,
        agentName: 'GAIA',
        title: `Blocked: ${toolName} is restricted by policy.`,
        message: reason,
        timestamp: Date.now(),
        read: false,
        dismissed: false,
        priority: 'critical',
        tool: toolName,
        decision: event.decision || 'BLOCK',
        reason,
        ruleIds: event.rule_ids ?? [],
        policyVersion: event.policy_version,
        receiptId: event.receipt_id,
    };
}

/** A call the user said no to; a relayed result carries its status inside `data`. */
function isDeclined(event: StreamEvent): boolean {
    const data = (event.data && typeof event.data === 'object' ? event.data : {}) as { status?: unknown; timed_out?: unknown };
    const status = event.status ?? data.status;
    return status === 'denied' && event.timed_out !== true && data.timed_out !== true;
}

/** What a `tool_result` adds to the tool step it finishes. */
export function toolResultUpdates(event: StreamEvent): Partial<AgentStep> {
    const updates: Partial<AgentStep> = {
        result: event.summary || event.title || 'Done',
        active: false,
        success: event.success !== false,
        latencyMs: event.latency_ms,
    };
    if (isDeclined(event)) updates.declined = true;
    // Pass through structured command output if available
    if (event.command_output) {
        updates.commandOutput = {
            command: event.command_output.command,
            stdout: event.command_output.stdout,
            stderr: event.command_output.stderr,
            returnCode: event.command_output.return_code,
            expectedExit: event.command_output.expected_exit,
            cwd: event.command_output.cwd,
            durationSeconds: event.command_output.duration_seconds,
            truncated: event.command_output.truncated,
        };
    }
    if (event.mcp_output?.text) updates.mcpOutput = event.mcp_output;
    // Pass through retrieval chunks if available
    if (event.result_data?.chunks && event.result_data.chunks.length > 0) {
        updates.retrievalChunks = event.result_data.chunks.map((c) => ({
            id: c.id,
            source: c.source,
            sourcePath: c.sourcePath,
            page: c.page,
            score: c.score,
            preview: c.preview,
            content: c.content,
        }));
    }
    // Pass through file list if available
    if (event.result_data?.type === 'file_list' &&
        (event.result_data as any).files?.length > 0) {
        updates.fileList = {
            files: (event.result_data as any).files,
            total: (event.result_data as any).total ??
                (event.result_data as any).files.length,
        };
    }
    return updates;
}
