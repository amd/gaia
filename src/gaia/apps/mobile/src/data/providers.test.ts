// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ProviderSummary, ProviderSummaryList } from '@/api/client.types';

import { providerRows } from './providers';

const summary = (p: Partial<ProviderSummary> & Pick<ProviderSummary, 'id'>): ProviderSummary => ({
    name: p.id,
    remote: p.id !== 'local',
    privacy_notice: '',
    configured: p.id === 'local',
    blocked: false,
    ...p,
});

describe('providerRows', () => {
    it('keeps configured and blocked as the PC reported them, and the Lemonade error', () => {
        const list: ProviderSummaryList = {
            providers: [summary({ id: 'local' }), summary({ id: 'fireworks', configured: true, blocked: true }), summary({ id: 'amd' })],
            lemonade_error: 'Lemonade is not answering',
        };
        const { providers, lemonadeError } = providerRows(list);
        expect(providers.map((p) => [p.id, p.configured, p.blocked])).toEqual([
            ['local', true, false],
            ['fireworks', true, true],
            ['amd', false, false],
        ]);
        expect(lemonadeError).toBe('Lemonade is not answering');
    });

    it('turns an absent Lemonade error into null and drops an id the app has no row for', () => {
        const { providers, lemonadeError } = providerRows({ providers: [summary({ id: 'local' }), summary({ id: 'other' })] });
        expect(providers.map((p) => p.id)).toEqual(['local']);
        expect(lemonadeError).toBeNull();
    });
});
