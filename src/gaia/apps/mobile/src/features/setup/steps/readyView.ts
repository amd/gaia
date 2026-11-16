// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { gpuGbAtSetting } from '@webui/components/setup/gpuMemory';
import { isEmbedding, isNpu, isVision, resolveVision } from '@webui/components/setup/selection';
import type { GpuMemoryStatus, SetupPlan, SetupRunStatus, SetupTest, StartCard } from '@webui/types';

export interface ReadyView {
    /** The model GAIA answers with, as the run set it up; null when the run named none. */
    answering: string | null;
    cloud: boolean;
    /** Jobs whose test failed on an extra that doesn't stop GAIA being ready. */
    failedJobs: SetupTest['job'][];
    /** Models the run left on the server's list, not downloaded. */
    notDownloaded: string[];
}

export function readyView(run: SetupRunStatus | null, plan: SetupPlan | undefined): ReadyView {
    const names = new Map((plan?.catalog ?? []).map((e) => [e.id, e.name]));
    return {
        answering: run?.chat_model ?? (plan && !plan.decided_after_server ? plan.chat_model.id : null),
        cloud: !!run?.skip_chat_model,
        failedJobs: (run?.tests ?? []).filter((r) => r.state === 'failed').map((r) => r.job),
        notDownloaded: (run?.models ?? []).filter((m) => m.state === 'skipped').map((m) => names.get(m.id) ?? m.id),
    };
}

/** A first run has no earlier chat to pick up, so "Check in" shows only when setup is run again. */
export function starterCards(cards: StartCard[] | undefined, again: boolean): StartCard[] {
    return again ? (cards ?? []) : (cards ?? []).filter((c) => c.skill !== 'check-in');
}

/** What setup left on the PC, one line each; the screen words each by `kind`. */
export type SummaryRow =
    | { kind: 'chat'; model: string; npu: boolean }
    | { kind: 'vision'; model: string; downloaded: boolean }
    | { kind: 'documents'; model: string }
    | { kind: 'notInstalled'; models: string[] }
    | { kind: 'gpuRestart'; gb: number | null }
    | { kind: 'gpuNow'; gb: number | null };

/** The desktop's Ready summary: chat, image reading, documents, what was skipped, and a GPU memory change. */
export function summaryRows(plan: SetupPlan | undefined, chatModel: string | null, gpu: GpuMemoryStatus | undefined): SummaryRow[] {
    const catalog = plan?.catalog;
    if (!catalog) return [];
    const rows: SummaryRow[] = [];
    const chat = catalog.find((e) => e.id === chatModel);
    if (chatModel) rows.push({ kind: 'chat', model: chat?.name ?? chatModel, npu: !!chat && isNpu(chat) });
    const visionId = resolveVision(catalog, {
        ticked: new Set(), unticked: new Set(), chatChosen: false, defaultChat: chatModel, vision: plan.vision_model ?? null,
    });
    const vision = catalog.find((e) => e.id === visionId) ?? catalog.find((e) => isVision(e) && e.gaia_model);
    rows.push({ kind: 'vision', model: vision?.name ?? 'Gemma-4-E4B-it', downloaded: !!vision?.downloaded });
    const docs = catalog.find((e) => isEmbedding(e) && e.downloaded);
    if (docs) rows.push({ kind: 'documents', model: docs.name });
    const skipped = catalog.filter((e) => e.gaia_model && e.planned && e.skipped).map((e) => e.name);
    if (skipped.length > 0) rows.push({ kind: 'notInstalled', models: skipped });
    const pending = gpu?.pending;
    if (pending?.state === 'restart_required') rows.push({ kind: 'gpuRestart', gb: gpu ? gpuGbAtSetting(gpu, pending.setting_gb) : null });
    if (pending?.state === 'took_effect') rows.push({ kind: 'gpuNow', gb: gpu?.gpu_gb ?? null });
    return rows;
}
