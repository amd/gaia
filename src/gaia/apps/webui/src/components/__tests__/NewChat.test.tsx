// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NewChat, START_RETRY_MS, loadingLine } from '../NewChat';
import { forgetGreeting } from '../../hooks/useGreeting';
import { useChatStore } from '../../stores/chatStore';
import { useModelStore } from '../../stores/modelStore';
import * as api from '../../services/api';
import type { GreetingEvent, StartScreen, SystemStatus } from '../../types';

vi.mock('../../services/api');

const START: StartScreen = {
    cards: [
        { skill: 'document-brief', title: 'Ask about your documents', note: '2 indexed documents', prompt: 'Using my indexed documents, give me the key points.', needs_input: false },
        { skill: 'research-report', title: 'Research a topic', note: 'A cited report from the web', prompt: 'Research and write a short cited report on: ', needs_input: true },
    ],
    working_folder: { path: '/home/u/my-project', name: 'my-project', gaia_folder: false },
};

/** The greeting stream answers with *events*, then ends. */
function greetWith(...events: GreetingEvent[]) {
    vi.mocked(api.streamGreeting).mockImplementation(async (onEvent) => {
        for (const event of events) onEvent(event);
    });
}

/** The greeting stream stays open; the test sends its events. */
function pendingGreeting() {
    let onEvent: ((event: GreetingEvent) => void) | null = null;
    let signal: AbortSignal | null = null;
    vi.mocked(api.streamGreeting).mockImplementation((handler, abort) => {
        onEvent = handler;
        signal = abort;
        return new Promise(() => {});
    });
    return {
        emit: (event: GreetingEvent) => onEvent?.(event),
        signal: () => signal,
    };
}

beforeEach(() => {
    vi.clearAllMocks();
    forgetGreeting();
    useChatStore.setState({ systemStatus: null });
    greetWith({ type: 'done', text: 'Hello. What should we work on?' });
    vi.mocked(api.getStartScreen).mockResolvedValue(START);
});

describe('NewChat while GAIA is starting', () => {
    it('holds the first message instead of sending one the chat would drop', () => {
        useChatStore.setState({ systemStatus: { init_state: 'initializing' } as SystemStatus });
        const onSend = vi.fn().mockResolvedValue(undefined);
        render(<NewChat onSend={onSend} />);

        const box = screen.getByLabelText('Message');
        expect(box).toBeDisabled();
        expect(box).toHaveAttribute('placeholder', 'GAIA is starting…');
        fireEvent.keyDown(box, { key: 'Enter' });
        expect(onSend).not.toHaveBeenCalled();
    });

    it('sends once GAIA is ready', () => {
        useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus });
        const onSend = vi.fn().mockResolvedValue(undefined);
        render(<NewChat onSend={onSend} />);

        const box = screen.getByLabelText('Message');
        fireEvent.change(box, { target: { value: 'hello' } });
        fireEvent.keyDown(box, { key: 'Enter' });
        expect(onSend).toHaveBeenCalledWith('hello');
    });
});

describe('NewChat while the agent gets ready', () => {
    it('shows the loading screen, not a greeting, and keeps the message box open', async () => {
        useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus });
        const greet = pendingGreeting();
        const onSend = vi.fn().mockResolvedValue(undefined);
        render(<NewChat onSend={onSend} />);

        expect(await screen.findByRole('status')).toHaveTextContent('Starting GAIA… 0s');
        act(() => greet.emit({ type: 'status', phase: 'building' }));
        expect(screen.getByRole('status')).toHaveTextContent('Starting the agent… 0s');
        expect(screen.queryByRole('heading', { level: 1 })).toBeNull();
        const box = screen.getByLabelText('Message');
        expect(box).toBeEnabled();
        fireEvent.change(box, { target: { value: 'hello' } });
        fireEvent.keyDown(box, { key: 'Enter' });
        expect(onSend).toHaveBeenCalledWith('hello');
    });

    it('names the start-up task while GAIA itself is starting', async () => {
        useChatStore.setState({
            systemStatus: {
                init_state: 'initializing',
                init_tasks: [{ name: 'Checking LLM server', status: 'done' }, { name: 'Loading AI model', status: 'running' }],
            } as SystemStatus,
        });
        pendingGreeting();
        render(<NewChat onSend={vi.fn()} />);
        expect(await screen.findByRole('status')).toHaveTextContent('Loading AI model… 0s');
    });

    it('counts the seconds while it waits', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        try {
            pendingGreeting();
            render(<NewChat onSend={vi.fn()} />);
            await screen.findByText('Starting GAIA… 0s');
            await act(() => vi.advanceTimersByTimeAsync(2_000));
            expect(screen.getByRole('status')).toHaveTextContent('Starting GAIA… 2s');
        } finally {
            vi.useRealTimers();
        }
    });
});

describe('NewChat greeting', () => {
    it("shows the agent's own words as they stream in", async () => {
        const greet = pendingGreeting();
        render(<NewChat onSend={vi.fn()} />);
        act(() => greet.emit({ type: 'chunk', content: 'Welcome back. ' }));
        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Welcome back.');
        expect(screen.queryByRole('status')).toBeNull();
        act(() => greet.emit({ type: 'chunk', content: 'Shall we finish the parser?' }));
        act(() => greet.emit({ type: 'done', text: 'Welcome back. Shall we finish the parser?' }));
        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Welcome back. Shall we finish the parser?');
        expect(screen.getByRole('heading', { level: 1 })).toHaveClass('new-chat-greeting');
    });

    it('asks once per empty chat, not on every render or remount', async () => {
        greetWith({ type: 'done', text: 'Hi there. What are we building today?' });
        const { unmount } = render(<NewChat onSend={vi.fn()} />);
        await screen.findByRole('heading', { name: 'Hi there. What are we building today?' });
        unmount();
        render(<NewChat onSend={vi.fn()} />);
        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Hi there. What are we building today?');
        expect(api.streamGreeting).toHaveBeenCalledTimes(1);
    });

    it('asks again for the next empty chat once this one is started', async () => {
        greetWith({ type: 'done', text: 'Hello.' });
        const { unmount } = render(<NewChat onSend={vi.fn()} />);
        await screen.findByRole('heading', { name: 'Hello.' });
        unmount();
        forgetGreeting();
        render(<NewChat onSend={vi.fn()} />);
        await waitFor(() => expect(api.streamGreeting).toHaveBeenCalledTimes(2));
    });

    it('says why there is no greeting, with a retry, instead of a canned one', async () => {
        greetWith({ type: 'error', message: "GAIA's agent couldn't write a greeting: Lemonade is not reachable." });
        render(<NewChat onSend={vi.fn()} />);
        expect(await screen.findByRole('alert')).toHaveTextContent("GAIA's agent couldn't write a greeting: Lemonade is not reachable.");
        expect(screen.queryByRole('heading', { level: 1 })).toBeNull();

        greetWith({ type: 'done', text: 'Back on track. What next?' });
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await screen.findByRole('heading', { name: 'Back on track. What next?' })).toBeInTheDocument();
        expect(api.streamGreeting).toHaveBeenCalledTimes(2);
    });

    it('stops the greeting when the empty chat goes away', () => {
        const greet = pendingGreeting();
        const { unmount } = render(<NewChat onSend={vi.fn()} />);
        unmount();
        expect(greet.signal()?.aborted).toBe(true);
    });
});

describe('NewChat empty state', () => {
    it('sends no chat message of its own', async () => {
        render(<NewChat onSend={vi.fn()} />);
        await screen.findByRole('list', { name: 'Things to try' });
        expect(api.sendMessageStream).not.toHaveBeenCalled();
        expect(api.loadModel).not.toHaveBeenCalled();
    });
});

describe('NewChat loading line', () => {
    const base = { kind: 'loading' as const, model: null, device: null, seconds: 2 };

    it('names each start-up step', () => {
        expect(loadingLine({ ...base, phase: 'starting' })).toBe('Starting GAIA… 2s');
        expect(loadingLine({ ...base, phase: 'starting' }, 'Loading ML libraries')).toBe('Loading ML libraries… 2s');
        expect(loadingLine({ ...base, phase: 'waiting' })).toBe('Waiting for another chat to finish… 2s');
        expect(loadingLine({ ...base, phase: 'model_queue' })).toBe('Waiting for the model: another app is using it… 2s');
        expect(loadingLine({ ...base, phase: 'building' })).toBe('Starting the agent… 2s');
        expect(loadingLine({ ...base, phase: 'loading' })).toBe('Loading the model… 2s');
        expect(loadingLine({ ...base, phase: 'priming' })).toBe('Reading GAIA’s instructions… 2s');
    });

    it('words each device, and leaves out one Lemonade did not report', () => {
        expect(loadingLine({ ...base, phase: 'loading', model: 'gemma4-it-e2b-FLM', device: 'npu' })).toBe('Loading gemma4-it-e2b-FLM on your NPU… 2s');
        expect(loadingLine({ ...base, phase: 'loading', model: 'Gemma-4-E4B-it-GGUF', device: 'cpu' })).toBe('Loading Gemma-4-E4B-it-GGUF on the CPU… 2s');
        expect(loadingLine({ ...base, phase: 'loading', model: 'Gemma-4-E4B-it-GGUF', device: null })).toBe('Loading Gemma-4-E4B-it-GGUF… 2s');
    });

    it('names the model the way the model chip does once its friendly name is known', async () => {
        useModelStore.setState({ names: { 'Gemma-4-E4B-it-GGUF': 'Gemma 4 E4B' } });
        const greet = pendingGreeting();
        render(<NewChat onSend={vi.fn()} />);
        act(() => greet.emit({ type: 'status', phase: 'loading', model: 'Gemma-4-E4B-it-GGUF', device: 'gpu' }));
        expect(await screen.findByRole('status')).toHaveTextContent('Loading Gemma 4 E4B on your GPU… 0s');
        useModelStore.setState({ names: {} });
    });
});
