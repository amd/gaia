// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { formatFitGb, formatSize, ltrIn } from '@webui/components/setup/format';
import {
    cantRunHere, catalogOf, isEmbedding, isNpu, isVision, pickChat, recommendedSelection, type Selection,
} from '@webui/components/setup/selection';
import type { SetupCatalogEntry, SetupNpuProblem, SetupPlan } from '@webui/types';

import type { Translate } from '@/i18n/useT';

export interface BackendView {
    /** The NPU chat model picking the NPU chooses; undefined when the NPU can't be offered. */
    npuModel: SetupCatalogEntry | undefined;
    /** What stops a model running on an NPU that is there. */
    npuProblems: SetupNpuProblem[];
    /** Lemonade sees an NPU, but its engine can't run GAIA's models here. */
    npuUnusable: boolean;
    onNpu: boolean;
    /** The chat model to return to when the GPU is picked again. */
    gpuChat: string | undefined;
}

/** Where chat runs and what is offered, from the two backends GAIA has tested: Vulkan on the GPU, FastFlowLM on an NPU. */
export function backendView(plan: SetupPlan, selection: Selection): BackendView {
    const catalog = catalogOf(plan);
    const npuProblems = plan.npu?.present && !plan.npu.ready ? plan.npu.problems : [];
    const npuModel = npuProblems.length ? undefined
        : catalog.find((e) => isNpu(e) && e.gaia_model && e.chat_choice && e.fits !== false)
        ?? catalog.find((e) => isNpu(e) && e.chat_choice && e.downloaded && !cantRunHere(e));
    const recommended = recommendedSelection(plan).defaultChat;
    return {
        npuModel,
        npuProblems,
        npuUnusable: !npuProblems.length && !npuModel && catalog.some((e) => isNpu(e) && e.fit_code === 'npu'),
        onNpu: !!catalog.find((e) => e.id === selection.defaultChat && isNpu(e)),
        gpuChat: recommended && !catalog.find((e) => e.id === recommended && isNpu(e))
            ? recommended
            : catalog.find((e) => e.chat_choice && e.gaia_model && e.support === 'verified' && !isNpu(e))?.id,
    };
}

/** Picking a backend picks its chat model, which also turns image reading off on the NPU. */
export function chooseBackend(plan: SetupPlan, selection: Selection, which: 'gpu' | 'npu'): Selection {
    const { npuModel, onNpu, gpuChat } = backendView(plan, selection);
    const catalog = catalogOf(plan);
    if (which === 'npu' && npuModel && !onNpu) return pickChat(selection, catalog, npuModel.id);
    if (which === 'gpu' && onNpu && gpuChat) return pickChat(selection, catalog, gpuChat);
    return selection;
}

/** What a model is for, in fixed words. */
export function purposeChips(e: SetupCatalogEntry, t: Translate): string[] {
    const chips: string[] = [];
    if (e.chat_choice && !isEmbedding(e)) chips.push(t('setup.models.purpose.chat'));
    if (isVision(e)) chips.push(t('setup.models.purpose.vision'));
    if (isEmbedding(e)) chips.push(t('setup.models.purpose.documents'));
    if (e.roles.includes('speech')) chips.push(t('setup.models.purpose.speech'));
    if (e.roles.includes('voice')) chips.push(t('setup.models.purpose.voice'));
    if (e.roles.includes('image_creation')) chips.push(t('setup.models.purpose.imageCreation'));
    return chips;
}

export function purposeLine(e: SetupCatalogEntry, t: Translate): string {
    if (isEmbedding(e)) return t('setup.models.line.documents');
    if (isVision(e)) return t('setup.models.line.vision');
    if (e.roles.includes('speech')) return t('setup.models.line.speech');
    if (e.roles.includes('voice')) return t('setup.models.line.voice');
    if (e.roles.includes('image_creation')) return t('setup.models.line.imageCreation');
    return t('setup.models.line.chat');
}

/** "On this PC", "Download 5.1 GB", "Partly downloaded, 1.2 GB left". */
export function statusText(e: SetupCatalogEntry, t: Translate, locale: string): string {
    if (e.downloaded) return t('setup.models.status.onPc');
    if (e.size_gb == null) return t('setup.models.status.sizeUnknown');
    const size = ltrIn(locale, formatSize(e.size_gb));
    return t(e.partial ? 'setup.models.status.partial' : 'setup.models.status.download', { size });
}

/** The memory note, with the backend's numbers and nothing invented. */
export function fitLine(e: SetupCatalogEntry, t: Translate, locale: string): string | null {
    if (e.fits !== true || e.need_gb == null || e.capacity_gb == null) return null;
    return t('setup.models.fit.ok', { need: ltrIn(locale, formatFitGb(e.need_gb)), have: ltrIn(locale, formatFitGb(e.capacity_gb)) });
}

/** Why a model doesn't fit: its real reason, never "too big" for one no backend here runs. */
export function tooBigText(e: SetupCatalogEntry, t: Translate, locale: string): string {
    if (e.fit_code === 'npu') return t('setup.models.warn.npu');
    if (e.fit_code === 'backend') return t('setup.models.warn.backend');
    return e.need_gb != null && e.capacity_gb != null
        ? t('setup.models.warn.tooBig', { need: ltrIn(locale, formatFitGb(e.need_gb)), have: ltrIn(locale, formatFitGb(e.capacity_gb)) })
        : t('setup.models.warn.tooBigNoNumbers');
}

/** The caveat under an unticked model that turns a feature off. */
export function skipCaveat(e: SetupCatalogEntry, t: Translate, imagesCovered: boolean): string | null {
    if (!isVision(e) && !isEmbedding(e)) return null;
    if (isVision(e) && !e.downloaded && imagesCovered) return null;
    return t(isVision(e) ? 'setup.models.warn.vision' : 'setup.models.warn.documents', { name: e.name });
}
