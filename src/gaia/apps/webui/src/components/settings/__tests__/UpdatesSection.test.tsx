// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { UpdatesSection } from '../UpdatesSection';
import { UpdateIndicator } from '../../UpdateIndicator';
import { INITIAL_STATE } from '../../../hooks/useUpdateStatus';
import type { UpdateState } from '../../../hooks/useUpdateStatus';

let state: UpdateState;
let listener: ((s: UpdateState) => void) | null;

function push(patch: Partial<UpdateState>) {
    state = { ...state, ...patch };
    listener?.(state);
}

const bridge = {
    getStatus: vi.fn(async () => state),
    check: vi.fn(async () => state),
    onStatusChange: vi.fn((cb: (s: UpdateState) => void) => {
        listener = cb;
        return () => { listener = null; };
    }),
    listReleases: vi.fn(async () => []),
    installVersion: vi.fn(async () => state),
    resumeUpdates: vi.fn(async () => { push({ pinnedVersion: null }); return state; }),
    setAutoUpdate: vi.fn(async (enabled: boolean) => { push({ autoUpdate: enabled }); return state; }),
    setIncludePrereleases: vi.fn(async (enabled: boolean) => { push({ includePrereleases: enabled }); return state; }),
    downloadUpdate: vi.fn(async () => { push({ status: 'downloading', progress: 0 }); return state; }),
    restart: vi.fn(async () => state),
};

beforeEach(() => {
    state = { ...INITIAL_STATE, currentVersion: '0.25.0' };
    listener = null;
    Object.values(bridge).forEach((f) => f.mockClear());
    vi.stubGlobal('gaiaUpdater', bridge);
});
afterEach(() => { vi.unstubAllGlobals(); });

const rcSwitch = () => screen.getByRole('switch', { name: /include release candidates/i });
const autoSwitch = () => screen.getByRole('switch', { name: /install updates automatically/i });

describe('UpdatesSection', () => {
    it('renders nothing outside the desktop app', () => {
        vi.stubGlobal('gaiaUpdater', undefined);
        const { container } = render(<UpdatesSection />);
        expect(container).toBeEmptyDOMElement();
    });

    it('shows auto-update off by default', async () => {
        render(<UpdatesSection />);
        await waitFor(() => expect(screen.getByText(/running gaia 0\.25\.0/i)).toBeInTheDocument());
        expect(autoSwitch()).not.toBeChecked();
    });

    it('turning auto-update on saves it', async () => {
        render(<UpdatesSection />);
        await userEvent.click(autoSwitch());
        expect(bridge.setAutoUpdate).toHaveBeenCalledWith(true);
        await waitFor(() => expect(autoSwitch()).toBeChecked());
    });

    it('opting into release candidates leaves auto-update off', async () => {
        render(<UpdatesSection />);
        await screen.findByText(/running gaia 0\.25\.0/i);
        expect(rcSwitch()).not.toBeChecked();
        expect(rcSwitch()).toBeEnabled();

        await userEvent.click(rcSwitch());
        expect(bridge.setIncludePrereleases).toHaveBeenCalledWith(true);
        await waitFor(() => expect(rcSwitch()).toBeChecked());
        expect(autoSwitch()).not.toBeChecked();
        expect(bridge.setAutoUpdate).not.toHaveBeenCalled();
    });

    it('tells a release-candidate build it is offered newer candidates anyway', async () => {
        state = { ...state, currentVersion: '0.25.2-rc.3' };
        render(<UpdatesSection />);
        expect(await screen.findByText(/this is a release candidate/i)).toBeInTheDocument();
        expect(rcSwitch()).not.toBeChecked();
    });

    it('says plainly when no release publishes an update for this platform', async () => {
        state = { ...state, status: 'no-channel', error: 'No GAIA release publishes a desktop update for this platform yet' };
        render(<UpdatesSection />);
        expect(await screen.findByText(/no gaia release publishes a desktop update/i)).toBeInTheDocument();
        expect(screen.queryByText(/up to date/i)).toBeNull();
    });

    it('offers an available update and downloads it on request', async () => {
        state = { ...state, status: 'available', version: '0.26.0' };
        render(<UpdatesSection />);
        const btn = await screen.findByRole('button', { name: /update to 0\.26\.0/i });
        await userEvent.click(btn);
        expect(bridge.downloadUpdate).toHaveBeenCalledTimes(1);
    });

    it('does not offer the update while pinned, and unpins on request', async () => {
        state = { ...state, status: 'available', version: '0.26.0', pinnedVersion: 'v0.24.0' };
        render(<UpdatesSection />);
        await screen.findByText(/pinned to v0\.24\.0/i);
        expect(screen.queryByRole('button', { name: /update to/i })).toBeNull();
        await userEvent.click(screen.getByRole('button', { name: /unpin/i }));
        expect(bridge.resumeUpdates).toHaveBeenCalledTimes(1);
    });

    it('says why a failed save failed', async () => {
        bridge.setAutoUpdate.mockRejectedValueOnce(new Error('disk is read-only'));
        render(<UpdatesSection />);
        await userEvent.click(autoSwitch());
        expect(await screen.findByRole('alert')).toHaveTextContent(/disk is read-only/);
        expect(autoSwitch()).not.toBeChecked();
    });

    it('opens the version picker', async () => {
        render(<UpdatesSection />);
        await userEvent.click(await screen.findByRole('button', { name: /install a specific version/i }));
        expect(await screen.findByRole('dialog', { name: /install a specific version/i })).toBeInTheDocument();
    });
});

describe('UpdateIndicator', () => {
    it('offers Update now for an available update', async () => {
        state = { ...state, status: 'available', version: '0.26.0' };
        render(<UpdateIndicator />);
        await userEvent.click(await screen.findByRole('button', { name: /update now/i }));
        expect(bridge.downloadUpdate).toHaveBeenCalledTimes(1);
    });

    it('restarts into a downloaded update through the bridge', async () => {
        state = { ...state, status: 'downloaded', version: '0.26.0' };
        render(<UpdateIndicator />);
        await userEvent.click(await screen.findByRole('button', { name: /restart to apply/i }));
        expect(bridge.restart).toHaveBeenCalledTimes(1);
        expect(bridge.check).not.toHaveBeenCalled();
    });
});
