// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ConnectionBanner } from '../ConnectionBanner';
import { ErrorNotice } from '../settings/ErrorNotice';
import { useChatStore } from '../../stores/chatStore';
import { useModelStore } from '../../stores/modelStore';
import type { SystemStatus } from '../../types';

beforeEach(() => {
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
    it('names the boot task that is running', () => {
        useChatStore.setState({ systemStatus: {
            lemonade_running: true, init_state: 'initializing',
            init_tasks: [{ name: 'Checking LLM server', status: 'done' }, { name: 'Loading AI model', status: 'running' }],
        } as SystemStatus });
        render(<ConnectionBanner />);
        expect(screen.getByRole('status')).toHaveTextContent('Loading AI model');
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
        expect(screen.getByText(/The model server isn't running/)).toBeInTheDocument();
        expect(container.querySelector('code')).toBeNull();
        expect(container).not.toHaveTextContent('lemonade-server serve');
    });

    it("doesn't claim a model server that never started stopped, and gives the backend's remedy", () => {
        const install = 'Lemonade Server is not installed. Run `gaia init` to install it, or set LEMONADE_SERVER_PATH to an existing install.';
        useChatStore.setState({ systemStatus: { lemonade_running: false, start_command: null, start_instruction: install } as SystemStatus });
        const { container } = render(<ConnectionBanner />);
        expect(container).toHaveTextContent("The model server isn't running. Set up models on this PC");
        expect(container).not.toHaveTextContent('gaia init');
        expect(container).not.toHaveTextContent('stopped');
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
        expect(container).not.toHaveTextContent("isn't running");
        expect(container).toHaveTextContent('If it stays this way, restart it:');
        expect(container.querySelector('code')?.textContent).toBe('/usr/bin/lemond');
    });

    it.each([
        ['hung', { lemonade_error: 'Lemonade health query failed' }, 'خادم النماذج لا يستجيب', 'فأعد تشغيله'],
        ['stopped', {}, 'خادم النماذج لا يعمل', 'لتشغيله يدويًا'],
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
