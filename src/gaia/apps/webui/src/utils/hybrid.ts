// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { displayModelName } from '../stores/modelStore';
import type { HybridProvider, HybridSettings } from '../types';
import type { Translate } from '../i18n/useT';

/** The backend's provider order for hybrid's model list. */
export const HYBRID_PROVIDERS: HybridProvider[] = ['local', 'amd', 'fireworks', 'claude'];

export function hybridProviderName(t: Translate, provider: HybridProvider): string {
    return t.bare(`settings.hybrid.provider.${provider}`);
}

/** The name the settings' model list gives *model*, else the one the picker would. */
export function hybridModelName(settings: Pick<HybridSettings, 'models'>, model: string): string {
    return settings.models.find((m) => m.id === model)?.display_name ?? displayModelName(model);
}
