// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { SetupModelProgress, SetupRunStatus } from '@webui/types';

import { translator } from '@/i18n/useT';

import { chipView, downloadView, milestones } from './downloadState';

const GB = 1e9;
const model = (id: string, state: SetupModelProgress['state'], over: Partial<SetupModelProgress> = {}): SetupModelProgress => ({
    id,
    roles: ['chat'],
    state,
    bytes_done: 0,
    bytes_total: 4 * GB,
    speed_bps: 0,
    eta_s: null,
    error: null,
    error_kind: null,
    can_retry: false,
    retrying_until: null,
    downloaded_before_gb: null,
    ...over,
});
const run = (state: SetupRunStatus['state'], models: SetupModelProgress[], over: Partial<SetupRunStatus> = {}): SetupRunStatus => ({
    state,
    models,
    overall: { bytes_done: 0, bytes_total: 0, speed_bps: 0, eta_s: null, percent: 37, failed: 0 },
    ...over,
});

describe('downloadView', () => {
    it('orders what is moving first, then failures, and hides skipped rows', () => {
        const view = downloadView(run('running', [model('q', 'queued'), model('f', 'failed'), model('d', 'downloading'), model('s', 'skipped')]), undefined);
        expect(view?.rows.map((m) => m.id)).toEqual(['d', 'f', 'q']);
        expect(view?.active).toBe(true);
        expect(view?.retryLater).toBe(true);
    });

    it('offers Finish setup only when every row is kept and the run stopped before its check', () => {
        const done = downloadView(run('failed', [model('a', 'ready'), model('b', 'unchecked')]), undefined);
        expect(done?.downloadDone).toBe(true);
        const broken = downloadView(run('failed', [model('a', 'ready'), model('b', 'failed')]), undefined);
        expect(broken?.downloadDone).toBe(false);
        expect(broken?.runFailed).toBe(true);
    });

    it('leaves a failed piece to the contents list, and ignores a server-only run', () => {
        expect(downloadView(run('failed', [], { components: { server: 'failed' } }), undefined)?.componentFailed).toBe(true);
        expect(downloadView(run('running', [], { server_only: true }), undefined)).toBeNull();
    });
});

describe('chipView', () => {
    it('counts only models still coming and never shows a finished run', () => {
        const moving = chipView(run('running', [model('a', 'ready'), model('b', 'downloading'), model('c', 'failed')]));
        expect(moving).toEqual({ kind: 'moving', done: 2, count: 2, percent: 37 });
        expect(chipView(run('ready', [model('a', 'ready')]))).toBeNull();
    });

    it('says a download failed once the run has stopped', () => {
        expect(chipView(run('failed', [model('a', 'failed')]))).toEqual({ kind: 'failed' });
        expect(chipView(run('running', [model('a', 'failed')]))).toMatchObject({ kind: 'moving' });
    });
});

describe('milestones', () => {
    it('speaks a state change, a failure and each quarter, never every percent', () => {
        const t = translator('en');
        const before = { states: new Map([['a', 'downloading' as const], ['b', 'downloading' as const]]), quarter: 0 };
        const out = milestones(before, [model('a', 'ready'), model('b', 'failed')], 30, { a: 'Alpha', b: 'Beta' }, t);
        expect(out.said).toEqual(['Alpha: Ready', 'Downloads 25 percent done.']);
        expect(out.failed).toEqual(['Beta failed to download.']);
    });
});
