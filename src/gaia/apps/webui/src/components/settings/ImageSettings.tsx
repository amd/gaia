// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import { displayModelName, useModelNames } from '../../stores/modelStore';
import { useT } from '../../i18n/useT';

/** Step counts offered beside a model's own; capped by the server's maximum. */
const STEP_CHOICES = [4, 8, 12, 20, 30, 50];

/** Text inside <option> can't hold <bdi>, so isolate marks keep "1024x768" in order. */
const ltr = (text: string) => `⁦${text}⁩`;

function errorText(err: unknown): string {
    return err instanceof api.ApiError ? err.detail || err.message : String(err);
}

/** The model, size and steps GAIA creates images with. */
export function ImageSettings() {
    const t = useT();
    const openSettings = useChatStore((s) => s.openSettings);
    useModelNames();
    const [settings, setSettings] = useState<api.ImageSettings | null>(null);
    const [loadError, setLoadError] = useState<{ unreachable: boolean; detail: string } | null>(null);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);

    const load = () => {
        setLoadError(null);
        api.getImageSettings().then(setSettings).catch((err) => setLoadError({
            unreachable: err instanceof api.ApiError && err.httpStatus === 503,
            detail: errorText(err),
        }));
    };
    useEffect(load, []);

    const save = (change: Parameters<typeof api.updateImageSettings>[0]) => {
        setSaving(true);
        setSaveError(null);
        api.updateImageSettings(change)
            .then(setSettings)
            .catch((err) => setSaveError(errorText(err)))
            .finally(() => setSaving(false));
    };

    const title = <h2 className="settings-pane-title">{t('settings.section.images')}</h2>;
    if (loadError) {
        return (
            <div className="settings-pane">
                {title}
                {loadError.unreachable ? (
                    <>
                        <p className="settings-error" role="alert">{t('images.settings.unreachable')}</p>
                        <p className="settings-hint">{loadError.detail}</p>
                        <button type="button" className="btn-secondary" onClick={load}>{t('images.settings.retry')}</button>
                    </>
                ) : (
                    <p className="settings-error" role="alert">{t('images.settings.loadFailed', { reason: loadError.detail })}</p>
                )}
            </div>
        );
    }
    if (!settings) {
        return <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.loading')}</p>;
    }

    const current = settings.models.find((m) => m.id === settings.model);
    const steps = current
        ? [...new Set([...STEP_CHOICES, current.default_steps, ...(settings.steps ? [settings.steps] : [])])]
            .filter((n) => n <= settings.max_steps)
            .sort((a, b) => a - b)
        : [];

    return (
        <div className="settings-pane">
            {title}
            <p className="settings-pane-lede">{t('images.settings.lede')}</p>

            {!settings.chat_model_creates_images && (
                <div className="settings-notice" role="status">
                    <p>{t('images.settings.chatModelCannot', { model: displayModelName(settings.chat_model) })}</p>
                    <button type="button" className="btn-secondary" onClick={() => openSettings('model')}>
                        {t('images.settings.chooseChatModel')}
                    </button>
                </div>
            )}
            {!settings.model_available && (
                <p className="settings-error" role="alert">{t('images.settings.modelGone', { model: settings.model })}</p>
            )}
            {settings.models.length === 0 ? (
                <p className="settings-hint">{t('images.settings.noModels')}</p>
            ) : (
                <>
                    <div className="settings-field">
                        <label className="settings-label" htmlFor="image-model">{t('images.settings.model')}</label>
                        <select
                            id="image-model"
                            className="settings-input"
                            value={settings.model_available ? settings.model : ''}
                            disabled={saving}
                            onChange={(e) => save({ model: e.target.value })}
                        >
                            {!settings.model_available && <option value="" disabled>{settings.model}</option>}
                            {settings.models.map((m) => (
                                <option key={m.id} value={m.id}>
                                    {m.downloads_on_first_use
                                        ? t.bare('images.settings.downloadsOnFirstUse', { model: m.id, size: ltr(`${m.size_gb ?? '?'} GB`) })
                                        : m.id}
                                </option>
                            ))}
                        </select>
                    </div>

                    {current && (
                        <>
                            <div className="settings-field">
                                <label className="settings-label" htmlFor="image-size">{t('images.settings.size')}</label>
                                <select
                                    id="image-size"
                                    className="settings-input"
                                    value={settings.size ?? ''}
                                    disabled={saving}
                                    onChange={(e) => save({ size: e.target.value || null })}
                                >
                                    <option value="">{t.bare('images.settings.automatic', { value: ltr(current.default_size) })}</option>
                                    {current.sizes.map((s) => <option key={s} value={s}>{ltr(s)}</option>)}
                                </select>
                            </div>
                            <div className="settings-field">
                                <label className="settings-label" htmlFor="image-steps">{t('images.settings.steps')}</label>
                                <select
                                    id="image-steps"
                                    className="settings-input"
                                    value={settings.steps ?? ''}
                                    disabled={saving}
                                    onChange={(e) => save({ steps: e.target.value ? Number(e.target.value) : null })}
                                >
                                    <option value="">{t.bare('images.settings.automatic', { value: ltr(String(current.default_steps)) })}</option>
                                    {steps.map((n) => <option key={n} value={n}>{ltr(String(n))}</option>)}
                                </select>
                                <span className="settings-hint">{t('images.settings.stepsHint')}</span>
                            </div>
                        </>
                    )}
                </>
            )}
            <p className="settings-hint">{t('images.settings.shared')}</p>
            {saveError && <p className="settings-error" role="alert">{t('images.settings.saveFailed', { reason: saveError })}</p>}
        </div>
    );
}
