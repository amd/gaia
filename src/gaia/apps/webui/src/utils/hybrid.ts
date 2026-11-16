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

/** The provider a model id belongs to, by its prefix, as the backend's provider_of. */
export function hybridProviderOf(model: string): HybridProvider {
    if (model.startsWith('amd.')) return 'amd';
    if (model.startsWith('fireworks.')) return 'fireworks';
    if (model.startsWith('claude-')) return 'claude';
    return 'local';
}

/**
 * The providers a template still needs set up: those of its models that can't
 * run here. Every needed provider when its models aren't known.
 */
export function hybridUnmetProviders(
    settings: Pick<HybridSettings, 'models'>,
    template: { cheap_model: string | null; strong_model: string | null; classifier_model: string | null; providers_needed: HybridProvider[] },
): HybridProvider[] {
    const members = [template.cheap_model, template.strong_model, template.classifier_model];
    if (members.some((m) => m === null)) return template.providers_needed;
    const blocked = new Set(members.flatMap((id) => {
        const listed = settings.models.find((m) => m.id === id);
        if (listed?.available) return [];
        return [listed?.provider ?? hybridProviderOf(id as string)];
    }));
    return template.providers_needed.filter((p) => blocked.has(p));
}

/** The name the settings' model list gives *model*, else the one the picker would. */
export function hybridModelName(settings: Pick<HybridSettings, 'models'>, model: string): string {
    return settings.models.find((m) => m.id === model)?.display_name ?? displayModelName(model);
}
