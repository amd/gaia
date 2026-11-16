// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useRef, useState } from 'react';
import type { FlatList, NativeScrollEvent, NativeSyntheticEvent } from 'react-native';

/** Within this many points of the newest message the list keeps following it. */
export const FOLLOW_PX = 24;
/** The jump button shows once the reader is this far from the newest message (the web's 200). */
export const JUMP_PX = 200;

/**
 * For an inverted list, whose offset is the distance from the newest message.
 * Follows while the reader is at the bottom and lets go once they scroll up.
 */
export function useFollowScroll<T>() {
    const listRef = useRef<FlatList<T>>(null);
    const [following, setFollowing] = useState(true);
    const [showJump, setShowJump] = useState(false);

    const onScroll = useCallback((event: NativeSyntheticEvent<NativeScrollEvent>) => {
        const distance = event.nativeEvent.contentOffset.y;
        setFollowing(distance <= FOLLOW_PX);
        setShowJump(distance > JUMP_PX);
    }, []);

    const jumpToBottom = useCallback(() => {
        listRef.current?.scrollToOffset({ offset: 0, animated: true });
    }, []);

    return { listRef, following, showJump, onScroll, jumpToBottom };
}
