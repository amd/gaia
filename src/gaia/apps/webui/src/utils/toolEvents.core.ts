// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { PATH_ACCESS_TOOL, PROJECT_TRUST_TOOL, pathAccessQuestion, pathAccessTitle, isPathAccessFollowUp } from './permissionPrompts.core';
import type { GaiaNotification } from '../types/agent';
import type { StreamEvent, AgentStep } from '../types';
import type { Translate } from '../i18n/translator';
import { APP_NAME } from './constants';

/** The prompt a re-attach replays for a `permission_request` already known, if any. */
export function knownPermissionPrompt(event: StreamEvent, notifications: readonly GaiaNotification[]): GaiaNotification | undefined {
    return event.confirm_id ? notifications.find((n) => n.id === event.confirm_id) : undefined;
}

/** The notification for a `permission_request` that is not already known. */
export function permissionRequestNotification(
    event: StreamEvent,
    sessionId: string,
    notifications: GaiaNotification[],
    t: Translate,
): GaiaNotification {
    const toolName = event.tool || '';
    const toolLabel = event.tool_label || toolName;
    const isPathAccess = toolName === PATH_ACCESS_TOOL;
    const followUp = isPathAccess && isPathAccessFollowUp(notifications, sessionId, event.args);
    return {
        id: event.confirm_id ?? `perm-${Date.now()}`,
        type: 'permission_request',
        agentId: sessionId,
        sessionId,
        agentName: APP_NAME,
        title: isPathAccess
            ? pathAccessTitle(event.args, followUp, t)
            : toolName === PROJECT_TRUST_TOOL ? t('prompt.trust.title') : t('prompt.title.tool', { tool: toolLabel }),
        message: isPathAccess
            ? pathAccessQuestion(event.args, followUp, t)
            : toolName === PROJECT_TRUST_TOOL
                ? t('prompt.trust.ask')
                : t('prompt.needsOk', { tool: toolLabel }),
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
        expiresAt: typeof event.expires_at === 'number' ? event.expires_at * 1000 : undefined,
    };
}

/** The notification for a tool call that governance policy blocked. */
export function policyAlertNotification(
    event: StreamEvent,
    sessionId: string,
    fallbackId: string,
    t: Translate,
): GaiaNotification & { tool: string } {
    const toolName = event.tool || t('activity.policy.unknownTool');
    const reason =
        event.reason ||
        event.message ||
        event.content ||
        t('activity.policy.defaultReason');
    return {
        id: event.receipt_id ?? fallbackId,
        type: 'policy_alert',
        agentId: sessionId,
        agentName: APP_NAME,
        title: t('activity.policy.blocked', { tool: toolName }),
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

/** How a gated call ended without running: refused by the user, or its prompt timed out. */
function denial(event: StreamEvent): 'declined' | 'timedOut' | null {
    const data = (event.data && typeof event.data === 'object' ? event.data : {}) as { status?: unknown; timed_out?: unknown };
    const status = event.status ?? data.status;
    if (status !== 'denied') return null;
    return event.timed_out === true || data.timed_out === true ? 'timedOut' : 'declined';
}

/** What a `tool_result` adds to the tool step it finishes. */
export function toolResultUpdates(event: StreamEvent): Partial<AgentStep> {
    const updates: Partial<AgentStep> = {
        result: event.summary || event.title || 'Done',
        active: false,
        success: event.success !== false,
        latencyMs: event.latency_ms,
    };
    const denied = denial(event);
    if (denied) updates[denied] = true;
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
    // A relayed result carries it inside `data`.
    const relayed = event.data && typeof event.data === 'object' ? (event.data as { needs_model?: unknown }).needs_model : undefined;
    if ((event.needs_model ?? relayed) === 'vision') updates.needsModel = 'vision';
    return updates;
}
