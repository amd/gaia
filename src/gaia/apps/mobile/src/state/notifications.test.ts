// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { StreamEvent } from '@webui/types';

import { IDLE_TURN, type ChatTurn, type PermissionRequest } from './chatStreams';
import { clearNotifications, noteTurn, selectUnreadCount, useNotificationStore } from './notifications';

const ask = { type: 'permission_request', tool: 'run_python', confirm_id: 'c1' } as PermissionRequest;
const blocked = { type: 'policy_alert', tool: 'run_shell_command', receipt_id: 'r7', reason: 'no shell' } as StreamEvent;

beforeEach(clearNotifications);

describe('noteTurn', () => {
    it('files one approval per request and one policy alert per block', () => {
        const asking: ChatTurn = { ...IDLE_TURN, pendingPermission: ask };
        noteTurn('s1', asking, IDLE_TURN);
        noteTurn('s1', { ...asking, liveText: 'more' }, asking);
        const policy: ChatTurn = { ...IDLE_TURN, events: [blocked], policyAlert: { tool: 'run_shell_command', receiptId: 'r7', seq: 0 } };
        noteTurn('s1', policy, IDLE_TURN);
        const items = useNotificationStore.getState().items;
        expect(items.map((n) => [n.id, n.type, n.sessionId])).toEqual([
            ['r7', 'policy_alert', 's1'],
            ['c1', 'permission_request', 's1'],
        ]);
        expect(selectUnreadCount(useNotificationStore.getState())).toBe(2);
    });
});
