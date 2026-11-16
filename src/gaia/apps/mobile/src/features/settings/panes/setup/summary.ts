// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { formatSize, ltrIn, totalMemory } from '@webui/components/setup/format';

import type { ActiveModel, PreflightReport, SetupPlan } from '@/api/client.types';
import type { Translate } from '@/i18n/useT';

/** The chat model's name: the active model, else the plan's local pick (a cloud user runs something else). */
export function chatModelName(active: ActiveModel | undefined, plan: SetupPlan | undefined): string | null {
    if (active?.model) return active.label ?? active.display_name ?? active.model;
    return plan?.catalog?.find((e) => e.id === plan.chat_model.id)?.name ?? null;
}

/** "{GPU} · Ryzen AI NPU · {size} of {total} for models", as far as the PC and the plan say. */
export function detectedLine(t: Translate, plan: SetupPlan | undefined, hardware: PreflightReport | undefined): string | null {
    const device = [hardware?.gpu_name, hardware?.npu_detected ? t('setup.checks.npu') : null].filter(Boolean).join(' · ');
    const total = totalMemory(plan?.system?.physical_memory, hardware?.ram_gb);
    const size = plan?.capacity_gb != null ? formatSize(plan.capacity_gb) : null;
    const memory = size && total
        ? t('settings.setup.memoryOf', { size: ltrIn(t.locale, size), total: ltrIn(t.locale, total) })
        : size ? t('settings.setup.memoryFor', { size: ltrIn(t.locale, size) })
            : total ? ltrIn(t.locale, total) : null;
    return [device, memory].filter(Boolean).join(' · ') || null;
}
