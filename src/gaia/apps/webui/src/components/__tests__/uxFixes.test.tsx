// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MessageBubble, escapeBareNumberLines } from '../MessageBubble';
import { AgentActivity, formatArgsDisplay, formatToolLatency } from '../AgentActivity';
import { isEchoOfFailedTool } from '../ChatView';
import { PermissionPrompt } from '../PermissionPrompt';
import { useChatStore } from '../../stores/chatStore';
import { useNotificationStore } from '../../stores/notificationStore';
import type { AgentStep, Message } from '../../types';

vi.mock('../../services/api');

const baseMessage: Message = {
    id: 1, session_id: 's', role: 'assistant', content: '',
    created_at: '2026-07-16T00:00:00.000Z', rag_sources: null,
};
const renderContent = (content: string) =>
    render(<MessageBubble message={{ ...baseMessage, content }} />);

describe('a bare number answer', () => {
    it('shows "391." instead of an empty list marker', () => {
        const { container } = renderContent('391.');
        expect(container.querySelector('ol')).toBeNull();
        expect(container.textContent).toContain('391.');
    });

    it('keeps the start of a real ordered list', () => {
        const { container } = renderContent('391. first\n392. second');
        expect(container.querySelector('ol')?.getAttribute('start')).toBe('391');
    });

    it('leaves fenced code alone', () => {
        expect(escapeBareNumberLines('```\n1.\n```')).toBe('```\n1.\n```');
        expect(escapeBareNumberLines('42.')).toBe('42\\.');
    });
});

describe('path chips', () => {
    it('covers a folder path whose last segment has no extension', () => {
        const { container } = renderContent('In C:\\Users\\Kalin\\gaia-ux-sandbox\\notes find it.');
        expect(container.querySelector('.file-path-link')?.textContent).toBe('C:\\Users\\Kalin\\gaia-ux-sandbox\\notes');
    });

    it('still covers a file path and leaves the full stop outside', () => {
        const { container } = renderContent('Saved to C:\\a\\b.txt.');
        expect(container.querySelector('.file-path-link')?.textContent).toBe('C:\\a\\b.txt');
        expect(container.querySelector('.md-p')?.textContent).toBe('Saved to C:\\a\\b.txt.');
    });
});

describe('tool arguments', () => {
    it('does not read a drive letter as a key', () => {
        render(<>{formatArgsDisplay('file_path: C:\\Users\\x.pdf\nsummary_type: detailed')}</>);
        expect(screen.getByText('file_path')).toBeInTheDocument();
        expect(screen.getByText('C:\\Users\\x.pdf')).toBeInTheDocument();
        expect(screen.queryByText('C')).toBeNull();
    });

    it('keeps a bare path as one value', () => {
        render(<>{formatArgsDisplay('C:\\a\\b.txt\nC:\\c')}</>);
        expect(screen.getByText('C:\\a\\b.txt')).toBeInTheDocument();
    });
});

describe('tool latency', () => {
    it.each([[47031, '47.0s'], [312.4, '312ms'], [0.2, '<1ms']])('%s ms -> %s', (ms, text) => {
        expect(formatToolLatency(ms)).toBe(text);
    });
});

describe('step count', () => {
    it('does not count progress-status rows, which are not saved with the turn', () => {
        const steps: AgentStep[] = [
            { id: 1, type: 'tool', label: 'x', tool: 'read_file', active: false, success: true, timestamp: 1 },
            { id: 2, type: 'status', label: 'Model is processing', active: false, timestamp: 2 },
            { id: 3, type: 'tool', label: 'x', tool: 'read_file', active: false, success: true, timestamp: 3 },
        ];
        render(<AgentActivity steps={steps} isActive={false} variant="summary" />);
        expect(screen.getByText(/^2 steps/)).toBeInTheDocument();
    });
});

describe('a running tool', () => {
    afterEach(() => useChatStore.getState().resetStreaming());

    it('stays running when a status arrives before its result', () => {
        const { addAgentStep } = useChatStore.getState();
        addAgentStep({ id: 1, type: 'tool', label: 'x', tool: 'summarize_document', active: true, timestamp: 1 });
        addAgentStep({ id: 2, type: 'status', label: 'Model is processing', active: true, timestamp: 2 });
        expect(useChatStore.getState().agentSteps[0].active).toBe(true);
    });
});

describe('a recoverable tool error', () => {
    const failed: AgentStep = { id: 1, type: 'tool', label: 'x', tool: 'run_shell_command', active: false, success: false, result: 'boom', timestamp: 1 };

    it('is not shown a second time under the failed tool', () => {
        expect(isEchoOfFailedTool({ type: 'agent_error', content: 'boom', recoverable: true }, [failed])).toBe(true);
    });

    it('still shows a turn-ending error', () => {
        expect(isEchoOfFailedTool({ type: 'agent_error', content: 'boom' }, [failed])).toBe(false);
    });

    it('is not shown twice when the tool raised, before its result lands', () => {
        const running: AgentStep = { id: 1, type: 'tool', label: 'x', tool: 'run_shell_command', active: true, timestamp: 1 };
        expect(isEchoOfFailedTool({ type: 'agent_error', content: 'boom', recoverable: true }, [running])).toBe(true);
    });
});

describe('the live status while GAIA waits on the user', () => {
    const streaming = (props: { liveStatus?: string | null; awaitingUser?: boolean }) =>
        render(<MessageBubble message={{ ...baseMessage, id: -1 }} isStreaming agentStepsActive {...props} />);

    // The label types out one timer per character, so step the clock rather than jump it.
    const wait = (ms: number) => {
        for (let t = 0; t < ms; t += 100) act(() => { vi.advanceTimersByTime(100); });
    };

    afterEach(() => vi.useRealTimers());

    it('says it is waiting for you and does not time the model', () => {
        vi.useFakeTimers();
        const { container } = streaming({ liveStatus: 'Reading your request', awaitingUser: true });
        wait(10_000);
        const indicator = container.querySelector('.thinking-indicator');
        expect(indicator?.textContent).toContain('Waiting for you');
        expect(indicator?.textContent).not.toContain('Reading your request');
        expect(container.querySelector('.thinking-indicator-elapsed')).toBeNull();
    });

    it('counts seconds while the model works', () => {
        vi.useFakeTimers();
        const { container } = streaming({ liveStatus: 'Reading your request' });
        wait(5_000);
        expect(container.querySelector('.thinking-indicator-elapsed')?.textContent).toMatch(/\d+s/);
    });
});

describe('path access prompt', () => {
    afterEach(() => useNotificationStore.setState({ notifications: [] }));

    it('offers to allow for this chat, matching the question', () => {
        useNotificationStore.getState().addNotification({
            id: 'p', type: 'permission_request', agentId: 'c', sessionId: 'c', agentName: 'GAIA',
            title: 'Let GAIA use this file?', message: 'Allow it for this chat?', timestamp: 1,
            read: false, dismissed: false, priority: 'high', tool: 'allow_path_access',
            toolArgs: { path: 'C:\\a.pdf', kind: 'file' },
        });
        render(<PermissionPrompt sessionId="c" />);
        expect(screen.getByRole('button', { name: 'Allow for this chat' })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Allow once' })).toBeNull();
    });
});
