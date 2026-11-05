// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { Loader2, Square, Volume2 } from 'lucide-react';
import * as api from '../../services/api';
import { useReadAloudStore } from '../../stores/readAloudStore';
import { useT } from '../../i18n/useT';
import { ReadAloudError } from '../ReadAloudButton';

/** Speeds offered, as percent of normal; the server accepts any in its range. */
const SPEEDS = [50, 75, 100, 125, 150, 175, 200];

const GROUPS: Array<{ accent: api.Voice['accent']; gender: api.Voice['gender'] }> = [
    { accent: 'american', gender: 'female' },
    { accent: 'american', gender: 'male' },
    { accent: 'british', gender: 'female' },
    { accent: 'british', gender: 'male' },
];

const PREVIEW_ID = 'settings:preview';
// Spoken, not shown, and every Kokoro voice speaks English, so it is not translated.
const PREVIEW_TEXT = "Hello, I'm GAIA. This is how I'll read my replies to you.";

function errorText(err: unknown): string {
    return err instanceof api.ApiError ? err.detail || err.message : String(err);
}

/** The voice and speed replies are read in, and whether every reply is read. */
export function ReadAloudSettings() {
    const t = useT();
    const settings = useReadAloudStore((s) => s.settings);
    const setSettings = useReadAloudStore((s) => s.setSettings);
    const read = useReadAloudStore((s) => s.read);
    const stop = useReadAloudStore((s) => s.stop);
    const previewing = useReadAloudStore((s) => s.messageId === PREVIEW_ID);
    const previewLoading = useReadAloudStore((s) => s.messageId === PREVIEW_ID && s.status === 'loading');
    const [loadError, setLoadError] = useState<string | null>(null);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        api.getSpeechSettings().then(setSettings).catch((err) => setLoadError(errorText(err)));
    }, [setSettings]);

    const save = (change: Parameters<typeof api.updateSpeechSettings>[0]) => {
        setSaving(true);
        setSaveError(null);
        // A preview in the old voice is stale; a reply playing in the chat is not.
        if (useReadAloudStore.getState().messageId === PREVIEW_ID) stop();
        api.updateSpeechSettings(change)
            .then(setSettings)
            .catch((err) => setSaveError(errorText(err)))
            .finally(() => setSaving(false));
    };

    if (loadError) {
        return (
            <div className="settings-pane">
                <h2 className="settings-pane-title">{t('settings.section.voice')}</h2>
                <p className="settings-error" role="alert">{t('readAloud.settings.loadFailed', { reason: loadError })}</p>
            </div>
        );
    }
    if (!settings) {
        return <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.loading')}</p>;
    }

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">{t('settings.section.voice')}</h2>
            <p className="settings-pane-lede">{t('readAloud.settings.lede')}</p>

            <div className="settings-field">
                <label className="settings-label" htmlFor="read-aloud-voice">{t('readAloud.settings.voice')}</label>
                <div className="read-aloud-voice-row">
                    <select
                        id="read-aloud-voice"
                        className="settings-input"
                        value={settings.voice}
                        disabled={saving}
                        onChange={(e) => save({ voice: e.target.value })}
                    >
                        {GROUPS.map((g) => (
                            <optgroup key={`${g.accent}-${g.gender}`} label={t(`readAloud.group.${g.accent}.${g.gender}`)}>
                                {settings.voices
                                    .filter((v) => v.accent === g.accent && v.gender === g.gender)
                                    .map((v) => (
                                        <option key={v.id} value={v.id} lang="en">{v.name}</option>
                                    ))}
                            </optgroup>
                        ))}
                    </select>
                    <button
                        type="button"
                        className="btn-secondary"
                        aria-pressed={previewing}
                        disabled={saving}
                        onClick={() => (previewing ? stop() : void read(PREVIEW_ID, PREVIEW_TEXT))}
                    >
                        {previewLoading ? <Loader2 size={13} className="spin" /> : previewing ? <Square size={13} /> : <Volume2 size={13} />}
                        {previewing ? t('readAloud.stop') : t('readAloud.settings.preview')}
                    </button>
                </div>
                <span className="settings-hint">{t('readAloud.settings.englishOnly')}</span>
                <ReadAloudError messageId={PREVIEW_ID} />
            </div>

            <div className="settings-field">
                <label className="settings-label" htmlFor="read-aloud-speed">{t('readAloud.settings.speed')}</label>
                <select
                    id="read-aloud-speed"
                    className="settings-input"
                    value={settings.speed}
                    disabled={saving}
                    onChange={(e) => save({ speed: Number(e.target.value) })}
                >
                    {(SPEEDS.includes(settings.speed) ? SPEEDS : [...SPEEDS, settings.speed].sort((a, b) => a - b)).map((s) => (
                        <option key={s} value={s}>
                            {/* <option> holds text only, so isolate marks stand in for <bdi dir="ltr">. */}
                            {s === 100 ? t.bare('readAloud.settings.normalSpeed') : `⁦${(s / 100).toLocaleString(t.locale)}×⁩`}
                        </option>
                    ))}
                </select>
            </div>

            <label className="setting-row">
                <span>{t('readAloud.settings.auto')}</span>
                <span className="toggle-switch">
                    <input
                        type="checkbox"
                        checked={settings.auto}
                        disabled={saving}
                        onChange={() => save({ auto: !settings.auto })}
                        aria-label={t('readAloud.settings.auto')}
                    />
                    <span className="toggle-track" />
                </span>
            </label>
            <p className="settings-hint">{t('readAloud.settings.shared')}</p>
            {saveError && <p className="settings-error" role="alert">{t('readAloud.settings.saveFailed', { reason: saveError })}</p>}
        </div>
    );
}
