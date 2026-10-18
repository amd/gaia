// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * What the desktop tray should show, built from state the app already
 * follows: the active-run list, this chat's stream, approval prompts and
 * the system status. Nothing here asks the backend for more.
 */

import type { Session, SystemStatus } from '../types';
import type { GaiaNotification, TrayActivity, TrayChatRef } from '../types/agent';

/** Phases where the answer is waiting on the model itself, not on a tool or the user. */
const MODEL_WAIT_PHASES = new Set(['loading_model', 'downloading_model', 'model_retry']);

const RECENT_CHATS = 5;

export interface TrayActivityInput {
    backendConnected: boolean;
    systemStatus: SystemStatus | null;
    sessions: Session[];
    runningSessionIds: string[];
    currentSessionId: string | null;
    isStreaming: boolean;
    awaitingUser: boolean;
    liveStatus: string | null;
    livePhase: string | null;
    notifications: GaiaNotification[];
}

function ref(id: string, byId: Map<string, Session>): TrayChatRef {
    return { id, title: byId.get(id)?.title ?? 'Untitled chat' };
}

export function buildTrayActivity(input: TrayActivityInput): TrayActivity {
    const byId = new Map(input.sessions.map((s) => [s.id, s]));
    const streamingHere = input.isStreaming && input.currentSessionId !== null;

    const runningIds = [...input.runningSessionIds];
    if (streamingHere && !runningIds.includes(input.currentSessionId!)) runningIds.push(input.currentSessionId!);
    const running = new Set(runningIds);

    // An approval card only counts while its chat is still running; a stopped run leaves stale cards.
    const approvals = new Set(
        input.notifications
            .filter((n) => n.type === 'permission_request' && !n.response && !n.dismissed && n.sessionId && running.has(n.sessionId))
            .map((n) => n.sessionId!),
    );
    const needsYou: TrayActivity['needsYou'] = [...approvals].map((id) => ({ ...ref(id, byId), reason: 'approval' }));
    if (streamingHere && input.awaitingUser && !approvals.has(input.currentSessionId!)) {
        needsYou.push({ ...ref(input.currentSessionId!, byId), reason: 'answer' });
    }

    const waitingOnModel = streamingHere && !input.awaitingUser && input.livePhase && MODEL_WAIT_PHASES.has(input.livePhase)
        ? input.liveStatus
        : null;

    const status = input.systemStatus;
    let model: TrayActivity['model'] = null;
    if (status?.default_model_is_cloud && status.default_model_name) {
        model = { name: status.default_model_name, where: 'cloud' };
    } else if (status?.model_loaded) {
        const cloud = status.model_labels?.includes('cloud');
        const device = status.model_device && status.model_device !== 'none' ? status.model_device : null;
        model = { name: status.model_loaded, where: cloud ? 'cloud' : device };
    }

    const recent = [...input.sessions]
        .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
        .slice(0, RECENT_CHATS)
        .map((s) => ref(s.id, byId));

    return {
        backendConnected: input.backendConnected,
        // A hosted model doesn't need the local model server.
        modelServerRunning: status && !status.default_model_is_cloud ? status.lemonade_running : null,
        model,
        waitingOnModel: waitingOnModel || null,
        running: runningIds.map((id) => ref(id, byId)),
        needsYou,
        recent,
    };
}
