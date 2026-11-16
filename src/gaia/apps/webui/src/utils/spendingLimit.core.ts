// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** What the monthly-limit field holds: the amount (null for none), whether it can be saved, and whether it differs from the saved limit. */
export function readLimitDraft(draft: string, saved: number | null): { parsed: number | null; valid: boolean; dirty: boolean } {
    const parsed = draft.trim() === '' ? null : Number(draft);
    const valid = parsed === null || (Number.isFinite(parsed) && parsed > 0);
    return { parsed, valid, dirty: parsed !== saved };
}
