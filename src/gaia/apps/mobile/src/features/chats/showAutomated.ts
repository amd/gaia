// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import AsyncStorage from '@react-native-async-storage/async-storage';
import { useCallback, useEffect, useState } from 'react';

// The web UI's key, so the choice means the same on both.
const SHOW_AUTOMATED_KEY = 'gaia-show-automated-runs';

/** Whether eval and scheduled chats show in the list; hidden until saved otherwise. */
export function useShowAutomated(): [boolean, () => void] {
    const [on, setOn] = useState(false);
    useEffect(() => {
        let live = true;
        AsyncStorage.getItem(SHOW_AUTOMATED_KEY).then(
            (v) => live && setOn(v === '1'),
            (err) => console.warn('Chat-list filter unreadable; hiding eval and scheduled runs.', err),
        );
        return () => {
            live = false;
        };
    }, []);
    const toggle = useCallback(() => {
        setOn((was) => {
            AsyncStorage.setItem(SHOW_AUTOMATED_KEY, was ? '0' : '1').catch((err) =>
                console.warn('Chat-list filter not saved; it will reset on restart.', err),
            );
            return !was;
        });
    }, []);
    return [on, toggle];
}
