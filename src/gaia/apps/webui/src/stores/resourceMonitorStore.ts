// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Whether chats show the resource usage strip. On until someone closes it. */

import { create } from 'zustand';
import { log } from '../utils/logger';

const VISIBLE_KEY = 'gaia.resourceMonitor.visible';

function readVisible(): boolean {
    try {
        return window.localStorage.getItem(VISIBLE_KEY) !== '0';
    } catch (err) {
        log.ui.warn('Resource usage setting unavailable; showing it', err);
        return true;
    }
}

interface ResourceMonitorState {
    visible: boolean;
    setVisible: (visible: boolean) => void;
    /** Re-read the remembered setting (tests, and a second window's changes). */
    reload: () => void;
}

export const useResourceMonitorStore = create<ResourceMonitorState>((set) => ({
    visible: readVisible(),
    setVisible: (visible) => {
        try {
            window.localStorage.setItem(VISIBLE_KEY, visible ? '1' : '0');
        } catch (err) {
            log.ui.warn('Could not remember the resource usage setting', err);
        }
        set({ visible });
    },
    reload: () => set({ visible: readVisible() }),
}));
