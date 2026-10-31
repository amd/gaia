// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Check, Cloud, Cpu, Loader2, Trash2 } from 'lucide-react';
import * as api from '../../services/api';
import { useModelStore, displayModelName, distinctModelLabels, sameModelId } from '../../stores/modelStore';
import { useChatStore } from '../../stores/chatStore';
import { ModelContextSize } from './ModelContextSize';
import { AddCustomModel } from './AddCustomModel';
import { STATE_LABELS, isDownloading, placementText } from '../modelMeta';
import '../ModelPicker.css';
import { CloudPrices } from './CloudPrices';
import { SpendingLimit } from './SpendingLimit';
import { formatPrice } from './cloudPrice';
import './CloudSpending.css';
import type { ProviderId, ProviderInfo, ProviderModel } from '../../types';

const DOWNLOAD_POLL_MS = 2000;

function maskedKey(p: ProviderInfo): string {
    if (p.key_source === 'environment') return `Set in ${p.env_var}`;
    return `••••••••••••••••••••${p.key_hint ? ` ${p.key_hint}` : ''}`;
}

/** Local vs Fireworks AI vs AMD LLM Gateway, keys, and the model list — the TUI's /provider. */
export function ModelSettings() {
    const active = useModelStore((s) => s.active);
    const selectModel = useModelStore((s) => s.select);
    const refreshActive = useModelStore((s) => s.refresh);
    const rememberNames = useModelStore((s) => s.rememberNames);

    const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const requested = useChatStore((s) => s.settingsProvider);
    const [chosen, setChosen] = useState<ProviderId>(requested ?? active?.provider ?? 'local');
    const [models, setModels] = useState<ProviderModel[] | null>(null);
    const [modelsError, setModelsError] = useState<string | null>(null);
    const [query, setQuery] = useState('');
    const [showAllFireworksModels, setShowAllFireworksModels] = useState(false);
    const [key, setKey] = useState('');
    const [replacingKey, setReplacingKey] = useState(false);
    const [confirmForget, setConfirmForget] = useState(false);
    const [amd, setAmd] = useState({ base_url: '', auth_header_name: '', auth_header_prefix: '' });
    const [busy, setBusy] = useState<string | null>(null);
    const [memory, setMemory] = useState<string | null>(null);
    const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
    const [confirmRemove, setConfirmRemove] = useState<string | null>(null);

    // A connect waiting on an OS prompt stops once the user picks another provider or leaves.
    const chosenRef = useRef(chosen);
    chosenRef.current = chosen;
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; };
    }, []);

    const provider = providers?.find((p) => p.id === chosen) ?? null;
    const offline = (p: ProviderInfo | null) => p?.network?.reachable === false;
    const connected = !!provider && (!provider.remote || !!provider.models_discovered);

    const loadProviders = useCallback(async () => {
        setLoadError(null);
        try {
            const r = await api.listProviders();
            setProviders(r.providers);
            if (r.lemonade_error) setLoadError(r.lemonade_error);
        } catch (err) {
            setLoadError(err instanceof Error ? err.message : String(err));
        }
    }, []);

    const loadModels = useCallback(async (id: ProviderId, quiet = false) => {
        if (!quiet) {
            setModels(null);
            setModelsError(null);
        }
        try {
            const r = await api.listProviderModels(id);
            rememberNames(r.models);
            setModels(r.models);
            setMemory(r.memory_gb ? `${r.memory_gb} GB (${r.memory_source})` : r.capacity_error ?? null);
        } catch (err) {
            setModelsError(err instanceof Error ? err.message : String(err));
        }
    }, [rememberNames]);

    useEffect(() => { void loadProviders(); }, [loadProviders]);
    // Ask again while the OS keychain dialog is open, so answering it is enough.
    const awaitingPermission = !!providers?.some((p) => p.key_state === 'waiting_for_os_permission');
    useEffect(() => {
        if (!awaitingPermission) return;
        const t = setInterval(() => { void loadProviders(); }, DOWNLOAD_POLL_MS);
        return () => clearInterval(t);
    }, [awaitingPermission, loadProviders]);
    const activeProvider = active?.provider;
    useEffect(() => { if (activeProvider && !requested) setChosen(activeProvider); }, [activeProvider, requested]);
    useEffect(() => {
        setKey('');
        setReplacingKey(false);
        setConfirmForget(false);
        setNotice(null);
        setQuery('');
        setShowAllFireworksModels(false);
    }, [chosen]);
    useEffect(() => {
        if (connected) void loadModels(chosen);
        else setModels(null);
    }, [chosen, connected, loadModels]);

    const connect = useCallback(async () => {
        if (!provider) return;
        setBusy('connect');
        setNotice(null);
        try {
            const body = chosen === 'amd'
                ? { api_key: key || undefined, ...Object.fromEntries(Object.entries(amd).filter(([, v]) => v !== '')) }
                : { api_key: key || undefined };
            const movedOn = () => !mounted.current || chosenRef.current !== chosen;
            const r = await api.connectProviderWaitingForOs(
                chosen,
                body,
                (waiting) => { if (!movedOn()) setNotice({ ok: true, text: waiting }); },
                movedOn,
            );
            if (movedOn()) return;
            setKey('');
            setReplacingKey(false);
            setNotice({
                ok: true,
                text: r.remember_error
                    ? `Connected for this session only — the key could not be kept: ${r.remember_error}`
                    : r.known_models ? `${provider.name} can't list its models right now. Your key still works, so GAIA connected using models it already knows.`
                    : r.remembered ? 'Connected. The key is kept for next time.' : 'Connected.',
            });
            await loadProviders();
        } catch (err) {
            if (mounted.current && chosenRef.current === chosen) {
                setNotice({ ok: false, text: err instanceof Error ? err.message : String(err) });
            }
        } finally {
            if (mounted.current) setBusy(null);
        }
    }, [amd, chosen, key, loadProviders, provider]);

    const forget = useCallback(async () => {
        setBusy('forget');
        setConfirmForget(false);
        setNotice(null);
        try {
            await api.forgetProviderKey(chosen);
            setNotice({ ok: true, text: 'Key cleared, here and for future sessions. An environment key, if set, stays active.' });
            await loadProviders();
            await refreshActive();
        } catch (err) {
            setNotice({ ok: false, text: err instanceof Error ? err.message : String(err) });
        } finally {
            setBusy(null);
        }
    }, [chosen, loadProviders, refreshActive]);

    const use = useCallback(async (model: string) => {
        setBusy(model);
        setNotice(null);
        try {
            await selectModel(model);
            setNotice({ ok: true, text: `New messages use ${displayModelName(model)}. It is remembered across restarts.` });
        } catch (err) {
            setNotice({ ok: false, text: err instanceof Error ? err.message : String(err) });
        } finally {
            setBusy(null);
        }
    }, [selectModel]);

    const download = useCallback(async (model: string) => {
        setBusy(`dl:${model}`);
        setNotice(null);
        try {
            await api.downloadModel(model);
            await loadModels('local', true);
        } catch (err) {
            setNotice({ ok: false, text: err instanceof Error ? err.message : String(err) });
        } finally {
            setBusy(null);
        }
    }, [loadModels]);

    const remove = useCallback(async (model: string) => {
        setBusy(`rm:${model}`);
        setNotice(null);
        try {
            const r = await api.removeCustomModel(model);
            setConfirmRemove(null);
            const cleared = r.cleared.includes('last_model')
                ? ' New messages use your default model.'
                : r.cleared.includes('default_model') ? " It was your default model, so GAIA's own default takes its place." : '';
            setNotice({ ok: true, text: `Removed ${r.display_name} and its download.${cleared}` });
            await loadModels('local', true);
            await refreshActive();
        } catch (err) {
            setNotice({ ok: false, text: err instanceof Error ? err.message : String(err) });
        } finally {
            setBusy(null);
        }
    }, [loadModels, refreshActive]);

    const added = useCallback(async (name: string) => {
        setNotice({ ok: true, text: `Added ${name}. Choose Download to fetch it, then Use.` });
        await loadModels('local', true);
    }, [loadModels]);

    // Poll while a download runs so its row shows progress, then its new state.
    const anyDownloading = !!models?.some(isDownloading);
    useEffect(() => {
        if (chosen !== 'local' || !anyDownloading) return;
        const t = setInterval(() => { void loadModels('local', true); }, DOWNLOAD_POLL_MS);
        return () => clearInterval(t);
    }, [anyDownloading, chosen, loadModels]);

    const filtered = useMemo(() => {
        const q = query.trim().toLowerCase();
        return (models ?? []).filter(
            (m) => !q || m.id.toLowerCase().includes(q) || (m.display_name ?? '').toLowerCase().includes(q),
        );
    }, [models, query]);
    const runnable = filtered.filter((m) => m.available !== false);
    const fireworksCollapsed = chosen === 'fireworks' && !showAllFireworksModels && !query.trim()
        && runnable.some((m) => m.rank !== null);
    const inCollapsedView = (m: ProviderModel) => m.rank !== null || m.id === active?.model;
    const visibleRunnable = fireworksCollapsed ? runnable.filter(inCollapsedView) : runnable;
    const hiddenFireworksModels = fireworksCollapsed ? runnable.filter((m) => !inCollapsedView(m)) : [];
    const unavailable = filtered.filter((m) => m.available === false);
    const labels = distinctModelLabels(models ?? []);
    const activeLocal = active && !active.remote && active.model ? active.model : null;

    const renderRow = (m: ProviderModel) => {
        const current = sameModelId(active?.model, m.id);
        const progress = isDownloading(m) ? m.download_progress : null;
        const meta = [
            placementText(m.placement),
            m.size_gb ? `${m.size_gb} GB ${m.downloaded ? 'on disk' : 'download'}` : null,
            m.tokens_per_second ? `${Math.round(m.tokens_per_second)} tok/s measured here` : null,
            !m.placement && m.context_length ? `Context ${m.context_length.toLocaleString()} tokens` : null,
        ].filter(Boolean).join(' · ');
        return (
            <li key={m.id} className={`model-row${current ? ' is-current' : ''}`}>
                <div className="model-row-text">
                    <span className="model-row-name" title={m.id}>
                        {labels[m.id]}
                        {m.rank && <span className="model-row-rank">#{m.rank} {m.note}</span>}
                        {m.state === 'loaded' && <span className="model-badge is-loaded">{STATE_LABELS.loaded}</span>}
                        {m.fits === true && m.available !== false && <span className="model-badge is-fit">Fits this PC</span>}
                        {m.custom && <span className="model-badge is-custom">Added by you</span>}
                    </span>
                    {provider?.remote && (
                        <span className={`model-row-meta${m.price ? '' : ' is-unknown-price'}`}>{formatPrice(m.price)}</span>
                    )}
                    {m.display_name && <span className="model-row-meta">{m.id}</span>}
                    {meta && <span className="model-row-meta">{meta}</span>}
                    {m.placement?.warning && <span className="model-row-meta is-warning" role="alert">{m.placement.warning}</span>}
                    {m.unavailable_reason && <span className="model-row-meta">Can't run here: {m.unavailable_reason}</span>}
                    {m.evidence && <span className="model-row-meta">Measured: {m.evidence}</span>}
                    {progress && (
                        <progress max={100} value={progress.percent} aria-label={`Downloading ${labels[m.id]}`} />
                    )}
                    {m.custom && m.source_url && (
                        <span className="model-row-meta">From {m.source_url}</span>
                    )}
                </div>
                {m.custom && !current && !progress && (
                    confirmRemove === m.id ? (
                        <span className="model-row-confirm">
                            <button type="button" className="btn-danger" onClick={() => void remove(m.id)} disabled={busy !== null}>
                                {busy === `rm:${m.id}` ? <Loader2 size={13} className="spin" /> : null}
                                {m.downloaded ? 'Remove and delete download' : 'Remove'}
                            </button>
                            <button type="button" className="btn-secondary" onClick={() => setConfirmRemove(null)} disabled={busy !== null}>
                                Keep
                            </button>
                        </span>
                    ) : (
                        <button
                            type="button"
                            className="btn-secondary"
                            onClick={() => setConfirmRemove(m.id)}
                            disabled={busy !== null}
                            aria-label={`Remove ${labels[m.id]}`}
                        >
                            Remove
                        </button>
                    )
                )}
                {current ? (
                    <span className="model-row-current"><Check size={13} aria-hidden="true" /> In use</span>
                ) : m.available === false ? null : progress ? (
                    <span className="model-row-current"><Loader2 size={13} className="spin" /> {Math.round(progress.percent)}%</span>
                ) : !m.downloaded ? (
                    <button type="button" className="btn-secondary" onClick={() => void download(m.id)} disabled={busy !== null} aria-label={`Download ${labels[m.id]}`}>
                        {busy === `dl:${m.id}` ? <Loader2 size={13} className="spin" /> : null} Download
                    </button>
                ) : (
                    <button type="button" className="btn-secondary" onClick={() => void use(m.id)} disabled={busy !== null} aria-label={`Use ${labels[m.id]}`}>
                        {busy === m.id ? <Loader2 size={13} className="spin" /> : null} Use
                    </button>
                )}
            </li>
        );
    };

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">Model</h2>
            <p className="settings-pane-lede">
                GAIA runs on this PC by default. Cloud providers are used only when you pick one of their models.
            </p>

            <div className="provider-tabs" role="radiogroup" aria-label="Where GAIA runs">
                {(providers ?? [{ id: 'local', name: 'Local', remote: false } as ProviderInfo]).map((p) => (
                    <button
                        key={p.id}
                        type="button"
                        role="radio"
                        aria-checked={chosen === p.id}
                        className={`provider-tab${chosen === p.id ? ' is-active' : ''}${offline(p) ? ' is-disabled' : ''}`}
                        onClick={() => setChosen(p.id)}
                        disabled={offline(p) && chosen !== p.id}
                        title={offline(p) ? p.network?.detail ?? undefined : undefined}
                    >
                        {p.remote ? <Cloud size={14} aria-hidden="true" /> : <Cpu size={14} aria-hidden="true" />}
                        <span>{p.name}</span>
                        {p.remote && p.models_discovered && <span className="provider-tab-dot" aria-label="connected" />}
                    </button>
                ))}
            </div>

            {loadError && <p className="settings-error" role="alert">{loadError}</p>}

            {provider && (
                <section className="settings-card" aria-label={provider.name}>
                    <p className="settings-note">{provider.privacy_notice}</p>

                    {provider.id === 'local' && (
                        <p className="settings-hint">
                            Each model runs where it was built to: GGUF models on the GPU, FLM models on the NPU.
                            {memory && ` Memory for models: ${memory}.`}
                        </p>
                    )}

                    {provider.remote && !connected && (
                        <div className="settings-field">
                            <span className="settings-label">Prices</span>
                            <CloudPrices provider={provider.id} />
                        </div>
                    )}

                    {offline(provider) && (
                        <div className="settings-field">
                            <p className="settings-error" role="alert">{provider.network?.detail}</p>
                            <button type="button" className="btn-secondary" onClick={() => void loadProviders()}>
                                Check again
                            </button>
                        </div>
                    )}

                    {provider.remote && !offline(provider) && (
                        <form
                            className="settings-field"
                            onSubmit={(e) => { e.preventDefault(); void connect(); }}
                        >
                            <div className="settings-row settings-row-between">
                                <span className="settings-label">API key</span>
                                {provider.key_source && (
                                    <span className={`settings-key-status ${connected ? 'settings-ok' : 'settings-error'}`}>
                                        {connected ? 'Connected' : 'Not connected'}
                                    </span>
                                )}
                            </div>
                            {!provider.key_source && (
                                <span className="settings-hint">
                                    {provider.key_state === 'waiting_for_os_permission'
                                        ? provider.key_message ?? 'Waiting for the OS to allow reading the saved key.'
                                        : 'No key yet.'}
                                </span>
                            )}
                            {provider.id === 'amd' && (!provider.key_source || replacingKey) && (
                                <div className="settings-grid">
                                    <input
                                        className="settings-input"
                                        placeholder="Gateway URL (default https://llm-api.amd.com/Unified/v1)"
                                        value={amd.base_url}
                                        onChange={(e) => setAmd({ ...amd, base_url: e.target.value })}
                                        aria-label="Gateway URL"
                                    />
                                    <input
                                        className="settings-input"
                                        placeholder="Auth header (default Ocp-Apim-Subscription-Key)"
                                        value={amd.auth_header_name}
                                        onChange={(e) => setAmd({ ...amd, auth_header_name: e.target.value })}
                                        aria-label="Auth header name"
                                    />
                                </div>
                            )}
                            {provider.key_source && !replacingKey ? (
                                <div className="settings-row">
                                    <input
                                        className="settings-input settings-key-masked"
                                        readOnly
                                        value={maskedKey(provider)}
                                        aria-label={`Saved ${provider.name} API key`}
                                    />
                                    {!connected && (
                                        <button type="submit" className="btn-primary" disabled={busy !== null}>
                                            {busy === 'connect' ? <Loader2 size={13} className="spin" /> : null}
                                            Reconnect
                                        </button>
                                    )}
                                    {provider.key_source !== 'environment' && (confirmForget ? (
                                        <>
                                            <span className="settings-hint">Remove this key?</span>
                                            <button type="button" className="btn-danger" onClick={() => void forget()} disabled={busy !== null}>Remove</button>
                                            <button type="button" className="btn-secondary" onClick={() => setConfirmForget(false)}>Keep</button>
                                        </>
                                    ) : (
                                        <>
                                            <button type="button" className="btn-secondary" onClick={() => setReplacingKey(true)} disabled={busy !== null}>Replace</button>
                                            <button
                                                type="button"
                                                className="btn-secondary settings-key-remove"
                                                onClick={() => setConfirmForget(true)}
                                                disabled={busy !== null}
                                                aria-label="Remove key"
                                                title="Remove key"
                                            >
                                                <Trash2 size={14} aria-hidden="true" />
                                            </button>
                                        </>
                                    ))}
                                </div>
                            ) : (
                                <div className="settings-row">
                                    <input
                                        className="settings-input"
                                        type="password"
                                        autoComplete="off"
                                        spellCheck={false}
                                        autoFocus={replacingKey}
                                        value={key}
                                        onChange={(e) => setKey(e.target.value)}
                                        placeholder={replacingKey ? 'Paste your new key' : 'Paste API key'}
                                        aria-label={`${provider.name} API key`}
                                    />
                                    <button type="submit" className="btn-primary" disabled={busy !== null || !key}>
                                        {busy === 'connect' ? <Loader2 size={13} className="spin" /> : null}
                                        {replacingKey ? 'Save key' : 'Connect'}
                                    </button>
                                    {replacingKey && (
                                        <button type="button" className="btn-secondary" onClick={() => { setKey(''); setReplacingKey(false); }} disabled={busy !== null}>
                                            Cancel
                                        </button>
                                    )}
                                </div>
                            )}
                            {provider.error && <p className="settings-error" role="alert">{provider.error}</p>}
                        </form>
                    )}

                    {notice && (
                        <p className={notice.ok ? 'settings-ok' : 'settings-error'} role={notice.ok ? 'status' : 'alert'}>
                            {notice.text}
                        </p>
                    )}

                    {provider.known_models && (
                        <p className="settings-hint" role="status">
                            {provider.name} can't list its models right now, so GAIA is showing models it already knows. Your key still works, but this list may be out of date.
                        </p>
                    )}

                    {provider.id === 'local' && activeLocal && active?.downloaded !== false && (
                        <ModelContextSize model={activeLocal} />
                    )}

                    {connected && (
                        <div className="settings-field">
                            <span className="settings-label">Models</span>
                            {(models?.length ?? 0) > 6 && (
                                <input
                                    className="settings-input"
                                    type="search"
                                    value={query}
                                    onChange={(e) => setQuery(e.target.value)}
                                    placeholder="Search models"
                                    aria-label="Search models"
                                />
                            )}
                            {modelsError && <p className="settings-error" role="alert">{modelsError}</p>}
                            {!models && !modelsError && <p className="settings-hint"><Loader2 size={13} className="spin" /> Loading…</p>}
                            {models && filtered.length === 0 && <p className="settings-hint">No models match.</p>}
                            <ul className="model-list">{visibleRunnable.map(renderRow)}</ul>
                            {fireworksCollapsed && hiddenFireworksModels.length > 0 && (
                                <button
                                    type="button"
                                    className="btn-secondary"
                                    onClick={() => setShowAllFireworksModels(true)}
                                >
                                    Show all models
                                </button>
                            )}
                            {chosen === 'fireworks' && showAllFireworksModels && !query.trim() && (
                                <button
                                    type="button"
                                    className="btn-secondary"
                                    onClick={() => setShowAllFireworksModels(false)}
                                >
                                    Show recommended models
                                </button>
                            )}
                            {unavailable.length > 0 && (
                                <details className="model-unavailable">
                                    <summary>Not available on this PC ({unavailable.length})</summary>
                                    <ul className="model-list">{unavailable.map(renderRow)}</ul>
                                </details>
                            )}
                        </div>
                    )}

                    {provider.id === 'local' && connected && <AddCustomModel onAdded={(name) => void added(name)} />}

                    {provider.remote && <SpendingLimit />}
                </section>
            )}
        </div>
    );
}
