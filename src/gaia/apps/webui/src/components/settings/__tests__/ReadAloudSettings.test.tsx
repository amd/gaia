// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ReadAloudSettings } from '../ReadAloudSettings';
import * as api from '../../../services/api';
import { useReadAloudStore } from '../../../stores/readAloudStore';

vi.mock('../../../services/api', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../../services/api')>();
    return { ...actual, getSpeechSettings: vi.fn(), updateSpeechSettings: vi.fn(), speak: vi.fn() };
});

const mockedApi = vi.mocked(api);

const SETTINGS: api.SpeechSettings = {
    voice: 'af_bella',
    speed: 100,
    auto: false,
    min_speed: 50,
    max_speed: 200,
    voices: [
        { id: 'af_bella', name: 'Bella', accent: 'american', gender: 'female', quality: 'A-' },
        { id: 'am_michael', name: 'Michael', accent: 'american', gender: 'male', quality: 'C+' },
        { id: 'bf_emma', name: 'Emma', accent: 'british', gender: 'female', quality: 'B-' },
        { id: 'bm_george', name: 'George', accent: 'british', gender: 'male', quality: 'C' },
    ],
};

beforeEach(() => {
    vi.clearAllMocks();
    useReadAloudStore.setState({ settings: null, messageId: null, status: null, error: null });
    mockedApi.getSpeechSettings.mockResolvedValue(SETTINGS);
    mockedApi.updateSpeechSettings.mockImplementation(async (change) => ({ ...SETTINGS, ...change }));
});

describe('ReadAloudSettings', () => {
    it('groups the voices by accent and gender', async () => {
        render(<ReadAloudSettings />);
        const voice = await screen.findByLabelText('Voice');
        const groups = within(voice).getAllByRole('group');
        expect(groups.map((g) => g.getAttribute('label'))).toEqual([
            'American English, female',
            'American English, male',
            'British English, female',
            'British English, male',
        ]);
        expect(within(groups[3]).getByRole('option', { name: 'George' })).toBeTruthy();
        expect((voice as HTMLSelectElement).value).toBe('af_bella');
    });

    it('changing a setting stops a preview but not a reply being read in the chat', async () => {
        const stop = vi.fn();
        useReadAloudStore.setState({ messageId: 's1:7', status: 'playing', stop });
        render(<ReadAloudSettings />);
        fireEvent.change(await screen.findByLabelText('Speed'), { target: { value: '150' } });
        await waitFor(() => expect(mockedApi.updateSpeechSettings).toHaveBeenCalledWith({ speed: 150 }));
        expect(stop).not.toHaveBeenCalled();
        useReadAloudStore.setState({ messageId: 'settings:preview' });
        fireEvent.change(screen.getByLabelText('Speed'), { target: { value: '75' } });
        expect(stop).toHaveBeenCalled();
    });

    it('saves a new voice, speed and auto-read to the server', async () => {
        render(<ReadAloudSettings />);
        fireEvent.change(await screen.findByLabelText('Voice'), { target: { value: 'bm_george' } });
        await waitFor(() => expect(mockedApi.updateSpeechSettings).toHaveBeenCalledWith({ voice: 'bm_george' }));
        await waitFor(() => expect((screen.getByLabelText('Voice') as HTMLSelectElement).value).toBe('bm_george'));

        fireEvent.change(screen.getByLabelText('Speed'), { target: { value: '150' } });
        await waitFor(() => expect(mockedApi.updateSpeechSettings).toHaveBeenCalledWith({ speed: 150 }));

        fireEvent.click(screen.getByRole('checkbox', { name: 'Read each reply aloud when it finishes' }));
        await waitFor(() => expect(mockedApi.updateSpeechSettings).toHaveBeenCalledWith({ auto: true }));
        await waitFor(() => expect(useReadAloudStore.getState().settings?.auto).toBe(true));
    });

    it('says why it could not save, in the pane', async () => {
        mockedApi.updateSpeechSettings.mockRejectedValue(
            new api.ApiError('friendly', 500, 'config.json is not valid JSON', null),
        );
        render(<ReadAloudSettings />);
        fireEvent.change(await screen.findByLabelText('Speed'), { target: { value: '75' } });
        expect(await screen.findByRole('alert')).toHaveProperty(
            'textContent',
            "Couldn't save the read-aloud settings: config.json is not valid JSON",
        );
    });

    it('says why it could not load instead of showing empty controls', async () => {
        mockedApi.getSpeechSettings.mockRejectedValue(new api.ApiError('friendly', 503, 'server down', null));
        render(<ReadAloudSettings />);
        expect((await screen.findByRole('alert')).textContent).toContain('server down');
        expect(screen.queryByLabelText('Voice')).toBeNull();
    });
});
