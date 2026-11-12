// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ModelChecklist } from '../ModelChecklist';
import { initialSelection } from '../selection';
import { GEMMA, entry, plan } from './fixtures';

const QWEN_VL = entry({
    id: 'Qwen3-VL-4B-Instruct-GGUF', name: 'Qwen3 VL 4B', roles: ['vision'], size_gb: 3.3, full_size_gb: 3.3,
    chat_choice: false,
});

function renderSettings(catalog: Parameters<typeof plan>[0]) {
    const p = plan(catalog);
    render(
        <>
            <h2 id="h">Models</h2>
            <ModelChecklist plan={p} selection={initialSelection(p)} onToggle={vi.fn()} onDefaultChat={vi.fn()} labelledBy="h" place="settings" />
        </>,
    );
}

describe('image-reading caveat in Settings', () => {
    it('stays quiet about an unticked image model when another one is on this PC', () => {
        renderSettings([{ ...GEMMA, downloaded: true, size_gb: 0 }, QWEN_VL]);
        expect(screen.queryByText(/can’t read images or screenshots/)).not.toBeInTheDocument();
    });

    it('warns when no image-reading model is ticked or on this PC', () => {
        renderSettings([{ ...GEMMA, selected: false, planned: false, recommended: false, gaia_model: true }, QWEN_VL]);
        expect(screen.getAllByText(/can’t read images or screenshots/).length).toBeGreaterThan(0);
    });
});
