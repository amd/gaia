// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ModelPrice } from '../../types';

function usd(n: number): string {
    return n < 0.1 ? `$${n.toFixed(3)}` : `$${n.toFixed(2)}`;
}

/** "$0.15 in · $0.50 out per 1M tokens", or "price unknown" — never $0 for a missing price. */
export function formatPrice(price: ModelPrice | null | undefined): string {
    if (!price) return 'price unknown';
    return `${usd(price.input_per_million)} in · ${usd(price.output_per_million)} out per 1M tokens`;
}

/** "$1.23" for a running total. */
export function formatTotal(n: number): string {
    return `$${n.toFixed(2)}`;
}
