// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A turn blocked on an approval must not read as a command still running (#5705). */

import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AgentActivity } from '../AgentActivity';
import { MessageBubble } from '../MessageBubble';
import type { AgentStep, Message } from '../../types';

const runningShell: AgentStep = {
    id: 1, type: 'tool', label: 'run_shell_command', tool: 'run_shell_command',
    active: true, timestamp: Date.now() - 30_000,
};

const row = () => document.querySelector('.flow-tool-label')?.textContent;
const rowTimer = () => document.querySelector('.flow-tool-latency')?.textContent;

describe('AgentActivity tool row', () => {
    it('times a running tool on its own row', () => {
        render(<AgentActivity steps={[runningShell]} isActive />);
        expect(row()).toBe('Running command');
        expect(rowTimer()).toMatch(/^\d+s$/);
    });

    it('says it is waiting, with no timer, on the row an approval holds', () => {
        render(<AgentActivity steps={[runningShell]} isActive awaitingUser />);
        expect(row()).toBe('Waiting for you');
        expect(rowTimer()).toBeUndefined();
        expect(document.querySelector('.flow-tool .flow-tool-spinner')).toBeNull();
        // Which call is held stays one hover away, narration or not.
        expect(document.querySelector('.flow-tool-header')?.getAttribute('title')).toBe('Running command');
    });
});

describe('MessageBubble with an approval open', () => {
    it('holds the tool row with no timer', () => {
        const streaming: Message = {
            id: -1, session_id: 's', role: 'assistant', content: '',
            created_at: '2026-10-16T00:00:00Z', rag_sources: null,
        };
        render(
            <MessageBubble message={streaming} isStreaming agentSteps={[runningShell]} agentStepsActive awaitingUser />,
        );
        expect(row()).toBe('Waiting for you');
        expect(rowTimer()).toBeUndefined();
    });
});
