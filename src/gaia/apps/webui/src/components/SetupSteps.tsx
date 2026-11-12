// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Check, Loader2 } from 'lucide-react';
import { useT } from '../i18n/useT';
import type { GpuMemoryStatus, SetupPlan } from '../types';
import { JobModels } from './setup/JobModels';
import { ChecklistLoading, SelectionSummary, tooBigText } from './setup/ModelChecklist';
import { formatSize } from './setup/format';
import { Reveal } from './setup/Reveal';
import { SetupFooter } from './setup/SetupFooter';
import { gpuGbAtSetting, restartLine } from './setup/GpuMemoryPanel';
import {
    catalogOf, isEmbedding, isVision, pendingDownloads, resolveVision, shortfall, recommendedSelection,
    type Selection,
} from './setup/selection';

interface ModelsStepProps {
    plan: SetupPlan | null;
    planError: string | null;
    onRetryPlan: () => void;
    /** Setup's own plan, used when the model list can't be read. */
    onUseRecommended: () => void;
    selection: Selection | null;
    onSelection: (selection: Selection) => void;
    cloud: boolean;
    keepTrying: boolean;
    onKeepTrying: (on: boolean) => void;
    onBack: () => void;
    /** `acknowledgeLowDisk`: the user pressed Continue anyway past a disk shortfall. */
    onContinue: (acknowledgeLowDisk: boolean) => void;
    busy: boolean;
    error: string | null;
}

/**
 * Step "Choose your models": the checklist, the totals against free disk,
 * the opt-in keep-trying, and Continue. Nothing downloads until Continue.
 */
export function ModelsStep({
    plan, planError, onRetryPlan, onUseRecommended, selection, onSelection, cloud, keepTrying, onKeepTrying,
    onBack, onContinue, busy, error,
}: ModelsStepProps) {
    const t = useT();
    const listRef = useRef<HTMLDivElement>(null);
    const [changing, setChanging] = useState(false);
    const ready = !!plan && !!selection && !!plan.catalog;
    // Decided once, from what the user arrived with: later ticks never swap the screen.
    const arrivedComplete = useRef<boolean | null>(null);
    if (ready && arrivedComplete.current === null) {
        arrivedComplete.current = !cloud && pendingDownloads(catalogOf(plan), selection).length === 0
            && !!selection.defaultChat && catalogOf(plan).some((e) => e.downloaded);
    }

    // On entry, focus the first model, announced with the step heading.
    useEffect(() => {
        if (!ready) return;
        listRef.current?.querySelector<HTMLInputElement>('input:not([disabled])')?.focus();
    }, [ready, changing]);

    if (planError && !ready) {
        return (
            <div className="setup-models-error">
                <p className="setup-error" role="alert">{t('setup.models.listFailed', { reason: planError })}</p>
                <div className="setup-row">
                    <button type="button" className="btn-primary" onClick={onRetryPlan}>{t('setup.tryAgain')}</button>
                    <button type="button" className="btn-secondary" onClick={onUseRecommended}>{t('setup.models.useRecommended')}</button>
                </div>
                <p className="setup-hint">{t('setup.models.useRecommendedNote')}</p>
            </div>
        );
    }
    if (!ready) return <ChecklistLoading />;

    const catalog = catalogOf(plan);
    const pending = pendingDownloads(catalog, selection);
    const noChat = !cloud && !selection.defaultChat;
    const short = shortfall(plan, selection);
    const big = cloud ? [] : catalog.filter((e) => e.fits === false && selection.ticked.has(e.id));
    const allPresent = !changing && !!arrivedComplete.current && pending.length === 0;
    const continueLabel = short ? t('setup.continueAnyway') : t('setup.continue');

    if (allPresent) {
        return (
            <div className="setup-present" role="status">
                <p><Check size={14} aria-hidden="true" /> {t('setup.models.allPresent')}</p>
                <button type="button" className="link-btn" onClick={() => setChanging(true)}>{t('setup.models.change')}</button>
                <SetupFooter>
                    <button type="button" className="btn-secondary" onClick={onBack} disabled={busy}>{t('setup.back')}</button>
                    <button type="button" className="btn-primary" onClick={() => onContinue(false)} disabled={busy}>
                        {busy && <Loader2 size={13} className="spin" aria-hidden="true" />} {t('setup.finish')}
                    </button>
                </SetupFooter>
                {error && <p className="setup-error" role="alert">{error}</p>}
            </div>
        );
    }

    return (
        <div className="setup-models" ref={listRef}>
            <p className="setup-hint">{t('setup.job.lede')}</p>
            <JobModels plan={plan} selection={selection} onSelection={onSelection} disabled={busy} />
            {!cloud && (
                <button type="button" className="link-btn setup-reset" onClick={() => onSelection(recommendedSelection(plan))} disabled={busy}>
                    {t('setup.models.resetRecommended')}
                </button>
            )}
            <div className="setup-models-footer">
                <SelectionSummary plan={plan} selection={selection} />
                <label className="setup-keep-trying">
                    <input type="checkbox" checked={keepTrying} onChange={(e) => onKeepTrying(e.target.checked)} disabled={busy} />
                    <span>{t('setup.keepTrying')}</span>
                </label>
                <Reveal open={noChat}>
                    <p className="setup-error" id="setup-no-chat">{t('setup.models.noChatReason')}</p>
                </Reveal>
                {big.length > 0 && <span className="visually-hidden">{big.map((e) => tooBigText(e, t)).join(' ')}</span>}
                {error && <p className="setup-error" role="alert">{error}</p>}
                <SetupFooter>
                    <button type="button" className="btn-secondary" onClick={onBack} disabled={busy}>{t('setup.back')}</button>
                    <button
                        type="button"
                        className="btn-primary"
                        onClick={() => onContinue(!!short)}
                        disabled={busy || noChat}
                        aria-describedby={noChat ? 'setup-no-chat' : undefined}
                    >
                        {busy && <Loader2 size={13} className="spin" aria-hidden="true" />} {continueLabel}
                    </button>
                </SetupFooter>
            </div>
        </div>
    );
}

interface ReadyProps {
    plan: SetupPlan | null;
    chatModel: string | null;
    gpu: GpuMemoryStatus | null;
}

/** What setup left on this PC: chat, vision, documents, what was skipped, and the GPU share. */
export function ReadySummary({ plan, chatModel, gpu }: ReadyProps) {
    const t = useT();
    if (!plan?.catalog) return null;
    const catalog = plan.catalog;
    const name = (id: string | null) => (id ? catalog.find((e) => e.id === id)?.name ?? id : null);
    const visionId = resolveVision(catalog, { ticked: new Set(), unticked: new Set(), chatChosen: false, defaultChat: chatModel, vision: plan.vision_model ?? null });
    const vision = catalog.find((e) => e.id === visionId) ?? catalog.find((e) => isVision(e) && e.gaia_model);
    const docs = catalog.find((e) => isEmbedding(e) && e.downloaded);
    const skipped = catalog.filter((e) => e.gaia_model && e.planned && e.skipped);
    const pending = gpu?.pending;
    const chat = catalog.find((e) => e.id === chatModel);
    return (
        <dl className="setup-summary-list">
            {chatModel && (
                <div><dt>{t('setup.ready.chat')}</dt><dd><bdi>{name(chatModel)}</bdi>{chat?.recipe === 'flm' ? ` (${t('setup.models.npu')})` : ''}</dd></div>
            )}
            <div>
                <dt>{t('setup.ready.vision')}</dt>
                <dd>{vision?.downloaded ? <bdi>{vision.name}</bdi> : t('setup.ready.visionOff', { name: vision?.name ?? 'Gemma-4-E4B-it' })}</dd>
            </div>
            {docs && <div><dt>{t('setup.ready.documents')}</dt><dd><bdi>{docs.name}</bdi></dd></div>}
            {skipped.length > 0 && (
                <div><dt>{t('setup.ready.notInstalled')}</dt><dd>{skipped.map((e) => e.name).join(', ')}</dd></div>
            )}
            {pending?.state === 'restart_required' && (
                <div className="is-warn"><dt>{t('setup.ready.gpu')}</dt><dd><AlertTriangle size={13} aria-hidden="true" /> {restartLine(t, gpu ? gpuGbAtSetting(gpu, pending.setting_gb) : null)}</dd></div>
            )}
            {pending?.state === 'took_effect' && (
                <div><dt>{t('setup.ready.gpu')}</dt><dd>{gpu?.gpu_gb != null ? t('setup.ready.gpuNow', { gb: formatSize(gpu.gpu_gb) }) : t('setup.ready.gpuChanged')}</dd></div>
            )}
        </dl>
    );
}

