// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { IDLE_TURN, type ChatTurn, type PermissionRequest } from '@/state/chatStreams';

import { alertsBetween } from './chatAlerts';

const running: ChatTurn = { ...IDLE_TURN, status: 'streaming' };
const asking: ChatTurn = { ...running, pendingPermission: { type: 'permission_request', tool: 'run_python', confirm_id: 'c1' } as PermissionRequest };

describe('alertsBetween', () => {
    it('reports an approval when a turn starts waiting, and how a turn ended when it leaves', () => {
        expect(alertsBetween({ a: running }, { a: asking })).toEqual([{ sessionId: 'a', event: 'needs_approval' }]);
        expect(alertsBetween({ a: asking }, { a: asking })).toEqual([]);
        expect(alertsBetween({ a: running }, {})).toEqual([{ sessionId: 'a', event: 'finished', outcome: 'done' }]);
        expect(alertsBetween({ a: { ...running, failed: true } }, {})).toEqual([{ sessionId: 'a', event: 'finished', outcome: 'failed' }]);
        expect(alertsBetween({ a: { ...running, stopState: 'stopped' } }, { a: IDLE_TURN })).toEqual([{ sessionId: 'a', event: 'finished', outcome: 'stopped' }]);
    });
});
