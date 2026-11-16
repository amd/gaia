// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ref, type MessageKey } from '@webui/i18n/registry';

import { GaiaApiError } from '@/api/errors';
import type { ContextChoice, ProviderModel } from '@/api/client.types';
import { describeError, type Translate } from '@/i18n/useT';
import { ctxLabel } from '@webui/components/modelMeta';
import { sameModelId, shortModelName } from '@webui/utils/modelList.core';
import { usd } from '@webui/components/settings/cloudPrice';
import type { ModelPrice } from '@webui/types';

export { ctxLabel };

export { sameModelId };

export function modelLabel(m: Pick<ProviderModel, 'id' | 'display_name'>): string {
    return m.display_name || shortModelName(m.id);
}

/** What went wrong, in the PC's own words when it gave them. */
export function errorText(err: unknown, t: Translate): string {
    return err instanceof GaiaApiError ? err.detail : describeError(err, t);
}

/** Keeps a size with its unit whole inside right-to-left text. */
export function isolate(text: string): string {
    return `⁦${text}⁩`;
}

export const AUTOMATIC = 'auto';

/** The line that says what saving a context size did, as the web's ModelContextSize words it. */
export function contextOutcome(next: ContextChoice, t: Translate): string {
    const params = {
        name: next.display_name,
        size: next.chosen ? isolate(ctxLabel(next.chosen)) : ref('settings.ctx.automaticSize'),
    };
    const reason = next.reload?.reason;
    const key: MessageKey = next.reload?.started ? 'settings.ctx.reloading'
        : reason === 'already_loaded' ? 'settings.ctx.alreadyLoaded'
        : reason === 'warm_up' ? 'settings.ctx.warmUp'
        : reason === 'busy' ? 'settings.ctx.busy'
        : reason === 'not_gaias_server' ? 'settings.ctx.ownServer'
        : reason === 'restarting' ? 'settings.ctx.restarting'
        : 'settings.ctx.nextLoad';
    return t(key, params);
}

/** "$0.15 in · $0.50 out per 1M tokens", each price kept whole inside right-to-left text. */
export function priceText(price: ModelPrice | null | undefined, t: Translate): string {
    if (!price) return t('settings.price.unknown');
    return t('settings.price.perMillion', {
        input: isolate(usd(price.input_per_million)),
        output: isolate(usd(price.output_per_million)),
    });
}

/** The PC refuses this from a phone (a 403); the row stays as it was and the info notice says so. */
export function refusedOnPhone(err: unknown): boolean {
    return err instanceof GaiaApiError && err.status === 403;
}
