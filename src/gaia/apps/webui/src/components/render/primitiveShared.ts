// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Shared helpers for the generic render primitives (issue #2108). */

/** Render cap for table rows, list items, and key_value items. */
export const MAX_ITEMS = 500;

export type Scalar = string | number | boolean | null;

export function isScalar(value: unknown): value is Scalar {
    return (
        value === null ||
        typeof value === 'string' ||
        typeof value === 'number' ||
        typeof value === 'boolean'
    );
}

/** Render a scalar payload value as plain text. */
export function scalarText(value: Scalar): string {
    return value === null ? '' : String(value);
}

export function isOptionalString(value: unknown): value is string | undefined {
    return value === undefined || typeof value === 'string';
}

/** The visible `+N more (truncated)` row shown when a cap kicks in. */
export function truncationLabel(total: number): string {
    return `+${total - MAX_ITEMS} more (truncated)`;
}
