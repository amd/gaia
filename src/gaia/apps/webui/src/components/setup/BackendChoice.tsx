// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { RadioGroup } from '../RadioGroup';
import { useT } from '../../i18n/useT';
import * as api from '../../services/api';
import type { SetupPlan } from '../../types';
import { NpuNotReady } from './NpuNotReady';
import { cantRunHere, catalogOf, isNpu, pickChat, recommendedSelection, type Selection } from './selection';

/**
 * At the start of a local setup: make llama.cpp use Vulkan, the GPU backend GAIA has
 * tested, where Lemonade's own pick or an earlier saved one is another and this PC
 * can run Vulkan. A server that isn't answering is left to the download to report.
 */
export async function applyTestedBackend(): Promise<void> {
    const server = await api.getModelServer();
    if (!server.reachable) return;
    const vulkan = (server.available_backends ?? []).some((b) => b.name === 'vulkan');
    if (vulkan && (server.preferred_backend ?? server.default_backend) !== 'vulkan') {
        await api.setModelServerBackend('vulkan');
    }
}

interface Props {
    plan: SetupPlan;
    selection: Selection;
    onSelection: (selection: Selection) => void;
    disabled?: boolean;
    /** Read this PC again, after the user fixed what stops the NPU. */
    onRecheck?: () => void;
}

/**
 * Where the chat model runs, from the two backends GAIA has tested: llama.cpp
 * on the GPU with Vulkan, or FastFlowLM on a Ryzen AI NPU. Picking one picks
 * that backend's chat model.
 */
export function BackendChoice({ plan, selection, onSelection, disabled = false, onRecheck }: Props) {
    const t = useT();
    const catalog = catalogOf(plan);
    // An NPU that is there but can't run a model yet is shown, with what to fix, not offered.
    const npuProblems = plan.npu?.present && !plan.npu.ready ? plan.npu.problems : [];
    // GAIA's own NPU model on a PC whose NPU holds it, else an NPU chat model already downloaded.
    const npuModel = npuProblems.length ? undefined
        : catalog.find((e) => isNpu(e) && e.gaia_model && e.chat_choice && e.fits !== false)
        ?? catalog.find((e) => isNpu(e) && e.chat_choice && e.downloaded && !cantRunHere(e));
    // Lemonade sees an NPU, but its NPU engine can't run here: say so rather than hide it silently.
    const npuUnusable = !npuProblems.length && !npuModel && catalog.some((e) => isNpu(e) && e.fit_code === 'npu');
    const onNpu = !!catalog.find((e) => e.id === selection.defaultChat && isNpu(e));
    // Back to the GPU: setup's own pick, unless that is the NPU model itself.
    const recommended = recommendedSelection(plan).defaultChat;
    const gpuChat = recommended && !catalog.find((e) => e.id === recommended && isNpu(e))
        ? recommended
        : catalog.find((e) => e.chat_choice && e.gaia_model && e.support === 'verified' && !isNpu(e))?.id;

    const choose = (id: 'gpu' | 'npu') => {
        if (id === 'npu' && npuModel && !onNpu) onSelection(pickChat(selection, catalog, npuModel.id));
        if (id === 'gpu' && onNpu && gpuChat) onSelection(pickChat(selection, catalog, gpuChat));
    };

    const choices: Array<{ id: 'gpu' | 'npu'; label: string; unavailable?: boolean }> = [
        { id: 'gpu', label: t('setup.backend.gpu') },
        ...(npuModel ? [{ id: 'npu' as const, label: t('setup.backend.npu') }] : []),
        ...(npuProblems.length ? [{ id: 'npu' as const, label: t('setup.backend.npu'), unavailable: true }] : []),
    ];
    const selected = onNpu ? 'npu' : 'gpu';

    return (
        <section className="setup-backend" aria-labelledby="setup-backend-title">
            <h3 className="mc-job-title" id="setup-backend-title">{t('setup.backend.title')}</h3>
            <RadioGroup className="segmented" aria-labelledby="setup-backend-title">
                {choices.map((c) => (
                    <button
                        key={c.id}
                        type="button"
                        role="radio"
                        aria-checked={selected === c.id}
                        aria-describedby={c.unavailable ? 'setup-npu-problems' : undefined}
                        className={selected === c.id ? 'is-active' : ''}
                        disabled={disabled || c.unavailable}
                        onClick={() => choose(c.id)}
                    >
                        <bdi>{c.label}</bdi>
                    </button>
                ))}
            </RadioGroup>
            {npuProblems.length > 0 ? (
                <NpuNotReady problems={npuProblems} disabled={disabled} onRecheck={onRecheck} />
            ) : (
                <p className="setup-hint">{t(npuModel ? 'setup.backend.hint' : npuUnusable ? 'setup.backend.hintNpuUnusable' : 'setup.backend.hintNoNpu')}</p>
            )}
        </section>
    );
}
