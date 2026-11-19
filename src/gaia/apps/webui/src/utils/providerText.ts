// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isMessageKey } from '../i18n/registry';
import { tEn, type Translate } from '../i18n/useT';
import type { ProviderInfo } from '../types';

/**
 * Providers are keyed under `providers.<id>`. Text with no key (a brand name) or whose
 * English no longer matches the backend's (a reworded notice) shows as sent.
 */
function providerText(t: Translate, key: string, server: string, bare = false): string {
    if (!isMessageKey(key) || tEn(key) !== server) return server;
    return bare ? t.bare(key) : t(key);
}

export function providerName(t: Translate, provider: Pick<ProviderInfo, 'id' | 'name'>): string {
    return providerText(t, `providers.${provider.id}.name`, provider.name, true);
}

/** What leaves the PC with this provider; shown before a cloud model is used. */
export function providerNotice(t: Translate, provider: Pick<ProviderInfo, 'id' | 'privacy_notice'>): string {
    return providerText(t, `providers.${provider.id}.notice`, provider.privacy_notice);
}
