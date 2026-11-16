// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

export type ChipId = 'folder' | 'private' | 'permission' | 'model' | 'meter';

/** Chips give up their label in this order; the model chip never does. The meter's bar goes before the permission chip's label, as the desktop's "Ask" stays readable. */
export const COLLAPSE_ORDER: readonly ChipId[] = ['folder', 'private', 'meter', 'permission'];

export const CHIP_GAP = 8;
/** A collapsed chip is its icon in a touch target. */
export const ICON_ONLY_WIDTH = 44;
/** The model's name ellipsizes down to this before any other chip collapses. */
export const MODEL_MIN_WIDTH = 96;
/** Below this width the model chip drops its name and shows its icon and chevron. */
export const MODEL_FLOOR_WIDTH = 72;
/** The context meter's bar and its gap; without it the meter is its percentage alone. */
export const METER_BAR_WIDTH = 48;

/** Which chips must show their icon alone (the meter: no bar) so the measured row fits `available`. Absent (0 or unmeasured) items count for nothing. */
export function collapsedChips(full: Partial<Record<ChipId, number>>, available: number): ChipId[] {
    if (available <= 0) return [];
    const width = (id: ChipId): number | undefined => {
        const w = full[id];
        return w === undefined || w <= 0 ? undefined : id === 'model' ? Math.min(w, MODEL_MIN_WIDTH) : w;
    };
    const present = (['folder', 'private', 'permission', 'model', 'meter'] as const).filter((id) => width(id) !== undefined);
    const collapsed: ChipId[] = [];
    const total = () =>
        present.reduce((sum, id) => {
            if (!collapsed.includes(id)) return sum + (width(id) ?? 0);
            return sum + (id === 'meter' ? Math.max(0, (width(id) ?? 0) - METER_BAR_WIDTH) : ICON_ONLY_WIDTH);
        }, 0) +
        CHIP_GAP * Math.max(0, present.length - 1);
    for (const id of COLLAPSE_ORDER) {
        if (total() <= available) break;
        if (present.includes(id)) collapsed.push(id);
    }
    return collapsed;
}
