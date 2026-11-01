// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Check, Info, Loader2 } from 'lucide-react';
import { useT } from '../i18n/useT';
import type { GpuMemoryStatus, PreflightReport, SetupPlan } from '../types';
import { ChecklistLoading, ModelChecklist, SelectionSummary, tooBigText } from './setup/ModelChecklist';
import { formatSize } from './setup/format';
import { Reveal } from './setup/Reveal';
import { gpuGbAtSetting } from './setup/GpuMemoryPanel';
import {
    catalogOf, isEmbedding, isVision, pendingDownloads, shortfall, toggle, recommendedSelection,
    type Selection,
} from './setup/selection';

/** One line of the system check: what was found, or that it is checked later. */
function CheckRow({ label, value, ok }: { label: string; value: string; ok: boolean }) {
    return (
        <li className={`setup-check-row${ok ? ' is-ok' : ''}`}>
            {ok ? <Check size={13} aria-hidden="true" /> : <Info size={13} aria-hidden="true" />}
            <span className="setup-check-label">{label}</span>
            <span className="setup-check-value">{value}</span>
        </li>
    );
}

/** Lemonade reports the devices, so before it runs they are not known yet. */
export function deviceKnown(hw: PreflightReport): boolean {
    return !!hw.gpu_name || !!hw.npu_detected || hw.lemonade_running;
}

function deviceText(hw: PreflightReport): { value: string; ok: boolean } {
    const parts = [hw.gpu_name, hw.npu_detected ? 'Ryzen AI NPU' : null].filter(Boolean);
    if (parts.length) return { value: parts.join(' · '), ok: true };
    if (!deviceKnown(hw)) return { value: 'Checked once the model server is running', ok: false };
    return { value: 'No GPU or NPU reported; models run on the CPU', ok: false };
}

/** Device, memory and disk, read the moment setup opens. */
export function SystemCheck({ hardware, error }: { hardware: PreflightReport | null; error: string | null }) {
    if (error) return <p className="setup-hint">This PC&rsquo;s hardware couldn&rsquo;t be read ({error}). Setup still checks it as it goes.</p>;
    if (!hardware) {
        return (
            <p className="setup-hint" role="status">
                <Loader2 size={13} className="spin" aria-hidden="true" /> Checking this PC…
            </p>
        );
    }
    const device = deviceText(hardware);
    // Before a model server runs, the NPU can't be asked about yet; the Device row says so.
    const warnings = hardware.lemonade_running
        ? hardware.warnings
        : hardware.warnings.filter((w) => !/\bNPU\b/.test(w));
    return (
        <>
            <ul className="setup-check" aria-label="This PC">
                <CheckRow label="Device" value={device.value} ok={device.ok} />
                <CheckRow
                    label="Memory"
                    value={hardware.ram_gb != null ? `${hardware.ram_gb} GB` : 'Not reported'}
                    ok={hardware.ram_gb != null}
                />
                <CheckRow
                    label="Disk"
                    value={hardware.disk_free_gb != null ? `${Math.round(hardware.disk_free_gb)} GB free` : 'Not reported'}
                    ok={hardware.disk_free_gb != null && hardware.compatible}
                />
            </ul>
            {!hardware.compatible && hardware.blockers.length > 0 && (
                <p className="setup-error" role="alert">{hardware.blockers.join(' ')}</p>
            )}
            {warnings.length > 0 && <p className="setup-hint">{warnings.join(' ')}</p>}
        </>
    );
}

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
    labelledBy: string;
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
    labelledBy, onBack, onContinue, busy, error,
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
                <div className="setup-row">
                    <button type="button" className="btn-secondary" onClick={onBack} disabled={busy}>{t('setup.back')}</button>
                    <button type="button" className="btn-primary" onClick={() => onContinue(false)} disabled={busy}>
                        {busy && <Loader2 size={13} className="spin" aria-hidden="true" />} {t('setup.finish')}
                    </button>
                    <button type="button" className="link-btn" onClick={() => setChanging(true)}>{t('setup.models.change')}</button>
                </div>
                {error && <p className="setup-error" role="alert">{error}</p>}
            </div>
        );
    }

    return (
        <div className="setup-models" ref={listRef}>
            <p className="setup-hint">{cloud ? t('setup.models.cloudLede') : t('setup.models.lede')}</p>
            <ModelChecklist
                plan={plan}
                selection={selection}
                onToggle={(id, on) => onSelection(toggle(selection, catalog, id, on))}
                onDefaultChat={(id) => onSelection({ ...selection, defaultChat: id, ticked: catalog.find((e) => e.id === id)?.downloaded ? selection.ticked : new Set([...selection.ticked, id]) })}
                labelledBy={labelledBy}
                cloud={cloud}
                disabled={busy}
            />
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
                <div className="setup-row setup-nav">
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
                </div>
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
    const vision = catalog.find((e) => isVision(e) && e.gaia_model);
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
                <div className="is-warn"><dt>{t('setup.ready.gpu')}</dt><dd><AlertTriangle size={13} aria-hidden="true" /> {t('gpu.pending.restart', { gb: formatSize(gpu ? gpuGbAtSetting(gpu, pending.setting_gb) : pending.setting_gb) })}</dd></div>
            )}
            {pending?.state === 'took_effect' && (
                <div><dt>{t('setup.ready.gpu')}</dt><dd>{t('setup.ready.gpuNow', { gb: formatSize(gpu?.gpu_gb ?? pending.setting_gb), was: formatSize(pending.previous_setting_gb ?? 0) })}</dd></div>
            )}
        </dl>
    );
}

