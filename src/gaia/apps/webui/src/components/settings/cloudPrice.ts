// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { MessageKey, Params } from '../../i18n/registry';
import { tEn, type Translate } from '../../i18n/useT';
import type { ModelPrice } from '../../types';

function usd(n: number): string {
    return n < 0.1 ? `$${n.toFixed(3)}` : `$${n.toFixed(2)}`;
}

/** "$0.15 in · $0.50 out per 1M tokens", or "price unknown" — never $0 for a missing price. */
export function formatPrice(price: ModelPrice | null | undefined, t: Translate = tEn): string {
    if (!price) return t('settings.price.unknown');
    return formatPerMillion(price.input_per_million, price.output_per_million, t);
}

/** formatPrice for a bare input/output pair; either missing reads "price unknown". Pass `t.bare` for an <option>. */
export function formatPerMillion(
    input: number | null | undefined,
    output: number | null | undefined,
    t: (key: MessageKey, params?: Params) => string = tEn,
): string {
    if (input == null || output == null) return t('settings.price.unknown');
    return t('settings.price.perMillion', { input: usd(input), output: usd(output) });
}

/** A cost that keeps a sub-cent amount readable: "$0.0028", not "$0.00". */
export function formatCost(n: number): string {
    if (!(n > 0 && n < 0.01)) return formatTotal(n);
    // Two significant digits in plain decimals, never "1.2e-7".
    const decimals = Math.min(20, 1 - Math.floor(Math.log10(n)));
    return `$${n.toFixed(decimals)}`;
}

/** "$1.23" for a running total. */
export function formatTotal(n: number): string {
    return `$${n.toFixed(2)}`;
}
