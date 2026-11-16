// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Schedule } from '@webui/types';

import { createScheduleAnnouncer } from './scheduleAlerts';

const schedule = (id: string, error_count: number): Schedule => ({ id, name: id, error_count, last_error: error_count ? 'boom' : null }) as Schedule;

describe('schedule failure alerts', () => {
    it('raises a failure from while the app was closed once, then only newer ones', async () => {
        const raised: string[] = [];
        const save = jest.fn(async () => undefined);
        const announce = createScheduleAnnouncer((s) => raised.push(`${s.id}:${s.error_count}`), { load: async () => ({ a: 1, b: 0 }), save });

        await announce([schedule('a', 1), schedule('b', 2)]);
        expect(raised).toEqual(['b:2']);
        expect(save).toHaveBeenLastCalledWith({ a: 1, b: 2 });

        await announce([schedule('a', 1), schedule('b', 2)]);
        expect(raised).toEqual(['b:2']);

        await announce([schedule('a', 2), schedule('b', 2)]);
        expect(raised).toEqual(['b:2', 'a:2']);
    });
});
