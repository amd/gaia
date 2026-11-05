// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LemonadeServerSection } from '../LemonadeServerSection';
import * as api from '../../../services/api';
import { useChatStore } from '../../../stores/chatStore';
import type { LemonadeTarget } from '../../../types';

vi.mock('../../../services/api');

const runSetupAgain = vi.fn();
const closeSettings = vi.fn();

const mockedApi = vi.mocked(api);

const OWN: LemonadeTarget = {
    mode: 'gaia', saved_mode: 'gaia', source: 'gaia', base_url: null, saved_base_url: null,
    api_key_stored: false, gaia_installed: true, offer: false,
    what_gaia_does: 'GAIA registers its embedding model on your server.',
};
const MINE: LemonadeTarget = {
    ...OWN, mode: 'external', saved_mode: 'external', source: 'config',
    base_url: 'http://localhost:13305/api/v1', saved_base_url: 'http://localhost:13305/api/v1', api_key_stored: true,
};

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ runSetupAgain, closeSettings });
});

describe('LemonadeServerSection', () => {
    it('says when the address set outside the app is ignored, and how to use it again', async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue({ ...OWN, env_url_ignored: 'http://127.0.0.1:59999/api/v1' });
        render(<LemonadeServerSection />);

        expect(await screen.findByText('GAIA uses its own Lemonade Server.')).toBeInTheDocument();
        expect(screen.getByText(
            "GAIA ignores the address set outside the app (http://127.0.0.1:59999/api/v1) because you chose GAIA's own. Change that address to use it again.",
        )).toBeInTheDocument();
    });

    it('says nothing about an outside address when none is ignored', async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue({ ...OWN, env_url_ignored: null });
        render(<LemonadeServerSection />);

        expect(await screen.findByText('GAIA uses its own Lemonade Server.')).toBeInTheDocument();
        expect(screen.queryByText(/GAIA ignores the address/)).not.toBeInTheDocument();
    });

    it("switches from GAIA's own to the user's server and never shows the key back", async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue(OWN);
        mockedApi.setLemonadeTarget.mockResolvedValue({ ...MINE, daemon: null });
        render(<LemonadeServerSection />);

        expect(await screen.findByText('GAIA uses its own Lemonade Server.')).toBeInTheDocument();
        expect(screen.getByText(/GAIA registers its embedding model on your server\./)).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Use GAIA.s own/ })).not.toBeInTheDocument();

        fireEvent.change(screen.getByLabelText('Lemonade Server address'), { target: { value: ' http://localhost:13305 ' } });
        const key = screen.getByLabelText('Lemonade API key');
        expect(key).toHaveAttribute('type', 'password');
        fireEvent.change(key, { target: { value: 'sk-secret' } });
        fireEvent.click(screen.getByRole('button', { name: 'Use this server' }));

        await waitFor(() => expect(mockedApi.setLemonadeTarget).toHaveBeenCalledExactlyOnceWith(
            { mode: 'external', base_url: 'http://localhost:13305', api_key: 'sk-secret' },
        ));
        expect(await screen.findByText(/http:\/\/localhost:13305\/api\/v1/, { selector: 'p[role="status"]' })).toBeInTheDocument();
        expect(screen.getByLabelText('Lemonade API key')).toHaveValue('');
        expect(document.body.innerHTML).not.toContain('sk-secret');
    });

    it('sends no key when the field is blank', async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue(MINE);
        mockedApi.setLemonadeTarget.mockResolvedValue({ ...MINE, daemon: null });
        render(<LemonadeServerSection />);

        expect(await screen.findByLabelText('Lemonade Server address')).toHaveValue(MINE.saved_base_url);
        expect(screen.getByLabelText('Lemonade API key')).toHaveValue('');
        fireEvent.click(screen.getByRole('button', { name: 'Use this server' }));
        await waitFor(() => expect(mockedApi.setLemonadeTarget).toHaveBeenCalledExactlyOnceWith(
            { mode: 'external', base_url: MINE.saved_base_url, api_key: undefined },
        ));
    });

    it("goes back to GAIA's own", async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue(MINE);
        mockedApi.setLemonadeTarget.mockResolvedValue({ ...OWN, daemon: null });
        render(<LemonadeServerSection />);

        fireEvent.click(await screen.findByRole('button', { name: /Use GAIA.s own/ }));
        await waitFor(() => expect(mockedApi.setLemonadeTarget).toHaveBeenCalledExactlyOnceWith({ mode: 'gaia' }));
        expect(await screen.findByText('GAIA uses its own Lemonade Server.')).toBeInTheDocument();
    });

    it("shows the server's answer verbatim when it is not switched", async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue(OWN);
        const message = 'The Lemonade Server at http://localhost:13305/api/v1 needs an API key. Enter the key it was started with (its LEMONADE_API_KEY).';
        mockedApi.setLemonadeTarget.mockRejectedValue(Object.assign(new Error(message), { reason: 'needs_key' }));
        render(<LemonadeServerSection />);

        fireEvent.change(await screen.findByLabelText('Lemonade Server address'), { target: { value: 'http://localhost:13305' } });
        fireEvent.click(screen.getByRole('button', { name: 'Use this server' }));
        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent(message);
        expect(alert).toHaveTextContent('Needs the API key');
        expect(screen.getByText('GAIA uses its own Lemonade Server.')).toBeInTheDocument();
    });

    it("offers GAIA's own when LEMONADE_BASE_URL chooses the server, and names no command (#6671)", async () => {
        const ENV = { ...MINE, source: 'env', base_url: 'http://box:13305/api/v1' } as const;
        mockedApi.getLemonadeTarget.mockResolvedValue(ENV);
        mockedApi.setLemonadeTarget.mockResolvedValue({ ...OWN, env_url_ignored: 'http://box:13305/api/v1', daemon: null });
        render(<LemonadeServerSection />);

        expect(await screen.findByText(/http:\/\/box:13305\/api\/v1/)).toBeInTheDocument();
        expect(screen.queryByText(/unset|restart GAIA/i)).not.toBeInTheDocument();
        expect(screen.queryByLabelText('Lemonade Server address')).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Use GAIA.s own/ }));
        await waitFor(() => expect(mockedApi.setLemonadeTarget).toHaveBeenCalledExactlyOnceWith({ mode: 'gaia' }));
        expect(await screen.findByText('GAIA uses its own Lemonade Server.')).toBeInTheDocument();
        expect(runSetupAgain).not.toHaveBeenCalled();
    });

    it("opens setup to install GAIA's own when it isn't installed yet", async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue({ ...MINE, source: 'env', gaia_installed: false });
        mockedApi.setLemonadeTarget.mockResolvedValue({ ...OWN, gaia_installed: false, daemon: null });
        render(<LemonadeServerSection />);

        fireEvent.click(await screen.findByRole('button', { name: /Use GAIA.s own/ }));
        await waitFor(() => expect(runSetupAgain).toHaveBeenCalledWith(true));
        expect(closeSettings).toHaveBeenCalled();
    });

    it('says why when the switch over LEMONADE_BASE_URL fails', async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue({ ...MINE, source: 'env' });
        mockedApi.setLemonadeTarget.mockRejectedValue(new Error("GAIA could not switch to its own Lemonade Server: boom Nothing was changed."));
        render(<LemonadeServerSection />);

        fireEvent.click(await screen.findByRole('button', { name: /Use GAIA.s own/ }));
        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent('Not switched');
        expect(alert).toHaveTextContent('Nothing was changed.');
    });

    it('says when it cannot read the current server', async () => {
        mockedApi.getLemonadeTarget.mockRejectedValue(new Error('lemonade_mode is not valid in ~/.gaia/config.json'));
        render(<LemonadeServerSection />);
        expect(await screen.findByRole('alert')).toHaveTextContent('lemonade_mode is not valid');
    });
});
