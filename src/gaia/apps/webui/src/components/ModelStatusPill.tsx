// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { Cloud, Cpu, HelpCircle, Loader2, RotateCw, Settings } from 'lucide-react';
import { Popover } from './Popover';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { useModelStore, useInferencePlace, displayModelName, UNKNOWN_PLACE_TITLE } from '../stores/modelStore';
import { ctxLabel, placementText } from './modelMeta';
import './ModelPicker.css';
import type { LocalModelStatus } from '../types';

const POLL_MS = 3000;

type Dot = 'loaded' | 'warming' | 'stopped' | 'idle';

const STATE_TEXT: Record<LocalModelStatus['state'], string> = {
    loaded: 'Loaded',
    warming: 'Warming up',
    idle: 'Not loaded — it loads with your next message',
    stopped: 'Model server stopped',
};

function memoryText(s: LocalModelStatus): string | null {
    const m = s.memory;
    if (!m) return s.memory_error;
    const used = m.model_gb ? `~${m.model_gb} GB used by the model` : null;
    const free = m.free_gb != null ? `${m.free_gb} GB free` : null;
    return [used, free].filter(Boolean).join(' · ') || `${m.total_gb} GB (${m.source})`;
}

/** The sidebar's "Local · model" pill: live state at a glance, details and Restart on click. */
export function ModelStatusPill() {
    const active = useModelStore((s) => s.active);
    useModelStore((s) => s.names);
    const place = useInferencePlace();
    const systemStatus = useChatStore((s) => s.systemStatus);
    const openSettings = useChatStore((s) => s.openSettings);
    const anchor = useRef<HTMLButtonElement>(null);
    const [open, setOpen] = useState(false);
    const [status, setStatus] = useState<LocalModelStatus | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [restarting, setRestarting] = useState(false);

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

    const restart = useCallback(async () => {
        setRestarting(true);
        setError(null);
        try {
            await api.restartLocalModel();
            await load();
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setRestarting(false);
        }
    }, [load]);

    // At a glance, from the status the app already polls; the popover reads the full report.
    const dot: Dot = !systemStatus
        ? 'idle'
        : !systemStatus.lemonade_running ? 'stopped' : systemStatus.model_loaded ? 'loaded' : 'idle';
    const Icon = place?.remote ? Cloud : place ? Cpu : HelpCircle;
    const name = active?.model ? displayModelName(active.model) : null;
    const text = [place?.label, name].filter(Boolean).join(' · ') || 'Model';
    const title = !place
        ? UNKNOWN_PLACE_TITLE
        : place.description ?? (place.remote ? `Chat history is sent to ${place.label}` : 'Runs on this PC');

    const s = status;
    const busy = restarting || !!s?.restarting;
    return (
        <div className="sb-location-wrap">
            <button
                ref={anchor}
                type="button"
                className="sb-location"
                onClick={() => setOpen((v) => !v)}
                aria-haspopup="dialog"
                aria-expanded={open}
                aria-label={place?.remote ? text : `${text}, model ${dot === 'idle' ? 'not loaded' : dot}`}
                title={title}
            >
                <Icon size={13} aria-hidden="true" />
                <span className="sb-location-text">{text}</span>
                {!place?.remote && (
                    <span className={`sb-location-dot is-${dot}`} aria-hidden="true" />
                )}
            </button>
            <Popover open={open} onClose={() => setOpen(false)} anchor={anchor} label="Model status" placement="above">
                <div className="model-status">
                    {!s && !error && <span><Loader2 size={13} className="spin" /> Checking the model…</span>}
                    {s && (
                        <>
                            <div className="model-status-head">
                                <span className={`model-status-dot is-${s.state}`} aria-hidden="true" />
                                <span role="status">
                                    {STATE_TEXT[s.state]}{s.state === 'loaded' && s.busy ? ' · answering' : ''}
                                </span>
                            </div>
                            {s.active_remote && (
                                <span className="settings-hint">
                                    New messages use {s.active_display_name} in the cloud. Local model below.
                                </span>
                            )}
                            <dl className="model-status-grid">
                                <dt>Model</dt>
                                <dd title={s.model ?? s.active_model ?? undefined}>
                                    {s.display_name ?? (s.active_remote ? '—' : s.active_display_name) ?? '—'}
                                </dd>
                                {s.placement && (<><dt>Runs on</dt><dd>{placementText(s.placement)}</dd></>)}
                                {s.state !== 'stopped' && (<><dt>Memory</dt><dd>{memoryText(s) ?? 'unknown'}</dd></>)}
                                {s.ctx_size != null && (<><dt>Context window</dt><dd>{ctxLabel(s.ctx_size)} tokens</dd></>)}
                            </dl>
                            {s.placement?.warning && <span className="model-status-warning" role="alert">{s.placement.warning}</span>}
                            {s.error && (
                                <span className="settings-error" role="alert" title={s.error_detail ?? undefined}>{s.error}</span>
                            )}
                            {s.restart_error && <span className="settings-error" role="alert">{s.restart_error}</span>}
                        </>
                    )}
                    {error && <span className="settings-error" role="alert">{error}</span>}
                    <div className="model-status-actions">
                        <button
                            type="button"
                            className="btn-secondary"
                            onClick={() => { setOpen(false); openSettings('model'); }}
                        >
                            <Settings size={13} aria-hidden="true" /> Model settings
                        </button>
                        <button
                            type="button"
                            className="btn-secondary"
                            onClick={() => void restart()}
                            disabled={busy || !s || s.busy || (s.active_remote && s.state !== 'loaded')}
                            title={s?.busy
                                ? 'GAIA is answering with this model. Restart it once the reply finishes.'
                                : s?.state === 'stopped'
                                    ? 'Start the model server and load the model'
                                    : 'Reload the model at its context size'}
                        >
                            {busy ? <Loader2 size={13} className="spin" /> : <RotateCw size={13} aria-hidden="true" />}
                            {busy ? 'Restarting…' : 'Restart'}
                        </button>
                    </div>
                </div>
            </Popover>
        </div>
    );
}
