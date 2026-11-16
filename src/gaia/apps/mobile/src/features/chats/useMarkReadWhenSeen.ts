// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect } from 'react';
import { AppState } from 'react-native';

/** A reply that lands while the phone shows the chat is already read; a backgrounded app waits for the foreground. */
export function useMarkReadWhenSeen(openId: string | null | undefined, unread: boolean | undefined, markRead: (id: string) => void) {
    useEffect(() => {
        if (!openId || !unread) return;
        if (AppState.currentState === 'active') {
            markRead(openId);
            return;
        }
        const sub = AppState.addEventListener('change', (state) => {
            if (state !== 'active') return;
            sub.remove();
            markRead(openId);
        });
        return () => sub.remove();
    }, [openId, unread, markRead]);
}
