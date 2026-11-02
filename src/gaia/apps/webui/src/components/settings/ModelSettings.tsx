// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, Cloud, Cpu, Loader2, Trash2 } from 'lucide-react';
import * as api from '../../services/api';
import { useModelStore, displayModelName, distinctModelLabels, sameModelId } from '../../stores/modelStore';
import { useChatStore } from '../../stores/chatStore';
import { useT, type Translate } from '../../i18n/useT';
import { AddCustomModel } from './AddCustomModel';
import { ErrorNotice, isServerDown } from './ErrorNotice';
import { LocalModelsSections } from './LocalModelsSections';
import { ModelInUse } from './ModelInUse';
import { ModelRow } from './ModelRow';
import { SettingsDisclosure } from './SettingsDisclosure';
import { isDownloading } from '../modelMeta';
import '../ModelPicker.css';
import { CloudPrices } from './CloudPrices';
import { SpendingLimit } from './SpendingLimit';
import './CloudSpending.css';
import type { ProviderId, ProviderInfo, ProviderModel } from '../../types';

const DOWNLOAD_POLL_MS = 2000;

function maskedKey(p: ProviderInfo, t: Translate): string {
    if (p.key_source === 'environment') return t('settings.key.setIn', { name: p.env_var ?? '' });
    return `••••••••••••••••••••${p.key_hint ? ` ${p.key_hint}` : ''}`;
}

/** Local vs Fireworks AI vs AMD LLM Gateway, keys, and the model list — the TUI's /provider. */
export function ModelSettings() {
    const t = useT();
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
    const [justAdded, setJustAdded] = useState(false);
    const [inUseRow, setInUseRow] = useState<ProviderModel | null>(null);
    const [downloadsSlot, setDownloadsSlot] = useState<HTMLDivElement | null>(null);

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
        setJustAdded(false);
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
                    ? t('settings.key.sessionOnly', { reason: r.remember_error })
                    : r.known_models ? t('settings.key.knownModels', { name: provider.name })
                    : r.remembered ? t('settings.key.kept') : t('settings.key.connectedDone'),
            });
            await loadProviders();
        } catch (err) {
            if (mounted.current && chosenRef.current === chosen) {
                setNotice({ ok: false, text: err instanceof Error ? err.message : String(err) });
            }
        } finally {
            if (mounted.current) setBusy(null);
        }
    }, [amd, chosen, key, loadProviders, provider, t]);

    const forget = useCallback(async () => {
        setBusy('forget');
        setConfirmForget(false);
        setNotice(null);
        try {
            await api.forgetProviderKey(chosen);
            setNotice({ ok: true, text: t('settings.key.cleared') });
            await loadProviders();
            await refreshActive();
        } catch (err) {
            setNotice({ ok: false, text: err instanceof Error ? err.message : String(err) });
        } finally {
            setBusy(null);
        }
    }, [chosen, loadProviders, refreshActive, t]);

    const use = useCallback(async (model: string) => {
        setBusy(model);
        setNotice(null);
        try {
            await selectModel(model);
            setNotice({ ok: true, text: t('settings.model.switched', { name: displayModelName(model) }) });
            await loadModels(chosen, true);
        } catch (err) {
            setNotice({ ok: false, text: err instanceof Error ? err.message : String(err) });
        } finally {
            setBusy(null);
        }
    }, [chosen, loadModels, selectModel, t]);

    const download = useCallback(async (model: string) => {
        setBusy(`dl:${model}`);
        setNotice(null);
        setJustAdded(false);
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
            const removed = r.cleared.includes('last_model') ? 'settings.model.removedLast'
                : r.cleared.includes('default_model') ? 'settings.model.removedDefault' : 'settings.model.removed';
            setNotice({ ok: true, text: t(removed, { name: r.display_name }) });
            await loadModels('local', true);
            await refreshActive();
        } catch (err) {
            setNotice({ ok: false, text: err instanceof Error ? err.message : String(err) });
        } finally {
            setBusy(null);
        }
    }, [loadModels, refreshActive, t]);

    const added = useCallback(async (name: string) => {
        setNotice({ ok: true, text: t('settings.model.added', { name }) });
        setJustAdded(true);
        await loadModels('local', true);
    }, [loadModels, t]);

    // Poll while a download runs so its row shows progress, then its new state.
    const anyDownloading = !!models?.some(isDownloading);
    // A finished download can be the model in use, so "Not downloaded" must clear.
    useEffect(() => {
        if (anyDownloading) return () => { void refreshActive(); };
    }, [anyDownloading, refreshActive]);
    useEffect(() => {
        if (chosen !== 'local' || !anyDownloading) return;
        const t = setInterval(() => { void loadModels('local', true); }, DOWNLOAD_POLL_MS);
        return () => clearInterval(t);
    }, [anyDownloading, chosen, loadModels]);

    // Keeps the in-use model's device and size on screen while another provider's tab is open.
    const activeModel = active?.model;
    const listedInUse = models?.find((m) => sameModelId(activeModel, m.id));
    useEffect(() => {
        if (listedInUse) setInUseRow(listedInUse);
    }, [listedInUse]);

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
    const searching = !!query.trim();
    const isCurrent = (m: ProviderModel) => sameModelId(active?.model, m.id);
    const local = provider?.id === 'local';
    const onThisPc = visibleRunnable.filter((m) => m.downloaded).sort((a, b) => Number(isCurrent(b)) - Number(isCurrent(a)));
    const toDownload = visibleRunnable.filter((m) => !m.downloaded);
    const readyModels = (models ?? []).filter((m) => m.available !== false && m.downloaded);
    const ranked = readyModels.filter(inCollapsedView);
    const switchChoices = chosen === 'fireworks' && ranked.some((m) => m.rank !== null) ? ranked : readyModels;

    const renderRow = (m: ProviderModel) => (
        <ModelRow
            key={m.id}
            model={m}
            label={labels[m.id]}
            current={isCurrent(m)}
            remote={!!provider?.remote}
            busy={busy}
            confirmingRemove={confirmRemove === m.id}
            onUse={() => void use(m.id)}
            onDownload={() => void download(m.id)}
            onAskRemove={() => setConfirmRemove(m.id)}
            onKeep={() => setConfirmRemove(null)}
            onRemove={() => void remove(m.id)}
        />
    );

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">{t('settings.section.model')}</h2>

            <ModelInUse
                row={listedInUse ?? (sameModelId(inUseRow?.id, active?.model) ? inUseRow : null)}
                providerName={providers?.find((p) => p.id === active?.provider)?.name ?? null}
                choices={connected ? switchChoices : []}
                labels={labels}
                disabled={busy !== null}
                onSwitch={(model) => void use(model)}
            />

            <div className="provider-tabs" role="radiogroup" aria-label={t('settings.model.whereRuns')}>
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
                        {p.remote && p.models_discovered && <span className="provider-tab-dot" aria-label={t('settings.key.connectedDot')} />}
                    </button>
                ))}
            </div>

            {loadError && (
                <ErrorNotice message={loadError} onRetry={() => { void loadProviders(); void loadModels(chosen); }} />
            )}

            {provider && (
                <section className="settings-card" aria-label={provider.name}>
                    {provider.remote && <p className="settings-note"><bdi>{provider.privacy_notice}</bdi></p>}

                    <details className="settings-more">
                        <summary><ChevronDown size={13} className="settings-disclosure-chevron" aria-hidden="true" /> {t('settings.model.howItWorks')}</summary>
                        <p className="settings-hint">{t('settings.model.lede')}</p>
                        {local && <p className="settings-hint"><bdi>{provider.privacy_notice}</bdi></p>}
                        {local && <p className="settings-hint">{t('settings.model.devices')}</p>}
                        {local && memory && <p className="settings-hint">{t('settings.model.memory', { memory })}</p>}
                    </details>

                    {provider.remote && !connected && (
                        <div className="settings-field">
                            <span className="settings-label">{t('settings.model.prices')}</span>
                            <CloudPrices provider={provider.id} />
                        </div>
                    )}

                    {offline(provider) && (
                        <div className="settings-field">
                            <p className="settings-error" role="alert">{provider.network?.detail}</p>
                            <button type="button" className="btn-secondary" onClick={() => void loadProviders()}>
                                {t('settings.key.checkAgain')}
                            </button>
                        </div>
                    )}

                    {provider.remote && !offline(provider) && (
                        <form
                            className="settings-field"
                            onSubmit={(e) => { e.preventDefault(); void connect(); }}
                        >
                            <div className="settings-row settings-row-between">
                                <span className="settings-label">{t('settings.key.title')}</span>
                                {provider.key_source && (
                                    <span className={`settings-key-status ${connected ? 'settings-ok' : 'settings-error'}`}>
                                        {connected ? t('settings.key.connected') : t('settings.key.notConnected')}
                                    </span>
                                )}
                            </div>
                            {!provider.key_source && (
                                <span className="settings-hint">
                                    {provider.key_state === 'waiting_for_os_permission'
                                        ? provider.key_message ?? t('settings.key.waitingOs')
                                        : t('settings.key.none')}
                                </span>
                            )}
                            {provider.id === 'amd' && (!provider.key_source || replacingKey) && (
                                <div className="settings-grid">
                                    <input
                                        className="settings-input"
                                        placeholder={t('settings.key.gatewayPlaceholder')}
                                        value={amd.base_url}
                                        onChange={(e) => setAmd({ ...amd, base_url: e.target.value })}
                                        aria-label={t('settings.key.gatewayUrl')}
                                    />
                                    <input
                                        className="settings-input"
                                        placeholder={t('settings.key.headerPlaceholder')}
                                        value={amd.auth_header_name}
                                        onChange={(e) => setAmd({ ...amd, auth_header_name: e.target.value })}
                                        aria-label={t('settings.key.headerName')}
                                    />
                                </div>
                            )}
                            {provider.key_source && !replacingKey ? (
                                <div className="settings-row">
                                    <input
                                        className="settings-input settings-key-masked"
                                        readOnly
                                        value={maskedKey(provider, t)}
                                        aria-label={t('settings.key.savedAria', { name: provider.name })}
                                    />
                                    {!connected && (
                                        <button type="submit" className="btn-primary" disabled={busy !== null}>
                                            {busy === 'connect' ? <Loader2 size={13} className="spin" /> : null}
                                            {t('settings.key.reconnect')}
                                        </button>
                                    )}
                                    {provider.key_source !== 'environment' && (confirmForget ? (
                                        <>
                                            <span className="settings-hint">{t('settings.key.removeAsk')}</span>
                                            <button type="button" className="btn-danger" onClick={() => void forget()} disabled={busy !== null}>{t('settings.models.remove')}</button>
                                            <button type="button" className="btn-secondary" onClick={() => setConfirmForget(false)}>{t('settings.models.keep')}</button>
                                        </>
                                    ) : (
                                        <>
                                            <button type="button" className="btn-secondary" onClick={() => setReplacingKey(true)} disabled={busy !== null}>{t('settings.key.replace')}</button>
                                            <button
                                                type="button"
                                                className="btn-secondary settings-key-remove"
                                                onClick={() => setConfirmForget(true)}
                                                disabled={busy !== null}
                                                aria-label={t('settings.key.remove')}
                                                title={t('settings.key.remove')}
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
                                        placeholder={replacingKey ? t('settings.key.pasteNew') : t('settings.key.paste')}
                                        aria-label={t('settings.key.fieldAria', { name: provider.name })}
                                    />
                                    <button type="submit" className="btn-primary" disabled={busy !== null || !key}>
                                        {busy === 'connect' ? <Loader2 size={13} className="spin" /> : null}
                                        {replacingKey ? t('settings.key.save') : t('settings.key.connect')}
                                    </button>
                                    {replacingKey && (
                                        <button type="button" className="btn-secondary" onClick={() => { setKey(''); setReplacingKey(false); }} disabled={busy !== null}>
                                            {t('settings.key.cancel')}
                                        </button>
                                    )}
                                </div>
                            )}
                            {provider.error && <p className="settings-error" role="alert">{provider.error}</p>}
                        </form>
                    )}

                    {notice?.ok && <p className="settings-ok" role="status">{notice.text}</p>}
                    {notice && !notice.ok && <ErrorNotice message={notice.text} />}

                    {provider.known_models && (
                        <p className="settings-hint" role="status">
                            {t('settings.key.knownList', { name: provider.name })}
                        </p>
                    )}

                    {local && <div className="models-downloads-slot" ref={setDownloadsSlot} />}

                    {connected && (
                        <div className="settings-field">
                            <span className="settings-label">{t('settings.model.models')}</span>
                            {(models?.length ?? 0) > 6 && (
                                <input
                                    className="settings-input"
                                    type="search"
                                    value={query}
                                    onChange={(e) => setQuery(e.target.value)}
                                    placeholder={t('settings.model.search')}
                                    aria-label={t('settings.model.search')}
                                />
                            )}
                            {modelsError && !(loadError && isServerDown(modelsError)) && <ErrorNotice message={modelsError} />}
                            {!models && !modelsError && <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.loading')}</p>}
                            {models && filtered.length === 0 && !modelsError && (
                                <p className="settings-hint">{searching || !local ? t('settings.model.noMatch') : t('settings.model.noModels')}</p>
                            )}
                            {local ? (
                                <>
                                    {onThisPc.length > 0 && (
                                        <SettingsDisclosure title={t('settings.model.groupDownloaded')} count={onThisPc.length} defaultOpen forceOpen={searching}>
                                            <ul className="model-list">{onThisPc.map(renderRow)}</ul>
                                        </SettingsDisclosure>
                                    )}
                                    {toDownload.length > 0 && (
                                        <SettingsDisclosure
                                            title={t('settings.model.groupAvailable')}
                                            count={toDownload.length}
                                            defaultOpen={onThisPc.length === 0}
                                            forceOpen={searching || justAdded || toDownload.some(isDownloading)}
                                        >
                                            <ul className="model-list">{toDownload.map(renderRow)}</ul>
                                        </SettingsDisclosure>
                                    )}
                                </>
                            ) : (
                                <ul className="model-list">{visibleRunnable.map(renderRow)}</ul>
                            )}
                            {fireworksCollapsed && hiddenFireworksModels.length > 0 && (
                                <button
                                    type="button"
                                    className="btn-secondary"
                                    onClick={() => setShowAllFireworksModels(true)}
                                >
                                    {t('settings.model.showAll')}
                                </button>
                            )}
                            {chosen === 'fireworks' && showAllFireworksModels && !searching && (
                                <button
                                    type="button"
                                    className="btn-secondary"
                                    onClick={() => setShowAllFireworksModels(false)}
                                >
                                    {t('settings.model.showRecommended')}
                                </button>
                            )}
                            {unavailable.length > 0 && (
                                <SettingsDisclosure title={t('settings.model.groupUnavailable')} count={unavailable.length} forceOpen={searching}>
                                    <ul className="model-list">{unavailable.map(renderRow)}</ul>
                                </SettingsDisclosure>
                            )}
                        </div>
                    )}

                    {local && connected && (
                        <SettingsDisclosure title={t('settings.custom.title')}>
                            <AddCustomModel onAdded={(name) => void added(name)} />
                        </SettingsDisclosure>
                    )}

                    {local && connected && <LocalModelsSections downloadsSlot={downloadsSlot} />}

                    {provider.remote && <SpendingLimit />}
                </section>
            )}
        </div>
    );
}
