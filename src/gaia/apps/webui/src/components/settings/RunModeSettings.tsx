// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { Cloud, Cpu, Loader2, RotateCcw } from 'lucide-react';
import * as api from '../../services/api';
import { requestErrorText } from '../../utils/requestErrors';
import { useChatStore } from '../../stores/chatStore';
import { cloudProviderName, displayModelName, syncModelAfterSettingChange, useModelStore } from '../../stores/modelStore';
import { sameListedModel } from '../modelMeta';
import { useT } from '../../i18n/useT';
import type { ProviderInfo, ProviderModel, RunMode, RunModeId, RunModesView } from '../../types';
import './RunModeSettings.css';

interface Listed {
    provider: ProviderInfo;
    models: ProviderModel[];
    error?: string;
}

const CLOUD_KEY_TARGET = '[data-settings-target="cloud-key"]';

/** Local models a mode can run now: downloaded, and runnable on this PC. */
function usable(m: ProviderModel, remote: boolean): boolean {
    return m.available !== false && (remote || m.downloaded);
}

/** Where GAIA runs: both modes side by side, what serves each capability, and Reset to defaults. */
export function RunModeSettings() {
    const t = useT();
    const openSettings = useChatStore((s) => s.openSettings);
    const jumpToSetting = useChatStore((s) => s.jumpToSetting);
    const turnRunning = useChatStore((s) => s.isStreaming);
    const activeModel = useModelStore((s) => s.active?.model);
    useModelStore((s) => s.names); // re-render when friendly names arrive
    const [view, setView] = useState<RunModesView | null>(null);
    const [listed, setListed] = useState<Listed[]>([]);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [error, setError] = useState<{ mode: RunModeId; text: string } | null>(null);
    const [busy, setBusy] = useState<RunModeId | null>(null);

    const load = useCallback(async () => {
        setLoadError(null);
        try {
            const [modes, { providers }] = await Promise.all([api.getRunModes(), api.listProviders()]);
            const lists = await Promise.all(providers
                // A cloud provider is listed once connected; Fireworks always, so its key can be asked for.
                .filter((p) => !p.remote || p.models_discovered || p.id === 'fireworks')
                .map(async (provider): Promise<Listed> => {
                    if (provider.blocked || (provider.remote && !provider.models_discovered)) return { provider, models: [] };
                    try {
                        return { provider, models: (await api.listProviderModels(provider.id)).models };
                    } catch (err) {
                        return { provider, models: [], error: requestErrorText(err) };
                    }
                }));
            useModelStore.getState().rememberNames(lists.flatMap((l) => l.models));
            setListed(lists);
            setView(modes);
        } catch (err) {
            setLoadError(requestErrorText(err));
        }
    }, []);

    // A pick made anywhere else (the composer, Agent & model) moves the mode with it.
    useEffect(() => { void load(); }, [load, activeModel]);

    const change = async (mode: RunModeId, send: () => Promise<RunModesView>) => {
        setBusy(mode);
        setError(null);
        try {
            setView(await send());
            await syncModelAfterSettingChange(activeModel);
        } catch (err) {
            setError({ mode, text: requestErrorText(err) });
        } finally {
            setBusy(null);
        }
    };

    const title = <h2 className="settings-pane-title">{t('settings.section.mode')}</h2>;
    if (loadError) {
        return (
            <div className="settings-pane">
                {title}
                <p className="settings-error" role="alert">{t('runMode.settings.loadFailed', { reason: loadError })}</p>
                <button type="button" className="btn-secondary" onClick={() => void load()}>{t('runMode.settings.retry')}</button>
            </div>
        );
    }
    if (!view) {
        return <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.loading')}</p>;
    }

    const card = (mode: RunMode) => {
        const cloud = mode.id === 'cloud';
        const lists = listed.filter((l) => l.provider.remote === cloud);
        const blocked = cloud && lists.some((l) => l.provider.blocked);
        const connected = lists.filter((l) => !l.provider.remote || l.provider.models_discovered);
        const needsKey = cloud && !blocked && connected.length === 0;
        // A key is saved but found no models: rejected, or the provider is listing none.
        const keyNotWorking = needsKey && lists.some((l) => l.provider.key_source);
        const choices = connected.map((l) => ({ ...l, models: l.models.filter((m) => usable(m, cloud)) }));
        const current = choices.flatMap((l) => l.models).find((m) => sameListedModel(mode.chat.model, m.id));
        const listsLoaded = connected.length > 0 && connected.every((l) => !l.error);
        const notDownloaded = !cloud && listsLoaded && !current;
        const canUse = !blocked && !needsKey && !notDownloaded;
        const modeName = t(`runMode.${mode.id}`);
        const provider = mode.chat.provider_name ?? cloudProviderName('fireworks');
        const Icon = cloud ? Cloud : Cpu;
        const optionLabel = (m: ProviderModel) => {
            const name = m.display_name ?? displayModelName(m.id);
            if (m.rank) return t.bare('runMode.settings.recommended', { name });
            return m.tight ? t.bare('runMode.settings.tight', { name }) : name;
        };
        const selectId = `run-mode-chat-${mode.id}`;
        return (
            <section
                key={mode.id}
                className={`settings-card run-mode-card${mode.active ? ' is-active' : ''}`}
                aria-labelledby={`run-mode-${mode.id}`}
                data-run-mode={mode.id}
            >
                <div className="run-mode-card-head">
                    <h3 className="run-mode-card-title" id={`run-mode-${mode.id}`}>
                        <Icon size={15} aria-hidden="true" /> {modeName}
                        {mode.active && <span className="run-mode-badge">{t('runMode.settings.inUse')}</span>}
                    </h3>
                    <div className="run-mode-card-actions">
                        {!mode.active && (
                            <button
                                type="button"
                                className="btn-primary"
                                disabled={!canUse || turnRunning || busy !== null}
                                onClick={() => void change(mode.id, () => api.selectRunMode(mode.id))}
                            >
                                {busy === mode.id && <Loader2 size={13} className="spin" aria-hidden="true" />}
                                {t(`runMode.settings.use.${mode.id}`)}
                            </button>
                        )}
                        <button
                            type="button"
                            className="btn-secondary"
                            disabled={!mode.chat.customized || busy !== null || (mode.active && turnRunning)}
                            title={mode.chat.customized
                                ? t('runMode.settings.resetTitle', { name: mode.chat.default_display_name })
                                : t('runMode.settings.resetNothing')}
                            onClick={() => void change(mode.id, () => api.resetRunMode(mode.id))}
                        >
                            <RotateCcw size={13} aria-hidden="true" /> {t('runMode.settings.reset')}
                        </button>
                    </div>
                </div>
                <p className="settings-hint">
                    {cloud ? t('runMode.settings.cloudLede', { provider }) : t('runMode.localNote')}
                </p>

                {blocked && (
                    <div className="settings-notice" role="status">
                        <p>{t('settings.privacy.localOnlyBlocked')}</p>
                        <button type="button" className="btn-secondary" onClick={() => openSettings('privacy')}>
                            {t('runMode.settings.openPrivacy')}
                        </button>
                    </div>
                )}
                {needsKey && (
                    <div className="settings-notice" role="status">
                        <p>{t(keyNotWorking ? 'runMode.settings.keyNotWorking' : 'runMode.settings.needsKey')}</p>
                        <button
                            type="button"
                            className="btn-secondary"
                            onClick={() => jumpToSetting({ section: 'model', target: CLOUD_KEY_TARGET }, 'fireworks')}
                        >
                            {t(keyNotWorking ? 'runMode.settings.checkKey' : 'runMode.settings.addKey')}
                        </button>
                    </div>
                )}
                {notDownloaded && (
                    <div className="settings-notice" role="status">
                        <p>{t('runMode.settings.notDownloaded', { name: mode.chat.display_name })}</p>
                        <button type="button" className="btn-secondary" onClick={() => openSettings('model', 'local')}>
                            {t('runMode.settings.download')}
                        </button>
                    </div>
                )}
                {lists.filter((l) => l.error || l.provider.error).map((l) => (
                    <p key={l.provider.id} className="settings-error" role="alert">{l.error ?? l.provider.error}</p>
                ))}
                {error?.mode === mode.id && <p className="settings-error" role="alert">{error.text}</p>}

                <dl className="run-mode-rows">
                    <div className="run-mode-row">
                        <dt><label htmlFor={selectId}>{t('runMode.settings.chat')}</label></dt>
                        <dd>
                            {choices.some((l) => l.models.length > 0) ? (
                                <select
                                    id={selectId}
                                    className="settings-input"
                                    value={current?.id ?? ''}
                                    disabled={busy !== null || (mode.active && turnRunning)}
                                    onChange={(e) => void change(mode.id, () => api.updateRunMode(mode.id, e.target.value))}
                                >
                                    {!current && (
                                        <option value="" disabled>
                                            {t.bare('runMode.settings.notListed', { name: mode.chat.display_name })}
                                        </option>
                                    )}
                                    {choices.map((l) => (cloud ? (
                                        <optgroup key={l.provider.id} label={l.provider.name}>
                                            {l.models.map((m) => <option key={m.id} value={m.id}>{optionLabel(m)}</option>)}
                                        </optgroup>
                                    ) : l.models.map((m) => <option key={m.id} value={m.id}>{optionLabel(m)}</option>)))}
                                </select>
                            ) : (
                                <span className="run-mode-value" dir="auto">{mode.chat.display_name}</span>
                            )}
                            <button
                                type="button"
                                className="link-btn run-mode-more"
                                onClick={() => openSettings('model', cloud ? 'fireworks' : 'local')}
                            >
                                {t('runMode.settings.moreModels')}
                            </button>
                        </dd>
                    </div>
                    {mode.capabilities.map((c) => (
                        <div className="run-mode-row" key={c.id}>
                            <dt>{t(`runMode.cap.${c.id}`)}</dt>
                            <dd>
                                {c.unavailable || !c.model ? (
                                    <span className="run-mode-value is-off">{t(`runMode.unavailable.${c.unavailable ?? 'platform'}`)}</span>
                                ) : (
                                    <span className="run-mode-value">
                                        {t('runMode.settings.onThisPc')} · <bdi dir="ltr">{c.display_name}</bdi>
                                    </span>
                                )}
                                {c.settings && !c.unavailable && (
                                    <button type="button" className="link-btn run-mode-more" onClick={() => openSettings(c.settings!)}>
                                        {t('runMode.settings.change')}
                                    </button>
                                )}
                                {c.cloud_note && (
                                    <span className="run-mode-why">{t(`runMode.cloudWhy.${c.cloud_note}`, { provider })}</span>
                                )}
                            </dd>
                        </div>
                    ))}
                </dl>
            </section>
        );
    };

    return (
        <div className="settings-pane">
            {title}
            <p className="settings-pane-lede">{t('runMode.settings.lede')}</p>
            {turnRunning && <p className="settings-hint" role="status">{t('runMode.settings.turnRunning')}</p>}
            {view.modes.map(card)}
        </div>
    );
}
