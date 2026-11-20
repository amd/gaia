// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { RadioGroup } from '../RadioGroup';
import { useCallback, useEffect, useState } from 'react';
import { Loader2, ScrollText } from 'lucide-react';
import * as api from '../../services/api';
import { useT } from '../../i18n/useT';
import { useChatStore } from '../../stores/chatStore';
import { useLogsStore } from '../../stores/logsStore';
import { displayModelName } from '../../stores/modelStore';
import { backendLabel, ctxLabel, loadedModelDetail, modelKindLabel, OWNER_LABELS, runsOn } from '../../utils/modelServer.core';
import { ErrorNotice } from './ErrorNotice';
import type { ModelServerInfo } from '../../types/api.gen';

function Row({ label, value, title }: { label: string; value: string; title?: string }) {
    return (
        <div className="status-row">
            <span className="status-label">{label}</span>
            <span className="status-value model-server-value" dir="auto" title={title ?? value}>{value}</span>
        </div>
    );
}

/** Which Lemonade GAIA uses, where it is, and what each loaded model runs on. */
export function ModelServerPanel() {
    const t = useT();
    const openSettings = useChatStore((s) => s.openSettings);
    const showServerLog = useLogsStore((s) => s.show);
    const [info, setInfo] = useState<ModelServerInfo | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [saving, setSaving] = useState<string | null>(null);
    const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
    const [reloading, setReloading] = useState(false);

    useEffect(() => {
        api.getModelServer()
            .then(setInfo)
            .catch((err) => setLoadError(err instanceof Error ? err.message : String(err)));
    }, []);

    const choose = useCallback(async (backend: string) => {
        setSaving(backend);
        setNotice(null);
        try {
            const next = await api.setModelServerBackend(backend);
            setInfo(next);
            setNotice({ ok: true, text: t('settings.server.savedBackend') });
        } catch (err) {
            setNotice({ ok: false, text: err instanceof Error ? err.message : String(err) });
        } finally {
            setSaving(null);
        }
    }, [t]);

    const chat = info?.chat_model ?? null;
    const reload = useCallback(async () => {
        if (!chat?.name) return;
        setReloading(true);
        try {
            if (!(await api.loadModelAskingFirst(chat.name, chat.ctx_size ?? undefined))) return;
            setNotice({ ok: true, text: t('settings.server.reloading', { name: chat.name }) });
        } catch (err) {
            setNotice({ ok: false, text: err instanceof Error ? err.message : String(err) });
        } finally {
            setReloading(false);
        }
    }, [chat, t]);

    const changeModel = (
        <button type="button" className="btn-secondary" onClick={() => openSettings('model')}>
            {t('settings.server.changeModel')}
        </button>
    );
    const viewLog = (
        <button type="button" className="btn-secondary" onClick={() => showServerLog('model_server')}>
            <ScrollText size={13} aria-hidden="true" /> {t('logs.serverLog')}
        </button>
    );

    if (loadError) {
        return (
            <section className="settings-section" aria-label={t('settings.advanced.modelServer')}>
                <h4>{t('settings.advanced.modelServer')}</h4>
                <ErrorNotice message={loadError} />
            </section>
        );
    }
    if (!info) {
        return (
            <section className="settings-section" aria-label={t('settings.advanced.modelServer')}>
                <h4>{t('settings.advanced.modelServer')}</h4>
                <p className="loading-text">{t('settings.server.checkingServer')}</p>
            </section>
        );
    }
    if (!info.reachable) {
        return (
            <section className="settings-section" aria-label={t('settings.advanced.modelServer')}>
                <h4>{t('settings.advanced.modelServer')}</h4>
                <ErrorNotice message={info.error ?? t('settings.errors.serverDownTitle')} />
                <div className="settings-row">
                    {changeModel}
                    {viewLog}
                </div>
            </section>
        );
    }

    // Only backends GAIA has tested, which the server lists; no Auto, since Lemonade's own pick may not be one.
    const available = info.available_backends ?? [];
    const preferred = info.preferred_backend ?? null;
    const choices = available.map((b) => b.name);
    // A saved choice GAIA no longer offers stays visible so it can be switched off.
    if (preferred && !choices.includes(preferred)) choices.push(preferred);
    const selected = preferred ?? info.default_backend ?? null;
    const installable = available.filter((b) => b.state === 'installable').map((b) => backendLabel(b.name));
    const chatIsLocalLlamacpp = !!chat?.name && chat.recipe === 'llamacpp';

    return (
        <section className="settings-section" aria-label={t('settings.advanced.modelServer')}>
            <h4>{t('settings.advanced.modelServer')}</h4>
            <div className="status-grid">
                <Row
                    label={t('settings.server.server')}
                    value={`${OWNER_LABELS[info.owner] ? t(OWNER_LABELS[info.owner]) : info.owner}${info.version ? ` v${info.version}` : ''}`}
                />
                <Row label={t('settings.server.addressShort')} value={info.base_url} />
                <Row
                    label={t('settings.server.chatModel')}
                    value={chat?.name ? displayModelName(chat.name) : t('settings.advanced.noneLoaded')}
                    title={chat?.name ?? undefined}
                />
                {chat && <Row label={t('settings.server.runsOn')} value={runsOn(chat)} />}
                {chat && chat.backend !== 'remote' && <Row label={t('settings.advanced.context')} value={ctxLabel(chat.ctx_size)} />}
                <Row
                    label={t('settings.advanced.embedding')}
                    value={info.embedding_model?.name
                        ? `${info.embedding_model.name} · ${runsOn(info.embedding_model)}`
                        : t('settings.advanced.noneLoaded')}
                />
                {(info.loaded_models ?? []).map((model, index) => (
                    <Row
                        key={`${model.name ?? 'unknown'}-${index}`}
                        label={modelKindLabel(model, t)}
                        value={loadedModelDetail(model, t, displayModelName)}
                        title={model.name ?? undefined}
                    />
                ))}
            </div>
            {info.embedding_note && <p className="settings-hint"><bdi>{info.embedding_note}</bdi></p>}
            {info.system_info_error && <p className="settings-hint"><bdi>{info.system_info_error}</bdi></p>}

            <div className="settings-field model-server-backend">
                <span className="settings-label">{t('settings.server.backend')}</span>
                {available.length === 0 && !preferred ? (
                    <span className="settings-hint">
                        {t('settings.server.noBackends')}
                    </span>
                ) : (
                    <>
                        <RadioGroup className="segmented" aria-label={t('settings.server.backendShort')}>
                            {choices.map((name) => (
                                <button
                                    key={name}
                                    type="button"
                                    role="radio"
                                    aria-checked={selected === name}
                                    className={selected === name ? 'is-active' : ''}
                                    disabled={saving !== null}
                                    onClick={() => { if (name !== selected) void choose(name); }}
                                >
                                    {saving === name ? <Loader2 size={12} className="spin" /> : null}
                                    {backendLabel(name)}
                                </button>
                            ))}
                        </RadioGroup>
                        <span className="settings-hint">
                            {t('settings.server.backendHint')}
                            {installable.length > 0 && ` ${t('settings.server.installable', { names: installable.join(', ') })}`}
                        </span>
                    </>
                )}
                {notice?.ok && <p className="settings-ok" role="status">{notice.text}</p>}
                {notice && !notice.ok && <ErrorNotice message={notice.text} />}
            </div>

            <div className="settings-row">
                {changeModel}
                {viewLog}
                {chatIsLocalLlamacpp && (
                    <button type="button" className="btn-secondary" onClick={() => void reload()} disabled={reloading}>
                        {reloading ? <Loader2 size={13} className="spin" /> : null}
                        {t('settings.server.reload')}
                    </button>
                )}
            </div>
        </section>
    );
}
