// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { WarmUpStatus } from '../types';

/** "your GPU", "your NPU", "the CPU"; null when the device is unknown. */
export function deviceLabel(device: WarmUpStatus['device']): string | null {
    if (device === 'gpu') return 'your GPU';
    if (device === 'npu') return 'your NPU';
    if (device === 'cpu') return 'the CPU';
    return null;
}

/** "23.3 GB", or "under 1 GB" for the embedding model. */
export function formatGb(gb: number): string {
    return gb < 1 ? 'under 1 GB' : `${gb.toFixed(1)} GB`;
}
