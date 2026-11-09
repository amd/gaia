// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GoBackOffer } from '../GoBackOffer';
import { INITIAL_STATE, type UpdateState } from '../../../hooks/useUpdateStatus';
import { useChatStore } from '../../../stores/chatStore';

let state: UpdateState;
let listener: ((s: UpdateState) => void) | null;

function push(patch: Partial<UpdateState>) {
    state = { ...state, ...patch };
    act(() => listener?.(state));
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
    markSetupWorked: vi.fn(async () => state),
    revertToLastWorking: vi.fn(async () => state),
    resumeUpdates: vi.fn(async () => state),
    setAutoUpdate: vi.fn(async () => state),
    setIncludePrereleases: vi.fn(async () => state),
    downloadUpdate: vi.fn(async () => state),
    restart: vi.fn(async () => state),
};

const WORKED = { version: '0.26.0-rc.12', at: '2026-11-02T09:30:00.000Z' };

beforeEach(() => {
    useChatStore.setState({ locale: 'en' });
    state = { ...INITIAL_STATE, currentVersion: '0.26.0-rc.13', lastWorking: WORKED };
    listener = null;
    Object.values(bridge).forEach((f) => f.mockClear());
    vi.stubGlobal('gaiaUpdater', bridge);
});
afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
});

const goBack = () => screen.findByRole('button', { name: 'Go back to GAIA 0.26.0-rc.12' });

describe('GoBackOffer', () => {
    it('offers the version setup last finished on, with its date', async () => {
        render(<GoBackOffer />);
        await goBack();
        const offer = screen.getByRole('region', { name: 'Go back to a version that worked' });
        // English dates follow the PC's own region.
        const date = new Date(WORKED.at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
        expect(offer).toHaveTextContent(`GAIA 0.26.0-rc.12 finished setup on this PC on ${date}.`);
    });

    it('says what happens to chats, settings and the newer version', async () => {
        render(<GoBackOffer />);
        await goBack();
        const offer = screen.getByRole('region', { name: 'Go back to a version that worked' });
        expect(offer).toHaveTextContent('Your chats, memory and settings stay as they are.');
        expect(offer).toHaveTextContent('GAIA 0.26.0-rc.13 stays available');
        expect(offer).toHaveTextContent('Settings → General → Updates');
    });

    it('goes back only when asked', async () => {
        render(<GoBackOffer />);
        await goBack();
        expect(bridge.revertToLastWorking).not.toHaveBeenCalled();
        fireEvent.click(await goBack());
        await waitFor(() => expect(bridge.revertToLastWorking).toHaveBeenCalledTimes(1));
    });

    it('shows the download, then the restart, in place of the button', async () => {
        let finish!: () => void;
        bridge.revertToLastWorking.mockImplementationOnce(() => new Promise((r) => { finish = () => r(state); }));
        render(<GoBackOffer />);
        fireEvent.click(await goBack());
        push({ status: 'downloading', progress: 40, version: '0.26.0-rc.12' });
        expect(screen.queryByRole('button', { name: /Go back/ })).toBeNull();
        expect(screen.getByRole('status')).toHaveTextContent('Downloading GAIA 0.26.0-rc.12… 40%');
        push({ status: 'downloaded', progress: 100 });
        expect(screen.getByRole('status')).toHaveTextContent('Restarting into GAIA 0.26.0-rc.12…');
        await act(async () => finish());
    });

    it('offers the restart when the user kept GAIA running', async () => {
        bridge.revertToLastWorking.mockImplementationOnce(async () => {
            push({ status: 'downloaded', progress: 100, version: '0.26.0-rc.12', pinnedVersion: 'v0.26.0-rc12' });
            return state;
        });
        render(<GoBackOffer />);
        fireEvent.click(await goBack());
        const restart = await screen.findByRole('button', { name: 'Restart into GAIA 0.26.0-rc.12' });
        expect(screen.getByRole('region', { name: 'Go back to a version that worked' })).toHaveTextContent('GAIA 0.26.0-rc.12 has downloaded');
        fireEvent.click(restart);
        await waitFor(() => expect(bridge.restart).toHaveBeenCalledTimes(1));
    });

    it('offers it again after a refused restart', async () => {
        bridge.revertToLastWorking.mockImplementationOnce(async () => {
            push({ status: 'downloaded', progress: 100, version: '0.26.0-rc.12' });
            push({ status: 'idle', version: null, progress: 0 });
            throw new Error("Error invoking remote method 'gaia:update:revert-to-working': Error: GAIA needs about 1 GB free");
        });
        render(<GoBackOffer />);
        fireEvent.click(await goBack());
        expect(await screen.findByRole('alert')).toHaveTextContent('GAIA needs about 1 GB free');
        expect(await goBack()).toBeEnabled();
    });

    it("doesn't take another update's download for the way back", async () => {
        render(<GoBackOffer />);
        await goBack();
        push({ status: 'downloading', progress: 10, version: '0.26.1' });
        expect(await goBack()).toBeInTheDocument();
        expect(screen.queryByRole('status')).toBeNull();
    });

    it('says why going back failed and offers it again', async () => {
        bridge.revertToLastWorking.mockRejectedValueOnce(new Error(
            "Error invoking remote method 'gaia:update:revert-to-working': Error: gaia-update-error:"
            + JSON.stringify({ code: 'downloadFailed', params: { version: '0.26.0-rc.12', reason: 'net::ERR_INTERNET_DISCONNECTED' }, message: 'x' }),
        ));
        render(<GoBackOffer />);
        fireEvent.click(await goBack());
        expect(await screen.findByRole('alert')).toHaveTextContent('ERR_INTERNET_DISCONNECTED');
        expect(await goBack()).toBeEnabled();
    });

    it('offers nothing when no older version finished setup here', async () => {
        state = { ...state, lastWorking: null };
        const { container } = render(<GoBackOffer />);
        await waitFor(() => expect(bridge.getStatus).toHaveBeenCalled());
        expect(container).toBeEmptyDOMElement();
    });

    it('offers nothing outside the desktop app', () => {
        vi.stubGlobal('gaiaUpdater', undefined);
        const { container } = render(<GoBackOffer />);
        expect(container).toBeEmptyDOMElement();
    });
});
