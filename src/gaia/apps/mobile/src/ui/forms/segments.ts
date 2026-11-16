// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

export const SEGMENT_PAD = 8;

/** True when every label fits on one line in an equal-width segment. */
export function segmentsFit(labelWidths: readonly number[], containerWidth: number): boolean {
    if (!labelWidths.length || containerWidth <= 0) return true;
    const segment = (containerWidth - 4) / labelWidths.length;
    return Math.max(...labelWidths) + SEGMENT_PAD * 2 <= segment;
}
