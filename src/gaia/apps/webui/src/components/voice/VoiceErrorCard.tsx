// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Download, Loader2, RotateCw, Settings } from 'lucide-react';
import { useT } from '../../i18n/useT';
import { useVoiceStore } from '../../stores/voiceStore';
import { downloadModel, retryVoice } from '../../voice/controller';
import { VOICE_ERROR_TEXT } from '../../voice/errors';
import { log } from '../../utils/logger';

/** The OS microphone pane, in the desktop app; null in a browser. */
export function micSettingsOpener(): (() => void) | null {
    const open = window.gaiaAPI?.system?.openMicSettings;
    if (!open) return null;
    return () => {
        open().catch((err: unknown) => log.ui.error('Could not open the microphone settings', err));
    };
}

/** Why voice can't go on, in plain words, with the one action that fixes it. */
export function VoiceErrorCard() {
    const t = useT();
    const error = useVoiceStore((s) => s.error);
    const status = useVoiceStore((s) => s.status);
    const downloading = useVoiceStore((s) => s.downloading);
    const downloadError = useVoiceStore((s) => s.downloadError);
    if (!error) return null;
    const text = VOICE_ERROR_TEXT[error.code];
    const missing = error.code === 'model_missing' ? (status?.models ?? []).filter((m) => !m.downloaded) : [];
    const openSettings = error.code === 'mic_denied' ? micSettingsOpener() : null;
    return (
        <div className={`vs-error is-${error.code}`} role="alert">
            <p className="vs-error-title">{t(text.title)}</p>
            <p className="vs-error-body">{t(text.body)}</p>
            {missing.length > 0 && (
                <ul className="vs-error-models">
                    {missing.map((m) => (
                        <li key={m.id}>
                            <span className="vs-error-model">
                                <span>{t(m.purpose === 'tts' ? 'voice.model.tts' : 'voice.model.transcription')}</span>
                                <bdi dir="ltr" className="vs-error-model-id">{m.id}</bdi>
                            </span>
                            <button
                                type="button"
                                className="btn-primary vs-error-btn"
                                disabled={downloading !== null}
                                onClick={() => void downloadModel(m.id)}
                            >
                                {downloading === m.id ? <Loader2 size={14} className="spin" /> : <Download size={14} />}
                                {downloading === m.id ? t('voice.downloading') : t('voice.download')}
                                {downloading !== m.id && m.size_gb != null && <bdi dir="ltr" className="vs-error-size">{m.size_gb} GB</bdi>}
                            </button>
                        </li>
                    ))}
                </ul>
            )}
            {downloadError && <p className="vs-error-detail">{t('voice.downloadFailed', { reason: downloadError })}</p>}
            {text.action !== 'none' && text.action !== 'download' && (
                <div className="vs-error-actions">
                    {openSettings && (
                        <button type="button" className="btn-secondary vs-error-btn" onClick={openSettings}>
                            <Settings size={14} />
                            {t('voice.openMicSettings')}
                        </button>
                    )}
                    <button type="button" className="btn-primary vs-error-btn" onClick={retryVoice}>
                        <RotateCw size={14} />
                        {t('voice.retry')}
                    </button>
                </div>
            )}
        </div>
    );
}
