// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as speechApi from '../../services/speechApi';
import { useReadAloudStore } from '../../stores/readAloudStore';
import { useReadAloudSettings } from '../useReadAloudSettings';

vi.mock('../../services/speechApi', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../services/speechApi')>();
    return { ...actual, getSpeechSettings: vi.fn() };
});

const getSpeechSettings = vi.mocked(speechApi.getSpeechSettings);
const settings = (auto: boolean): speechApi.SpeechSettings => ({
    voice: 'af_bella', speed: 100, auto, min_speed: 50, max_speed: 200, voices: [],
});

beforeEach(() => {
    getSpeechSettings.mockReset();
    useReadAloudStore.setState({ settings: null });
});

describe('useReadAloudSettings', () => {
    it('loads the settings, and again when the window comes back, so a change in the TUI applies', async () => {
        getSpeechSettings.mockResolvedValueOnce(settings(false)).mockResolvedValueOnce(settings(true));
        const { unmount } = renderHook(() => useReadAloudSettings());
        await waitFor(() => expect(useReadAloudStore.getState().settings?.auto).toBe(false));
        window.dispatchEvent(new Event('focus'));
        await waitFor(() => expect(useReadAloudStore.getState().settings?.auto).toBe(true));
        unmount();
        window.dispatchEvent(new Event('focus'));
        expect(getSpeechSettings).toHaveBeenCalledTimes(2);
    });
});
