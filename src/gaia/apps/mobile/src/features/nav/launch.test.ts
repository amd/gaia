// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { chatToRestore } from './launch';

const sessions = [{ id: 'aaaaaaaa-1111' }, { id: 'bbbbbbbb-2222' }];
const launch = { lastChatId: 'bbbbbbbb-2222', sessions, choseNewChat: false, pathname: '/' };

describe('launch restore', () => {
    it('reopens the last chat, but not after New chat, off Home, or for a deleted chat', () => {
        expect(chatToRestore(launch)).toBe('bbbbbbbb-2222');
        expect(chatToRestore({ ...launch, choseNewChat: true })).toBeNull();
        expect(chatToRestore({ ...launch, pathname: '/chat/aaaaaaaa-1111' })).toBeNull();
        expect(chatToRestore({ ...launch, lastChatId: 'gone' })).toBeNull();
    });
});
