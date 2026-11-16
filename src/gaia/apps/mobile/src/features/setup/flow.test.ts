// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { SetupPlan, SetupRunStatus } from '@/api/client.types';

import { liveStageOf, navTargets, offersResume, railSteps, runWasLost, type SetupStage } from './flow';

const visited = (...ids: SetupStage[]) => new Set<SetupStage>(ids);
const run = (r: Partial<SetupRunStatus>): SetupRunStatus => ({ state: 'idle', ...r });

describe('liveStageOf', () => {
    it('follows the phase until a download starts', () => {
        expect(liveStageOf('check', undefined)).toBe('check');
        expect(liveStageOf('plan', run({}))).toBe('plan');
    });

    it('moves from download to test once the models are in and verifying, then to ready', () => {
        const pulling = run({ state: 'running', models: [{ id: 'm', state: 'downloading' } as never] });
        expect(liveStageOf('download', pulling)).toBe('download');
        expect(liveStageOf('download', run({ state: 'verifying' }))).toBe('test');
        expect(liveStageOf('download', run({ state: 'ready' }))).toBe('ready');
    });

    it('ignores a run that only started the model server', () => {
        expect(liveStageOf('plan', run({ state: 'ready', server_only: true }))).toBe('plan');
    });
});

describe('rail and navigation', () => {
    it('marks earlier steps done, the live one current, later ones pending and unreachable', () => {
        const steps = railSteps({ live: 'plan', shown: 'plan', running: false, failed: false, visited: visited('check', 'plan') });
        expect(steps.map((s) => s.status)).toEqual(['done', 'current', 'pending', 'pending', 'pending']);
        expect(steps.map((s) => s.reachable)).toEqual([true, true, false, false, false]);
    });

    it('shows a running download as active and a failed one as failed', () => {
        const base = { live: 'download', shown: 'download', visited: visited('check', 'plan') } as const;
        expect(railSteps({ ...base, running: true, failed: false })[2]!.status).toBe('active');
        expect(railSteps({ ...base, running: false, failed: true })[2]!.status).toBe('failed');
    });

    it('disables previous at the first step and next past where setup is', () => {
        const first = railSteps({ live: 'plan', shown: 'check', running: false, failed: false, visited: visited('check', 'plan') });
        expect(navTargets(first)).toEqual({ prev: null, next: 'plan' });
        const live = railSteps({ live: 'plan', shown: 'plan', running: false, failed: false, visited: visited('check', 'plan') });
        expect(navTargets(live)).toEqual({ prev: 'check', next: null });
    });
});

describe('resume and interruption', () => {
    const plan = { catalog: [{ partial: true, selected: true }] } as unknown as SetupPlan;

    it('offers a resume for a selected partial download when no run exists', () => {
        expect(offersResume(plan, undefined)).toBe(true);
        expect(offersResume(plan, run({}))).toBe(true);
        expect(offersResume(plan, run({ state: 'running' }))).toBe(false);
        expect(offersResume({ catalog: [{ partial: true, selected: false }] } as unknown as SetupPlan, undefined)).toBe(false);
    });

    it('sees a run lost to a restart only when it had models and is now empty', () => {
        const had = run({ state: 'running', models: [{ id: 'm' } as never] });
        expect(runWasLost(had, run({}))).toBe(true);
        expect(runWasLost(undefined, run({}))).toBe(false);
        expect(runWasLost(had, run({ state: 'running' }))).toBe(false);
    });
});
