// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Settings › Voice: how replies are read aloud (Speaking), how GAIA hears you
 * (Listening), and how voice mode takes turns (Voice mode). The microphone is
 * remembered per device; everything else is shared with the TUI.
 */

import { useCallback, useEffect, useState } from 'react';
import { Check, Download, Loader2 } from 'lucide-react';
import { ApiError } from '../../services/api';
import {
    downloadVoiceModel, getVoiceSettings, getVoiceStatus, updateVoiceSettings,
    SILENCE_MS_MAX, SILENCE_MS_MIN, type VoiceSettings as Settings, type VoiceSettingsUpdate, type VoiceStatus,
} from '../../services/voiceApi';
import { useChatStore } from '../../stores/chatStore';
import { LOCALES, localeOptionLabel } from '../../i18n/registry';
import { useT } from '../../i18n/useT';
import { log } from '../../utils/logger';
import { savedMicId, saveMicId } from '../../voice/audio';
import { ReadAloudSettings } from './ReadAloudSettings';
import './VoiceSettings.css';

/** Only the recognizers voice ships with; `auto` picks between them by language. */
const ASR_MODELS = ['auto', 'Moonshine-Tiny-Streaming', 'Whisper-Base'];
const SILENCE_STEPS = Array.from({ length: (SILENCE_MS_MAX - SILENCE_MS_MIN) / 100 + 1 }, (_, i) => SILENCE_MS_MIN + i * 100);

function errorText(err: unknown): string {
    return err instanceof ApiError ? err.detail || err.message : err instanceof Error ? err.message : String(err);
}

function useMicrophones(): MediaDeviceInfo[] {
    const [mics, setMics] = useState<MediaDeviceInfo[]>([]);
    useEffect(() => {
        const devices = navigator.mediaDevices;
        if (!devices?.enumerateDevices) return;
        const read = () => {
            devices.enumerateDevices()
                .then((all) => setMics(all.filter((d) => d.kind === 'audioinput' && d.deviceId !== 'default' && d.deviceId !== 'communications')))
                .catch((err: unknown) => log.ui.warn('Could not list microphones', err));
        };
        read();
        // A microphone plugged in later shows up without a reload.
        devices.addEventListener?.('devicechange', read);
        return () => devices.removeEventListener?.('devicechange', read);
    }, []);
    return mics;
}

function Toggle({ label, checked, disabled, onChange }: { label: string; checked: boolean; disabled?: boolean; onChange: () => void }) {
    return (
        <label className="setting-row">
            <span>{label}</span>
            <span className="toggle-switch">
                <input type="checkbox" checked={checked} disabled={disabled} onChange={onChange} aria-label={label} />
                <span className="toggle-track" />
            </span>
        </label>
    );
}

export function VoiceSettings() {
    const t = useT();
    const locale = useChatStore((s) => s.locale);
    const [settings, setSettings] = useState<Settings | null>(null);
    const [status, setStatus] = useState<VoiceStatus | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [statusError, setStatusError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [mic, setMic] = useState(savedMicId);
    const [micError, setMicError] = useState<string | null>(null);
    const [downloading, setDownloading] = useState<string | null>(null);
    const [downloadError, setDownloadError] = useState<string | null>(null);
    const mics = useMicrophones();

    const refreshStatus = useCallback(() => {
        getVoiceStatus(locale).then((s) => { setStatus(s); setStatusError(null); }).catch((err) => setStatusError(errorText(err)));
    }, [locale]);

    useEffect(() => {
        getVoiceSettings().then(setSettings).catch((err) => setLoadError(errorText(err)));
    }, []);
    useEffect(refreshStatus, [refreshStatus]);

    const save = (change: VoiceSettingsUpdate) => {
        setSaving(true);
        setSaveError(null);
        updateVoiceSettings(change)
            .then((next) => { setSettings(next); refreshStatus(); })
            .catch((err) => setSaveError(errorText(err)))
            .finally(() => setSaving(false));
    };

    const pickMic = (id: string) => {
        try {
            saveMicId(id);
            setMic(id);
            setMicError(null);
        } catch (err) {
            setMicError(errorText(err));
        }
    };

    const download = (model: string) => {
        setDownloading(model);
        setDownloadError(null);
        downloadVoiceModel(model)
            .then(refreshStatus)
            .catch((err) => setDownloadError(errorText(err)))
            .finally(() => setDownloading(null));
    };

    const unlabeled = mics.some((m) => !m.label);

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">{t('settings.section.voice')}</h2>

            <section className="settings-card" aria-labelledby="voice-speaking">
                <h3 className="settings-label voice-settings-group" id="voice-speaking">{t('voice.settings.speaking')}</h3>
                <ReadAloudSettings embedded />
            </section>

            {loadError ? (
                <p className="settings-error" role="alert">{t('voice.settings.loadFailed', { reason: loadError })}</p>
            ) : !settings ? (
                <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.loading')}</p>
            ) : (
                <>
                    <section className="settings-card" aria-labelledby="voice-listening">
                        <h3 className="settings-label voice-settings-group" id="voice-listening">{t('voice.settings.listening')}</h3>

                        <div className="settings-field">
                            <label className="settings-label" htmlFor="voice-mic">{t('voice.settings.mic')}</label>
                            <select id="voice-mic" className="settings-input" value={mic} onChange={(e) => pickMic(e.target.value)}>
                                <option value="">{t.bare('voice.settings.micDefault')}</option>
                                {mics.map((m, i) => (
                                    <option key={m.deviceId} value={m.deviceId}>{m.label || t.bare('voice.settings.micUnnamed', { n: i + 1 })}</option>
                                ))}
                                {mic && !mics.some((m) => m.deviceId === mic) && (
                                    <option value={mic}>{t.bare('voice.settings.micUnnamed', { n: mics.length + 1 })}</option>
                                )}
                            </select>
                            {unlabeled && <span className="settings-hint">{t('voice.settings.micList')}</span>}
                            {micError && <p className="settings-error" role="alert">{t('voice.settings.micSaveFailed', { reason: micError })}</p>}
                        </div>

                        <div className="settings-field">
                            <label className="settings-label" htmlFor="voice-language">{t('voice.settings.language')}</label>
                            <select
                                id="voice-language"
                                className="settings-input"
                                value={settings.language ?? ''}
                                disabled={saving}
                                onChange={(e) => save({ language: e.target.value })}
                            >
                                <option value="">{t.bare('voice.settings.languageFollow')}</option>
                                {LOCALES.map(({ meta }) => (
                                    <option key={meta.code} value={meta.code} lang={meta.code} dir={meta.dir}>{localeOptionLabel(meta)}</option>
                                ))}
                            </select>
                        </div>

                        <div className="settings-field">
                            <label className="settings-label" htmlFor="voice-asr">{t('voice.settings.asr')}</label>
                            <select
                                id="voice-asr"
                                className="settings-input"
                                value={settings.asr_model}
                                disabled={saving}
                                onChange={(e) => save({ asr_model: e.target.value })}
                            >
                                {(ASR_MODELS.includes(settings.asr_model) ? ASR_MODELS : [...ASR_MODELS, settings.asr_model]).map((m) => (
                                    <option key={m} value={m}>{m === 'auto' ? t.bare('voice.settings.asrAuto') : m}</option>
                                ))}
                            </select>
                        </div>

                        <div className="settings-field">
                            <span className="settings-label">{t('voice.settings.models')}</span>
                            {statusError && <p className="settings-error" role="alert">{t('voice.settings.statusFailed', { reason: statusError })}</p>}
                            {status && status.models.length > 0 && (
                                <ul className="voice-settings-models">
                                    {status.models.map((m) => (
                                        <li key={m.id}>
                                            <span className="voice-settings-model">
                                                <span>{t(m.purpose === 'tts' ? 'voice.model.tts' : 'voice.model.transcription')}</span>
                                                <bdi dir="ltr" className="voice-settings-model-id">{m.id}</bdi>
                                            </span>
                                            {m.downloaded ? (
                                                <span className="voice-settings-ok"><Check size={13} aria-hidden="true" /> {t('voice.settings.downloaded')}</span>
                                            ) : (
                                                <button type="button" className="btn-secondary voice-settings-dl" disabled={downloading !== null} onClick={() => download(m.id)}>
                                                    {downloading === m.id ? <Loader2 size={13} className="spin" /> : <Download size={13} />}
                                                    {downloading === m.id ? t('voice.downloading') : t('voice.download')}
                                                    {downloading !== m.id && m.size_gb != null && <bdi dir="ltr" className="voice-settings-size">{m.size_gb} GB</bdi>}
                                                </button>
                                            )}
                                        </li>
                                    ))}
                                </ul>
                            )}
                            {downloadError && <p className="settings-error" role="alert">{t('voice.downloadFailed', { reason: downloadError })}</p>}
                        </div>
                    </section>

                    <section className="settings-card" aria-labelledby="voice-mode">
                        <h3 className="settings-label voice-settings-group" id="voice-mode">{t('voice.settings.voiceMode')}</h3>

                        <div className="settings-field">
                            <label className="settings-label" htmlFor="voice-turns">{t('voice.settings.turnTaking')}</label>
                            <select
                                id="voice-turns"
                                className="settings-input"
                                value={settings.turn_taking}
                                disabled={saving}
                                onChange={(e) => save({ turn_taking: e.target.value as Settings['turn_taking'] })}
                            >
                                <option value="hands_free">{t.bare('voice.settings.handsFree')}</option>
                                <option value="push_to_talk">{t.bare('voice.settings.pushToTalk')}</option>
                            </select>
                        </div>

                        <div className="settings-field">
                            <label className="settings-label" htmlFor="voice-silence">{t('voice.settings.silence')}</label>
                            <select
                                id="voice-silence"
                                className="settings-input"
                                value={settings.silence_ms}
                                disabled={saving}
                                onChange={(e) => save({ silence_ms: Number(e.target.value) })}
                            >
                                {(SILENCE_STEPS.includes(settings.silence_ms) ? SILENCE_STEPS : [...SILENCE_STEPS, settings.silence_ms].sort((a, b) => a - b)).map((ms) => (
                                    <option key={ms} value={ms}>{t.bare('voice.settings.ms', { n: ms })}</option>
                                ))}
                            </select>
                        </div>

                        <Toggle
                            label={t('voice.settings.statusUpdates')}
                            checked={settings.status_updates}
                            disabled={saving}
                            onChange={() => save({ status_updates: !settings.status_updates })}
                        />
                        <Toggle
                            label={t('voice.settings.captions')}
                            checked={settings.captions}
                            disabled={saving}
                            onChange={() => save({ captions: !settings.captions })}
                        />
                        <Toggle
                            label={t('voice.settings.wakeWord')}
                            checked={settings.wake_word}
                            disabled={saving}
                            onChange={() => save({ wake_word: !settings.wake_word })}
                        />
                        <Toggle
                            label={t('voice.settings.chime')}
                            checked={settings.chime}
                            disabled={saving}
                            onChange={() => save({ chime: !settings.chime })}
                        />
                        {saveError && <p className="settings-error" role="alert">{t('voice.settings.saveFailed', { reason: saveError })}</p>}
                    </section>
                </>
            )}
        </div>
    );
}
