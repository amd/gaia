// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { ReadableStream as NodeReadableStream } from 'node:stream/web';

import type { Message, MessageList, StreamEvent, ToolConfirmRequest, UserInputRequest } from '@/api/client.types';
import { STREAM_IDLE_MS } from '@/api/constants';
import { createHttpClient } from '@/api/http';
import type { FetchResponseLike } from '@/platform/fetch.types';
import { readTurn } from '@/state/chatStreams';
import { bytes, createRecordingFetch, jsonResponse, type RecordedCall } from '@/test-support/fakeFetch';

import { attachTurn, detachTurn, sendTurn, stopTurn, type ChatStreamDeps } from './chatStream';
import { answerQuestion, confirmPermission } from './mutations';
import { queryKeys } from './queries';

interface View {
    status: string;
    liveText: string;
    events: string[];
    permission: string | null;
    question: string | null;
    error: string | null;
    transcript: string[] | null;
    cancels: number;
    /** This phone closed its stream connection. */
    dropped: boolean;
}

type Step =
    | { send: string }
    | { attach: true }
    | { events: StreamEvent[] }
    | { end: true }
    | { silence: number }
    | { stop: true }
    | { confirm: Omit<ToolConfirmRequest, 'session_id'> }
    | { answer: Omit<UserInputRequest, 'session_id'> }
    | { saved: string[] }
    | { expect: Partial<View> };

const IDLE: Partial<View> = { status: 'idle', liveText: '', events: [], permission: null, question: null, error: null };

const cases: [string, Step[]][] = [
    [
        'a normal answer',
        [
            { send: 'What is in notes.txt?' },
            { expect: { status: 'connecting', transcript: ['What is in notes.txt?'] } },
            {
                events: [
                    { type: 'status', status: 'working', phase: 'reasoning' },
                    { type: 'chunk', content: 'Let me ' },
                    { type: 'chunk', content: 'look.' },
                ],
            },
            { expect: { status: 'streaming', liveText: 'Let me look.', events: ['status'] } },
            {
                events: [
                    { type: 'tool_start', tool: 'read_file' },
                    { type: 'tool_result', tool: 'read_file', success: true },
                    { type: 'chunk', content: 'It lists groceries.' },
                ],
            },
            { expect: { liveText: 'Let me look.\n\nIt lists groceries.', events: ['status', 'tool_start', 'tool_result'] } },
            { saved: ['What is in notes.txt?', 'Let me look.\n\nIt lists groceries.'] },
            { events: [{ type: 'done', message_id: 2, content: 'Let me look.\n\nIt lists groceries.' }] },
            { end: true },
            { expect: { ...IDLE, transcript: ['What is in notes.txt?', 'Let me look.\n\nIt lists groceries.'] } },
        ],
    ],
    [
        'a permission request, allowed',
        [
            { send: 'Delete old.txt' },
            {
                events: [
                    { type: 'tool_start', tool: 'delete_file' },
                    { type: 'permission_request', tool: 'delete_file', confirm_id: 'c1', args: { path: 'old.txt' } },
                ],
            },
            { expect: { status: 'streaming', permission: 'c1', events: ['tool_start', 'permission_request'] } },
            { confirm: { approved: true, confirm_id: 'c1' } },
            { expect: { status: 'streaming', permission: null } },
            { saved: ['Delete old.txt', 'Deleted.'] },
            {
                events: [
                    { type: 'tool_result', tool: 'delete_file', success: true },
                    { type: 'chunk', content: 'Deleted.' },
                    { type: 'done', message_id: 2, content: 'Deleted.' },
                ],
            },
            { end: true },
            { expect: IDLE },
        ],
    ],
    [
        'a question, answered',
        [
            { send: 'Plan a trip' },
            {
                events: [
                    { type: 'tool_start', tool: 'ask_user' },
                    { type: 'needs_input', request_id: 'q1', question: 'Where to?', options: [] },
                ],
            },
            { expect: { status: 'streaming', question: 'q1' } },
            { answer: { request_id: 'q1', value: 'Paris' } },
            { expect: { status: 'streaming', question: null } },
            { saved: ['Plan a trip', 'Paris it is.'] },
            {
                events: [
                    { type: 'tool_end', tool: 'ask_user', success: true },
                    { type: 'chunk', content: 'Paris it is.' },
                    { type: 'done', message_id: 2, content: 'Paris it is.' },
                ],
            },
            { end: true },
            { expect: IDLE },
        ],
    ],
    [
        'an error event',
        [
            { send: 'Hi' },
            { events: [{ type: 'chunk', content: 'Hel' }, { type: 'error', content: 'Sorry, something went wrong on my end.' }] },
            { end: true },
            { expect: { ...IDLE, error: 'mobileApp.error.turnFailed' } },
        ],
    ],
    [
        'an idle stall, though not while the turn waits in line',
        [
            { send: 'Hi' },
            { events: [{ type: 'status', status: 'working', phase: 'queued', message: 'Waiting for the GPU' }] },
            { silence: STREAM_IDLE_MS },
            { expect: { status: 'streaming', error: null, dropped: false } },
            { events: [{ type: 'chunk', content: 'Half an ans' }] },
            { silence: STREAM_IDLE_MS },
            // The lost view is picked up again, not reported.
            { expect: { status: 'streaming', liveText: 'Half an ans', error: null, dropped: true } },
        ],
    ],
    [
        'a cancel, pressed before the first event',
        [
            { send: 'Write a long essay' },
            { stop: true },
            { expect: { status: 'stopping', cancels: 0 } },
            { events: [{ type: 'chunk', content: 'Once upon' }] },
            { expect: { status: 'stopping', liveText: 'Once upon', cancels: 1 } },
            { events: [{ type: 'done', message_id: 8, content: 'Once upon', cancelled: true }] },
            { end: true },
            { expect: { ...IDLE, cancels: 1 } },
        ],
    ],
    [
        'reopening a chat whose turn is running',
        [
            { attach: true },
            { expect: { status: 'idle' } },
            {
                events: [
                    { type: 'chunk', content: 'Sure.' },
                    { type: 'tool_start', tool: 'write_file' },
                    { type: 'permission_request', tool: 'write_file', confirm_id: 'c0' },
                    { type: 'tool_result', tool: 'write_file', success: true },
                    { type: 'chunk', content: 'Saved it.' },
                ],
            },
            { expect: { status: 'streaming', liveText: 'Sure.\n\nSaved it.', permission: null } },
            { saved: ['Save a note', 'Sure.\n\nSaved it.'] },
            { events: [{ type: 'done', message_id: 2, content: 'Sure.\n\nSaved it.' }] },
            { end: true },
            { expect: IDLE },
        ],
    ],
];

const BASE = 'http://127.0.0.1:4210';

interface Harness {
    sessionId: string;
    deps: ChatStreamDeps;
    calls: RecordedCall[];
    stream: { push(events: StreamEvent[]): void; end(): void };
    saved: Message[];
    close(): void;
}

function scriptedStream() {
    let controller!: { enqueue(chunk: Uint8Array): void; close(): void };
    const body = new NodeReadableStream<Uint8Array>({
        start(c) {
            controller = c;
        },
    });
    const response: FetchResponseLike = {
        ok: true,
        status: 200,
        headers: { get: () => 'text/event-stream' },
        body: body as unknown as ReadableStream<Uint8Array>,
        text: async () => '',
    };
    return {
        response,
        push(events: StreamEvent[]) {
            for (const event of events) controller.enqueue(bytes(`data: ${JSON.stringify(event)}\n\n`));
        },
        end() {
            controller.close();
        },
    };
}

async function settle(): Promise<void> {
    for (let i = 0; i < 10; i++) await jest.advanceTimersByTimeAsync(1);
}

function toMessage(content: string, index: number): Message {
    return {
        id: index + 1,
        session_id: 'saved',
        role: index % 2 ? 'assistant' : 'user',
        content,
        created_at: '2026-11-12T00:00:00Z',
        rag_sources: null,
    };
}

async function setup(sessionId: string): Promise<Harness> {
    const stream = scriptedStream();
    const saved: Message[] = [];
    const { fetch, calls } = createRecordingFetch((call) => {
        if (/\/api\/chat\/(send|attach)/.test(call.url)) return stream.response;
        if (call.url.includes('/messages?')) return jsonResponse({ messages: saved, total: saved.length });
        return jsonResponse({ status: 'ok' });
    });
    const client = createHttpClient({ baseUrl: BASE, fetch });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // An open chat screen shows the transcript, so the end of a turn re-reads it.
    const observer = new QueryObserver(queryClient, {
        queryKey: queryKeys.messages(sessionId),
        queryFn: () => client.getMessages(sessionId),
    });
    const unsubscribe = observer.subscribe(() => {});
    await settle();
    return {
        sessionId,
        deps: { client, queryClient },
        calls,
        stream,
        saved,
        close() {
            detachTurn(sessionId);
            unsubscribe();
            queryClient.clear();
        },
    };
}

function view(h: Harness): View {
    const turn = readTurn(h.sessionId);
    const transcript = h.deps.queryClient.getQueryData<MessageList>(queryKeys.messages(h.sessionId));
    return {
        status: turn.status,
        liveText: turn.liveText,
        events: turn.events.map((e) => e.type),
        permission: turn.pendingPermission?.confirm_id ?? null,
        question: turn.pendingQuestion?.request_id ?? null,
        error: turn.error?.messageKey ?? null,
        transcript: transcript ? transcript.messages.map((m) => m.content) : null,
        cancels: h.calls.filter((c) => c.url.endsWith('/api/chat/cancel')).length,
        dropped: h.calls.some((c) => /\/api\/chat\/(send|attach)/.test(c.url) && c.signal?.aborted === true),
    };
}

async function run(h: Harness, step: Step): Promise<void> {
    if ('expect' in step) {
        expect(view(h)).toMatchObject(step.expect);
        return;
    }
    if ('send' in step) expect(sendTurn(h.deps, h.sessionId, step.send)).toBe(true);
    else if ('attach' in step) attachTurn(h.deps, h.sessionId);
    else if ('events' in step) h.stream.push(step.events);
    else if ('end' in step) h.stream.end();
    else if ('silence' in step) await jest.advanceTimersByTimeAsync(step.silence);
    else if ('stop' in step) stopTurn(h.deps, h.sessionId);
    else if ('confirm' in step) await confirmPermission(h.deps.client, { session_id: h.sessionId, ...step.confirm });
    else if ('answer' in step) await answerQuestion(h.deps.client, { session_id: h.sessionId, ...step.answer });
    else h.saved.splice(0, h.saved.length, ...step.saved.map(toMessage));
    await settle();
}

describe('a chat turn on this phone', () => {
    beforeEach(() => jest.useFakeTimers({ doNotFake: ['nextTick', 'queueMicrotask'] }));
    afterEach(() => jest.useRealTimers());

    it.each(cases)('%s', async (name, steps) => {
        const h = await setup(name);
        try {
            for (const step of steps) await run(h, step);
        } finally {
            h.close();
        }
    });
});
