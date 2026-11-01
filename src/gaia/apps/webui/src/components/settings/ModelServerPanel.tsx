// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import type { MessageKey } from '../../i18n/registry';
import { useT, type Translate } from '../../i18n/useT';
import { useChatStore } from '../../stores/chatStore';
import { displayModelName } from '../../stores/modelStore';
import { ErrorNotice } from './ErrorNotice';
import type { LoadedModelInfo, ModelServerInfo } from '../../types/api.gen';

const BACKEND_LABELS: Record<string, string> = {
    vulkan: 'Vulkan',
    rocm: 'ROCm',
    metal: 'Metal',
    cpu: 'CPU',
    npu: 'NPU',
    remote: 'Remote (cloud)',
};

const OWNER_LABELS: Record<string, MessageKey> = {
    gaia: 'settings.server.ownerGaia',
    user: 'settings.server.ownerUser',
    remote: 'settings.server.ownerRemote',
};

export function backendLabel(backend: string | null | undefined): string {
    if (!backend) return 'unknown';
    return BACKEND_LABELS[backend] ?? backend;
}

/** "Vulkan", "Vulkan (Lemonade's default)", "Remote (cloud)", or "unknown". */
export function runsOn(model: LoadedModelInfo | null | undefined): string {
    if (!model) return 'unknown';
    const label = backendLabel(model.backend);
    return model.backend_source === 'lemonade_default' ? `${label} (Lemonade's default)` : label;
}

function ctxLabel(ctx: number | null | undefined): string {
    return ctx ? `${Math.round(ctx / 1024)}K tokens` : 'unknown';
}

const MODEL_KIND_LABELS: Record<string, MessageKey> = {
    asr: 'settings.server.kind.speechRecognition',
    embedding: 'settings.server.kind.embedding',
    image: 'settings.server.kind.image',
    llm: 'settings.server.kind.language',
    rerank: 'settings.server.kind.reranker',
    reranking: 'settings.server.kind.reranker',
    stt: 'settings.server.kind.speechRecognition',
    transcription: 'settings.server.kind.speechRecognition',
    tts: 'settings.server.kind.speech',
    vision: 'settings.server.kind.vision',
    '3d': 'settings.server.kind.threeD',
};

function modelKindLabel(model: LoadedModelInfo, t: Translate): string {
    if (!model.model_type) return t('settings.server.loadedModel');
    const type = model.model_type.toLowerCase();
    const key = MODEL_KIND_LABELS[type];
    return key ? t(key) : t('settings.server.kindOther', { kind: `${type.charAt(0).toUpperCase()}${type.slice(1)}` });
}

function loadedModelDetail(model: LoadedModelInfo, t: Translate): string {
    const name = model.name ? displayModelName(model.name) : t.bare('settings.server.unknownModel');
    const detail = [name, runsOn(model)];
    if (model.backend !== 'remote' && model.ctx_size) detail.push(ctxLabel(model.ctx_size));
    return detail.join(' · ');
}

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
                {changeModel}
            </section>
        );
    }

    const available = info.available_backends ?? [];
    const preferred = info.preferred_backend ?? null;
    const choices = ['auto', ...available.map((b) => b.name)];
    // A saved choice this machine no longer offers stays visible so it can be undone.
    if (preferred && !choices.includes(preferred)) choices.push(preferred);
    const selected = preferred ?? 'auto';
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
                        value={loadedModelDetail(model, t)}
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
                        <div className="segmented" role="radiogroup" aria-label={t('settings.server.backendShort')}>
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
                                    {name === 'auto'
                                        ? `Auto${info.default_backend ? ` (${backendLabel(info.default_backend)})` : ''}`
                                        : backendLabel(name)}
                                </button>
                            ))}
                        </div>
                        <span className="settings-hint">
                            {t('settings.server.backendHint')}
                            {installable.length > 0 && ` ${t('settings.server.installable', { names: installable.join(', ') })}`}
                        </span>
                    </>
                )}
                {notice && (
                    <p className={notice.ok ? 'settings-ok' : 'settings-error'} role={notice.ok ? 'status' : 'alert'}>
                        {notice.text}
                    </p>
                )}
            </div>

            <div className="settings-row">
                {changeModel}
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
