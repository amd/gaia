// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { placementText } from '@webui/components/modelMeta';
import { formatPrice } from '@webui/components/settings/cloudPrice';

import type { ProviderModel } from '@/api/client.types';
import type { Translate } from '@/i18n/useT';

/** What a model's info lists: where it runs, size, speed, rank, price. */
export function modelDetails(m: ProviderModel, remote: boolean, t: Translate): string[] {
    const rank = m.rank ? `#${m.rank} ${m.note ?? ''}`.trim() : null;
    if (remote) return [rank, formatPrice(m.price, t)].filter((x): x is string => !!x);
    return [
        placementText(m.placement),
        m.size_gb ? `${m.size_gb} GB` : null,
        m.tokens_per_second ? t('modelMenu.speed', { n: Math.round(m.tokens_per_second) }) : null,
        rank,
        m.custom ? t('modelMenu.addedByYou') : null,
    ].filter((x): x is string => !!x);
}
