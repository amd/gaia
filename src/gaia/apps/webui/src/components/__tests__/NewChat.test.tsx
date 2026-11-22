// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NewChat, START_RETRY_MS, greeting, warmUpLine } from '../NewChat';
import { useBrowserPaneStore } from '../../stores/browserPaneStore';
import { useChatStore } from '../../stores/chatStore';
import { useModelStore } from '../../stores/modelStore';
import * as api from '../../services/api';
import { FlagshipMissing } from '../../utils/newTask';
import newChatCss from '../NewChat.css?raw';
import type { ActiveModel, AgentProgram, AgentPrograms, Document, Settings, StartScreen, SystemStatus } from '../../types';

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
    vi.mocked(api.listSkills).mockResolvedValue({ skills: [], invalid: {} });
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

        expect(await screen.findByText(/2 documents in your library. Attach the ones you want to search here./)).toBeInTheDocument();
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

    it('shows a few attached documents and folds the rest behind "+N more" (#7733)', async () => {
        const many = Array.from({ length: 600 }, (_, i) => libraryDoc(`m${i}`, `file-${i}.txt`));
        vi.mocked(api.listDocuments).mockResolvedValue({ documents: many, total: 600, total_size_bytes: 0, total_chunks: 0 });
        useChatStore.setState({ draftDocumentIds: many.map((d) => d.id) });
        render(<NewChat onSend={vi.fn()} />);

        const more = await screen.findByRole('button', { name: '+595 more' });
        const list = screen.getByRole('list', { name: 'Documents for this chat' });
        expect(list.querySelectorAll('li')).toHaveLength(5);
        expect(list).not.toHaveClass('is-expanded');

        fireEvent.click(more);
        expect(list.querySelectorAll('li')).toHaveLength(600);
        // Expanded, the list scrolls inside its own box so the composer stays put.
        expect(list).toHaveClass('is-expanded');
        expect(more).toHaveAttribute('aria-expanded', 'true');

        fireEvent.click(screen.getByRole('button', { name: 'Show fewer' }));
        expect(list.querySelectorAll('li')).toHaveLength(5);
    });

    it('lists every attached document when there are only a few', async () => {
        useChatStore.setState({ draftDocumentIds: ['d1', 'd2'] });
        render(<NewChat onSend={vi.fn()} />);

        const list = await screen.findByRole('list', { name: 'Documents for this chat' });
        expect(list.querySelectorAll('li')).toHaveLength(2);
        expect(screen.queryByRole('button', { name: /more$/ })).toBeNull();
    });

    it('opens the library to choose documents', async () => {
        render(<NewChat onSend={vi.fn()} />);

        fireEvent.click(await screen.findByRole('button', { name: 'Choose' }));

        expect(useChatStore.getState().showDocLibrary).toBe(true);
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

    it('keeps the message box open once every boot task has finished', () => {
        useChatStore.setState({ systemStatus: {
            init_state: 'initializing',
            init_tasks: [{ name: 'Loading AI model', status: 'done' }],
        } as SystemStatus });
        render(<NewChat onSend={vi.fn().mockResolvedValue(undefined)} />);
        expect(screen.getByLabelText('Message')).not.toBeDisabled();
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

describe('NewChat without the GAIA agent', () => {
    it('points a refused send at the install notice only while that notice shows', async () => {
        useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus });
        const onSend = vi.fn().mockRejectedValue(new FlagshipMissing());
        const { rerender } = render(<NewChat onSend={onSend} agentMissing />);

        const box = screen.getByLabelText('Message');
        fireEvent.change(box, { target: { value: 'hello' } });
        fireEvent.keyDown(box, { key: 'Enter' });
        expect(await screen.findByRole('alert')).toHaveTextContent('Install it from the notice above');
        expect(box).toHaveValue('hello');

        rerender(<NewChat onSend={onSend} agentMissing={false} />);
        expect(screen.queryByText(/notice above/)).not.toBeInTheDocument();
    });
});

describe('NewChat try chips', () => {
    it('fills the box with an example that ends in a card, without sending it', () => {
        useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus });
        const onSend = vi.fn().mockResolvedValue(undefined);
        render(<NewChat onSend={onSend} />);

        fireEvent.click(screen.getByRole('button', { name: /^Recipe:/ }));

        expect(screen.getByLabelText('Message')).toHaveValue('Give me a banana bread recipe');
        expect(screen.getByLabelText('Message')).toHaveFocus();
        expect(onSend).not.toHaveBeenCalled();
    });

    it('keeps a question already typed and adds the example after it (#7594)', () => {
        useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus });
        render(<NewChat onSend={vi.fn().mockResolvedValue(undefined)} />);

        fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'What should I cook this week?' } });
        fireEvent.click(screen.getByRole('button', { name: /^Recipe:/ }));

        expect(screen.getByLabelText('Message')).toHaveValue('What should I cook this week?\n\nGive me a banana bread recipe');
    });

    it('swaps the earlier chip example for the next one, with and without a typed question', () => {
        useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus });
        render(<NewChat onSend={vi.fn().mockResolvedValue(undefined)} />);
        const box = screen.getByLabelText('Message');

        fireEvent.click(screen.getByRole('button', { name: /^Recipe:/ }));
        fireEvent.click(screen.getByRole('button', { name: /^Workout:/ }));
        expect(box).toHaveValue('A 15-minute workout I can do at home');
        fireEvent.click(screen.getByRole('button', { name: /^Workout:/ }));
        expect(box).toHaveValue('A 15-minute workout I can do at home');

        fireEvent.change(box, { target: { value: 'What should I do today?' } });
        fireEvent.click(screen.getByRole('button', { name: /^Recipe:/ }));
        fireEvent.click(screen.getByRole('button', { name: /^Workout:/ }));
        expect(box).toHaveValue('What should I do today?\n\nA 15-minute workout I can do at home');
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
            vi.mocked(api.requestWarmUp).mockResolvedValue({ state: 'warming', elapsed_seconds: 2 });
            vi.mocked(api.getWarmUp).mockResolvedValue({ state: 'ready', elapsed_seconds: 3 });
            render(<NewChat onSend={vi.fn()} />);

            await screen.findByText(/Getting GAIA ready/);
            await vi.advanceTimersByTimeAsync(1_100);
            await waitFor(() => expect(screen.queryByText(/Getting GAIA ready/)).toBeNull());
        } finally {
            vi.useRealTimers();
        }
    });

    it('stays quiet while a warm-up is too short to mention', async () => {
        useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus });
        vi.mocked(api.requestWarmUp).mockResolvedValue({ state: 'warming', elapsed_seconds: 0.4 });
        vi.mocked(api.getWarmUp).mockResolvedValue({ state: 'warming', elapsed_seconds: 1.2 });
        render(<NewChat onSend={vi.fn()} />);
        await waitFor(() => expect(api.requestWarmUp).toHaveBeenCalled());
        expect(screen.queryByText(/Getting GAIA ready/)).toBeNull();
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

    it('stays clean: no task cards, only the greeting and the composer', async () => {
        render(<NewChat onSend={vi.fn()} />);
        await screen.findByText('my-project');
        expect(screen.queryByRole('list', { name: 'Things to try' })).not.toBeInTheDocument();
        expect(screen.queryByText('Ask about your documents')).not.toBeInTheDocument();
    });

    it('draws the NPU backdrop behind the content', () => {
        const { container } = render(<NewChat onSend={vi.fn()} />);
        expect(container.querySelector('main.new-chat > canvas.npu-backdrop')).toHaveAttribute('aria-hidden', 'true');
    });

    it('keeps the composer and greeting in place while typing', () => {
        const { container } = render(<NewChat onSend={vi.fn()} />);
        const main = container.querySelector('main.new-chat')!;
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'h' } });
        expect(main).not.toHaveClass('is-docked');
        expect(container.querySelector('.new-chat-greeting')).not.toHaveAttribute('aria-hidden', 'true');
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: '' } });
        expect(main).not.toHaveClass('is-docked');
    });

    it('drops the composer to the bottom when the message is sent', async () => {
        let finish: (v: boolean) => void = () => {};
        const onSend = vi.fn(() => new Promise<boolean>((r) => { finish = r; }));
        const { container } = render(<NewChat onSend={onSend} />);
        const main = container.querySelector('main.new-chat')!;
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'hello' } });
        fireEvent.keyDown(screen.getByLabelText('Message'), { key: 'Enter' });
        await waitFor(() => expect(main).toHaveClass('is-docked'));
        await act(async () => { finish(true); });
    });

    it('shows the working folder under the message box, where its picker opens', async () => {
        const { container } = render(<NewChat onSend={vi.fn()} />);
        const chip = (await screen.findByText('my-project')).parentElement!;
        expect(chip).toHaveAttribute('title', expect.stringContaining('/home/u/my-project'));
        expect(container.querySelector('.new-chat-meta')).toContainElement(chip);
        expect(container.querySelector('.composer')).not.toContainElement(chip);
    });

    it("names no folder when GAIA works in its own, and says what Add a folder does", async () => {
        vi.mocked(api.getStartScreen).mockResolvedValue({
            ...START, working_folder: { path: '/home/u/.gaia/documents', name: 'documents', gaia_folder: true, choosable: true },
        });
        render(<NewChat onSend={vi.fn()} />);
        const add = await screen.findByRole('button', { name: 'Add a folder' });
        expect(add.getAttribute('title')).toMatch(/its own folder, \/home\/u\/\.gaia\/documents\. Add a folder this chat can read and write\./);
        expect(screen.queryByText('documents')).not.toBeInTheDocument();
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

        fireEvent.click(await screen.findByRole('button', { name: 'Add a folder' }));
        fireEvent.click(await screen.findByRole('button', { name: 'site' }));
        await screen.findByText('No folders inside.');
        fireEvent.click(screen.getByRole('button', { name: 'Use this folder' }));

        expect(useChatStore.getState().draftFolder).toBe('/home/u/site');
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        // Picking adds a folder as its own chip; the working folder stays beside it.
        expect(screen.getByText('site').parentElement).toHaveAttribute('title', 'This chat can read and write /home/u/site.');
        expect(screen.getByText('my-project')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Add a folder' })).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: "Don't give this chat site" }));
        expect(useChatStore.getState().draftFolder).toBeNull();
        expect(screen.getByRole('button', { name: 'Add a folder' })).toBeInTheDocument();
    });

    describe('in the desktop app', () => {
        const pickFolder = vi.fn<(defaultPath?: string) => Promise<string | null>>();
        beforeEach(() => {
            useChatStore.setState({ draftFolder: null });
            window.gaiaAPI = { dialog: { pickFolder } } as unknown as NonNullable<typeof window.gaiaAPI>;
            vi.mocked(api.checkEnvironmentFolder).mockResolvedValue({ refusal: null });
        });
        afterEach(() => {
            delete window.gaiaAPI;
        });

        it('picks the folder in the OS picker, starting from the working folder', async () => {
            pickFolder.mockResolvedValue('/home/u/site');
            render(<NewChat onSend={vi.fn()} />);

            fireEvent.click(await screen.findByRole('button', { name: 'Add a folder' }));

            expect(await screen.findByRole('button', { name: "Don't give this chat site" })).toBeInTheDocument();
            expect(pickFolder).toHaveBeenCalledWith('/home/u/my-project');
            expect(useChatStore.getState().draftFolder).toBe('/home/u/site');
            expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
            expect(api.browseFiles).not.toHaveBeenCalled();
            expect(api.checkEnvironmentFolder).toHaveBeenCalledWith('/home/u/site');
        });

        it('refuses the home folder as soon as it is picked, and says why (#7996)', async () => {
            const why = "GAIA can't be given /home/u: it is your home directory. Pick a narrower folder.";
            pickFolder.mockResolvedValue('/home/u');
            vi.mocked(api.checkEnvironmentFolder).mockResolvedValue({ refusal: why });
            render(<NewChat onSend={vi.fn()} />);

            fireEvent.click(await screen.findByRole('button', { name: 'Add a folder' }));

            expect(await screen.findByText(why)).toBeInTheDocument();
            expect(useChatStore.getState().draftFolder).toBeNull();
            expect(screen.getByRole('button', { name: 'Add a folder' })).toBeInTheDocument();
        });

        it('takes a folder outside the home folder, as adding it does', async () => {
            pickFolder.mockResolvedValue('D:\\Projects\\site');
            render(<NewChat onSend={vi.fn()} />);

            fireEvent.click(await screen.findByRole('button', { name: 'Add a folder' }));

            await waitFor(() => expect(useChatStore.getState().draftFolder).toBe('D:\\Projects\\site'));
            expect(api.browseFiles).not.toHaveBeenCalled();
        });

        it("adds nothing and says so when the folder couldn't be checked", async () => {
            pickFolder.mockResolvedValue('/home/u/site');
            vi.mocked(api.checkEnvironmentFolder).mockRejectedValue(new Error('Failed to fetch'));
            render(<NewChat onSend={vi.fn()} />);

            fireEvent.click(await screen.findByRole('button', { name: 'Add a folder' }));

            expect(await screen.findByText("Couldn't check that folder, so it wasn't added: Failed to fetch")).toBeInTheDocument();
            expect(useChatStore.getState().draftFolder).toBeNull();
        });

        it('keeps the folder as it was when the OS picker is cancelled', async () => {
            pickFolder.mockResolvedValue(null);
            render(<NewChat onSend={vi.fn()} />);

            fireEvent.click(await screen.findByRole('button', { name: 'Add a folder' }));

            await waitFor(() => expect(pickFolder).toHaveBeenCalledTimes(1));
            expect(useChatStore.getState().draftFolder).toBeNull();
            expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        });
    });

    it("shows the folder as plain text when this chat's agent can't be given another", async () => {
        vi.mocked(api.getStartScreen).mockResolvedValue({ ...START, working_folder: { ...START.working_folder, choosable: false } });
        render(<NewChat onSend={vi.fn()} />);

        expect(await screen.findByText('my-project')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Add a folder' })).not.toBeInTheDocument();
    });

    it('starts with text handed over from setup', () => {
        render(<NewChat onSend={vi.fn()} initialText="Summarize this: " />);
        expect(screen.getByLabelText('Message')).toHaveValue('Summarize this: ');
    });

    it('puts the caret after text handed over, so typing adds to it', () => {
        render(<NewChat onSend={vi.fn()} initialText="Use the summarize skill: " />);
        const box = screen.getByLabelText('Message') as HTMLTextAreaElement;
        expect(document.activeElement).toBe(box);
        expect(box.selectionStart).toBe(box.value.length);
        expect(box.selectionEnd).toBe(box.value.length);
    });

    it('says in one plain sentence that the assistant did not start, with the detail behind Details', async () => {
        const detail = `The GAIA agent could not be started: sidecar binary lock ${'PENDING-sha256 '.repeat(60).trim()}`;
        vi.mocked(api.getStartScreen).mockResolvedValue({ ...START, cards: [], skills_error: detail });
        render(<NewChat onSend={vi.fn()} />);
        expect(await screen.findByText(/assistant didn.t start yet/)).toBeInTheDocument();
        expect(screen.queryByText(detail)).not.toBeInTheDocument();
        // The folder still shows: it does not depend on the agent.
        expect(screen.getByText('my-project')).toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: /Details/ }));
        expect(screen.getByText(detail)).toBeInTheDocument();
    });

    it('tries the start screen again', async () => {
        vi.mocked(api.getStartScreen)
            .mockRejectedValueOnce(new Error('backend restarting'))
            .mockResolvedValue(START);
        render(<NewChat onSend={vi.fn()} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
        expect(await screen.findByText('my-project')).toBeInTheDocument();
        expect(screen.queryByText(/didn.t start/)).not.toBeInTheDocument();
    });

    it('fetches the start screen again on its own once the assistant has started', async () => {
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
            await waitFor(() => expect(screen.queryByText(/didn.t start/)).not.toBeInTheDocument());
        } finally {
            vi.useRealTimers();
        }
    });

    it('makes no model call of its own', async () => {
        render(<NewChat onSend={vi.fn()} />);
        await screen.findByText('my-project');
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

describe('NewChat research mode', () => {
    beforeEach(() => {
        useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus, research: {} });
    });
    afterEach(() => {
        useChatStore.setState({ research: {} });
    });

    it('sends the first message as a /research turn while Research is on', async () => {
        const onSend = vi.fn().mockResolvedValue(undefined);
        render(<NewChat onSend={onSend} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Research' }));
        const box = screen.getByRole('textbox', { name: 'Message' });
        expect(box).toHaveAttribute('placeholder', 'What should GAIA research?');
        fireEvent.change(box, { target: { value: 'the history of RISC-V' } });
        fireEvent.keyDown(box, { key: 'Enter' });
        await waitFor(() => expect(onSend).toHaveBeenCalledWith('/research the history of RISC-V'));
    });

    it('sends the message as typed while Research is off', async () => {
        const onSend = vi.fn().mockResolvedValue(undefined);
        render(<NewChat onSend={onSend} />);
        const box = await screen.findByRole('textbox', { name: 'Message' });
        fireEvent.change(box, { target: { value: 'hello' } });
        fireEvent.keyDown(box, { key: 'Enter' });
        await waitFor(() => expect(onSend).toHaveBeenCalledWith('hello'));
    });
});

it('marks a right-to-left override in a held document name and its button', async () => {
    const rlo = String.fromCodePoint(0x202e);
    useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus, draftDocumentIds: ['d9'] });
    vi.mocked(api.listDocuments).mockResolvedValue({
        documents: [libraryDoc('d9', `report${rlo}txt.md`)], total: 1, total_size_bytes: 100, total_chunks: 2,
    });
    render(<NewChat onSend={vi.fn()} />);
    const list = await screen.findByRole('list', { name: 'Documents for this chat' });
    expect(list).toHaveTextContent('report[U+202E]txt.md');
    expect(screen.getByRole('button', { name: "Don't attach report[U+202E]txt.md" })).toBeInTheDocument();
    expect(list.innerHTML).not.toContain(rlo);
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

describe('the greeting gloss', () => {
    it('is set smaller than the greeting, so the English never out-sizes the Arabic', () => {
        const rule = newChatCss.match(/\.new-chat-greeting \.i18n-gloss\.i18n-gloss \{([^}]*)\}/);
        expect(rule?.[1]).toMatch(/font-size:\s*0\.55em/);
        expect(rule?.[1]).toMatch(/display:\s*block/);
    });
});

describe('NewChat when the chosen gaiacpp cannot start', () => {
    const MISSING = {
        ...START,
        cards: [],
        skills_error: "the daemon refused to ensure the 'gaia' sidecar: Cannot start the GAIA agent",
        agent_problem: { code: 'gaiacpp_missing' as const, detail: 'gaiacpp is chosen as the agent but it is not installed on this PC (looked in: C:\\gaia\\bin\\gaiacpp.exe).' },
    };
    const IDLE = { state: 'idle' as const, bytes_done: 0, bytes_total: null, error: null };
    const programs = (gaiacpp: Partial<AgentProgram> = {}): AgentPrograms => ({
        current: 'gaiacpp',
        programs: [
            { id: 'gaiapy', installed: true, size_bytes: null, can_download: false, unavailable_reason: null },
            { id: 'gaiacpp', installed: false, size_bytes: 4_000_000, can_download: true, unavailable_reason: null, ...gaiacpp },
        ],
        download: IDLE,
    });

    beforeEach(() => {
        useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus });
        vi.mocked(api.getStartScreen).mockResolvedValue(MISSING);
        vi.mocked(api.getAgentPrograms).mockResolvedValue(programs());
    });

    it('says in plain words that gaiacpp is not on this PC, with the detail behind Details', async () => {
        render(<NewChat onSend={vi.fn()} />);

        expect(await screen.findByText("gaiacpp, the agent you chose, isn't on this PC, so GAIA can't start it.")).toBeInTheDocument();
        expect(screen.queryByText(/assistant didn.t start yet/)).not.toBeInTheDocument();
        expect(screen.queryByText(MISSING.agent_problem.detail)).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Details/ }));
        expect(screen.getByText(MISSING.agent_problem.detail)).toBeInTheDocument();

        expect(await screen.findByRole('button', { name: 'Download gaiacpp again' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Use the Python agent' })).toBeInTheDocument();
        // Nothing is switched until the user picks it.
        expect(api.updateSettings).not.toHaveBeenCalled();
    });

    it('says gaiacpp failed its check when the file no longer matches its download', async () => {
        vi.mocked(api.getStartScreen).mockResolvedValue({
            ...MISSING, agent_problem: { code: 'gaiacpp_invalid', detail: 'does not match the gaiacpp that was downloaded' },
        });
        render(<NewChat onSend={vi.fn()} />);

        expect(await screen.findByText(/gaiacpp, the agent you chose, failed its check/)).toBeInTheDocument();
    });

    it('downloads gaiacpp again, then asks for the agent again', async () => {
        vi.mocked(api.startAgentDownload).mockResolvedValue({ state: 'done', bytes_done: 10, bytes_total: 10, error: null });
        render(<NewChat onSend={vi.fn()} />);
        await screen.findByText(/isn't on this PC/);
        vi.mocked(api.getStartScreen).mockResolvedValue(START);

        fireEvent.click(await screen.findByRole('button', { name: 'Download gaiacpp again' }));

        await waitFor(() => expect(api.startAgentDownload).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(api.getStartScreen).toHaveBeenCalledTimes(2));
        await waitFor(() => expect(screen.queryByText(/isn't on this PC/)).not.toBeInTheDocument());
        expect(api.updateSettings).not.toHaveBeenCalled();
    });

    it("says why gaiacpp can't be downloaded instead of offering a dead button", async () => {
        vi.mocked(api.getAgentPrograms).mockResolvedValue(programs({ can_download: false, unavailable_reason: 'gaiacpp is not built for this kind of PC' }));
        render(<NewChat onSend={vi.fn()} />);

        expect(await screen.findByText("gaiacpp can't be downloaded here: gaiacpp is not built for this kind of PC")).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Download gaiacpp/ })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Use the Python agent' })).toBeInTheDocument();
    });

    it('switches to the Python agent only when asked, then asks for the agent again', async () => {
        vi.mocked(api.updateSettings).mockResolvedValue({ flagship_agent: 'gaiapy' } as Settings);
        render(<NewChat onSend={vi.fn()} />);
        await screen.findByText(/isn't on this PC/);
        expect(api.updateSettings).not.toHaveBeenCalled();
        vi.mocked(api.getStartScreen).mockResolvedValue(START);

        fireEvent.click(screen.getByRole('button', { name: 'Use the Python agent' }));

        await waitFor(() => expect(api.updateSettings).toHaveBeenCalledWith({ flagship_agent: 'gaiapy' }));
        expect(api.updateSettings).toHaveBeenCalledTimes(1);
        await waitFor(() => expect(screen.queryByText(/isn't on this PC/)).not.toBeInTheDocument());
    });

    it("keeps gaiacpp chosen, and says why, when the Python agent isn't installed either", async () => {
        const missing = programs();
        missing.programs[0] = { ...missing.programs[0], installed: false };
        vi.mocked(api.getAgentPrograms).mockResolvedValue(missing);
        render(<NewChat onSend={vi.fn()} />);
        await screen.findByText(/isn't on this PC/);

        fireEvent.click(screen.getByRole('button', { name: 'Use the Python agent' }));

        expect(await screen.findByText(/Couldn't switch to the Python agent: it isn’t installed on this PC, so gaiacpp stays your agent/)).toBeInTheDocument();
        expect(api.updateSettings).not.toHaveBeenCalled();
        expect(screen.getByText(/isn't on this PC/)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Use the Python agent' })).toBeEnabled();
    });

    it('saves nothing when it cannot check the Python agent', async () => {
        render(<NewChat onSend={vi.fn()} />);
        await screen.findByText(/isn't on this PC/);
        vi.mocked(api.getAgentPrograms).mockRejectedValue(new Error('the server is not answering'));

        fireEvent.click(screen.getByRole('button', { name: 'Use the Python agent' }));

        expect(await screen.findByText(/Couldn't switch to the Python agent: the server is not answering/)).toBeInTheDocument();
        expect(api.updateSettings).not.toHaveBeenCalled();
    });

    it('says so, and stays put, when the switch is refused', async () => {
        vi.mocked(api.updateSettings).mockRejectedValue(new Error('GAIA is answering in a chat'));
        render(<NewChat onSend={vi.fn()} />);
        await screen.findByText(/isn't on this PC/);

        fireEvent.click(screen.getByRole('button', { name: 'Use the Python agent' }));

        expect(await screen.findByText(/Couldn't switch to the Python agent: GAIA is answering in a chat/)).toBeInTheDocument();
        expect(screen.getByText(/isn't on this PC/)).toBeInTheDocument();
    });

    it('keeps a message sent meanwhile in the box instead of losing it', async () => {
        const onSend = vi.fn().mockResolvedValue(undefined);
        render(<NewChat onSend={onSend} />);
        await screen.findByText(/isn't on this PC/);

        const box = screen.getByLabelText('Message');
        fireEvent.change(box, { target: { value: 'summarize my notes' } });
        fireEvent.keyDown(box, { key: 'Enter' });

        expect(onSend).not.toHaveBeenCalled();
        expect(box).toHaveValue('summarize my notes');
        expect(screen.getByText('Your message is still in the box. Send it once the agent starts.')).toBeInTheDocument();
    });

    it('puts a message back in the box when sending it fails because the agent did not start', async () => {
        vi.mocked(api.getStartScreen).mockResolvedValue(START);
        const onSend = vi.fn().mockRejectedValue(new Error("GAIA couldn't start its agent."));
        render(<NewChat onSend={onSend} />);
        await screen.findByText('my-project');

        const box = screen.getByLabelText('Message');
        fireEvent.change(box, { target: { value: 'summarize my notes' } });
        fireEvent.keyDown(box, { key: 'Enter' });

        expect(await screen.findByText(/couldn't start its agent/)).toBeInTheDocument();
        expect(box).toHaveValue('summarize my notes');
    });
});
