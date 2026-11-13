// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Whether the one-time "what this card can do" tip is still due. Dismissing it,
 * or using a control it points at, hides it on every card at once and for good.
 */

import { useSyncExternalStore } from 'react';

const KEY = 'gaia:guide-tip-dismissed';
const EVENT = 'gaia:guide-tip-dismissed';

function read(): boolean {
    try {
        return localStorage.getItem(KEY) === '1';
    } catch (err) {
        console.warn('[guide] could not read whether the card tip was dismissed; showing it', err);
        return false;
    }
}

function subscribe(onChange: () => void): () => void {
    window.addEventListener(EVENT, onChange);
    window.addEventListener('storage', onChange);
    return () => {
        window.removeEventListener(EVENT, onChange);
        window.removeEventListener('storage', onChange);
    };
}

export function dismissGuideTip(): void {
    if (read()) return;
    try {
        localStorage.setItem(KEY, '1');
    } catch (err) {
        console.warn('[guide] could not save that the card tip was dismissed; it may show again', err);
    }
    window.dispatchEvent(new Event(EVENT));
}

export function useGuideTipDismissed(): boolean {
    return useSyncExternalStore(subscribe, read, () => true);
}
