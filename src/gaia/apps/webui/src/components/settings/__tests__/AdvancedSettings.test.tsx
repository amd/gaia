// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AdvancedSettings } from '../AdvancedSettings';
import * as api from '../../../services/api';
import type { SystemStatus } from '../../../types';

vi.mock('../../../services/api');
vi.mock('../../../hooks/useModelActions', () => ({
    useModelActions: () => ({
        isLoadingModel: false, isDownloadingModel: false, loadModel: vi.fn(), downloadModel: vi.fn(),
    }),
}));

const mockedApi = vi.mocked(api);

function status(overrides: Partial<SystemStatus>): SystemStatus {
    return {
        lemonade_running: true, model_loaded: 'amd.o1-mini', embedding_model_loaded: true,
        disk_space_gb: 800, memory_available_gb: 30, initialized: true, version: '0.25.0',
        lemonade_version: '2026.40.0', model_size_gb: null, model_device: 'none',
        model_context_size: null, model_labels: ['cloud', 'chat'], gpu_name: null,
        gpu_vram_gb: null, tokens_per_second: null, time_to_first_token: null,
        processor_name: null, context_size_sufficient: true, model_downloaded: null,
        default_model_name: 'amd.DeepSeek-V4.1-Flash', default_model_size_gb: null,
        lemonade_url: 'http://localhost:13305', expected_model_loaded: false,
        download_progress: null, active_profile: 'gaia',
        ...overrides,
    } as SystemStatus;
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getMCPRuntimeStatus.mockResolvedValue({ servers: [] } as never);
    mockedApi.getLemonadeTarget.mockResolvedValue({
        mode: 'gaia', saved_mode: null, source: 'gaia', base_url: null, saved_base_url: null,
        api_key_stored: false, gaia_installed: true, offer: false, what_gaia_does: '',
    });
    mockedApi.getSettings.mockResolvedValue(
        { custom_model: null, dynamic_tools: false, dynamic_tools_locked: false } as never,
    );
});

describe('AdvancedSettings', () => {
    it('offers no local model actions for a cloud model', async () => {
        mockedApi.getSystemStatus.mockResolvedValue(status({ default_model_is_cloud: true }));
        render(<AdvancedSettings />);
        await screen.findByText('amd.o1-mini');

        expect(screen.queryByText('Wrong model loaded.')).not.toBeInTheDocument();
        expect(screen.queryByText('Force Re-download')).not.toBeInTheDocument();
        expect(screen.queryByText('Device')).not.toBeInTheDocument();
        expect(screen.queryByText('Context Size')).not.toBeInTheDocument();
        expect(screen.queryByText(/Expected:/)).not.toBeInTheDocument();
    });

    it('keeps them for a local model', async () => {
        mockedApi.getSystemStatus.mockResolvedValue(status({
            default_model_is_cloud: false, model_loaded: 'Qwen3-4B-GGUF', model_device: 'gpu',
            default_model_name: 'Gemma-4-E4B-it-GGUF', model_labels: null,
        }));
        render(<AdvancedSettings />);
        await screen.findByText('Wrong model loaded.');

        expect(screen.getByText('Force Re-download')).toBeInTheDocument();
        expect(screen.getByText('Device')).toBeInTheDocument();
        expect(screen.getByText('Context Size')).toBeInTheDocument();
    });
});
