// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, type MutableRefObject, type RefObject } from 'react';
import { scrollBehavior } from '../utils/motion';

/** Bring a turn into view from the session panel; the callback returns false when it isn't loaded. */
export function useJumpToMessage(
    scrollRef: RefObject<HTMLElement | null>,
    nearBottomRef: MutableRefObject<boolean>,
): (messageId: number) => boolean {
    return useCallback((messageId: number): boolean => {
        const answer = scrollRef.current?.querySelector<HTMLElement>(`[data-message-id="${messageId}"]`);
        if (!answer) return false;
        const asked = answer.previousElementSibling as HTMLElement | null;
        const target = asked?.dataset.role === 'user' ? asked : answer;
        // Stop auto-follow so the jump isn't undone by the next streamed token.
        nearBottomRef.current = false;
        target.scrollIntoView?.({ block: 'start', behavior: scrollBehavior() });
        answer.classList.add('msg-jump-target');
        window.setTimeout(() => answer.classList.remove('msg-jump-target'), 1600);
        return true;
    }, [scrollRef, nearBottomRef]);
}
