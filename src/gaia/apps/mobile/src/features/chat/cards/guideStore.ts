// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import AsyncStorage from '@react-native-async-storage/async-storage';
import { useEffect, useSyncExternalStore } from 'react';

const ticksKey = (guideKey: string) => `gaia.guide.${guideKey}`;
const TIP_KEY = 'gaia.tips.guide';

/** Ticks stay on this phone only; they don't sync with the PC. */
export async function loadTicks(guideKey: string): Promise<Set<number>> {
    try {
        const raw = await AsyncStorage.getItem(ticksKey(guideKey));
        const parsed: unknown = raw ? JSON.parse(raw) : [];
        return new Set(Array.isArray(parsed) ? parsed.filter((n): n is number => Number.isInteger(n)) : []);
    } catch (err) {
        console.warn('[guide] could not read saved ticks; starting unticked', err);
        return new Set();
    }
}

export function saveTicks(guideKey: string, ticks: ReadonlySet<number>): void {
    const write = ticks.size ? AsyncStorage.setItem(ticksKey(guideKey), JSON.stringify([...ticks])) : AsyncStorage.removeItem(ticksKey(guideKey));
    write.catch((err: unknown) => console.warn('[guide] could not save ticks; they hold until the app closes', err));
}

// null until the saved choice is read, so the tip never flashes for someone who dismissed it.
let tipDismissed: boolean | null = null;
let tipLoading = false;
const listeners = new Set<() => void>();

function setTipDismissed(value: boolean): void {
    tipDismissed = value;
    listeners.forEach((fn) => fn());
}

function loadTip(): void {
    if (tipLoading) return;
    tipLoading = true;
    AsyncStorage.getItem(TIP_KEY).then(
        (saved) => setTipDismissed(saved === '1'),
        (err: unknown) => {
            console.warn('[guide] could not read whether the card tip was dismissed; showing it', err);
            setTipDismissed(false);
        },
    );
}

/** Hides the tip on every card at once and for good. */
export function dismissGuideTip(): void {
    if (tipDismissed === true) return;
    setTipDismissed(true);
    AsyncStorage.setItem(TIP_KEY, '1').catch((err: unknown) => console.warn('[guide] could not save that the card tip was dismissed; it may show again', err));
}

export function useGuideTipDismissed(): boolean {
    useEffect(loadTip, []);
    const value = useSyncExternalStore(
        (fn) => {
            listeners.add(fn);
            return () => listeners.delete(fn);
        },
        () => tipDismissed,
        () => null,
    );
    return value !== false;
}
