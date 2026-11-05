// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect } from 'react';
import * as api from '../services/api';
import { useReadAloudStore } from '../stores/readAloudStore';
import { log } from '../utils/logger';

/**
 * Keeps the read-aloud settings current for auto-read: loaded on mount, and
 * re-read when the window regains focus, since the TUI's `/voice` changes them
 * in the same config file.
 */
export function useReadAloudSettings(): void {
    useEffect(() => {
        let live = true;
        const load = async () => {
            try {
                const settings = await api.getSpeechSettings();
                if (live && settings) useReadAloudStore.getState().setSettings(settings);
            } catch (err) {
                log.ui.error('Could not load the read-aloud settings; replies will not be read automatically', err);
            }
        };
        void load();
        window.addEventListener('focus', load);
        return () => {
            live = false;
            window.removeEventListener('focus', load);
        };
    }, []);
}
