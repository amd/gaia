// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { UpdatesSection, describeUpdateStatus } from '../UpdatesSection';
import { translator } from '../../../i18n/useT';
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
    revertUpdate: vi.fn(async () => state),
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

    it('says it could not read the update status, in plain words, instead of claiming up to date', async () => {
        bridge.getStatus.mockRejectedValueOnce(
            new Error("Error invoking remote method 'gaia-updater:get-status': Error: No handler registered for 'gaia-updater:get-status'"),
        );
        render(<UpdatesSection />);
        const line = await screen.findByText(
            /couldn't read the update status\. press try again, or restart gaia if it keeps happening\./i,
            { selector: '[role="status"]' },
        );
        expect(line.textContent).not.toMatch(/handler|gaia-updater|remote method|unknown/i);
        expect(screen.queryByText(/up to date/i, { selector: '[role="status"]' })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: /try again/i })).toBeEnabled();
        expect(screen.queryByRole('button', { name: /check for updates/i })).not.toBeInTheDocument();
    });

    it('reads the status again on Try again', async () => {
        bridge.getStatus.mockRejectedValueOnce(new Error('No handler registered'));
        render(<UpdatesSection />);
        await screen.findByText(/couldn't read the update status/i, { selector: '[role="status"]' });
        await userEvent.click(screen.getByRole('button', { name: /try again/i }));
        await screen.findByText(/gaia is up to date/i, { selector: '[role="status"]' });
        expect(bridge.getStatus).toHaveBeenCalledTimes(2);
        expect(screen.getByRole('button', { name: /check for updates/i })).toBeEnabled();
    });

    it('drops the read failure once the updater pushes a real status', async () => {
        bridge.getStatus.mockRejectedValueOnce(new Error('No handler registered'));
        render(<UpdatesSection />);
        await screen.findByText(/couldn't read the update status/i, { selector: '[role="status"]' });
        act(() => push({ status: 'available', version: '0.26.0' }));
        await screen.findByText(/gaia 0\.26\.0 is available/i, { selector: '[role="status"]' });
        expect(screen.queryByText(/couldn't read the update status/i, { selector: '[role="status"]' })).not.toBeInTheDocument();
    });

    it('says the update status could not be read in Arabic', () => {
        const line = describeUpdateStatus({ ...INITIAL_STATE, readFailed: true }, translator('ar'));
        expect(line).toContain('تعذّرت قراءة حالة التحديث');
        expect(line).toContain('«إعادة المحاولة»');
        expect(line).not.toContain('محدّث');
    });

    it('shows auto-update off by default', async () => {
        render(<UpdatesSection />);
        await waitFor(() => expect(screen.getByText(/running gaia 0\.25\.0/i, { selector: '[role="status"]' })).toBeInTheDocument());
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
        await screen.findByText(/running gaia 0\.25\.0/i, { selector: '[role="status"]' });
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
        expect(await screen.findByText(/no gaia release publishes a desktop update/i, { selector: '[role="status"]' })).toBeInTheDocument();
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

    it('offers the way back only when there is an earlier version', async () => {
        render(<UpdatesSection />);
        await screen.findByText(/running gaia 0\.25\.0/i, { selector: '[role="status"]' });
        expect(screen.queryByRole('button', { name: /go back/i })).not.toBeInTheDocument();
        act(() => push({ previousVersion: '0.24.0' }));
        await userEvent.click(await screen.findByRole('button', { name: /go back to gaia 0\.24\.0/i }));
        expect(bridge.revertUpdate).toHaveBeenCalledTimes(1);
    });

    it('says why going back failed', async () => {
        state = { ...state, previousVersion: '0.24.0' };
        bridge.revertUpdate.mockRejectedValueOnce(new Error('no connection'));
        render(<UpdatesSection />);
        await userEvent.click(await screen.findByRole('button', { name: /go back/i }));
        expect(await screen.findByRole('alert')).toHaveTextContent(/no connection/);
    });

    it('says a failed install left the old version running', async () => {
        state = { ...state, installFailed: { target: '0.26.0', from: '0.25.0' } };
        render(<UpdatesSection />);
        expect(await screen.findByRole('alert')).toHaveTextContent(/0\.26\.0 didn't install.*still on 0\.25\.0/);
    });

    it('on a Mac links an available update to its release page instead of installing it', async () => {
        state = {
            ...state,
            status: 'available',
            version: '0.26.0',
            manualInstall: true,
            downloadUrl: 'https://github.com/amd/gaia/releases/tag/v0.26.0',
        };
        render(<UpdatesSection />);
        const link = await screen.findByRole('link', { name: /download gaia 0\.26\.0/i });
        expect(link).toHaveAttribute('href', 'https://github.com/amd/gaia/releases/tag/v0.26.0');
        expect(link).toHaveAttribute('target', '_blank');
        expect(link).toHaveAccessibleDescription(/can't update itself on this mac/i);
        expect(screen.queryByRole('button', { name: /update to/i })).toBeNull();
        expect(screen.queryByRole('switch', { name: /install updates automatically/i })).toBeNull();
        expect(rcSwitch()).toBeEnabled();
        expect(bridge.downloadUpdate).not.toHaveBeenCalled();
    });

    it('on a Mac with nothing offered shows no download link', async () => {
        state = { ...state, manualInstall: true };
        render(<UpdatesSection />);
        await screen.findByText(/gaia is up to date/i, { selector: '[role="status"]' });
        expect(screen.queryByRole('link', { name: /download gaia/i })).toBeNull();
        expect(screen.queryByText(/can't update itself on this mac/i)).toBeNull();
    });

    it('explains the Mac download in Arabic', () => {
        const t = translator('ar');
        expect(t('update.settings.manualHint')).toContain('Mac');
        expect(t('update.settings.manualHint')).not.toBe(translator('en')('update.settings.manualHint'));
        expect(t('update.settings.downloadVersion', { version: '0.26.0' })).toContain('0.26.0');
    });

    it('opens the version picker', async () => {
        render(<UpdatesSection />);
        await userEvent.click(await screen.findByRole('button', { name: /install a specific version/i }));
        expect(await screen.findByRole('dialog', { name: /install a specific version/i })).toBeInTheDocument();
    });
});

describe('UpdateIndicator', () => {
    it('offers the way back when GAIA did not start after updating', async () => {
        state = { ...state, startFailed: true, previousVersion: '0.24.0' };
        render(<UpdateIndicator />);
        await userEvent.click(await screen.findByRole('button', { name: /go back to 0\.24\.0/i }));
        expect(bridge.revertUpdate).toHaveBeenCalledTimes(1);
    });

    it('shows download progress once going back has started', async () => {
        state = { ...state, startFailed: true, previousVersion: '0.24.0', status: 'downloading', progress: 40 };
        render(<UpdateIndicator />);
        expect(await screen.findByText(/downloading update.*40%/i)).toBeInTheDocument();
    });

    it('offers Update now for an available update', async () => {
        state = { ...state, status: 'available', version: '0.26.0' };
        render(<UpdateIndicator />);
        await userEvent.click(await screen.findByRole('button', { name: /update now/i }));
        expect(bridge.downloadUpdate).toHaveBeenCalledTimes(1);
    });

    it('on a Mac offers a download link, never Update now', async () => {
        state = {
            ...state,
            status: 'available',
            version: '0.26.0-rc.13',
            manualInstall: true,
            downloadUrl: 'https://github.com/amd/gaia/releases/tag/v0.26.0-rc13',
        };
        render(<UpdateIndicator />);
        const link = await screen.findByRole('link', { name: /^download$/i });
        expect(link).toHaveAttribute('href', 'https://github.com/amd/gaia/releases/tag/v0.26.0-rc13');
        expect(screen.queryByRole('button', { name: /update now/i })).toBeNull();
        expect(screen.getByRole('status')).toHaveAttribute('title', expect.stringMatching(/can't update itself on this mac/i));
    });

    it('on a Mac falls back to the releases page when the update names no page', async () => {
        state = { ...state, status: 'available', version: '0.26.0', manualInstall: true, downloadUrl: null };
        render(<UpdateIndicator />);
        expect(await screen.findByRole('link', { name: /^download$/i }))
            .toHaveAttribute('href', 'https://github.com/amd/gaia/releases');
    });

    it('restarts into a downloaded update through the bridge', async () => {
        state = { ...state, status: 'downloaded', version: '0.26.0' };
        render(<UpdateIndicator />);
        await userEvent.click(await screen.findByRole('button', { name: /restart to apply/i }));
        expect(bridge.restart).toHaveBeenCalledTimes(1);
        expect(bridge.check).not.toHaveBeenCalled();
    });
});
