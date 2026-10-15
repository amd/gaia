// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import type { LoadedModelInfo, ModelServerInfo } from '../../types/api.gen';

const BACKEND_LABELS: Record<string, string> = {
    vulkan: 'Vulkan',
    rocm: 'ROCm',
    metal: 'Metal',
    cpu: 'CPU',
    npu: 'NPU',
    remote: 'Remote (cloud)',
};

const OWNER_LABELS: Record<string, string> = {
    gaia: "GAIA's own Lemonade",
    user: 'Your Lemonade',
    remote: 'Lemonade on another machine',
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

function Row({ label, value, title }: { label: string; value: string; title?: string }) {
    return (
        <div className="status-row">
            <span className="status-label">{label}</span>
            <span className="status-value model-server-value" title={title ?? value}>{value}</span>
        </div>
    );
}

/** Which Lemonade GAIA uses, where it is, and what each loaded model runs on. */
export function ModelServerPanel() {
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
            setNotice({ ok: true, text: 'Saved. It applies the next time the chat model loads.' });
        } catch (err) {
            setNotice({ ok: false, text: err instanceof Error ? err.message : String(err) });
        } finally {
            setSaving(null);
        }
    }, []);

    const chat = info?.chat_model ?? null;
    const reload = useCallback(async () => {
        if (!chat?.name) return;
        setReloading(true);
        try {
            await api.loadModel(chat.name, chat.ctx_size ?? undefined);
            setNotice({ ok: true, text: `Reloading ${chat.name}. Reopen this panel in a moment to see its backend.` });
        } catch (err) {
            setNotice({ ok: false, text: err instanceof Error ? err.message : String(err) });
        } finally {
            setReloading(false);
        }
    }, [chat]);

    const changeModel = (
        <button type="button" className="btn-secondary" onClick={() => openSettings('model')}>
            Change model
        </button>
    );

    if (loadError) {
        return (
            <section className="settings-section" aria-label="Model server">
                <h4>Model server</h4>
                <p className="settings-error" role="alert">{loadError}</p>
            </section>
        );
    }
    if (!info) {
        return (
            <section className="settings-section" aria-label="Model server">
                <h4>Model server</h4>
                <p className="loading-text">Checking the model server…</p>
            </section>
        );
    }
    if (!info.reachable) {
        return (
            <section className="settings-section" aria-label="Model server">
                <h4>Model server</h4>
                <p className="settings-error" role="alert">{info.error}</p>
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
        <section className="settings-section" aria-label="Model server">
            <h4>Model server</h4>
            <div className="status-grid">
                <Row
                    label="Server"
                    value={`${OWNER_LABELS[info.owner] ?? info.owner}${info.version ? ` v${info.version}` : ''}`}
                />
                <Row label="Address" value={info.base_url} />
                <Row label="Chat model" value={chat?.name ?? 'None loaded'} />
                {chat && <Row label="Runs on" value={runsOn(chat)} />}
                {chat && chat.backend !== 'remote' && <Row label="Context window" value={ctxLabel(chat.ctx_size)} />}
                <Row
                    label="Embedding model"
                    value={info.embedding_model?.name
                        ? `${info.embedding_model.name} · ${runsOn(info.embedding_model)}`
                        : 'None loaded'}
                />
            </div>
            {info.system_info_error && <p className="settings-hint">{info.system_info_error}</p>}

            <div className="settings-field model-server-backend">
                <span className="settings-label">llama.cpp backend for chat models</span>
                {available.length === 0 && !preferred ? (
                    <span className="settings-hint">
                        This Lemonade does not report which backends this PC can run, so there is nothing to
                        choose. Update Lemonade to pick one here.
                    </span>
                ) : (
                    <>
                        <div className="segmented" role="radiogroup" aria-label="llama.cpp backend">
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
                            Applies the next time a chat model loads. The embedding model keeps its own backend.
                            {installable.length > 0 && ` ${installable.join(', ')} downloads on first use.`}
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
                        Reload chat model now
                    </button>
                )}
            </div>
        </section>
    );
}
