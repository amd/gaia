// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { useT } from '../../i18n/useT';
import { scopedStatus, useSetupDownloads } from '../../stores/setupDownloadsStore';
import type { GpuMemoryStatus, SetupCatalogEntry, SetupModelProgress, SetupPlan } from '../../types';
import { ConfirmSheet } from '../setup/ConfirmSheet';
import { DownloadProgress, downloadsActive } from '../setup/DownloadProgress';
import { formatSize } from '../setup/format';
import { GpuMemoryPanel } from '../setup/GpuMemoryPanel';
import { driveName, ModelChecklist, SelectionSummary, tooBigText } from '../setup/ModelChecklist';
import {
    catalogOf, initialSelection, isVision, pendingDownloads, shortfall, skipChanges, toggle, tooBig,
    type Selection,
} from '../setup/selection';
import '../setup/setup.css';

function errorText(err: unknown): string {
    return err instanceof Error ? err.message : String(err);
}

function sameSet(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
    return a.size === b.size && [...a].every((x) => b.has(x));
}

type Sheet =
    | { kind: 'remove'; entry: SetupCatalogEntry }
    | { kind: 'no_fit'; models: SetupCatalogEntry[] }
    | { kind: 'low_disk'; message: string }
    | { kind: 'server'; detail: { message: string } };

/**
 * Settings > Model, for models on this PC: the setup checklist with live state
 * (Apply downloads what is ticked and missing; nothing is deleted except by an
 * explicit Remove), the GPU memory split on an integrated GPU, and the
 * downloads in progress.
 */
export function LocalModelsSections() {
    const t = useT();
    const [plan, setPlan] = useState<SetupPlan | null>(null);
    const [planError, setPlanError] = useState<string | null>(null);
    const [selection, setSelection] = useState<Selection | null>(null);
    const [sheet, setSheet] = useState<Sheet | null>(null);
    const [busy, setBusy] = useState(false);
    const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
    const [noFitAcked, setNoFitAcked] = useState(false);
    const [gpu, setGpu] = useState<GpuMemoryStatus | null>(null);
    const [gpuError, setGpuError] = useState<string | null>(null);
    const rawStatus = useSetupDownloads((s) => s.status);
    const started = useSetupDownloads((s) => s.started);
    const status = useMemo(() => scopedStatus(rawStatus, started), [rawStatus, started]);
    const storeError = useSetupDownloads((s) => s.error);
    const refreshDownloads = useSetupDownloads((s) => s.refresh);
    const enqueue = useSetupDownloads((s) => s.enqueue);
    const cancelDownload = useSetupDownloads((s) => s.cancel);

    const loadPlan = useCallback(async (reseed = false) => {
        setPlanError(null);
        try {
            const p = await api.getSetupPlan();
            setPlan(p);
            setSelection((s) => (reseed || !s ? initialSelection(p) : s));
        } catch (err) {
            setPlanError(errorText(err));
        }
    }, []);

    useEffect(() => {
        void loadPlan(true);
        void refreshDownloads();
        void (async () => {
            try {
                setGpu((await api.getGpuMemory()) ?? null);
            } catch (err) {
                setGpuError(errorText(err));
            }
        })();
    }, [loadPlan, refreshDownloads]);

    // A model that finishes downloading is on this PC now: read the plan again.
    const readyIds = (status?.models ?? []).filter((m) => m.state === 'ready').map((m) => m.id).join(',');
    const firstReady = useRef(readyIds);
    useEffect(() => {
        if (readyIds === firstReady.current) return;
        firstReady.current = readyIds;
        void loadPlan(true);
    }, [readyIds, loadPlan]);

    const progress = useMemo(() => {
        const out: Record<string, SetupModelProgress> = {};
        for (const m of status?.models ?? []) out[m.id] = m;
        return out;
    }, [status]);

    const names = useMemo(() => {
        const out: Record<string, string> = {};
        for (const e of plan?.catalog ?? []) out[e.id] = e.name;
        return out;
    }, [plan]);

    const baseline = useMemo(() => (plan ? initialSelection(plan) : null), [plan]);
    const catalog = catalogOf(plan);
    const toDownload = plan && selection ? pendingDownloads(catalog, selection).filter((e) => progress[e.id]?.state !== 'downloading') : [];
    const downloadGb = toDownload.reduce((sum, e) => sum + (e.size_gb ?? 0), 0);
    const staged = !!plan && !!selection && !!baseline
        && (!sameSet(selection.ticked, baseline.ticked) || selection.defaultChat !== baseline.defaultChat || toDownload.length > 0);

    const apply = useCallback(async (acknowledgeNoFit: boolean, acknowledgeLowDisk: boolean) => {
        if (!plan || !selection) return;
        setNotice(null);
        if (!acknowledgeNoFit) {
            const big = tooBig(catalog, selection).filter((e) => !e.downloaded || e.id === selection.defaultChat);
            if (big.length > 0 && big.every((e) => e.need_gb != null)) {
                setSheet({ kind: 'no_fit', models: big });
                return;
            }
        }
        const short = shortfall(plan, selection);
        if (short && !acknowledgeLowDisk) {
            setSheet({
                kind: 'low_disk',
                message: short.drop
                    ? t('setup.summary.shortDrop', { need: formatSize(short.needGb), free: formatSize(short.freeGb), drive: driveName(plan.disk?.path ?? null) ?? t('setup.summary.theDrive'), freeUp: formatSize(short.freeUpGb), name: short.drop.name, saves: formatSize(short.drop.size_gb ?? 0) })
                    : t('setup.summary.short', { need: formatSize(short.needGb), free: formatSize(short.freeGb), drive: driveName(plan.disk?.path ?? null) ?? t('setup.summary.theDrive'), freeUp: formatSize(short.freeUpGb) }),
            });
            return;
        }
        setBusy(true);
        try {
            let latest = plan;
            if (selection.defaultChat && selection.defaultChat !== latest.chat_model.id) {
                try {
                    latest = await api.chooseSetupChatModel(selection.defaultChat, acknowledgeNoFit || noFitAcked);
                } catch (err) {
                    const detail = api.setupConfirmFrom(err);
                    if (detail) {
                        setSheet({ kind: 'server', detail });
                        return;
                    }
                    throw err;
                }
            }
            for (const change of skipChanges(latest, selection)) {
                latest = await api.skipSetupModel(change.id, change.skipped);
            }
            const ids = pendingDownloads(catalogOf(latest), selection).map((e) => e.id).filter((id) => progress[id]?.state !== 'downloading');
            setPlan(latest);
            setSelection({ ...selection, defaultChat: latest.chat_model.id });
            if (ids.length) await enqueue(ids, { acknowledgeLowDisk: true });
            setNotice({ ok: true, text: t('settings.models.applied') });
        } catch (err) {
            setNotice({ ok: false, text: errorText(err) });
        } finally {
            setBusy(false);
        }
    }, [plan, selection, catalog, noFitAcked, progress, enqueue, t]);

    const remove = useCallback(async (entry: SetupCatalogEntry) => {
        setSheet(null);
        setBusy(true);
        setNotice(null);
        try {
            const r = await api.removeSetupModel(entry.id);
            setPlan(r);
            setSelection(initialSelection(r));
            setNotice({ ok: true, text: t('settings.models.removed', { name: entry.name, size: formatSize(r.freed_gb) }) });
        } catch (err) {
            setNotice({ ok: false, text: errorText(err) });
        } finally {
            setBusy(false);
        }
    }, [t]);

    const retry = useCallback((id: string, kind: SetupModelProgress['error_kind']) => {
        void (async () => {
            try {
                if (kind === 'verify') await api.removeSetupModel(id);
                await enqueue([id]);
            } catch (err) {
                setNotice({ ok: false, text: errorText(err) });
            }
        })();
    }, [enqueue]);

    const showDownloads = !!status && (status.models?.length ?? 0) > 0 && (downloadsActive(status)
        || status.models!.some((m) => m.state !== 'skipped'));

    return (
        <>
            <section className="settings-field models-pane-section" aria-labelledby="settings-models-title">
                <span className="settings-label" id="settings-models-title">{t('settings.models.title')}</span>
                <p className="settings-hint">{t('settings.models.lede')}</p>
                {planError && <p className="settings-error" role="alert">{planError}</p>}
                {plan && !plan.lemonade.reachable && (
                    <p className="settings-hint">{t('settings.models.unavailable')}</p>
                )}
                {!plan && !planError && <p className="settings-hint"><Loader2 size={13} className="spin" aria-hidden="true" /> {t('setup.models.loading')}</p>}
                {plan && plan.lemonade.reachable && selection && (
                    <>
                        <ModelChecklist
                            plan={plan}
                            selection={selection}
                            onToggle={(id, on) => setSelection(toggle(selection, catalog, id, on))}
                            onDefaultChat={(id) => setSelection({ ...selection, defaultChat: id, ticked: catalog.find((e) => e.id === id)?.downloaded ? selection.ticked : new Set([...selection.ticked, id]) })}
                            labelledBy="settings-models-title"
                            disabled={busy}
                            progress={progress}
                            rowActions={(e) => (e.downloaded && e.id !== selection.defaultChat ? (
                                <button
                                    type="button"
                                    className="btn-secondary"
                                    onClick={() => setSheet({ kind: 'remove', entry: e })}
                                    disabled={busy || progress[e.id]?.state === 'downloading'}
                                    aria-label={t('settings.models.removeAria', { name: e.name })}
                                >
                                    {t('settings.models.remove')}
                                </button>
                            ) : null)}
                        />
                        <SelectionSummary plan={plan} selection={selection} />
                        <div className="models-apply">
                            <button type="button" className="btn-primary" disabled={!staged || busy} onClick={() => void apply(false, false)}>
                                {busy && <Loader2 size={13} className="spin" aria-hidden="true" />}
                                {busy ? t('settings.models.applying') : toDownload.length > 0
                                    ? t('settings.models.applyDownload', { size: formatSize(Math.round(downloadGb * 10) / 10) })
                                    : t('settings.models.apply')}
                            </button>
                        </div>
                    </>
                )}
                {notice && <p className={notice.ok ? 'settings-ok' : 'settings-error'} role={notice.ok ? 'status' : 'alert'}>{notice.text}</p>}
            </section>

            {(gpu?.applies || gpuError) && (
                <section className="settings-field models-pane-section" aria-labelledby="settings-gpu-title">
                    <span className="settings-label" id="settings-gpu-title">{t('gpu.settingsTitle')}</span>
                    {gpuError && <p className="settings-error" role="alert">{gpuError}</p>}
                    {gpu?.applies && <GpuMemoryPanel status={gpu} onStatus={setGpu} mode="settings" names={names} />}
                </section>
            )}

            {showDownloads && status && (
                <section className="settings-field models-pane-section" aria-labelledby="settings-downloads-title">
                    <span className="settings-label" id="settings-downloads-title">{t('settings.models.downloads')}</span>
                    <DownloadProgress
                        status={status}
                        names={names}
                        freeGb={plan?.disk?.free_gb ?? null}
                        onCancel={() => void cancelDownload()}
                        onRetry={retry}
                        onResume={(id) => void enqueue([id])}
                        onResumeAll={() => void enqueue((status.models ?? []).filter((m) => m.state === 'cancelled').map((m) => m.id))}
                    />
                    {storeError && <p className="settings-error" role="alert">{storeError}</p>}
                </section>
            )}

            {sheet?.kind === 'remove' && (
                <ConfirmSheet
                    title={t('settings.models.removeTitle', { name: sheet.entry.name })}
                    confirmLabel={t('settings.models.removeConfirm')}
                    cancelLabel={t('settings.models.keep')}
                    danger
                    onConfirm={() => void remove(sheet.entry)}
                    onCancel={() => setSheet(null)}
                >
                    <p>{isVision(sheet.entry)
                        ? t('settings.models.removeVision', { size: formatSize(sheet.entry.full_size_gb ?? 0) })
                        : t('settings.models.removeBody', { size: formatSize(sheet.entry.full_size_gb ?? 0) })}</p>
                </ConfirmSheet>
            )}
            {sheet?.kind === 'no_fit' && (
                <ConfirmSheet
                    title={sheet.models.length === 1 ? t('setup.confirm.noFitOne', { name: sheet.models[0].name }) : t('setup.confirm.noFitMany')}
                    confirmLabel={t('setup.confirm.downloadAnyway')}
                    cancelLabel={t('setup.confirm.pickSmaller')}
                    onConfirm={() => { setSheet(null); setNoFitAcked(true); void apply(true, false); }}
                    onCancel={() => setSheet(null)}
                >
                    {sheet.models.map((e) => <p key={e.id}><bdi>{e.name}</bdi>: {tooBigText(e, t)}</p>)}
                </ConfirmSheet>
            )}
            {sheet?.kind === 'low_disk' && (
                <ConfirmSheet
                    title={t('setup.confirm.lowDiskTitle')}
                    confirmLabel={t('setup.confirm.downloadAnyway')}
                    cancelLabel={t('setup.confirm.changeModels')}
                    onConfirm={() => { setSheet(null); void apply(true, true); }}
                    onCancel={() => setSheet(null)}
                >
                    <p>{sheet.message}</p>
                </ConfirmSheet>
            )}
            {sheet?.kind === 'server' && (
                <ConfirmSheet
                    title={t('setup.confirm.noFitServerTitle')}
                    confirmLabel={t('setup.confirm.useAnyway')}
                    cancelLabel={t('setup.confirm.changeModels')}
                    onConfirm={() => { setSheet(null); setNoFitAcked(true); void apply(true, false); }}
                    onCancel={() => setSheet(null)}
                >
                    <p>{sheet.detail.message}</p>
                </ConfirmSheet>
            )}
        </>
    );
}
