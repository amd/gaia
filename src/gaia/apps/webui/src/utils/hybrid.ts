// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { displayModelName } from '../stores/modelStore';
import type { HybridProvider, HybridSettings, HybridTemplate } from '../types';
import type { Translate } from '../i18n/useT';

export const HYBRID_PROVIDERS: HybridProvider[] = ['local', 'amd', 'fireworks', 'claude'];

export function hybridProviderName(t: Translate, provider: HybridProvider): string {
    return t.bare(`settings.hybrid.provider.${provider}`);
}

/** The name the settings' model list gives *model*, else the one the picker would. */
export function hybridModelName(settings: Pick<HybridSettings, 'models'>, model: string): string {
    return settings.models.find((m) => m.id === model)?.display_name ?? displayModelName(model);
}

/** Every model a hybrid turn may send the request to; the classifier defaults to the cheap one. */
export function hybridMembers(s: Pick<HybridSettings, 'cheap_model' | 'strong_model' | 'classifier_model'>): string[] {
    return [s.cheap_model, s.strong_model, s.classifier_model ?? s.cheap_model];
}

/** Local only when every member is a listed local model; an unlisted one could be anywhere. */
export function hybridIsLocal(settings: HybridSettings): boolean {
    return hybridMembers(settings).every((id) => settings.models.find((m) => m.id === id)?.provider === 'local');
}

/** "Hybrid (<cheap> + <strong>)". */
export function hybridEntryName(t: Translate, settings: HybridSettings): string {
    return t.bare('modelMenu.hybridName', {
        cheap: hybridModelName(settings, settings.cheap_model),
        strong: hybridModelName(settings, settings.strong_model),
    });
}

/** Whether the shipped policies were tuned on this cheap/strong pair. */
export function hybridPairMeasured(settings: HybridSettings, templates: HybridTemplate[]): boolean {
    return templates.some((tpl) => tpl.measured && tpl.cheap_model === settings.cheap_model && tpl.strong_model === settings.strong_model);
}

/** The classifier runs on this PC, where a small model may label requests less reliably. */
export function hybridClassifierIsLocal(settings: HybridSettings): boolean {
    const classifier = settings.classifier_model ?? settings.cheap_model;
    return settings.models.find((m) => m.id === classifier)?.provider === 'local';
}
