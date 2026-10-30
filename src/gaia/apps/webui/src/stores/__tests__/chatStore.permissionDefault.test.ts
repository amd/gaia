// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The new-chat permission mode: the saved default, unless the user picked one.

import { beforeEach, describe, expect, it } from 'vitest';
import { useChatStore } from '../chatStore';

beforeEach(() => {
    useChatStore.setState({
        draftPermissionMode: 'ask', draftPermissionModeChosen: false,
        defaultPermissionMode: 'ask', defaultPermissionModeKnown: false,
    });
});

describe('draft permission mode', () => {
    it('an untouched draft follows the default when it arrives', () => {
        useChatStore.getState().setDefaultPermissionMode('full_access');
        const s = useChatStore.getState();
        expect(s.draftPermissionMode).toBe('full_access');
        expect(s.defaultPermissionModeKnown).toBe(true);
    });

    it('a pick the user made stays, even one equal to the old guess', () => {
        useChatStore.getState().setDraftPermissionMode('ask');
        useChatStore.getState().setDefaultPermissionMode('full_access');
        expect(useChatStore.getState().draftPermissionMode).toBe('ask');
    });

    it('after a chat starts, the next draft is the default again and follows it', () => {
        const store = useChatStore.getState();
        store.setDefaultPermissionMode('full_access');
        store.setDraftPermissionMode('ask');
        store.resetDraftPermissionMode();
        expect(useChatStore.getState().draftPermissionMode).toBe('full_access');
        useChatStore.getState().setDefaultPermissionMode('ask');
        expect(useChatStore.getState().draftPermissionMode).toBe('ask');
    });
});
