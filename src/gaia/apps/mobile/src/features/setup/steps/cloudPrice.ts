// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ModelPrice } from '@webui/types';

import type { Translate } from '@/i18n/useT';

const usd = (n: number) => (n < 0.1 ? `$${n.toFixed(3)}` : `$${n.toFixed(2)}`);

// Mirrors webui's formatPrice, whose module pulls in the chat store.
/** "$0.15 in · $0.50 out per 1M tokens", or "price unknown": never $0 for a missing price. */
export function formatPrice(price: ModelPrice | null | undefined, t: Translate): string {
    if (!price) return t('settings.price.unknown');
    return t('settings.price.perMillion', { input: usd(price.input_per_million), output: usd(price.output_per_million) });
}
