// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { TurnSummary } from '@webui/types';

import { liveDataFailed, turnFacts } from './turnSummary';

const LOCAL: TurnSummary = { cost: { kind: 'local' }, model: { name: 'Gemma-4-E4B-it-GGUF', provider: 'local', provider_name: 'Local', remote: false } };

describe('where a turn sent its data', () => {
    it('keeps documents on the PC for a local model', () => {
        const facts = turnFacts({ ...LOCAL, documents: [{ name: 'q3.pdf', path: '/q3.pdf', passages: 2 }] }, false);
        expect(facts.model.key).toBe('rail.thisPc');
        expect(facts.documents?.key).toBe('rail.docsLocal');
    });

    it('names the provider that got the passages from a cloud model', () => {
        const cloud: TurnSummary = {
            cost: { kind: 'estimate', usd: 0.0016 },
            model: { name: 'claude-sonnet', provider: 'claude', provider_name: 'Claude', remote: true },
            documents: [{ name: 'q3.pdf', path: '/q3.pdf' }],
        };
        const facts = turnFacts(cloud, false);
        expect(facts.documents).toEqual({ key: 'rail.docsSent', params: { provider: 'Claude' } });
        expect(facts.cost).toEqual({ key: 'rail.costAbout', params: { usd: '$0.0016' } });
    });

    it('says a stopped turn reported no cost, and flags only a fully failed live lookup', () => {
        expect(turnFacts({ cost: { kind: 'unreported' } }, true).cost.key).toBe('rail.costUnreportedStopped');
        expect(liveDataFailed({ ...LOCAL, live_data: { attempted: 2, failed: 2 } })).toBe(true);
        expect(liveDataFailed({ ...LOCAL, live_data: { attempted: 2, failed: 1 } })).toBe(false);
    });
});
