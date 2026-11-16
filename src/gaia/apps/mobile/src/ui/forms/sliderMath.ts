// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Snaps to the step grid from `min`, then clamps to [min, max]. */
export function snapValue(raw: number, min: number, max: number, step: number): number {
    if (max <= min) return min;
    const clamped = Math.min(max, Math.max(min, raw));
    if (!(step > 0)) return clamped;
    const snapped = min + Math.round((clamped - min) / step) * step;
    const decimals = (String(step).split('.')[1] ?? '').length;
    return Math.min(max, Number(snapped.toFixed(decimals)));
}
