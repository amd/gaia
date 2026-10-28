// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NewChat, START_RETRY_MS, greeting, warmUpLine } from '../NewChat';
import { useBrowserPaneStore } from '../../stores/browserPaneStore';
import { useChatStore } from '../../stores/chatStore';
import { useModelStore } from '../../stores/modelStore';
import * as api from '../../services/api';
import type { ActiveModel, Document, StartScreen, SystemStatus } from '../../types';

vi.mock('../../services/api');

const START: StartScreen = {
    cards: [
        { skill: 'document-brief', title: 'Ask about your documents', note: '2 indexed documents', prompt: 'Using my indexed documents, give me the key points.', needs_input: false },
        { skill: 'research-report', title: 'Research a topic', note: 'A cited report from the web', prompt: 'Research and write a short cited report on: ', needs_input: true },
    ],
    working_folder: { path: '/home/u/my-project', name: 'my-project', gaia_folder: false, choosable: true },
};

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ systemStatus: null });
    vi.mocked(api.requestWarmUp).mockResolvedValue({ state: 'ready' });
    vi.mocked(api.getStartScreen).mockResolvedValue(START);
    vi.mocked(api.listDocuments).mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    useChatStore.setState({ documents: [], draftDocumentIds: [], showDocLibrary: false });
});

const libraryDoc = (id: string, filename: string): Document => ({
    id, filename, filepath: `/home/u/${filename}`, file_size: 100, chunk_count: 2,
    indexed_at: '', last_accessed_at: null, sessions_using: 0, indexing_status: 'complete',
});

describe('NewChat and the document library', () => {
    const docs = [libraryDoc('d1', 'report.pdf'), libraryDoc('d2', 'notes.md')];

    beforeEach(() => {
        useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus });
        vi.mocked(api.listDocuments).mockResolvedValue({ documents: docs, total: 2, total_size_bytes: 200, total_chunks: 4 });
    });

    it('says a new chat cannot search the library until documents are attached', async () => {
        render(<NewChat onSend={vi.fn()} />);

        expect(await screen.findByText(/Your library has 2 documents; this chat can search only the ones you attach/)).toBeInTheDocument();
    });

    it('attaches the whole library to the chat it creates', async () => {
        const onSend = vi.fn().mockResolvedValue(undefined);
        render(<NewChat onSend={onSend} />);

        fireEvent.click(await screen.findByRole('button', { name: 'Attach all 2' }));

        expect(useChatStore.getState().draftDocumentIds).toEqual(['d1', 'd2']);
        const attached = screen.getByRole('list', { name: 'Documents for this chat' });
        expect(attached).toHaveTextContent('report.pdf');
        expect(attached).toHaveTextContent('notes.md');
    });

    it('lets one attached document be dropped again', async () => {
        useChatStore.setState({ draftDocumentIds: ['d1', 'd2'] });
        render(<NewChat onSend={vi.fn()} />);

        fireEvent.click(await screen.findByRole('button', { name: "Don't attach notes.md" }));

        expect(useChatStore.getState().draftDocumentIds).toEqual(['d1']);
        expect(screen.getByText(/1 more document in your library/)).toBeInTheDocument();
    });

    it('opens the library to choose documents', async () => {
        render(<NewChat onSend={vi.fn()} />);

        fireEvent.click(await screen.findByRole('button', { name: 'Choose' }));

        expect(useChatStore.getState().showDocLibrary).toBe(true);
    });

    it('attaches the library when the documents card is picked', async () => {
        const onSend = vi.fn().mockResolvedValue(undefined);
        render(<NewChat onSend={onSend} />);
        await screen.findByText(/Your library has 2 documents/);

        fireEvent.click(await screen.findByText('Ask about your documents'));

        await waitFor(() => expect(onSend).toHaveBeenCalled());
        expect(useChatStore.getState().draftDocumentIds).toEqual(['d1', 'd2']);
    });

    it('offers nothing when the library is empty', async () => {
        vi.mocked(api.listDocuments).mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
        render(<NewChat onSend={vi.fn()} />);

        await waitFor(() => expect(api.listDocuments).toHaveBeenCalled());
        expect(screen.queryByText(/Your library has/)).toBeNull();
    });
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

describe('NewChat while the chat is being created (#5712)', () => {
    beforeEach(() => {
        useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus, messageQueues: {}, currentSessionId: null });
    });

    function sendTwo(onSend: (text: string) => Promise<boolean | void>) {
        render(<NewChat onSend={onSend} />);
        const box = screen.getByLabelText('Message');
        fireEvent.change(box, { target: { value: 'first' } });
        fireEvent.keyDown(box, { key: 'Enter' });
        // Cleared at once, so what is typed next is its own message.
        expect(box).toHaveValue('');
        expect(box).toBeEnabled();
        fireEvent.change(box, { target: { value: 'second' } });
        fireEvent.keyDown(box, { key: 'Enter' });
        return box;
    }

    it('queues a message sent before the chat exists instead of dropping it', async () => {
        let created!: () => void;
        const onSend = vi.fn(() => new Promise<void>((resolve) => { created = () => {
            useChatStore.setState({ currentSessionId: 'chat-1' });
            resolve();
        }; }));
        sendTwo(onSend);

        expect(onSend).toHaveBeenCalledTimes(1);
        await act(async () => created());

        expect(useChatStore.getState().messageQueues['chat-1']?.map((m) => [m.text, m.state])).toEqual([['second', 'queued']]);
    });

    it('never sends held messages on their own when the first one was not sent', async () => {
        let created!: () => void;
        const onSend = vi.fn(() => new Promise<boolean>((resolve) => { created = () => {
            useChatStore.setState({ currentSessionId: 'chat-1' });
            resolve(false);
        }; }));
        sendTwo(onSend);

        await act(async () => created());

        expect(useChatStore.getState().messageQueues['chat-1']?.map((m) => [m.text, m.state, m.error]))
            .toEqual([['second', 'failed', 'The first message was not sent.']]);
    });

    it('puts every message back in the box when the chat could not be created', async () => {
        let fail!: () => void;
        const onSend = vi.fn(() => new Promise<void>((_, reject) => { fail = () => reject(new Error('no backend')); }));
        const box = sendTwo(onSend);

        await act(async () => fail());

        expect(box).toHaveValue('first\n\nsecond');
        expect(screen.getByText('no backend')).toBeInTheDocument();
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

    it('takes focus when New chat is chosen while it is already showing', () => {
        const { rerender } = render(<NewChat onSend={vi.fn()} focusRequest={1} />);
        const box = screen.getByPlaceholderText('Ask GAIA anything');
        const other = document.createElement('input');
        document.body.appendChild(other);
        other.focus();
        rerender(<NewChat onSend={vi.fn()} focusRequest={2} />);
        expect(document.activeElement).toBe(box);
        other.remove();
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
            ...START, working_folder: { path: '/home/u/.gaia/documents', name: 'documents', gaia_folder: true, choosable: true },
        });
        render(<NewChat onSend={vi.fn()} />);
        const chip = (await screen.findByText('documents')).parentElement!;
        expect(chip.getAttribute('title')).toMatch(/its own folder/);
    });

    it('picks a folder for the chat from the folder chip, and drops it again', async () => {
        useChatStore.setState({ draftFolder: null });
        vi.mocked(api.browseFiles).mockResolvedValueOnce({
            current_path: '/home/u',
            parent_path: null,
            entries: [{ name: 'site', path: '/home/u/site', type: 'folder', size: 0, extension: null, modified: null }],
            quick_links: [],
        }).mockResolvedValueOnce({ current_path: '/home/u/site', parent_path: '/home/u', entries: [], quick_links: [] });
        render(<NewChat onSend={vi.fn()} />);

        fireEvent.click(await screen.findByRole('button', { name: 'Folder for this chat: my-project' }));
        fireEvent.click(await screen.findByRole('button', { name: 'site' }));
        await screen.findByText('No folders inside.');
        fireEvent.click(screen.getByRole('button', { name: 'Use this folder' }));

        expect(useChatStore.getState().draftFolder).toBe('/home/u/site');
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        const chip = screen.getByRole('button', { name: 'Folder for this chat: my-project, plus site' });
        // Picking adds a folder; the working folder stays in the label.
        expect(chip).toHaveTextContent('my-project + site');

        fireEvent.click(screen.getByRole('button', { name: "Don't give this chat site" }));
        expect(useChatStore.getState().draftFolder).toBeNull();
        expect(screen.getByRole('button', { name: 'Folder for this chat: my-project' })).toBeInTheDocument();
    });

    it("shows the folder as plain text when this chat's agent can't be given another", async () => {
        vi.mocked(api.getStartScreen).mockResolvedValue({ ...START, working_folder: { ...START.working_folder, choosable: false } });
        render(<NewChat onSend={vi.fn()} />);

        expect(await screen.findByText('my-project')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Folder for this chat/ })).not.toBeInTheDocument();
    });

    it('starts with text handed over from setup', () => {
        render(<NewChat onSend={vi.fn()} initialText="Summarize this: " />);
        expect(screen.getByLabelText('Message')).toHaveValue('Summarize this: ');
    });

    it('says in one plain sentence why there are no suggestions, with the detail behind Details', async () => {
        const detail = `The GAIA agent could not be started: sidecar binary lock ${'PENDING-sha256 '.repeat(60).trim()}`;
        vi.mocked(api.getStartScreen).mockResolvedValue({ ...START, cards: [], skills_error: detail });
        render(<NewChat onSend={vi.fn()} />);
        expect(await screen.findByText(/assistant didn.t start, so it can.t suggest tasks yet/)).toBeInTheDocument();
        expect(screen.queryByText(detail)).not.toBeInTheDocument();
        // The folder still shows: only the cards depend on the agent.
        expect(screen.getByText('my-project')).toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: /Details/ }));
        expect(screen.getByText(detail)).toBeInTheDocument();
    });

    it('tries the suggestions again', async () => {
        vi.mocked(api.getStartScreen)
            .mockRejectedValueOnce(new Error('backend restarting'))
            .mockResolvedValue(START);
        render(<NewChat onSend={vi.fn()} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
        expect(await screen.findByRole('list', { name: 'Things to try' })).toBeInTheDocument();
        expect(screen.queryByText(/didn.t start/)).not.toBeInTheDocument();
    });

    it('fetches the suggestions again on its own once the assistant has started', async () => {
        vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
        try {
            vi.mocked(api.getStartScreen)
                .mockResolvedValueOnce({ ...START, cards: [], skills_error: 'The GAIA agent could not be started yet.' })
                .mockResolvedValueOnce({ ...START, cards: [], skills_error: 'The GAIA agent could not be started yet.' })
                .mockResolvedValue(START);
            render(<NewChat onSend={vi.fn()} />);
            expect(await screen.findByText(/didn.t start/)).toBeInTheDocument();
            await act(async () => {});  // let the retry effect start its interval

            await act(async () => { vi.advanceTimersByTime(START_RETRY_MS); });
            await waitFor(() => expect(api.getStartScreen).toHaveBeenCalledTimes(2));
            await act(async () => {});
            // Still failing: the reason stays up rather than flickering away.
            expect(screen.getByText(/didn.t start/)).toBeInTheDocument();

            await act(async () => { vi.advanceTimersByTime(START_RETRY_MS); });
            expect(await screen.findByRole('list', { name: 'Things to try' })).toBeInTheDocument();
            expect(screen.queryByText(/didn.t start/)).not.toBeInTheDocument();
        } finally {
            vi.useRealTimers();
        }
    });

    it('makes no model call of its own', async () => {
        render(<NewChat onSend={vi.fn()} />);
        await screen.findByRole('list', { name: 'Things to try' });
        expect(api.sendMessageStream).not.toHaveBeenCalled();
        expect(api.loadModel).not.toHaveBeenCalled();
    });
});

describe('NewChat warm-up line', () => {

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

    it('names the model the way the model chip does once its friendly name is known', () => {
        useModelStore.setState({ names: { 'Gemma-4-E4B-it-GGUF': 'Gemma 4 E4B' } });
        expect(warmUpLine({ seconds: 3, model: 'Gemma-4-E4B-it-GGUF', device: 'gpu' })).toBe('Loading Gemma 4 E4B on your GPU… 3s');
        useModelStore.setState({ names: {} });
    });
});

describe('NewChat after first-run setup failed', () => {
    const error = 'Gemma-4-E4B-it-GGUF - Not enough free memory to load Gemma-4-E4B-it-GGUF with a 32,768-token window: '
        + 'needs about 7.8 GB and only 6.0 GB is free. See https://amd-gaia.ai/docs/reference/troubleshooting';
    const failed = { init_state: 'ready', setup_error: error, model_loaded: null } as SystemStatus;
    const local: ActiveModel = { provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true };

    beforeEach(() => {
        useModelStore.setState({ active: local });
        useChatStore.setState({ settingsSection: null });
    });

    it('says so with the error before the first message, and opens the model picker', () => {
        useChatStore.setState({ systemStatus: failed });
        render(<NewChat onSend={vi.fn()} />);

        const notice = screen.getByRole('alert');
        expect(notice).toHaveTextContent('GAIA’s first-run setup didn’t finish');
        expect(notice).toHaveTextContent(error);
        fireEvent.click(screen.getByRole('button', { name: 'Choose a model' }));
        expect(useChatStore.getState().settingsSection).toBe('model');
    });

    it('stays quiet once chats run in the cloud or a model has loaded', () => {
        useChatStore.setState({ systemStatus: failed });
        useModelStore.setState({ active: { ...local, provider: 'fireworks', remote: true } });
        const { unmount } = render(<NewChat onSend={vi.fn()} />);
        expect(screen.queryByRole('alert')).toBeNull();
        unmount();

        useModelStore.setState({ active: local });
        useChatStore.setState({ systemStatus: { ...failed, model_loaded: 'Qwen3-4B-GGUF' } });
        render(<NewChat onSend={vi.fn()} />);
        expect(screen.queryByRole('alert')).toBeNull();
    });

    it('is not shown when setup finished', () => {
        useChatStore.setState({ systemStatus: { init_state: 'ready', setup_error: null } as SystemStatus });
        render(<NewChat onSend={vi.fn()} />);
        expect(screen.queryByRole('alert')).toBeNull();
    });
});

describe('NewChat and the browser pane', () => {
    afterEach(() => {
        delete window.gaiaBrowserPane;
        useBrowserPaneStore.setState({ draftOpen: false });
    });

    it('offers the browser pane button in the desktop app', () => {
        window.gaiaBrowserPane = {} as NonNullable<typeof window.gaiaBrowserPane>;
        useBrowserPaneStore.setState({ draftOpen: false });
        render(<NewChat onSend={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Show browser pane' }));
        expect(useBrowserPaneStore.getState().draftOpen).toBe(true);
        expect(screen.getByRole('button', { name: 'Hide browser pane' })).toHaveAttribute('aria-pressed', 'true');
    });

    it('has no browser pane button in a plain browser', () => {
        render(<NewChat onSend={vi.fn()} />);
        expect(screen.queryByRole('button', { name: /browser pane/ })).toBeNull();
    });
});
