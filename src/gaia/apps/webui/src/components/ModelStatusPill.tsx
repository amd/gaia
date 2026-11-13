// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { Cloud, Cpu, HelpCircle, Loader2, RotateCw, ScrollText, Settings } from 'lucide-react';
import { Popover } from './Popover';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { useLogsStore } from '../stores/logsStore';
import { useModelStore, useInferencePlace, displayModelName, sameModelId, UNKNOWN_PLACE_TITLE } from '../stores/modelStore';
import { ctxLabel } from './modelMeta';
import { useT, type Translate } from '../i18n/useT';
import { ref, type MessageKey } from '../i18n/registry';
import './ModelPicker.css';
import type { LocalModelStatus, ModelPlacement } from '../types';

const POLL_MS = 3000;

type Dot = 'loaded' | 'warming' | 'stopped' | 'idle';

const STATE_TEXT: Record<LocalModelStatus['state'], MessageKey> = {
    loaded: 'modelStatus.state.loaded',
    warming: 'modelStatus.state.warming',
    idle: 'modelStatus.state.idle',
    stopped: 'modelStatus.state.stopped',
};

const PILL_ARIA: Record<Dot, MessageKey> = {
    loaded: 'modelStatus.pillAria.loaded',
    warming: 'modelStatus.pillAria.warming',
    idle: 'modelStatus.pillAria.idle',
    stopped: 'modelStatus.pillAria.stopped',
};

// Sizes go in as string params, so an rtl line keeps each one whole and left to right.
// The no-break space keeps the dot with the part after it when the line wraps.
function memoryText(s: LocalModelStatus, t: Translate): string | null {
    const m = s.memory;
    if (!m) return s.memory_error;
    if (!m.model_gb && m.free_gb == null) return t('modelStatus.memoryTotal', { size: `${m.total_gb} GB`, source: m.source });
    return t.compose((say) => [
        m.model_gb ? say('modelStatus.memoryUsed', { size: `~${m.model_gb} GB` }) : null,
        m.free_gb != null ? say('modelStatus.memoryFree', { size: `${m.free_gb} GB` }) : null,
    ].filter(Boolean).join(' ·\u00A0'));
}

function placementLabel(p: ModelPlacement, t: Translate): string | null {
    if (!p.label) return null;
    return p.source === 'expected' ? t('modelStatus.expected', { label: p.label }) : p.label;
}

/** The sidebar's "Local · model" pill: live state at a glance, details and Restart on click. */
export function ModelStatusPill() {
    const t = useT();
    const active = useModelStore((s) => s.active);
    useModelStore((s) => s.names);
    const place = useInferencePlace();
    const systemStatus = useChatStore((s) => s.systemStatus);
    const openSettings = useChatStore((s) => s.openSettings);
    const showServerLog = useLogsStore((s) => s.show);
    const anchor = useRef<HTMLButtonElement>(null);
    const [open, setOpen] = useState(false);
    const [status, setStatus] = useState<LocalModelStatus | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [restarting, setRestarting] = useState(false);
    const [confirming, setConfirming] = useState(false);

    const load = useCallback(async () => {
        try {
            setStatus(await api.getLocalModelStatus());
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        }
    }, []);

    useEffect(() => {
        if (!open) return;
        void load();
        const t = setInterval(() => { void load(); }, POLL_MS);
        return () => clearInterval(t);
    }, [open, load]);

    const restart = useCallback(async (confirm: boolean) => {
        setConfirming(false);
        setRestarting(true);
        setError(null);
        try {
            await api.restartLocalModel(confirm);
            await load();
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setRestarting(false);
        }
    }, [load]);

    // At a glance, from the status the app already polls; the popover reads the full report.
    // Green only for the model the pill names — another resident model means a load is due.
    const dot: Dot = !systemStatus
        ? 'idle'
        : !systemStatus.lemonade_running
            ? 'stopped'
            : sameModelId(systemStatus.model_loaded, active?.model) ? 'loaded' : 'idle';
    const Icon = place?.remote ? Cloud : place ? Cpu : HelpCircle;
    const name = active?.model ? displayModelName(active.model) : null;
    const text = [place?.label, name].filter(Boolean).join(' · ') || t('modelStatus.fallbackName');
    const title = !place
        ? UNKNOWN_PLACE_TITLE
        : place.description ?? (place.remote ? t('modelStatus.sentTo', { name: place.label }) : t('modelStatus.runsHere'));

    const s = status;
    const busy = restarting || !!s?.restarting;
    const notOurs = !!s && s.owner !== 'gaia';
    // GAIA never starts a server it doesn't run, so there is nothing to offer then.
    const canRestart = !!s && !(notOurs && s.state === 'stopped');
    return (
        <div className="sb-location-wrap">
            <button
                ref={anchor}
                type="button"
                className="sb-location"
                onClick={() => setOpen((v) => !v)}
                aria-haspopup="dialog"
                aria-expanded={open}
                aria-label={place?.remote ? text : t(PILL_ARIA[dot], { text })}
                title={name ? `${text}. ${title}` : title}
            >
                <Icon size={13} aria-hidden="true" />
                <span className="sb-location-text">
                    {place?.label && name && <span className="sb-location-place">{place.label}</span>}
                    <span className="sb-location-name" dir="auto">{name ?? place?.label ?? t('modelStatus.fallbackName')}</span>
                </span>
                {!place?.remote && (
                    <span className={`sb-location-dot is-${dot}`} aria-hidden="true" />
                )}
            </button>
            <Popover open={open} onClose={() => setOpen(false)} anchor={anchor} label={t('modelStatus.label')} placement="above">
                <div className="model-status">
                    {!s && !error && <span><Loader2 size={13} className="spin" /> {t('modelStatus.checking')}</span>}
                    {s && (
                        <>
                            <div className="model-status-head">
                                <span className={`model-status-dot is-${s.state}`} aria-hidden="true" />
                                <span role="status">
                                    {s.state === 'loaded' && s.busy_reason === 'request' ? t('modelStatus.state.loadedAnswering') : t(STATE_TEXT[s.state])}
                                </span>
                            </div>
                            {s.active_remote && (
                                <span className="settings-hint">
                                    {t('modelStatus.cloudLine', { name: s.active_display_name ?? s.active_model ?? '—' })}
                                </span>
                            )}
                            <dl className="model-status-grid">
                                <dt>{t('modelStatus.model')}</dt>
                                <dd title={s.model ?? s.active_model ?? undefined}>
                                    <bdi>{s.display_name ?? (s.active_remote ? '—' : s.active_display_name) ?? '—'}</bdi>
                                </dd>
                                {s.placement && (<><dt>{t('modelStatus.runsOn')}</dt><dd><bdi>{placementLabel(s.placement, t)}</bdi></dd></>)}
                                {s.state !== 'stopped' && (<><dt>{t('modelStatus.memory')}</dt><dd>{memoryText(s, t) ?? t('modelStatus.unknown')}</dd></>)}
                                {s.ctx_size != null && (<><dt>{t('modelStatus.context')}</dt><dd>{t('settings.ctx.tokens', { size: ctxLabel(s.ctx_size) })}</dd></>)}
                            </dl>
                            {s.placement?.warning && <span className="model-status-warning" role="alert">{s.placement.warning}</span>}
                            {s.error && (
                                <span className="settings-error" role="alert" title={s.error_detail ?? undefined}>{s.error}</span>
                            )}
                            {s.restart_error && <span className="settings-error" role="alert">{s.restart_error}</span>}
                        </>
                    )}
                    {error && <span className="settings-error" role="alert">{error}</span>}
                    {confirming && s && (
                        <div className="model-status-confirm" role="alert">
                            <span>
                                {t('modelStatus.confirm', { name: s.display_name ?? ref('modelStatus.theModel'), url: s.base_url ?? '—' })}
                            </span>
                            <div className="model-status-actions">
                                <button type="button" className="btn-secondary" onClick={() => setConfirming(false)}>{t('modelStatus.cancel')}</button>
                                <button type="button" className="btn-primary" onClick={() => void restart(true)}>{t('modelStatus.reloadIt')}</button>
                            </div>
                        </div>
                    )}
                    <div className="model-status-actions">
                        <button
                            type="button"
                            className="btn-secondary"
                            onClick={() => { setOpen(false); openSettings('model'); }}
                        >
                            <Settings size={13} aria-hidden="true" /> {t('modelStatus.settings')}
                        </button>
                        {!s?.active_remote && (
                            <button
                                type="button"
                                className="btn-secondary"
                                onClick={() => { setOpen(false); showServerLog('model_server'); }}
                            >
                                <ScrollText size={13} aria-hidden="true" /> {t('logs.serverLog')}
                            </button>
                        )}
                        {canRestart && <button
                            type="button"
                            className="btn-secondary"
                            onClick={() => (notOurs ? setConfirming(true) : void restart(false))}
                            disabled={busy || !s || s.busy || (s.active_remote && s.state !== 'loaded')}
                            title={s?.busy
                                ? (s.busy_reason === 'warm_up'
                                    ? t('modelStatus.busyWarmUp')
                                    : t('modelStatus.busyRequest'))
                                : s?.state === 'stopped'
                                    ? t('modelStatus.startServer')
                                    : t('modelStatus.reload')}
                        >
                            {busy ? <Loader2 size={13} className="spin" /> : <RotateCw size={13} aria-hidden="true" />}
                            {busy ? t('modelStatus.restarting') : t('modelStatus.restart')}
                        </button>}
                    </div>
                </div>
            </Popover>
        </div>
    );
}
