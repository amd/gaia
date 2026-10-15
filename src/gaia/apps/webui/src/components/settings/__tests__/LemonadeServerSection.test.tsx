// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LemonadeServerSection } from '../LemonadeServerSection';
import * as api from '../../../services/api';
import type { LemonadeTarget } from '../../../types';

vi.mock('../../../services/api');

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
});

describe('LemonadeServerSection', () => {
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
        expect(await screen.findByText('http://localhost:13305/api/v1', { selector: 'p[role="status"] code' })).toBeInTheDocument();
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

    it('is read-only when LEMONADE_BASE_URL chooses the server', async () => {
        mockedApi.getLemonadeTarget.mockResolvedValue({ ...MINE, source: 'env', base_url: 'http://box:13305/api/v1' });
        render(<LemonadeServerSection />);

        expect(await screen.findByText('http://box:13305/api/v1')).toBeInTheDocument();
        expect(screen.getByText(/Unset/)).toHaveTextContent('Unset LEMONADE_BASE_URL and restart GAIA to change this here.');
        expect(screen.queryByLabelText('Lemonade Server address')).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Use GAIA.s own/ })).not.toBeInTheDocument();
    });

    it('says when it cannot read the current server', async () => {
        mockedApi.getLemonadeTarget.mockRejectedValue(new Error('lemonade_mode is not valid in ~/.gaia/config.json'));
        render(<LemonadeServerSection />);
        expect(await screen.findByRole('alert')).toHaveTextContent('lemonade_mode is not valid');
    });
});
