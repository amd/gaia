// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { toolResultUpdates } from '../toolEvents';
import type { StreamEvent } from '../../types';

const result = (extra: Partial<StreamEvent>): StreamEvent =>
    ({ type: 'tool_result', summary: "Tool 'run_python' was denied by the user.", success: false, ...extra }) as StreamEvent;

describe('a declined call', () => {
    it('is marked declined when the user said no', () => {
        expect(toolResultUpdates(result({ status: 'denied' })).declined).toBe(true);
    });

    it('is marked declined when a relayed result nests the status in data', () => {
        expect(toolResultUpdates(result({ data: { status: 'denied' } })).declined).toBe(true);
    });

    it.each([
        ['on the event', { status: 'denied', timed_out: true }],
        ['in relayed data', { data: { status: 'denied', timed_out: true } }],
    ])('is not called declined when nobody answered (%s)', (_where, extra) => {
        expect(toolResultUpdates(result(extra as Partial<StreamEvent>)).declined).toBeUndefined();
    });

    it('leaves an ordinary failure alone', () => {
        expect(toolResultUpdates(result({ status: 'error' })).declined).toBeUndefined();
    });
});
