// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** *res* when it has the shape the caller relies on; otherwise a plain error naming what was missing. */
export function expectShape<T>(res: T | null | undefined, ok: (r: T) => boolean, what: string): T {
    if (res == null || !ok(res)) throw new Error(`the server sent no ${what}`);
    return res;
}
