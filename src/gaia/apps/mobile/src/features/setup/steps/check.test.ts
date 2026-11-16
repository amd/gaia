// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { LemonadeFound, LemonadeTarget, PreflightReport, SetupPlan } from '@/api/client.types';
import { translator } from '@/i18n/useT';

import { offerSettled, offerState, type OfferInputs } from './offerState';
import { checkLede, checkRows, findingWarnings } from './checkRows';

const t = translator('en');

const hardware = (h: Partial<PreflightReport> = {}): PreflightReport =>
    ({ gpu_name: 'Radeon 8060S', npu_detected: true, disk_free_gb: 412.4, ram_gb: 64, blockers: [], warnings: [], lemonade_running: true, ...h }) as PreflightReport;

const plan = (p: Record<string, unknown> = {}): SetupPlan =>
    ({
        lemonade: { mode: 'gaia', url: null, reachable: true },
        capacity_gb: 60,
        system: { lemonade_version: '10.8' },
        catalog: [{ id: 'a', downloaded: true }, { id: 'b', downloaded: false }],
        ...p,
    }) as unknown as SetupPlan;

const states = (rows: ReturnType<typeof checkRows>) => rows.map((r) => r.state);

describe('checkRows', () => {
    it('is all done once the PC and its server are read', () => {
        const rows = checkRows({ hardware: hardware(), hardwareError: null, plan: plan(), serverRun: null, awaitingChoice: false }, t);
        expect(states(rows)).toEqual(['done', 'done', 'done', 'done', 'done']);
        expect(rows[3]!.value).toBe("Lemonade 10.8 · GAIA's own");
        expect(rows[4]!.value).toBe('2 available, 1 already here');
    });

    it('works on the hardware while the server has not answered', () => {
        const rows = checkRows({ hardware: null, hardwareError: null, plan: plan({ lemonade: { mode: 'gaia', url: null, reachable: false }, capacity_gb: null }), serverRun: null, awaitingChoice: false }, t);
        expect(states(rows)).toEqual(['active', 'active', 'active', 'waiting', 'waiting']);
    });

    it('fails the hardware rows with the reason on the first, and runs on', () => {
        const rows = checkRows({ hardware: null, hardwareError: 'boom', plan: null, serverRun: null, awaitingChoice: false }, t);
        expect(states(rows).slice(0, 3)).toEqual(['failed', 'failed', 'failed']);
        expect(rows[0]!.value).toBe("Your PC's hardware couldn't be read. Setup still checks it as it goes. boom");
    });

    it('asks, not spins, while the PC chooses a server', () => {
        const rows = checkRows({ hardware: hardware(), hardwareError: null, plan: plan({ lemonade: { mode: 'gaia', url: null, reachable: false }, capacity_gb: null }), serverRun: null, awaitingChoice: true }, t);
        expect(rows[1]!.state).toBe('waiting');
        expect(rows[3]).toMatchObject({ state: 'question', value: 'Choose below' });
    });

    it('fails the server and memory rows when the server install stops', () => {
        const rows = checkRows(
            { hardware: hardware(), hardwareError: null, plan: plan({ lemonade: { mode: 'gaia', url: null, reachable: false }, capacity_gb: null }), serverRun: { state: 'failed', server_only: true }, awaitingChoice: false },
            t,
        );
        expect(rows[1]!.state).toBe('failed');
        expect(rows[3]!.state).toBe('failed');
    });
});

describe('checkLede', () => {
    const p = plan();
    it('names what is already on the PC', () => {
        expect(checkLede(false, p, new Set())).toBe('setup.check.lede');
        expect(checkLede(true, p, new Set())).toBe('setup.check.ledeOthersOnPc');
        expect(checkLede(true, p, new Set(['a']))).toBe('setup.check.ledeAllOnPc');
        expect(checkLede(true, p, new Set(['a', 'b']))).toBe('setup.check.ledeSomeOnPc');
        expect(checkLede(true, plan({ catalog: [{ id: 'b', downloaded: false }] }), new Set(['b']))).toBe('setup.check.ledeDone');
    });
});

describe('findingWarnings', () => {
    it('lists blockers, and holds the NPU warning back until the server runs', () => {
        const h = hardware({ blockers: ['Not enough disk'], warnings: ['NPU driver is old', 'Low memory'], lemonade_running: false });
        expect(findingWarnings(h)).toEqual(['Not enough disk', 'Low memory']);
        expect(findingWarnings({ ...h, lemonade_running: true })).toEqual(['Not enough disk', 'NPU driver is old', 'Low memory']);
        expect(findingWarnings(null)).toEqual([]);
    });
});

describe('offerState', () => {
    const target = (o: Partial<LemonadeTarget> = {}) => ({ offer: true, mode: 'gaia', base_url: null, ...o }) as LemonadeTarget;
    const found = (status: LemonadeFound['status']) => ({ url: 'http://pc:13305', status, version: '10.8', minimum: '10.7', message: '' }) as LemonadeFound;
    const base: OfferInputs = { target: target(), targetError: null, detect: undefined, detectError: null, wasOffered: false };

    it('looks until both answers are in', () => {
        expect(offerState({ ...base, target: undefined }).kind).toBe('looking');
        expect(offerState(base).kind).toBe('looking');
    });

    it('has nothing to offer when the PC already chose, or when nothing runs', () => {
        expect(offerState({ ...base, target: target({ offer: false }) }).kind).toBe('none');
        expect(offerState({ ...base, detect: { found: null } }).kind).toBe('none');
    });

    it('shows a found server for the PC to choose, and leaves a too-old one alone', () => {
        expect(offerState({ ...base, detect: { found: found('needs_key') } })).toMatchObject({ kind: 'offer', needsKey: true });
        const old = offerState({ ...base, detect: { found: found('too_old') } });
        expect(old.kind).toBe('tooOld');
        expect(offerSettled(old)).toBe(true);
    });

    it('says what the PC chose once the offer is gone', () => {
        expect(offerState({ ...base, wasOffered: true, target: target({ offer: false, mode: 'external', base_url: 'http://pc:13305' }) })).toEqual({
            kind: 'chosen',
            mode: 'external',
            url: 'http://pc:13305',
        });
    });

    it('does not guess when a check fails or a server stays silent', () => {
        const err = new Error('refused');
        expect(offerState({ ...base, targetError: err })).toEqual({ kind: 'error', error: err });
        expect(offerState({ ...base, detectError: err })).toEqual({ kind: 'error', error: err });
        const silent = offerState({ ...base, detect: { found: null, unchecked: ['http://pc:13305'] } });
        expect(silent).toEqual({ kind: 'noAnswer', urls: ['http://pc:13305'] });
        expect(offerSettled(silent)).toBe(false);
    });
});
