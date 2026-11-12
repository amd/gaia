// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A model's Details read as separate facts, with a context size people can take in (#7412). */

import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ModelRow } from '../ModelRow';
import type { ProviderModel } from '../../../types';

const MODEL = {
    id: 'accounts/fireworks/models/kimi',
    context_length: 1_048_576,
    downloaded: true,
    labels: [],
    rank: null,
    note: null,
    evidence: '14/14 tasks - quality 4.92/5',
} as unknown as ProviderModel;

function renderRow() {
    const noop = vi.fn();
    return render(
        <ul>
            <ModelRow
                model={MODEL}
                label="Kimi"
                current={false}
                remote
                busy={null}
                confirmingRemove={false}
                onUse={noop}
                onDownload={noop}
                onAskRemove={noop}
                onKeep={noop}
                onRemove={noop}
            />
        </ul>,
    );
}

describe('ModelRow details', () => {
    it('gives each fact its own line', () => {
        const { container } = renderRow();
        const facts = [...container.querySelectorAll('.settings-more > .model-row-meta')].map((el) => el.textContent);
        expect(facts).toEqual(['Context about 1M tokens', 'Measured: 14/14 tasks - quality 4.92/5']);
        // Separate block elements, so "tokens" and "Measured" never run together.
        expect(container.querySelector('.settings-more')?.classList.contains('model-row-facts')).toBe(true);
    });

    it('does not spell out a seven-digit token count', () => {
        renderRow();
        expect(screen.queryByText(/1,048,576/)).toBeNull();
    });
});
