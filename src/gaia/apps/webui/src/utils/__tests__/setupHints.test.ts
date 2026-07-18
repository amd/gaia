// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect } from 'vitest';
import { isSystemReady, shouldShowFirstRunTip, shouldShowNoModelTip } from '../setupHints';
import type { SystemStatus } from '../../types';

function status(overrides: Partial<SystemStatus> = {}): SystemStatus {
    return {
        lemonade_running: true,
        model_loaded: 'Gemma-4-E4B-it-GGUF',
        embedding_model_loaded: true,
        disk_space_gb: 100,
        memory_available_gb: 16,
        initialized: true,
        version: '1.0.0',
        lemonade_version: '1.0.0',
        model_size_gb: 4,
        model_device: 'gpu',
        model_context_size: 32768,
        model_labels: null,
        gpu_name: null,
        gpu_vram_gb: null,
        tokens_per_second: null,
        time_to_first_token: null,
        processor_name: null,
        device_supported: true,
        context_size_sufficient: true,
        model_downloaded: true,
        default_model_name: 'Gemma-4-E4B-it-GGUF',
        default_model_size_gb: 4,
        lemonade_url: 'http://localhost:8000',
        expected_model_loaded: true,
        download_progress: null,
        ...overrides,
    };
}

describe('setupHints', () => {
    it('treats null status (backend unreachable) as not-ready, no tips', () => {
        expect(isSystemReady(null)).toBe(false);
        expect(shouldShowFirstRunTip(null)).toBe(false);
        expect(shouldShowNoModelTip(null)).toBe(false);
    });

    it('reports ready when Lemonade is up and a model is loaded', () => {
        expect(isSystemReady(status())).toBe(true);
    });

    it('SUPPRESSES the first-run tip when ready even if the marker is missing (#2119)', () => {
        // The core bug: initialized=false (marker absent) but the probe shows a
        // working system → the tip must NOT show.
        expect(shouldShowFirstRunTip(status({ initialized: false }))).toBe(false);
    });

    it('shows the first-run tip when not initialized and no model is loaded', () => {
        expect(
            shouldShowFirstRunTip(status({ initialized: false, model_loaded: null }))
        ).toBe(true);
    });

    it('shows the no-model tip when Lemonade is up, model missing, but initialized', () => {
        expect(shouldShowNoModelTip(status({ model_loaded: null }))).toBe(true);
    });

    it('does not show the no-model tip when the first-run tip is showing', () => {
        const s = status({ initialized: false, model_loaded: null });
        expect(shouldShowFirstRunTip(s)).toBe(true);
        expect(shouldShowNoModelTip(s)).toBe(false);
    });

    it('shows no tips on a fully-ready, initialized system', () => {
        expect(shouldShowFirstRunTip(status())).toBe(false);
        expect(shouldShowNoModelTip(status())).toBe(false);
    });
});
