// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import AsyncStorage from '@react-native-async-storage/async-storage';

import type { Schedule } from '@webui/types';

// The key the web keeps its copy under (stores/scheduleStore.ts).
const ANNOUNCED_ERRORS_KEY = 'gaia-schedule-announced-errors';

type ErrorCounts = Record<string, number>;

/** error_count per schedule id that an earlier launch already announced. */
export async function loadAnnouncedErrors(): Promise<ErrorCounts> {
    let raw: string | null;
    try {
        raw = await AsyncStorage.getItem(ANNOUNCED_ERRORS_KEY);
    } catch (err) {
        console.warn('Could not read the announced schedule failures; ones from before launch may be announced again', err);
        return {};
    }
    if (!raw) return {};
    try {
        const parsed: unknown = JSON.parse(raw);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            return Object.fromEntries(Object.entries(parsed).filter(([, v]) => typeof v === 'number'));
        }
    } catch {
        // Falls through to the warning below.
    }
    console.warn(`Ignored a corrupt "${ANNOUNCED_ERRORS_KEY}"; schedule failures from before launch may be announced again`);
    return {};
}

export async function saveAnnouncedErrors(counts: ErrorCounts): Promise<void> {
    try {
        await AsyncStorage.setItem(ANNOUNCED_ERRORS_KEY, JSON.stringify(counts));
    } catch (err) {
        console.warn('Could not save the announced schedule failures; they may be announced again at the next launch', err);
    }
}

/** The schedules whose last run failed after the counts were taken. */
export function newScheduleFailures(seen: ErrorCounts, list: readonly Schedule[]): Schedule[] {
    return list.filter((s) => s.error_count > (seen[s.id] ?? 0) && !!s.last_error);
}

/**
 * Compares each list it is given with the counts announced so far: the first one against the last launch's saved counts,
 * so a failure from while the app was closed is raised once, and later ones against the previous list.
 */
export function createScheduleAnnouncer(
    raise: (failed: Schedule) => void,
    io: { load: () => Promise<ErrorCounts>; save: (counts: ErrorCounts) => Promise<void> } = { load: loadAnnouncedErrors, save: saveAnnouncedErrors },
): (list: readonly Schedule[]) => Promise<void> {
    let seen: ErrorCounts | null = null;
    let queue: Promise<void> = Promise.resolve();
    const run = async (list: readonly Schedule[]) => {
        seen ??= await io.load();
        for (const failed of newScheduleFailures(seen, list)) raise(failed);
        seen = Object.fromEntries(list.map((s) => [s.id, s.error_count]));
        await io.save(seen);
    };
    return (list) => (queue = queue.then(() => run(list)));
}
