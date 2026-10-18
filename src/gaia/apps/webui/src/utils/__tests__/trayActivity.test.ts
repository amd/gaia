// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { buildTrayActivity, type TrayActivityInput } from '../trayActivity';
import type { Session, SystemStatus } from '../../types';
import type { GaiaNotification } from '../../types/agent';

function session(id: string, title: string, updated_at: string): Session {
    return {
        id, title, updated_at, created_at: updated_at, model: 'm', system_prompt: null,
        message_count: 1, document_ids: [],
    };
}

const STATUS = {
    lemonade_running: true, model_loaded: 'Gemma-4-E4B-it-GGUF', model_device: 'gpu',
    model_labels: ['chat'], default_model_is_cloud: false, default_model_name: 'Gemma-4-E4B-it-GGUF',
} as SystemStatus;

function input(overrides: Partial<TrayActivityInput> = {}): TrayActivityInput {
    return {
        backendConnected: true,
        systemStatus: STATUS,
        sessions: [
            session('a', 'Trip plan', '2026-10-18T10:00:00'),
            session('b', 'Budget', '2026-10-18T12:00:00'),
            session('c', 'Old', '2026-10-01T09:00:00'),
        ],
        runningSessionIds: [],
        currentSessionId: null,
        isStreaming: false,
        awaitingUser: false,
        liveStatus: null,
        livePhase: null,
        notifications: [],
        ...overrides,
    };
}

function approval(sessionId: string, extra: Partial<GaiaNotification> = {}): GaiaNotification {
    return {
        id: `p-${sessionId}`, type: 'permission_request', agentId: sessionId, sessionId, agentName: 'GAIA',
        title: 'Allow?', message: '', timestamp: 0, read: false, dismissed: false, priority: 'high', ...extra,
    } as GaiaNotification;
}

describe('buildTrayActivity', () => {
    it('idle: model, recent chats newest first, nothing running', () => {
        const a = buildTrayActivity(input());
        expect(a.model).toEqual({ name: 'Gemma-4-E4B-it-GGUF', where: 'gpu' });
        expect(a.modelServerRunning).toBe(true);
        expect(a.recent.map((c) => c.title)).toEqual(['Budget', 'Trip plan', 'Old']);
        expect(a.running).toEqual([]);
        expect(a.needsYou).toEqual([]);
        expect(a.waitingOnModel).toBeNull();
    });

    it('a background run and this chat streaming both count as running', () => {
        const a = buildTrayActivity(input({ runningSessionIds: ['c'], currentSessionId: 'a', isStreaming: true }));
        expect(a.running).toEqual([{ id: 'c', title: 'Old' }, { id: 'a', title: 'Trip plan' }]);
    });

    it('an open approval in a running chat needs you; a stale one does not', () => {
        const a = buildTrayActivity(input({
            runningSessionIds: ['a'],
            notifications: [approval('a'), approval('b'), approval('c', { response: 'allow' })],
        }));
        expect(a.needsYou).toEqual([{ id: 'a', title: 'Trip plan', reason: 'approval' }]);
    });

    it('this chat waiting on a question needs your answer', () => {
        const a = buildTrayActivity(input({ currentSessionId: 'b', isStreaming: true, awaitingUser: true }));
        expect(a.needsYou).toEqual([{ id: 'b', title: 'Budget', reason: 'answer' }]);
    });

    it('a chat waiting for approval is listed once', () => {
        const a = buildTrayActivity(input({
            currentSessionId: 'a', isStreaming: true, awaitingUser: true, notifications: [approval('a')],
        }));
        expect(a.needsYou).toEqual([{ id: 'a', title: 'Trip plan', reason: 'approval' }]);
    });

    it.each([['loading_model'], ['downloading_model'], ['model_retry']])(
        'the %s phase is waiting for the model',
        (phase) => {
            const a = buildTrayActivity(input({
                currentSessionId: 'a', isStreaming: true, livePhase: phase, liveStatus: 'Loading Gemma into memory',
            }));
            expect(a.waitingOnModel).toBe('Loading Gemma into memory');
        },
    );

    it.each([['reasoning'], ['tool'], [null]])('the %s phase is just working', (phase) => {
        const a = buildTrayActivity(input({
            currentSessionId: 'a', isStreaming: true, livePhase: phase, liveStatus: 'Reasoning',
        }));
        expect(a.waitingOnModel).toBeNull();
    });

    it('a phase left over after the stream ended is ignored', () => {
        expect(buildTrayActivity(input({ livePhase: 'loading_model', liveStatus: 'x' })).waitingOnModel).toBeNull();
    });

    it('a hosted model is not an error when the local model server is off', () => {
        const a = buildTrayActivity(input({
            systemStatus: { ...STATUS, lemonade_running: false, default_model_is_cloud: true, default_model_name: 'big-model' },
        }));
        expect(a.modelServerRunning).toBeNull();
        expect(a.model).toEqual({ name: 'big-model', where: 'cloud' });
    });

    it('a stopped local model server is reported', () => {
        expect(buildTrayActivity(input({ systemStatus: { ...STATUS, lemonade_running: false } })).modelServerRunning).toBe(false);
    });

    it('"none" is not a device', () => {
        const a = buildTrayActivity(input({ systemStatus: { ...STATUS, model_device: 'none' } }));
        expect(a.model).toEqual({ name: 'Gemma-4-E4B-it-GGUF', where: null });
    });

    it('no status yet: nothing known about the model', () => {
        const a = buildTrayActivity(input({ systemStatus: null }));
        expect(a.model).toBeNull();
        expect(a.modelServerRunning).toBeNull();
    });

    it('at most five recent chats', () => {
        const sessions = Array.from({ length: 8 }, (_, i) => session(`s${i}`, `Chat ${i}`, `2026-10-0${i + 1}T00:00:00`));
        expect(buildTrayActivity(input({ sessions })).recent).toHaveLength(5);
    });
});
