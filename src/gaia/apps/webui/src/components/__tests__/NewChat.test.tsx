// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NewChat, greeting, warmUpLine } from '../NewChat';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { StartScreen, SystemStatus } from '../../types';

vi.mock('../../services/api');

const START: StartScreen = {
    cards: [
        { skill: 'document-brief', title: 'Ask about your documents', note: '2 indexed documents', prompt: 'Using my indexed documents, give me the key points.', needs_input: false },
        { skill: 'research-report', title: 'Research a topic', note: 'A cited report from the web', prompt: 'Research and write a short cited report on: ', needs_input: true },
    ],
    working_folder: { path: '/home/u/my-project', name: 'my-project', gaia_folder: false },
};

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ systemStatus: null });
    vi.mocked(api.requestWarmUp).mockResolvedValue({ state: 'ready' });
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

describe('NewChat while the first prompt is being primed', () => {
    it('shows the wait without holding the message box', async () => {
        useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus });
        vi.mocked(api.requestWarmUp).mockResolvedValue({ state: 'warming', elapsed_seconds: 4.6 });
        vi.mocked(api.getWarmUp).mockResolvedValue({ state: 'warming', elapsed_seconds: 5.2 });
        const onSend = vi.fn().mockResolvedValue(undefined);
        render(<NewChat onSend={onSend} />);

        expect(await screen.findByRole('status')).toHaveTextContent('Getting GAIA ready… 4s');
        const box = screen.getByLabelText('Message');
        expect(box).toBeEnabled();
        fireEvent.change(box, { target: { value: 'hello' } });
        fireEvent.keyDown(box, { key: 'Enter' });
        expect(onSend).toHaveBeenCalledWith('hello');
    });

    it('clears the wait once the prompt is ready', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        try {
            useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus });
            vi.mocked(api.requestWarmUp).mockResolvedValue({ state: 'warming', elapsed_seconds: 1 });
            vi.mocked(api.getWarmUp).mockResolvedValue({ state: 'ready', elapsed_seconds: 3 });
            render(<NewChat onSend={vi.fn()} />);

            await screen.findByText(/Getting GAIA ready/);
            await vi.advanceTimersByTimeAsync(1_100);
            await waitFor(() => expect(screen.queryByText(/Getting GAIA ready/)).toBeNull());
        } finally {
            vi.useRealTimers();
        }
    });

    it('stays quiet when nothing needs priming', async () => {
        render(<NewChat onSend={vi.fn()} />);
        await waitFor(() => expect(api.requestWarmUp).toHaveBeenCalled());
        expect(screen.queryByText(/Getting GAIA ready/)).toBeNull();
    });
});

describe('NewChat empty state', () => {
    it('greets in one line', () => {
        render(<NewChat onSend={vi.fn()} />);
        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/^Good (morning|afternoon|evening)\. What should we work on\?$/);
        expect(greeting(new Date(2026, 0, 1, 9))).toBe('Good morning. What should we work on?');
        expect(greeting(new Date(2026, 0, 1, 14))).toBe('Good afternoon. What should we work on?');
        expect(greeting(new Date(2026, 0, 1, 21))).toBe('Good evening. What should we work on?');
    });

    it('offers the task cards the backend picked', async () => {
        render(<NewChat onSend={vi.fn()} />);
        const list = await screen.findByRole('list', { name: 'Things to try' });
        expect(list.querySelectorAll('li')).toHaveLength(2);
        expect(screen.getByText('2 indexed documents')).toBeInTheDocument();
    });

    it('a complete task card starts the chat with its prompt', async () => {
        useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus });
        const onSend = vi.fn().mockResolvedValue(undefined);
        render(<NewChat onSend={onSend} />);
        fireEvent.click(await screen.findByRole('button', { name: /Ask about your documents/ }));
        expect(onSend).toHaveBeenCalledWith(START.cards[0].prompt);
    });

    it('a card that needs a subject fills the box and waits for it', async () => {
        const onSend = vi.fn().mockResolvedValue(undefined);
        render(<NewChat onSend={onSend} />);
        fireEvent.click(await screen.findByRole('button', { name: /Research a topic/ }));
        const box = screen.getByLabelText('Message');
        expect(box).toHaveValue(START.cards[1].prompt);
        expect(box).toHaveFocus();
        expect(onSend).not.toHaveBeenCalled();
    });

    it('shows the working folder', async () => {
        render(<NewChat onSend={vi.fn()} />);
        const chip = (await screen.findByText('my-project')).parentElement!;
        expect(chip).toHaveAttribute('title', expect.stringContaining('/home/u/my-project'));
    });

    it("says when GAIA works in its own folder", async () => {
        vi.mocked(api.getStartScreen).mockResolvedValue({
            ...START, working_folder: { path: '/home/u/.gaia/documents', name: 'documents', gaia_folder: true },
        });
        render(<NewChat onSend={vi.fn()} />);
        const chip = (await screen.findByText('documents')).parentElement!;
        expect(chip.getAttribute('title')).toMatch(/its own folder/);
    });

    it('starts with text handed over from setup', () => {
        render(<NewChat onSend={vi.fn()} initialText="Summarize this: " />);
        expect(screen.getByLabelText('Message')).toHaveValue('Summarize this: ');
    });

    it('says why there are no suggestions instead of hiding it', async () => {
        vi.mocked(api.getStartScreen).mockRejectedValue(new Error('skills unavailable'));
        render(<NewChat onSend={vi.fn()} />);
        expect(await screen.findByText(/Suggestions are unavailable: skills unavailable/)).toBeInTheDocument();
        expect(screen.getByLabelText('Message')).toBeInTheDocument();
    });

    it('makes no model call of its own', async () => {
        render(<NewChat onSend={vi.fn()} />);
        await screen.findByRole('list', { name: 'Things to try' });
        expect(api.sendMessageStream).not.toHaveBeenCalled();
        expect(api.loadModel).not.toHaveBeenCalled();
    });
});

describe('NewChat warm-up line', () => {
    it('waits for the setup check before asking for a warm-up', async () => {
        const { rerender } = render(<NewChat onSend={vi.fn()} setupChecked={false} />);
        await screen.findByRole('list', { name: 'Things to try' });
        expect(api.requestWarmUp).not.toHaveBeenCalled();
        rerender(<NewChat onSend={vi.fn()} setupChecked />);
        await waitFor(() => expect(api.requestWarmUp).toHaveBeenCalledOnce());
    });

    it('names the model and device being loaded', async () => {
        vi.mocked(api.requestWarmUp).mockResolvedValue({
            state: 'warming', elapsed_seconds: 3.4, model: 'Qwen3.6-35B-A3B-GGUF', device: 'gpu',
        });
        vi.mocked(api.getWarmUp).mockResolvedValue({ state: 'warming', elapsed_seconds: 4 });
        render(<NewChat onSend={vi.fn()} />);
        expect(await screen.findByRole('status')).toHaveTextContent('Loading Qwen3.6-35B-A3B-GGUF on your GPU… 3s');
    });

    it('words each device, and leaves out one Lemonade did not report', () => {
        expect(warmUpLine({ seconds: 1, model: 'gemma4-it-e2b-FLM', device: 'npu' })).toBe('Loading gemma4-it-e2b-FLM on your NPU… 1s');
        expect(warmUpLine({ seconds: 2, model: 'Gemma-4-E4B-it-GGUF', device: 'cpu' })).toBe('Loading Gemma-4-E4B-it-GGUF on the CPU… 2s');
        expect(warmUpLine({ seconds: 2, model: 'Gemma-4-E4B-it-GGUF', device: null })).toBe('Loading Gemma-4-E4B-it-GGUF… 2s');
        expect(warmUpLine({ seconds: 0, model: null, device: null })).toBe('Getting GAIA ready… 0s');
    });
});
