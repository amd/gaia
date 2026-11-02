// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import installerSource from '../../../services/backend-installer.cjs?raw';
import engineSource from '../../../services/engine-install.cjs?raw';
import indexQuerySource from '../../../services/index-query.cjs?raw';
import { EngineInstallScreen, STAGE_LABELS } from '../EngineInstallScreen';
import { isInstallScreen } from '../../utils/engineInstall';
import { useChatStore } from '../../stores/chatStore';
import type { EngineInstallError, EngineInstallStatus, GaiaInstallBridge } from '../../types/agent';

const RUNNING: EngineInstallStatus = {
    phase: 'running', stage: 'install-package', percent: 42, message: 'Installing amd-gaia[ui]==0.25.4', error: null,
};
const ERROR: EngineInstallError = {
    stage: 'pre-checks',
    reason: 'disk',
    message: 'Not enough free disk space. Required: 2048 MB, available: 512 MB.',
    logTail: ['[pre] checking disk', '[pre] 512 MB free'],
};
const FAILED: EngineInstallStatus = { ...RUNNING, phase: 'failed', error: ERROR };

function mountBridge(initial: EngineInstallStatus) {
    let listener: ((status: EngineInstallStatus) => void) | null = null;
    const bridge: GaiaInstallBridge = {
        onProgress: vi.fn((cb) => {
            listener = cb;
            return () => { listener = null; };
        }),
        status: vi.fn(async () => initial),
        retry: vi.fn(async () => true),
        continueWithoutAgentUpdate: vi.fn(async () => true),
        openLogFile: vi.fn(async () => ({ ok: true, message: null })),
    };
    window.gaiaInstall = bridge;
    return { bridge, send: (status: EngineInstallStatus) => act(() => listener!(status)) };
}

beforeEach(() => {
    useChatStore.setState({ locale: 'en' });
});

afterEach(() => {
    cleanup();
    delete window.gaiaInstall;
    window.history.replaceState(null, '', '/');
});

describe('EngineInstallScreen while installing', () => {
    it('shows the stage in plain words on a real progress bar, never the id or the installer\'s message', async () => {
        mountBridge(RUNNING);
        render(<EngineInstallScreen />);

        const bar = await screen.findByRole('progressbar', { name: 'Installing GAIA' });
        expect(bar.tagName).toBe('PROGRESS');
        expect(bar).toHaveAttribute('value', '42');
        expect(screen.getByText('42%').tagName).toBe('BDI');
        expect(screen.getByText('42%')).toHaveAttribute('dir', 'ltr');
        expect(document.body.textContent).not.toContain('install-package');
        expect(document.body.textContent).not.toContain('amd-gaia');
    });

    it('follows progress from the desktop app', async () => {
        const { send } = mountBridge(RUNNING);
        render(<EngineInstallScreen />);
        await screen.findByText('Installing GAIA');

        send({ ...RUNNING, stage: 'verify', percent: 99 });

        expect(screen.getByRole('progressbar', { name: 'Finishing up' })).toHaveAttribute('value', '99');
    });

    it('the agent refresh is its own stage, with a moving bar and no invented percent', async () => {
        mountBridge({ ...RUNNING, stage: 'agent-refresh', percent: null });
        render(<EngineInstallScreen />);

        const bar = await screen.findByRole('progressbar', { name: 'Updating the GAIA agent' });
        expect(bar).not.toHaveAttribute('value');
        expect(document.body.textContent).not.toContain('%');
    });

    it('an unlabeled stage reads as neutral, and is reported', async () => {
        const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
        mountBridge({ ...RUNNING, stage: 'fetch-widgets' });
        render(<EngineInstallScreen />);

        await screen.findByRole('progressbar', { name: 'Setting up GAIA' });
        expect(warn).toHaveBeenCalledWith(expect.stringContaining('fetch-widgets'));
        warn.mockRestore();
    });

    it('is in Arabic when that is the chosen language', async () => {
        useChatStore.setState({ locale: 'ar' });
        mountBridge(RUNNING);
        render(<EngineInstallScreen />);

        expect(await screen.findByRole('heading')).toHaveTextContent(/\p{Script=Arabic}/u);
    });

    it('stops listening when it goes away', async () => {
        const { bridge } = mountBridge(RUNNING);
        const unsubscribe = vi.fn();
        vi.mocked(bridge.onProgress).mockReturnValue(unsubscribe);

        render(<EngineInstallScreen />).unmount();

        expect(unsubscribe).toHaveBeenCalled();
    });
});

describe('EngineInstallScreen after a failure', () => {
    it('shows the real error, what to do, and focuses Try again', async () => {
        mountBridge(FAILED);
        render(<EngineInstallScreen />);

        expect(await screen.findByRole('heading', { name: 'GAIA couldn’t finish installing' })).toBeInTheDocument();
        const alert = screen.getByRole('alert');
        expect(alert).toHaveTextContent('Free up some disk space, then try again.');
        expect(alert).toHaveTextContent(ERROR.message);
        expect(screen.getByRole('button', { name: 'Try again' })).toHaveFocus();
        expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    });

    it.each([
        ['network', 'Check your internet connection'],
        ['tls', 'company proxy'],
        ['locked', 'Another GAIA program'],
    ] as const)('names the fix for a %s failure', async (reason, hint) => {
        mountBridge({ ...FAILED, error: { ...ERROR, stage: 'install-package', reason } });
        render(<EngineInstallScreen />);

        expect(await screen.findByRole('alert')).toHaveTextContent(hint);
    });

    it('a failed agent refresh says so, and its error stays', async () => {
        const message = 'Could not reach https://hub.amd-gaia.ai: timed out';
        mountBridge({ ...FAILED, error: { ...ERROR, stage: 'agent-refresh', reason: null, message } });
        render(<EngineInstallScreen />);

        expect(await screen.findByRole('heading', { name: 'GAIA couldn’t update its agent' })).toBeInTheDocument();
        expect(screen.getByRole('alert')).toHaveTextContent(message);
        expect(screen.getByRole('alert')).toHaveTextContent('Your chats and models are safe.');
    });

    it('keeps the log tail behind a Details disclosure, and opens only the installer log', async () => {
        const { bridge } = mountBridge(FAILED);
        render(<EngineInstallScreen />);

        const summary = await screen.findByText('Details');
        const details = summary.closest('details')!;
        expect(details.open).toBe(false);
        expect(details).toHaveTextContent('[pre] 512 MB free');

        fireEvent.click(screen.getByRole('button', { name: 'Open log file', hidden: true }));
        expect(bridge.openLogFile).toHaveBeenCalledWith();
    });

    it('says so when the log cannot be opened', async () => {
        const { bridge } = mountBridge(FAILED);
        vi.mocked(bridge.openLogFile).mockResolvedValue({ ok: false, message: 'No application is associated' });
        render(<EngineInstallScreen />);
        await screen.findByText('Details');

        fireEvent.click(screen.getByRole('button', { name: 'Open log file', hidden: true }));

        expect(await screen.findByText('No application is associated')).toBeInTheDocument();
    });

    it('shows no command to type, whatever the installer suggested', async () => {
        mountBridge(FAILED);
        render(<EngineInstallScreen />);
        await screen.findByRole('alert');

        expect(screen.getByRole('alert').textContent).not.toMatch(/uv pip|gaia init/);
    });
});

describe('EngineInstallScreen retrying', () => {
    it('Try again re-runs the install over IPC and returns to progress', async () => {
        const { bridge, send } = mountBridge(FAILED);
        render(<EngineInstallScreen />);

        fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
        expect(bridge.retry).toHaveBeenCalledTimes(1);

        send({ ...RUNNING, stage: 'pre-checks', percent: 1 });

        expect(await screen.findByRole('progressbar', { name: 'Checking your PC' })).toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('a second failure replaces the first error', async () => {
        const { send } = mountBridge(FAILED);
        render(<EngineInstallScreen />);
        fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));

        send({ ...FAILED, error: { ...ERROR, reason: 'locked', message: 'os error 32' } });

        await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('os error 32'));
        expect(screen.getByRole('alert')).not.toHaveTextContent(ERROR.message);
    });

    it('stops a double click from asking twice while the first is in flight', async () => {
        const { bridge } = mountBridge(FAILED);
        vi.mocked(bridge.retry).mockReturnValue(new Promise(() => {}));
        render(<EngineInstallScreen />);

        const button = await screen.findByRole('button', { name: 'Try again' });
        fireEvent.click(button);
        fireEvent.click(button);

        expect(bridge.retry).toHaveBeenCalledTimes(1);
    });

    it('shows a retry the desktop app refused to run', async () => {
        const { bridge } = mountBridge(FAILED);
        const error = vi.spyOn(console, 'error').mockImplementation(() => {});
        vi.mocked(bridge.retry).mockRejectedValue(new Error('ipc channel closed'));
        render(<EngineInstallScreen />);

        fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));

        expect(await screen.findByText('ipc channel closed')).toBeInTheDocument();
        error.mockRestore();
    });
});

describe('when the screen is shown', () => {
    it('never in a browser, even with the URL asking for it', () => {
        window.history.replaceState(null, '', '/?engine=install');
        expect(isInstallScreen()).toBe(false);
    });

    it('in the desktop app only on the page main loads for an install', () => {
        mountBridge(RUNNING);
        expect(isInstallScreen()).toBe(false);
        window.history.replaceState(null, '', '/?engine=install');
        expect(isInstallScreen()).toBe(true);
    });

    it('matches the query main.cjs loads', () => {
        expect(indexQuerySource).toContain('INSTALL_SCREEN_QUERY = Object.freeze({ engine: "install" })');
    });
});

describe('stage labels', () => {
    it('cover every stage the installer and main report', () => {
        const block = /const STAGES = Object\.freeze\(\{([\s\S]*?)\}\);/.exec(installerSource)![1];
        const stages = [...block.matchAll(/:\s*"([^"]+)"/g)].map((m) => m[1]);
        const starting = /STARTING_STAGE = "([^"]+)"/.exec(engineSource)![1];

        expect(stages.length).toBeGreaterThan(4);
        expect(Object.keys(STAGE_LABELS).sort()).toEqual([...stages, starting].sort());
    });
});
