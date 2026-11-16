// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { SystemStatus } from '@/api/client.types';
import { STATUS_IDLE_POLL_MS, STATUS_POLL_MS } from '@/api/constants';

import { statusPollMs } from './queries';

describe('the status poll', () => {
    it('stays scheduled when the PC is healthy, so an idle app notices it going away', () => {
        const healthy = { lemonade_running: true } as SystemStatus;
        expect(statusPollMs(healthy, false)).toBe(STATUS_IDLE_POLL_MS);
        expect(statusPollMs(healthy, true)).toBe(STATUS_POLL_MS);
    });
});
