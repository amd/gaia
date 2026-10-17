// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, Cloud, Cpu, Download, HelpCircle, KeyRound, Loader2 } from 'lucide-react';
import { Popover } from './Popover';
import './ModelPicker.css';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import {
    useModelStore, useInferencePlace, selectChipModel, displayModelName, UNKNOWN_PLACE_TITLE,
} from '../stores/modelStore';
import { STATE_LABELS, isDownloading, modelMetaLine, modelState } from './modelMeta';
import type { ProviderInfo, ProviderModel } from '../types';

interface ProviderModels {
    provider: ProviderInfo;
    models: ProviderModel[];
    error?: string;
}

const DOWNLOAD_POLL_MS = 2000;

/** Loaded (green dot), downloaded, or a download button — always with a text label. */
function StateMark({ m, onDownload, busy }: { m: ProviderModel; onDownload: () => void; busy: boolean }) {
    if (isDownloading(m)) {
        const pct = Math.round(m.download_progress?.percent ?? 0);
        return (
            <span className="model-state is-downloading" title={`Downloading, ${pct}%`}>
                <Loader2 size={12} className="spin" aria-hidden="true" /> {pct}%
            </span>
        );
    }
    const state = modelState(m);
    if (state === 'not_downloaded') {
        return (
            <button
                type="button"
                className="model-state-btn"
                onClick={(e) => { e.stopPropagation(); onDownload(); }}
                disabled={busy}
                aria-label={`Download ${m.display_name ?? m.id}`}
                title={STATE_LABELS.not_downloaded}
            >
                <Download size={13} aria-hidden="true" />
            </button>
        );
    }
    if (state === 'loaded') {
        return <span className="model-state is-loaded" role="img" aria-label={STATE_LABELS.loaded} title={STATE_LABELS.loaded} />;
    }
    return <span className="model-state is-ready" title={STATE_LABELS.downloaded}>Ready</span>;
}

/** The model the open chat runs on, where it runs, and a picker to change it. */
export function ModelChip() {
    const active = useModelStore((s) => s.active);
    const select = useModelStore((s) => s.select);
    const queued = useModelStore((s) => s.queued);
    const queue = useModelStore((s) => s.queue);
    const queueError = useModelStore((s) => s.queueError);
    const rememberNames = useModelStore((s) => s.rememberNames);
    useModelStore((s) => s.names); // re-render when friendly names arrive
    const place = useInferencePlace();
    const chipModel = useChatStore((s) => selectChipModel(active, s.sessions, s.currentSessionId));
    const turnRunning = useChatStore((s) => s.isStreaming);
    const openSettings = useChatStore((s) => s.openSettings);
    const anchor = useRef<HTMLButtonElement>(null);
    const [open, setOpen] = useState(false);
    const [groups, setGroups] = useState<ProviderModels[] | null>(null);
    const [unavailable, setUnavailable] = useState(0);
    const [error, setError] = useState<string | null>(null);
    const [pendingCloud, setPendingCloud] = useState<{ model: string; provider: ProviderInfo } | null>(null);
    const [switching, setSwitching] = useState<string | null>(null);
    const [downloading, setDownloading] = useState<string | null>(null);

    const load = useCallback(async (quiet = false) => {
        if (!quiet) {
            setError(null);
            setGroups(null);
        }
        try {
            const { providers, lemonade_error } = await api.listProviders();
            if (lemonade_error) throw new Error(lemonade_error);
            // Fireworks is always offered (with "Add your key"); the gateway once registered.
            const shown = providers.filter((p) => !p.remote || p.models_discovered || p.id === 'fireworks' || p.registered);
            const lists = await Promise.all(shown.map(async (provider) => {
                if (provider.remote && !provider.models_discovered) return { provider, models: [] };
                try {
                    return { provider, models: (await api.listProviderModels(provider.id)).models };
                } catch (err) {
                    return { provider, models: [], error: err instanceof Error ? err.message : String(err) };
                }
            }));
            rememberNames(lists.flatMap((g) => g.models));
            setUnavailable(lists.flatMap((g) => g.models).filter((m) => m.available === false).length);
            setGroups(lists.map((g) => ({ ...g, models: g.models.filter((m) => m.available !== false) })));
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        }
    }, [rememberNames]);

    useEffect(() => {
        if (open) {
            setPendingCloud(null);
            void load();
        }
    }, [open, load]);

    const anyDownloading = !!groups?.some((g) => g.models.some(isDownloading));
    useEffect(() => {
        if (!open || !anyDownloading) return;
        const t = setInterval(() => { void load(true); }, DOWNLOAD_POLL_MS);
        return () => clearInterval(t);
    }, [open, anyDownloading, load]);

    const download = useCallback(async (model: string) => {
        setDownloading(model);
        setError(null);
        try {
            await api.downloadModel(model);
            await load(true);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setDownloading(null);
        }
    }, [load]);

    const choose = useCallback(async (model: string, provider: ProviderInfo) => {
        if (provider.remote && !active?.remote && pendingCloud?.model !== model) {
            setPendingCloud({ model, provider });
            return;
        }
        setPendingCloud(null);
        if (turnRunning) {
            // The running reply keeps its model; this one starts with the next message.
            queue(model === active?.model ? null : model);
            setOpen(false);
            return;
        }
        setSwitching(model);
        setError(null);
        try {
            await select(model);
            setOpen(false);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setSwitching(null);
        }
    }, [active?.model, active?.remote, pendingCloud, queue, select, turnRunning]);

    const Icon = place?.remote ? Cloud : place ? Cpu : HelpCircle;
    const name = chipModel ? displayModelName(chipModel) : 'Model';
    const label = `${name}${place ? ` · ${place.label}` : ''}`;
    const notDownloaded = active?.downloaded === false && chipModel === active.model;
    const title = queueError
        ? queueError
        : queued
            ? `${displayModelName(queued)} is used from your next message.`
            : active?.config_error
                ? active.config_error
                : notDownloaded
                    ? `${name} isn't downloaded yet. Open the list to download it.`
                    : !place
                        ? UNKNOWN_PLACE_TITLE
                        // The server writes the model name as Markdown code; a tooltip shows backticks literally.
                        : place.description?.replace(/`/g, '')
                            ?? (place.remote ? `Runs on ${place.label} — chat history leaves this PC` : 'Runs on this PC');

    return (
        <div className="composer-chip-wrap">
            <button
                ref={anchor}
                type="button"
                className={`composer-chip${notDownloaded || queueError ? ' is-warning' : ''}`}
                onClick={() => setOpen((v) => !v)}
                aria-haspopup="dialog"
                aria-expanded={open}
                title={title}
            >
                <Icon size={13} aria-hidden="true" />
                <span className="composer-chip-label">{label}</span>
                {queued && <span className="composer-chip-next">→ {displayModelName(queued)} next</span>}
                {notDownloaded && <Download size={12} aria-label="Not downloaded" />}
                <ChevronDown size={12} aria-hidden="true" />
            </button>
            <Popover open={open} onClose={() => setOpen(false)} anchor={anchor} label="Choose a model" align="end">
                {turnRunning && (
                    <div className="popover-message" role="status">
                        This reply keeps {chipModel ? displayModelName(chipModel) : 'its model'}. A model you pick
                        here is used from your next message.
                    </div>
                )}
                {queueError && <div className="popover-message is-error" role="alert">{queueError}</div>}
                {error && <div className="popover-message is-error" role="alert">{error}</div>}
                {!groups && !error && (
                    <div className="popover-message"><Loader2 size={13} className="spin" /> Loading models…</div>
                )}
                {groups?.map(({ provider, models, error: groupError }) => (
                    <div key={provider.id}>
                        <div className="popover-heading">{provider.name}</div>
                        {groupError && <div className="popover-message is-error">{groupError}</div>}
                        {provider.remote && !provider.models_discovered && (
                            <button
                                type="button"
                                className="popover-item"
                                onClick={() => { setOpen(false); openSettings('model'); }}
                            >
                                <span className="popover-item-check"><KeyRound size={13} aria-hidden="true" /></span>
                                <span className="popover-item-body">
                                    <span className="popover-item-title">Add your key</span>
                                    <span className="popover-item-note">Connect {provider.name} in Settings</span>
                                </span>
                            </button>
                        )}
                        {!groupError && models.length === 0 && provider.models_discovered !== false && (
                            <div className="popover-message">No models yet.</div>
                        )}
                        {models.map((m) => {
                            const current = (queued ?? active?.model) === m.id;
                            const needsDownload = !provider.remote && modelState(m) === 'not_downloaded';
                            const note = provider.remote
                                ? (m.rank ? `#${m.rank} ${m.note}` : null)
                                : [modelMetaLine(m), m.rank ? `#${m.rank} ${m.note}` : null].filter(Boolean).join(' · ');
                            return (
                                <div key={m.id} className="popover-row">
                                    <button
                                        type="button"
                                        className="popover-item"
                                        onClick={() => (needsDownload ? download(m.id) : choose(m.id, provider))}
                                        disabled={switching !== null || isDownloading(m)}
                                        aria-current={current ? 'true' : undefined}
                                        title={m.placement?.warning ?? m.id}
                                    >
                                        <span className="popover-item-check" aria-hidden="true">
                                            {switching === m.id ? <Loader2 size={13} className="spin" /> : current ? <Check size={13} /> : null}
                                        </span>
                                        <span className="popover-item-body">
                                            <span className="popover-item-title">{m.display_name ?? displayModelName(m.id)}</span>
                                            {note && <span className="popover-item-note">{note}</span>}
                                            {m.placement?.warning && (
                                                <span className="popover-item-note is-warning">{m.placement.warning}</span>
                                            )}
                                        </span>
                                    </button>
                                    {!provider.remote && (
                                        <StateMark m={m} busy={downloading !== null} onDownload={() => void download(m.id)} />
                                    )}
                                </div>
                            );
                        })}
                    </div>
                ))}
                {pendingCloud && (
                    <div className="popover-message" role="alert">
                        {pendingCloud.provider.privacy_notice}{' '}
                        <button type="button" className="link-btn" onClick={() => choose(pendingCloud.model, pendingCloud.provider)}>
                            Use {displayModelName(pendingCloud.model)}
                        </button>
                    </div>
                )}
                {unavailable > 0 && (
                    <button
                        type="button"
                        className="popover-item"
                        onClick={() => { setOpen(false); openSettings('model'); }}
                    >
                        <span className="popover-item-check" />
                        <span className="popover-item-body">
                            <span className="popover-item-note">
                                {unavailable} {unavailable === 1 ? 'model isn’t' : 'models aren’t'} available on this PC — see why in Settings
                            </span>
                        </span>
                    </button>
                )}
                <div className="popover-divider" />
                <button
                    type="button"
                    className="popover-item"
                    onClick={() => { setOpen(false); openSettings('model'); }}
                >
                    <span className="popover-item-check" />
                    <span className="popover-item-body"><span className="popover-item-title">Providers and keys…</span></span>
                </button>
            </Popover>
        </div>
    );
}
