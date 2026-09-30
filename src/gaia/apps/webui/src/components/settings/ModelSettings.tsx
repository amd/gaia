// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Check, Cloud, Cpu, Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import { useModelStore, shortModelName } from '../../stores/modelStore';
import type { ProviderId, ProviderInfo, ProviderModel } from '../../types';

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
    const systemStatus = useChatStore((s) => s.systemStatus);
    const activeDevice = useChatStore((s) => s.activeDevice);
    const setActiveDevice = useChatStore((s) => s.setActiveDevice);
    const detectedDevices = useChatStore((s) => s.detectedDevices);

    const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [chosen, setChosen] = useState<ProviderId>(active?.provider ?? 'local');
    const [models, setModels] = useState<ProviderModel[] | null>(null);
    const [modelsError, setModelsError] = useState<string | null>(null);
    const [query, setQuery] = useState('');
    const [key, setKey] = useState('');
    const [amd, setAmd] = useState({ base_url: '', auth_header_name: '', auth_header_prefix: '' });
    const [busy, setBusy] = useState<string | null>(null);
    const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

    const provider = providers?.find((p) => p.id === chosen) ?? null;
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

    const loadModels = useCallback(async (id: ProviderId) => {
        setModels(null);
        setModelsError(null);
        try {
            setModels((await api.listProviderModels(id)).models);
        } catch (err) {
            setModelsError(err instanceof Error ? err.message : String(err));
        }
    }, []);

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
            setNotice({ ok: true, text: `New messages use ${shortModelName(model)}. It is remembered across restarts.` });
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
            setNotice({ ok: true, text: `Downloading ${model}. Progress shows below.` });
        } catch (err) {
            setNotice({ ok: false, text: err instanceof Error ? err.message : String(err) });
        } finally {
            setBusy(null);
        }
    }, []);

    const progress = systemStatus?.download_progress ?? null;
    useEffect(() => {
        if (progress?.state === 'complete' && chosen === 'local') void loadModels('local');
    }, [progress?.state, chosen, loadModels]);

    const filtered = useMemo(() => {
        const q = query.trim().toLowerCase();
        return (models ?? []).filter((m) => !q || m.id.toLowerCase().includes(q));
    }, [models, query]);

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
                        className={`provider-tab${chosen === p.id ? ' is-active' : ''}`}
                        onClick={() => setChosen(p.id)}
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

                    {provider.id === 'local' && detectedDevices.length > 1 && (
                        <div className="settings-field">
                            <span className="settings-label">Run local models on</span>
                            <div className="segmented" role="radiogroup" aria-label="Device">
                                {detectedDevices.map((d) => (
                                    <button
                                        key={d}
                                        type="button"
                                        role="radio"
                                        aria-checked={activeDevice === d}
                                        className={activeDevice === d ? 'is-active' : ''}
                                        onClick={() => setActiveDevice(d)}
                                    >
                                        {d.toUpperCase()}
                                    </button>
                                ))}
                            </div>
                            <span className="settings-hint">Applies to new chats.</span>
                        </div>
                    )}

                    {provider.remote && (
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

                    {progress && (progress.state === 'downloading' || progress.state === 'starting') && (
                        <div className="settings-progress" role="status">
                            <span>Downloading {progress.model_name} — {Math.round(progress.percent)}%</span>
                            <progress max={100} value={progress.percent} aria-label={`Downloading ${progress.model_name}`} />
                        </div>
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
                            <ul className="model-list">
                                {filtered.map((m) => {
                                    const current = active?.model === m.id;
                                    return (
                                        <li key={m.id} className={`model-row${current ? ' is-current' : ''}`}>
                                            <div className="model-row-text">
                                                <span className="model-row-name">
                                                    {shortModelName(m.id)}
                                                    {m.rank && <span className="model-row-rank">#{m.rank} {m.note}</span>}
                                                </span>
                                                {m.evidence && <span className="model-row-meta">Measured: {m.evidence}</span>}
                                                {m.context_length && (
                                                    <span className="model-row-meta">Context {m.context_length.toLocaleString()} tokens</span>
                                                )}
                                            </div>
                                            {current ? (
                                                <span className="model-row-current"><Check size={13} aria-hidden="true" /> In use</span>
                                            ) : !m.downloaded ? (
                                                <button type="button" className="btn-secondary" onClick={() => void download(m.id)} disabled={busy !== null}>
                                                    {busy === `dl:${m.id}` ? <Loader2 size={13} className="spin" /> : null} Download
                                                </button>
                                            ) : (
                                                <button type="button" className="btn-secondary" onClick={() => void use(m.id)} disabled={busy !== null}>
                                                    {busy === m.id ? <Loader2 size={13} className="spin" /> : null} Use
                                                </button>
                                            )}
                                        </li>
                                    );
                                })}
                            </ul>
                        </div>
                    )}
                </section>
            )}
        </div>
    );
}
