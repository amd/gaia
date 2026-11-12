// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupSettings } from '../SetupSettings';
import * as api from '../../../services/api';
import { useModelStore } from '../../../stores/modelStore';
import { useChatStore } from '../../../stores/chatStore';
import type { ActiveModel, SetupPlan } from '../../../types';

vi.mock('../../../services/api');
const mockedApi = vi.mocked(api);

const QWEN = 'Qwen3.6-35B-A3B-GGUF';
const plan = {
    chat_model: { id: QWEN, user_set: false, size_gb: 23.3, downloaded: false },
    lemonade: { mode: 'gaia', url: 'http://localhost:1/api/v1', reachable: true },
    catalog: [
        { id: QWEN, name: 'Qwen3.6 35B A3B', downloaded: false, full_size_gb: 23.3 },
        { id: 'user.embeddinggemma-300m-GGUF', name: 'EmbeddingGemma 300M', downloaded: true, full_size_gb: 0.3 },
    ],
} as unknown as SetupPlan;

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getSetupPlan.mockResolvedValue(plan);
    mockedApi.getOnboardingPreflight.mockResolvedValue({ gpu_name: 'Radeon', npu_detected: false, ram_gb: 64 } as never);
    useModelStore.setState({ active: null });
});

describe('SetupSettings', () => {
    it('names the cloud model in use, not the local model setup would download', async () => {
        useModelStore.setState({
            active: {
                provider: 'fireworks', model: 'fireworks.deepseek-v4p1-flash', display_name: 'deepseek-v4p1-flash',
                label: 'deepseek-v4p1-flash · Fireworks AI', remote: true, is_default: false,
            } as ActiveModel,
        });
        render(<SetupSettings />);
        expect(await screen.findByText('Running deepseek-v4p1-flash · Fireworks AI')).toBeTruthy();
        expect(screen.queryByText(/Qwen3\.6/)).toBeNull();
    });

    it('falls back to the planned model before the active model is known', async () => {
        render(<SetupSettings />);
        expect(await screen.findByText('Running Qwen3.6 35B A3B')).toBeTruthy();
    });

    it('sends each row to the setting it names, not the top of a tab', async () => {
        render(<SetupSettings />);
        fireEvent.click(await screen.findByRole('button', { name: /Context window/ }));
        expect(useChatStore.getState().settingsSection).toBe('model');
        expect(useChatStore.getState().settingsJump).toEqual({ section: 'model', target: '#ctx-size', from: 'setup' });
        fireEvent.click(screen.getByRole('button', { name: /GPU memory/ }));
        expect(useChatStore.getState().settingsJump?.target).toBe('#settings-gpu-title');
        expect(useChatStore.getState().settingsProvider).toBe('local');
    });

    it('counts only the models the plan says are on this PC', async () => {
        render(<SetupSettings />);
        expect(await screen.findByText(/Installed: 1,/)).toBeTruthy();
    });
});
