// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The user's Lemonade wants an API key, or a different one (`ApiError.reason`). */
export function lemonadeKeyRejected(err: unknown): boolean {
    const reason = (err as { reason?: unknown } | null)?.reason;
    return reason === 'needs_key' || reason === 'bad_key';
}
