// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import { ConnectionBanner } from '../ConnectionBanner';
import { ErrorNotice } from '../settings/ErrorNotice';
import { useChatStore } from '../../stores/chatStore';
import { useModelStore } from '../../stores/modelStore';
import type { SystemStatus } from '../../types';

vi.mock('../../services/api', async (importOriginal) => {
    const real = await importOriginal<typeof api>();
    const mocked = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]));
    return { ...mocked, ApiError: real.ApiError };
});
const mocked = vi.mocked(api);

beforeEach(() => {
    vi.clearAllMocks();
    mocked.dismissDownloadFailure.mockResolvedValue({ status: 'dismissed', model: '' });
    useChatStore.setState({ backendConnected: true, currentSessionId: null, messages: [] });
    useModelStore.setState({ pickLoad: null });
});

describe('a model picked from the list', () => {
    const ready = { lemonade_running: true, init_state: 'ready', model_loaded: 'gemma4-it-e4b-FLM' } as SystemStatus;
    const QWEN = 'Qwen3.6-35B-A3B-GGUF';

    beforeEach(() => {
        useChatStore.setState({ systemStatus: ready });
    });

    it('shows its load with a timer, and that typing is fine meanwhile', () => {
        useModelStore.setState({ pickLoad: { model: QWEN, seconds: 12, error: null } });
        const { container } = render(<ConnectionBanner />);
        expect(screen.getByRole('status')).toHaveTextContent(`Loading ${QWEN}… 12s`);
        expect(container).toHaveTextContent('You can type now');
        expect(container).not.toHaveTextContent('Loading AI model');
    });

    it('stays up during a chat, where the next message waits on it', () => {
        useChatStore.setState({ currentSessionId: 's1', messages: [{ id: 1 } as never] });
        useModelStore.setState({ pickLoad: { model: QWEN, seconds: 3, error: null } });
        render(<ConnectionBanner />);
        expect(screen.getByRole('status')).toHaveTextContent(`Loading ${QWEN}`);
    });

    it('says why the load failed and offers to try again', () => {
        const retry = vi.fn().mockResolvedValue(undefined);
        const reason = `Not enough free memory to load ${QWEN}: it needs about 33.0 GB and only 3.7 GB is free. Close apps that use a lot of memory, then try again.`;
        useModelStore.setState({ pickLoad: { model: QWEN, seconds: 4, error: { reason } }, retryPickLoad: retry });
        render(<ConnectionBanner />);
        const alert = screen.getByRole('alert');
        expect(alert).toHaveTextContent(`Couldn't load ${QWEN}.`);
        expect(alert).toHaveTextContent('only 3.7 GB is free');
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expect(retry).toHaveBeenCalledTimes(1);
        fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
        expect(useModelStore.getState().pickLoad).toBeNull();
    });

    it('translates a failure GAIA itself reports', () => {
        useChatStore.setState({ locale: 'ar' });
        try {
            useModelStore.setState({ pickLoad: { model: QWEN, seconds: 0, error: { key: 'banner.pickLoad.lostTrack', reason: 'offline' } } });
            render(<ConnectionBanner />);
            expect(screen.getByRole('alert')).toHaveTextContent('تعذّر على GAIA متابعة التحميل');
        } finally {
            useChatStore.setState({ locale: 'en' });
        }
    });
});

describe('the start-up banner', () => {
    const booting = (step: string | undefined, name: string) => useChatStore.setState({ systemStatus: {
        lemonade_running: true, init_state: 'initializing',
        init_tasks: [{ name: 'Checking LLM server', step: 'check_server', status: 'done' }, { name, step, status: 'running' }],
    } as SystemStatus });

    afterEach(() => useChatStore.setState({ locale: 'en' }));

    it.each([
        ['check_server', 'Checking LLM server', 'Checking the model server', 'جارٍ فحص خادم النماذج'],
        ['load_libraries', 'Loading ML libraries', 'Getting document search ready', 'جارٍ تجهيز البحث في المستندات'],
        ['load_model', 'Loading AI model', 'Loading the model', 'جارٍ تحميل النموذج'],
    ])('names the %s step in the interface language, not the server\'s label', (step, name, en, ar) => {
        booting(step, name);
        const { unmount } = render(<ConnectionBanner />);
        expect(screen.getByRole('status')).toHaveTextContent(en);
        expect(screen.getByRole('status')).not.toHaveTextContent(name);
        unmount();

        useChatStore.setState({ locale: 'ar' });
        render(<ConnectionBanner />);
        expect(screen.getByRole('status')).toHaveTextContent(ar);
        expect(screen.getByRole('status')).not.toHaveTextContent(name);
    });

    it.each([
        ['an unknown step', 'warm_flux_capacitor', 'Warming the flux capacitor'],
        ['no step at all', undefined, 'Loading AI model'],
    ])('reads as getting GAIA ready for %s, never the server\'s label', (_, step, name) => {
        booting(step, name);
        const { unmount } = render(<ConnectionBanner />);
        expect(screen.getByRole('status')).toHaveTextContent('Getting GAIA ready');
        expect(screen.getByRole('status')).not.toHaveTextContent(name);
        unmount();

        useChatStore.setState({ locale: 'ar' });
        render(<ConnectionBanner />);
        expect(screen.getByRole('status')).toHaveTextContent('جارٍ تجهيز GAIA');
        expect(screen.getByRole('status')).not.toHaveTextContent(name);
    });

    it('never says it is loading once every boot task has finished', () => {
        useChatStore.setState({ systemStatus: {
            lemonade_running: true, init_state: 'initializing',
            init_tasks: [{ name: 'Checking LLM server', status: 'done' }, { name: 'Loading AI model', status: 'done' }],
        } as SystemStatus });
        render(<ConnectionBanner />);
        expect(screen.queryByRole('status')).toBeNull();
    });

    it('gives way to "slow to answer" when the status it rests on is stale', () => {
        useChatStore.setState({ systemStatus: {
            lemonade_running: true, init_state: 'initializing',
            init_tasks: [{ name: 'Loading AI model', status: 'running' }],
        } as SystemStatus });
        const { container } = render(<ConnectionBanner backendSlow />);
        expect(container).toHaveTextContent('GAIA is slow to answer');
        expect(container).not.toHaveTextContent('Loading AI model');
    });
});

describe('the model server is down', () => {
    it.each([
        '/usr/bin/lemonade-server serve --ctx-size 32768',
        '/usr/bin/lemond',
    ])('prints the backend start command %s verbatim', (command) => {
        useChatStore.setState({ systemStatus: { lemonade_running: false, start_command: command } as SystemStatus });
        const { container } = render(<ConnectionBanner />);
        expect(container.querySelector('code')?.textContent).toBe(command);
    });

    it('never invents a start command the backend did not give', () => {
        useChatStore.setState({ systemStatus: { lemonade_running: false, start_command: null } as SystemStatus });
        const { container } = render(<ConnectionBanner />);
        expect(screen.getByText(/The model server isn't answering/)).toBeInTheDocument();
        expect(container.querySelector('code')).toBeNull();
        expect(container).not.toHaveTextContent('lemonade-server serve');
    });

    it("doesn't claim a model server that never started stopped, and gives the backend's remedy", () => {
        const install = 'Lemonade Server is not installed. Run `gaia init` to install it, or set LEMONADE_SERVER_PATH to an existing install.';
        useChatStore.setState({ systemStatus: { lemonade_running: false, start_command: null, start_instruction: install } as SystemStatus });
        const { container } = render(<ConnectionBanner />);
        expect(container).toHaveTextContent("The model server isn't answering. Set up models on this PC");
        expect(container).not.toHaveTextContent('gaia init');
        expect(container).not.toHaveTextContent('stopped');
    });

    it("words the 'GAIA doesn't run this model server' line in Arabic too (#8345)", () => {
        const other = "GAIA doesn't run this model server, so it can't start it. Start it the way you usually do.";
        useChatStore.setState({ systemStatus: { lemonade_running: false, lemonade_probe: 'down', start_command: null, start_instruction: other } as SystemStatus });
        const english = render(<ConnectionBanner />);
        expect(english.container).toHaveTextContent(other);
        english.unmount();

        useChatStore.setState({ locale: 'ar' });
        try {
            const { container } = render(<ConnectionBanner />);
            expect(container).toHaveTextContent('لا يشغّل خادم النماذج هذا');
            // A remedy with a command in it is shown as the backend wrote it.
            useChatStore.setState({ systemStatus: { lemonade_running: false, lemonade_probe: 'down', start_command: null, start_instruction: 'Start the Lemonade app from Applications, then retry.' } as SystemStatus });
            expect(render(<ConnectionBanner />).container).toHaveTextContent('Start the Lemonade app from Applications, then retry.');
        } finally {
            useChatStore.setState({ locale: 'en' });
        }
    });

    it('offers a way to set up local models when none are installed, and only then', () => {
        const opened = vi.fn();
        window.addEventListener('gaia:set-up-local', opened);
        const install = 'Lemonade Server is not installed. Run `gaia init` to install it.';
        useChatStore.setState({ systemStatus: { lemonade_running: false, start_instruction: install } as SystemStatus });
        const { rerender } = render(<ConnectionBanner />);
        fireEvent.click(screen.getByRole('button', { name: 'Set up local models' }));
        expect(opened).toHaveBeenCalledTimes(1);
        window.removeEventListener('gaia:set-up-local', opened);

        act(() => useChatStore.setState({ systemStatus: { lemonade_running: false, start_command: '/usr/bin/lemond' } as SystemStatus }));
        rerender(<ConnectionBanner />);
        expect(screen.queryByRole('button', { name: 'Set up local models' })).not.toBeInTheDocument();
    });

    it('calls a model server whose health check failed busy, not stopped, and keeps the reason and remedy', () => {
        useChatStore.setState({ systemStatus: {
            lemonade_running: false, lemonade_error: 'Lemonade health query failed', start_command: '/usr/bin/lemond',
        } as SystemStatus });
        const { container } = render(<ConnectionBanner />);
        expect(container).toHaveTextContent("The model server isn't answering.");
        expect(container).not.toHaveTextContent('Lemonade');
        expect(container).toHaveTextContent('may be busy');
        expect(container).toHaveTextContent('If it stays this way, restart it:');
        expect(container.querySelector('code')?.textContent).toBe('/usr/bin/lemond');
    });

    it.each([
        ['hung', { lemonade_error: 'Lemonade health query failed' }, 'خادم النماذج لا يستجيب', 'فأعد تشغيله'],
        ['stopped', {}, 'خادم النماذج لا يستجيب', 'لتشغيله يدويًا'],
    ])('reads in Arabic when the model server is %s, the command kept left to right', (_, extra, message, hint) => {
        useChatStore.setState({ locale: 'ar', systemStatus: {
            lemonade_running: false, start_command: '/usr/bin/lemond', ...extra,
        } as SystemStatus });
        try {
            const { container } = render(<ConnectionBanner />);
            expect(container).toHaveTextContent(message);
            expect(container).toHaveTextContent(hint);
            const code = container.querySelector('code');
            expect(code?.textContent).toBe('/usr/bin/lemond');
            expect(code).toHaveAttribute('dir', 'ltr');
        } finally {
            useChatStore.setState({ locale: 'en' });
        }
    });
});

describe('a model server that listens but never answers', () => {
    it('does not offer local setup or name a setup command, since the server is installed', () => {
        useChatStore.setState({ systemStatus: {
            lemonade_running: false, lemonade_error: 'Lemonade did not answer in time',
            start_instruction: 'Lemonade Server is not installed. Run `gaia init` to install it.',
        } as SystemStatus });
        const { container } = render(<ConnectionBanner />);
        expect(container).toHaveTextContent("The model server isn't answering.");
        expect(screen.queryByRole('button', { name: 'Set up local models' })).not.toBeInTheDocument();
        expect(container).not.toHaveTextContent('Set up models on this PC');
        expect(container).not.toHaveTextContent('gaia init');
    });
});

describe('context window too small', () => {
    it('points at Settings instead of a shell command', () => {
        useChatStore.setState({ systemStatus: {
            lemonade_running: true, model_loaded: 'Gemma-4-E4B-it-GGUF', model_downloaded: true, expected_model_loaded: true,
            context_size_sufficient: false, model_context_size: 8192, start_command: '/usr/bin/lemond',
        } as SystemStatus });
        const { container } = render(<ConnectionBanner />);
        expect(screen.getByText(/LLM context window is too small/)).toBeInTheDocument();
        expect(screen.getByText(/Settings → Advanced/)).toBeInTheDocument();
        expect(container.querySelector('code')).toBeNull();
    });
});

describe('a failed model download', () => {
    const healthy = {
        lemonade_running: true, model_loaded: 'Gemma-4-E4B-it-GGUF', model_downloaded: true, expected_model_loaded: true,
        context_size_sufficient: true,
    };
    const progress = (state: 'downloading' | 'error', model = 'Gemma-4-E4B-it-GGUF') => ({
        state, model_name: model, percent: 40, file: null, file_index: 1, total_files: 1,
        downloaded_bytes: 0, total_bytes: 0, message: state === 'error' ? 'disk full' : undefined,
    });

    it('asks the server to forget the failure, then goes away, and a later failure shows again', async () => {
        useChatStore.setState({ systemStatus: { ...healthy, download_progress: progress('error') } as SystemStatus });
        const { rerender } = render(<ConnectionBanner />);
        expect(screen.getByRole('alert')).toHaveTextContent('failed');
        fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
        await waitFor(() => expect(mocked.dismissDownloadFailure).toHaveBeenCalledWith('Gemma-4-E4B-it-GGUF'));
        await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());

        act(() => { useChatStore.setState({ systemStatus: { ...healthy, download_progress: progress('downloading') } as SystemStatus }); });
        rerender(<ConnectionBanner />);
        act(() => { useChatStore.setState({ systemStatus: { ...healthy, download_progress: progress('error') } as SystemStatus }); });
        rerender(<ConnectionBanner />);
        expect(screen.getByRole('alert')).toHaveTextContent('failed');
    });

    it('keeps the warning when the server could not forget it', async () => {
        mocked.dismissDownloadFailure.mockRejectedValueOnce(new Error('offline'));
        useChatStore.setState({ systemStatus: { ...healthy, download_progress: progress('error') } as SystemStatus });
        render(<ConnectionBanner />);

        fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));

        await waitFor(() => expect(mocked.dismissDownloadFailure).toHaveBeenCalledTimes(1));
        expect(screen.getByRole('alert')).toHaveTextContent('failed');
    });

    it("names another model's failure and dismisses only it", async () => {
        useChatStore.setState({ systemStatus: { ...healthy, download_progress: progress('error', 'settings-GGUF') } as SystemStatus });
        render(<ConnectionBanner />);
        expect(screen.getByRole('alert')).toHaveTextContent('Download of settings-GGUF failed');

        fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));

        await waitFor(() => expect(mocked.dismissDownloadFailure).toHaveBeenCalledWith('settings-GGUF'));
        await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());

        // The active model's own state is untouched: its later failure still shows.
        act(() => { useChatStore.setState({ systemStatus: { ...healthy, download_progress: progress('error') } as SystemStatus }); });
        expect(screen.getByRole('alert')).toHaveTextContent('Download of Gemma-4-E4B-it-GGUF failed');
    });

    it('offers no dismiss while the download is still running', () => {
        useChatStore.setState({ systemStatus: { ...healthy, download_progress: progress('downloading') } as SystemStatus });
        render(<ConnectionBanner />);
        expect(screen.getByRole('status')).toHaveTextContent('Downloading');
        expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull();
    });
});

describe('an unreadable config is surfaced, not hidden', () => {
    it('shows the backend config error verbatim', () => {
        const message = 'GAIA config at /home/u/.gaia/config.json is not valid JSON: x. Delete it to reset to defaults.';
        useChatStore.setState({ systemStatus: {
            lemonade_running: true, model_loaded: 'Gemma-4-E4B-it-GGUF', model_downloaded: true, expected_model_loaded: true,
            context_size_sufficient: true, config_error: message,
        } as SystemStatus });
        render(<ConnectionBanner />);
        expect(screen.getByRole('alert')).toHaveTextContent('GAIA settings could not be loaded.');
        expect(screen.getByText(message)).toBeInTheDocument();
        expect(screen.queryByLabelText('Dismiss')).toBeNull();
    });
});

describe('checking again', () => {
    it('shows that a check is running until it answers', async () => {
        useChatStore.setState({ systemStatus: { lemonade_running: false, start_command: null } as SystemStatus });
        let finish!: () => void;
        render(<ConnectionBanner onRetry={() => new Promise<void>((r) => { finish = r; })} />);
        fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
        const busy = screen.getByRole('button', { name: 'Checking…' });
        expect(busy).toBeDisabled();
        await act(async () => { finish(); });
        expect(screen.getByRole('button', { name: 'Check again' })).toBeEnabled();
    });
});

describe('a slow backend', () => {
    it('says GAIA is slow, not that the connection was lost', () => {
        useChatStore.setState({ systemStatus: null });
        render(<ConnectionBanner backendSlow />);
        expect(screen.getByText(/GAIA is slow to answer/)).toBeInTheDocument();
        expect(screen.queryByText(/Can’t reach GAIA’s assistant/)).toBeNull();
    });
});

describe('ErrorNotice with the model server down', () => {
    const down = 'Lemonade Server not reachable at http://localhost:8000';

    it('says it may clear by itself when a server is installed', () => {
        useChatStore.setState({ systemStatus: { lemonade_running: false, start_command: '/usr/bin/lemond' } as SystemStatus });
        render(<ErrorNotice message={down} />);
        expect(screen.getByRole('alert')).toHaveTextContent('normally starts on its own');
        expect(screen.queryByRole('button', { name: 'Set up local models' })).not.toBeInTheDocument();
    });

    it('offers setup, and never promises a server that is not installed will start', () => {
        useChatStore.setState({ systemStatus: { lemonade_running: false, start_instruction: 'Lemonade Server is not installed. Run `gaia init` to install it.' } as SystemStatus });
        render(<ErrorNotice message={down} />);
        expect(screen.getByRole('alert')).not.toHaveTextContent('normally starts on its own');
        expect(screen.getByRole('button', { name: 'Set up local models' })).toBeInTheDocument();
    });
});
