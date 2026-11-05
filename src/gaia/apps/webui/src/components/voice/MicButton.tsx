// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Loader2, Mic, X } from 'lucide-react';
import type { Dictation } from '../../hooks/useDictation';
import { useT } from '../../i18n/useT';
import { VOICE_ERROR_TEXT } from '../../voice/errors';

/** The composer's dictation button: click to start, click again to stop. */
export function MicButton({ dictation, onStart, disabled }: { dictation: Dictation; onStart: () => void; disabled?: boolean }) {
    const t = useT();
    const recording = dictation.state === 'recording';
    const finishing = dictation.state === 'finishing';
    const label = recording || finishing ? t('dictation.stop') : t('dictation.start');
    return (
        <button
            type="button"
            className={`composer-icon-btn composer-mic${recording || finishing ? ' is-recording' : ''}`}
            onClick={() => (recording ? dictation.stop() : !finishing && onStart())}
            disabled={disabled || finishing}
            aria-label={label}
            aria-pressed={recording || finishing}
            title={recording ? t.hint(label, 'Enter') : label}
        >
            {finishing ? <Loader2 size={16} className="spin" /> : <Mic size={16} />}
        </button>
    );
}

/** Why dictation stopped, with the one action that fixes it. */
export function DictationNotice({ dictation }: { dictation: Dictation }) {
    const t = useT();
    const { error, limited, missingModel } = dictation;
    if (!error && !limited) return null;
    if (limited && !error) {
        return (
            <div className="composer-dictation-notice" role="status">
                <span>{t('dictation.limit')}</span>
                <button type="button" className="composer-icon-btn" onClick={dictation.dismissError} aria-label={t('dictation.dismiss')}>
                    <X size={12} />
                </button>
            </div>
        );
    }
    if (!error) return null;
    const text = VOICE_ERROR_TEXT[error.code];
    return (
        <div className="composer-dictation-notice is-error" role="alert">
            <span className="composer-dictation-text">
                <strong>{t(text.title)}</strong> {t(text.body)}
            </span>
            {error.code === 'model_missing' && missingModel && (
                <button type="button" className="btn-secondary composer-dictation-action" onClick={dictation.download} disabled={dictation.downloading}>
                    {dictation.downloading ? <Loader2 size={13} className="spin" /> : null}
                    {dictation.downloading ? t('voice.downloading') : t('voice.download')}
                    {!dictation.downloading && missingModel.size_gb != null && (
                        <bdi dir="ltr" className="composer-dictation-size">{missingModel.size_gb} GB</bdi>
                    )}
                </button>
            )}
            {dictation.downloadError && <span className="composer-dictation-error">{t('voice.downloadFailed', { reason: dictation.downloadError })}</span>}
            <button type="button" className="composer-icon-btn" onClick={dictation.dismissError} aria-label={t('dictation.dismiss')}>
                <X size={12} />
            </button>
        </div>
    );
}
