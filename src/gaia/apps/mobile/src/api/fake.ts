// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type {
    CreateSessionRequest,
    GaiaClient,
    Goal,
    HealthResponse,
    Message,
    MessageList,
    Schedule,
    ScheduleResult,
    Session,
    SessionList,
    StreamEvent,
    SystemStatus,
    ToolConfirmRequest,
    UserInputRequest,
} from './client.types';
import { GaiaApiError } from './errors';

/** What a test scripts the fake PC with. */
export interface FakeClientData {
    sessions?: Session[];
    schedules?: Schedule[];
    /** Past runs by schedule name, newest first. */
    scheduleResults?: Record<string, ScheduleResult[]>;
    messages?: Record<string, Message[]>;
    /** What every send streams back; a function takes the sent text and streams what a test scripts. */
    replies?: StreamEvent[] | ((message: string) => AsyncIterable<StreamEvent>);
    /** What attachToRun replays, by chat; a chat not listed has no turn running. */
    running?: Record<string, StreamEvent[]>;
    status?: SystemStatus;
    sessionEvents?: unknown[];
    goals?: Goal[] | Error;
}

/** The client a test drives, with what it was asked to do. */
export interface FakeGaiaClient extends GaiaClient {
    readonly sent: { sessionId: string; message: string }[];
    readonly created: CreateSessionRequest[];
    readonly cancelled: string[];
    readonly confirmations: ToolConfirmRequest[];
    readonly answers: UserInputRequest[];
}

/** A GaiaClient over in-memory data, for tests only; the app never ships it. */
export function createFakeClient(data: FakeClientData = {}): FakeGaiaClient {
    const sessions = [...(data.sessions ?? [])];
    const messages = data.messages ?? {};
    const replies = data.replies ?? [{ type: 'done' } as StreamEvent];
    const running = data.running ?? {};
    const schedules = [...(data.schedules ?? [])];
    const goals = data.goals instanceof Error ? [] : [...(data.goals ?? [])];
    const sent: FakeGaiaClient['sent'] = [];
    const created: CreateSessionRequest[] = [];
    const cancelled: string[] = [];
    const confirmations: ToolConfirmRequest[] = [];
    const answers: UserInputRequest[] = [];

    function findSession(sessionId: string): Session {
        const session = sessions.find((s) => s.id === sessionId);
        if (!session) throw new GaiaApiError(404, 'Session not found');
        return session;
    }

    function findSchedule(name: string): Schedule {
        const schedule = schedules.find((s) => s.name === name);
        if (!schedule) throw new GaiaApiError(404, `Schedule '${name}' not found`);
        return schedule;
    }

    return {
        sent,
        created,
        cancelled,
        confirmations,
        answers,
        async health(): Promise<HealthResponse> {
            return {
                status: 'ok',
                service: 'gaia-agent-ui',
                stats: { sessions: sessions.length, messages: 0, documents: 0, total_chunks: 0, total_size_bytes: 0 },
            };
        },
        async listSessions(): Promise<SessionList> {
            return { sessions: [...sessions], total: sessions.length };
        },
        async getSession(sessionId) {
            return findSession(sessionId);
        },
        async createSession(request = {}) {
            created.push(request);
            const now = new Date().toISOString();
            const session: Session = {
                id: `fake-${created.length}`,
                title: request.title ?? '',
                created_at: now,
                updated_at: now,
                model: request.model ?? '',
                system_prompt: request.system_prompt ?? null,
                message_count: 0,
                document_ids: request.document_ids ?? [],
            };
            sessions.unshift(session);
            return session;
        },
        async getMessages(sessionId): Promise<MessageList> {
            const list = messages[sessionId] ?? [];
            return { messages: list, total: list.length };
        },
        async *sendMessage(sessionId, message) {
            sent.push({ sessionId, message });
            yield* typeof replies === 'function' ? replies(message) : replies;
        },
        async *attachToRun(sessionId) {
            yield* running[sessionId] ?? [];
        },
        async cancelChat(sessionId) {
            cancelled.push(sessionId);
            return { status: 'ok', cancelled: true };
        },
        async confirmTool(request) {
            confirmations.push(request);
            return { status: 'ok', approved: request.approved, granted: null };
        },
        async sendUserInput(request) {
            answers.push(request);
            return { status: 'ok', request_id: request.request_id };
        },
        async *watchSessionEvents() {
            yield* (data.sessionEvents ?? []) as StreamEvent[];
        },
        async getActiveRuns() {
            return { session_ids: [], queued: [], awaiting_user: {} };
        },
        async listSchedules() {
            return { schedules: [...schedules], total: schedules.length };
        },
        async listGoals() {
            if (data.goals instanceof Error) throw data.goals;
            return { goals, total: goals.length };
        },
        async listScheduleResults(name, limit) {
            findSchedule(name);
            const all = data.scheduleResults?.[name] ?? [];
            return { results: all.slice(0, limit), total: all.length };
        },
        async getSystemStatus() {
            if (!data.status) throw new GaiaApiError(503, 'This fake client was given no system status.');
            return data.status;
        },
    };
}
