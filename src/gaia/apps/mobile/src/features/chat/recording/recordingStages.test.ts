// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { StreamEvent } from '@/api/client.types';

import { recordingFromEvents } from './recordingStages';

const stage = (n: number, state: number, extra: Partial<StreamEvent> = {}): StreamEvent => ({ type: 'status', phase: 'recording', stage: n, state, ...extra });

describe('recordingFromEvents', () => {
    it('is null without recording events', () => {
        expect(recordingFromEvents([{ type: 'status', phase: 'reasoning' }])).toBeNull();
    });

    it('marks earlier stages done, keeps skipped ones, and shows Download and Analyze only once reached', () => {
        const view = recordingFromEvents([stage(2, 1), stage(2, 2), stage(3, 0, { percent: 40, eta_seconds: 90 })]);
        expect(view?.stages.map((s) => [s.name, s.state])).toEqual([
            ['decode', 'done'],
            ['transcribe', 'skipped'],
            ['voices', 'running'],
            ['prepare', 'waiting'],
        ]);
        expect(view?.stages[2]).toMatchObject({ percent: 40, etaSeconds: 90 });
        expect(recordingFromEvents([stage(0, 0), stage(5, 0)])?.stages.map((s) => s.name)).toContain('analyze');
    });

    it('drops items an earlier section already showed near a seam', () => {
        const found = (section: number, seconds: number): StreamEvent => ({
            type: 'status', phase: 'recording_found', section, sections: 2, start: '0:00', end: '5:00', summary: 's',
            items: [{ title: 'Budget', start: '1:00', seconds }],
        });
        const view = recordingFromEvents([found(1, 300), found(2, 330)]);
        expect(view?.found.map((s) => s.items.length)).toEqual([1, 0]);
        expect(view?.total).toBe(2);
    });
});
