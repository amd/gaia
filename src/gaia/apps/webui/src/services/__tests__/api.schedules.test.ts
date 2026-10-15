// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * "Every day at 9am" means 9am where the user is, so every schedule request
 * carries the browser's IANA time zone and the card shows local clock time.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createSchedule, parseScheduleInput } from '../api';
import { formatNextRun } from '../../components/ScheduleManager';

function bodyOf(call: unknown[]): Record<string, unknown> {
    const init = call[1] as RequestInit;
    return JSON.parse(init.body as string);
}

describe('schedule requests carry the browser time zone', () => {
    beforeEach(() => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
            new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } }),
        ));
        vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({
            timeZone: 'America/Los_Angeles',
        } as Intl.ResolvedDateTimeFormatOptions);
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('sends it on create', async () => {
        await createSchedule('brief', 'every day at 9am', 'p');
        const call = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
        expect(bodyOf(call)).toMatchObject({
            interval: 'every day at 9am',
            timezone: 'America/Los_Angeles',
        });
    });

    it('sends it on the live preview parse', async () => {
        await parseScheduleInput('daily at 9am');
        const call = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
        expect(bodyOf(call)).toMatchObject({ timezone: 'America/Los_Angeles' });
    });
});

describe('formatNextRun', () => {
    const now = new Date('2026-10-15T09:16:00Z');

    it('shows the local clock time with the countdown', () => {
        const next = new Date('2026-10-15T16:00:00Z');
        const text = formatNextRun(next.toISOString(), now);
        const clock = next.toLocaleString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' });
        expect(text).toBe(`${clock} (in 6h)`);
    });

    it('falls back to the relative form once the time has passed', () => {
        expect(formatNextRun('2026-10-15T09:15:30Z', now)).toBe('just now');
    });

    it('says never when there is no next run', () => {
        expect(formatNextRun(null, now)).toBe('never');
    });
});
