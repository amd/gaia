// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaApiError } from '@/api/errors';
import type { Translate } from '@/i18n/useT';

import { createConfirmBroker } from './ConfirmSheet';
import { logsByStage } from './logLines';
import { lowDiskRequest, noFitRequest, serverConfirmRequest, setupConfirmFrom, type ConfirmRequest } from './setupConfirm';

const t = ((key: string, params?: Record<string, string>) => `${key}${params ? JSON.stringify(params) : ''}`) as unknown as Translate;
const request: ConfirmRequest = { title: 'T', body: [], confirmLabel: 'Yes', cancelLabel: 'No' };

describe('confirm broker', () => {
    it('hands the confirm to the caller that asked', async () => {
        const seen: unknown[] = [];
        const broker = createConfirmBroker((p) => seen.push(p));
        const answer = broker.ask(request);
        broker.settle(true);
        await expect(answer).resolves.toBe(true);
        expect(seen).toHaveLength(2);
        expect(seen[1]).toBeNull();
    });

    it('answers no on cancel and when a newer question replaces the open one', async () => {
        const broker = createConfirmBroker(() => undefined);
        const first = broker.ask(request);
        const second = broker.ask(request);
        await expect(first).resolves.toBe(false);
        broker.settle(false);
        await expect(second).resolves.toBe(false);
    });
});

describe('setupConfirmFrom', () => {
    const conflict = (body: Record<string, unknown> | null, status = 409) =>
        new GaiaApiError(status, 'x', undefined, undefined, body);

    it('reads a confirmable 409 and ignores every other error', () => {
        const body = { code: 'low_disk', message: 'm', acknowledge: 'acknowledge_low_disk' };
        expect(setupConfirmFrom(conflict(body))).toEqual(body);
        expect(setupConfirmFrom(conflict(body, 500))).toBeNull();
        expect(setupConfirmFrom(conflict({ code: 'busy', message: 'm' }))).toBeNull();
        expect(setupConfirmFrom(conflict(null))).toBeNull();
        expect(setupConfirmFrom(new Error('x'))).toBeNull();
    });
});

describe('confirm wording', () => {
    it('low disk carries the three sizes, kept left to right', () => {
        const r = lowDiskRequest({ needGb: 23.3, freeGb: 10, freeUpGb: 13.3 }, t);
        expect(r.title).toBe('setup.confirm.lowDiskTitle');
        expect(r.body[0]).toContain('23.3 GB');
        expect(r.body[0]).toContain('10.0 GB');
        expect(r.body[0]).toContain('13.3 GB');
        expect(r.cancelLabel).toBe('setup.confirm.changeModels');
    });

    it('may-not-fit names the model for one and lists each for many', () => {
        expect(noFitRequest([{ name: 'Big', why: 'w' }], t).title).toContain('"name":"Big"');
        const many = noFitRequest([{ name: 'A', why: 'a' }, { name: 'B', why: 'b' }], t);
        expect(many.title).toBe('setup.confirm.noFitMany');
        expect(many.body).toEqual(['A: a', 'B: b']);
    });

    it('a server no-fit says "use it anyway" with the server message', () => {
        const r = serverConfirmRequest({ code: 'no_fit', message: 'msg', acknowledge: 'acknowledge_no_fit' }, t);
        expect(r.confirmLabel).toBe('setup.confirm.useAnyway');
        expect(r.body).toEqual(['msg']);
    });
});

describe('logsByStage', () => {
    it('lists only stages with lines, the screen notes first, redrawn bars collapsed', () => {
        const run = { state: 'running', logs: { download: ['a 10%', 'a 20%', 'done'] } } as never;
        const out = logsByStage(run, { check: ['noted'] });
        expect(out).toEqual([
            { stage: 'check', lines: ['noted'] },
            { stage: 'download', lines: ['a 20%', 'done'] },
        ]);
    });
});
