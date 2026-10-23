// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Check, Cloud, Cpu, Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { useModelStore, displayModelName, sameModelId } from '../../stores/modelStore';
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

function keyStatus(p: ProviderInfo): string {
    switch (p.key_source) {
        case 'environment': return `A key is set in ${p.env_var} and is in use.`;
        case 'lemonade': return 'A key is already configured on the local model server.';
        case 'stored': return 'A saved key will be used.';
        default: return 'No key yet.';
    }
}

function keyPlaceholder(p: ProviderInfo): string {
    if (p.key_source === 'environment') return 'Blank uses the environment key';
    if (p.key_source) return 'Blank keeps the saved key';
    return 'Paste API key';
}

/** Local vs Fireworks AI vs AMD LLM Gateway, keys, and the model list — the TUI's /provider. */
export function ModelSettings() {
    const active = useModelStore((s) => s.active);
    const selectModel = useModelStore((s) => s.select);
    const refreshActive = useModelStore((s) => s.refresh);
    const rememberNames = useModelStore((s) => s.rememberNames);

    const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [chosen, setChosen] = useState<ProviderId>(active?.provider ?? 'local');
    const [models, setModels] = useState<ProviderModel[] | null>(null);
    const [modelsError, setModelsError] = useState<string | null>(null);
    const [query, setQuery] = useState('');
    const [key, setKey] = useState('');
    const [amd, setAmd] = useState({ base_url: '', auth_header_name: '', auth_header_prefix: '' });
    const [busy, setBusy] = useState<string | null>(null);
    const [memory, setMemory] = useState<string | null>(null);
    const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
    const [confirmRemove, setConfirmRemove] = useState<string | null>(null);

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
    const activeProvider = active?.provider;
    useEffect(() => { if (activeProvider) setChosen(activeProvider); }, [activeProvider]);
    useEffect(() => {
        setKey('');
        setNotice(null);
        setQuery('');
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
            const r = await api.connectProvider(chosen, body);
            setKey('');
            setNotice({
                ok: true,
                text: r.remember_error
                    ? `Connected for this session only — the key could not be kept: ${r.remember_error}`
                    : r.remembered ? 'Connected. The key is kept for next time.' : 'Connected.',
            });
            await loadProviders();
        } catch (err) {
            setNotice({ ok: false, text: err instanceof Error ? err.message : String(err) });
        } finally {
            setBusy(null);
        }
    }, [amd, chosen, key, loadProviders, provider]);

    const forget = useCallback(async () => {
        setBusy('forget');
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
    const unavailable = filtered.filter((m) => m.available === false);
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
                        {m.display_name ?? displayModelName(m.id)}
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
                        <progress max={100} value={progress.percent} aria-label={`Downloading ${m.display_name ?? m.id}`} />
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
                            aria-label={`Remove ${m.display_name ?? displayModelName(m.id)}`}
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
                    <button type="button" className="btn-secondary" onClick={() => void download(m.id)} disabled={busy !== null} aria-label={`Download ${m.display_name ?? displayModelName(m.id)}`}>
                        {busy === `dl:${m.id}` ? <Loader2 size={13} className="spin" /> : null} Download
                    </button>
                ) : (
                    <button type="button" className="btn-secondary" onClick={() => void use(m.id)} disabled={busy !== null} aria-label={`Use ${m.display_name ?? displayModelName(m.id)}`}>
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
                            <span className="settings-label">API key</span>
                            <span className="settings-hint">{keyStatus(provider)}</span>
                            {provider.id === 'amd' && (
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
                            <div className="settings-row">
                                <input
                                    className="settings-input"
                                    type="password"
                                    autoComplete="off"
                                    spellCheck={false}
                                    value={key}
                                    onChange={(e) => setKey(e.target.value)}
                                    placeholder={keyPlaceholder(provider)}
                                    aria-label={`${provider.name} API key`}
                                />
                                <button type="submit" className="btn-primary" disabled={busy !== null || (!key && !provider.key_source)}>
                                    {busy === 'connect' ? <Loader2 size={13} className="spin" /> : null}
                                    {provider.models_discovered ? 'Reconnect' : 'Connect'}
                                </button>
                                {(provider.key_source === 'lemonade' || provider.key_source === 'stored') && (
                                    <button type="button" className="btn-secondary" onClick={() => void forget()} disabled={busy !== null}>
                                        Forget key
                                    </button>
                                )}
                            </div>
                            {provider.error && <p className="settings-error" role="alert">{provider.error}</p>}
                        </form>
                    )}

                    {notice && (
                        <p className={notice.ok ? 'settings-ok' : 'settings-error'} role={notice.ok ? 'status' : 'alert'}>
                            {notice.text}
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
                            <ul className="model-list">{runnable.map(renderRow)}</ul>
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
