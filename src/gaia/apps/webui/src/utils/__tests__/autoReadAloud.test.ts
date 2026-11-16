// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import type { AgentStep, Message } from '../../types';
import { readsAloudWhenDone } from '../autoReadAloud';

const reply = (over: Partial<Message> = {}): Message => ({
    id: 7,
    session_id: 's',
    role: 'assistant',
    content: 'Here is a short answer. It has two sentences.',
    created_at: '2026-11-05T00:00:00Z',
    rag_sources: null,
    ...over,
});

const errorStep = (detail: string): AgentStep => ({
    id: 1, type: 'error', label: 'Error', detail, active: false, timestamp: 0,
} as AgentStep);

describe('readsAloudWhenDone', () => {
    it('reads a finished answer', () => {
        expect(readsAloudWhenDone(reply())).toBe(true);
    });

    it('skips an empty or stopped answer', () => {
        expect(readsAloudWhenDone(reply({ content: '  ' }))).toBe(false);
        expect(readsAloudWhenDone(reply({ stopState: 'stopped' }))).toBe(false);
    });

    it('skips an answer that is only code', () => {
        expect(readsAloudWhenDone(reply({ content: '```python\nprint("hello")\n```' }))).toBe(false);
    });

    it('skips a turn that failed', () => {
        expect(readsAloudWhenDone(reply({ failure: 'model_server_down' }))).toBe(false);
        expect(readsAloudWhenDone(reply({ failed: true }))).toBe(false);
    });

    it('skips an answer that is only the agent error', () => {
        const text = "Agent error: Requested device 'gpu' is not available on this host (detected: []).";
        expect(readsAloudWhenDone(reply({ content: text, agentSteps: [errorStep(text)] }))).toBe(false);
    });

    it('skips an answer that reads as an error', () => {
        expect(readsAloudWhenDone(reply({ content: 'Error: connection refused' }))).toBe(false);
    });

    it('reads an answer that only quotes a tool error', () => {
        const steps = [errorStep('File not found: notes.txt')];
        expect(readsAloudWhenDone(reply({ content: 'I could not find notes.txt, so I searched instead.', agentSteps: steps }))).toBe(true);
    });
});
