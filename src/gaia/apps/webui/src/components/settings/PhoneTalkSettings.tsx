// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Settings › Phone › Talk on calls: the PIN that unlocks a call, whether calls
 * connect to GAIA at all, and the voice she speaks in. The PIN is sent once and
 * never kept in the page.
 */

import { useEffect, useRef, useState } from 'react';
import { Check, Loader2, Square, Volume2 } from 'lucide-react';
import { useT } from '../../i18n/useT';
import * as api from '../../services/api';
import * as speechApi from '../../services/speechApi';
import { clearPhonePin, setPhonePin, type PhoneSettings, type PhoneSettingsPatch } from '../../services/phoneApi';
import { log } from '../../utils/logger';

const GROUPS: Array<{ accent: speechApi.Voice['accent']; gender: speechApi.Voice['gender'] }> = [
    { accent: 'american', gender: 'female' },
    { accent: 'american', gender: 'male' },
    { accent: 'british', gender: 'female' },
    { accent: 'british', gender: 'male' },
];

// Spoken, not shown: every Kokoro voice speaks English, so it is not translated.
const PREVIEW_TEXT = "Hi, it's GAIA on your PC. Type your PIN to talk with me.";

export type TalkStatus = 'ready' | 'off' | 'missing';

/** What the status line says: ready, switched off, or what is still missing. */
export function talkStatus(s: Pick<PhoneSettings, 'pin_set' | 'talk_on_calls' | 'talk_available'>): { state: TalkStatus; needsPin: boolean; needsAccess: boolean } {
    if (!s.talk_on_calls) return { state: 'off', needsPin: false, needsAccess: false };
    const needsPin = !s.pin_set;
    const needsAccess = !s.talk_available;
    return { state: needsPin || needsAccess ? 'missing' : 'ready', needsPin, needsAccess };
}

interface Props {
    settings: PhoneSettings;
    disabled: boolean;
    busy: string | null;
    /** Runs a change with its failure and notice shown in this group, then rereads phone settings. */
    act: (label: string, fn: () => Promise<unknown>, done?: string) => Promise<boolean>;
    save: (patch: PhoneSettingsPatch) => Promise<boolean>;
}

export function PhoneTalkSettings({ settings, disabled, busy, act, save }: Props) {
    const t = useT();
    const [editingPin, setEditingPin] = useState(false);
    const [pin, setPin] = useState('');
    const [pinError, setPinError] = useState<string | null>(null);
    const [voices, setVoices] = useState<speechApi.Voice[] | null>(null);
    const [voicesError, setVoicesError] = useState<string | null>(null);
    const [previewing, setPreviewing] = useState<'loading' | 'playing' | null>(null);
    const [previewError, setPreviewError] = useState<string | null>(null);
    const audioRef = useRef<HTMLAudioElement | null>(null);

    useEffect(() => {
        speechApi.getSpeechSettings()
            .then((s) => setVoices(s.voices))
            .catch((err) => setVoicesError(err instanceof api.ApiError ? err.detail || err.message : String(err)));
    }, []);

    const stopPreview = () => {
        const audio = audioRef.current;
        if (audio) {
            audio.pause();
            URL.revokeObjectURL(audio.src);
        }
        audioRef.current = null;
        setPreviewing(null);
    };
    useEffect(() => () => stopPreview(), []);

    const preview = async () => {
        if (previewing) {
            stopPreview();
            return;
        }
        setPreviewError(null);
        setPreviewing('loading');
        try {
            const blob = await speechApi.speak(PREVIEW_TEXT, { voice: settings.voice });
            const audio = new Audio(URL.createObjectURL(blob));
            audioRef.current = audio;
            audio.onended = stopPreview;
            setPreviewing('playing');
            await audio.play();
        } catch (err) {
            log.ui.warn('Phone voice preview failed', err);
            stopPreview();
            setPreviewError(err instanceof api.ApiError ? err.detail || err.message : err instanceof Error ? err.message : String(err));
        }
    };

    const savePin = () => {
        if (!/^\d{4,8}$/.test(pin)) {
            setPinError(t('phone.pin.digits'));
            return;
        }
        setPinError(null);
        const value = pin;
        setPin('');
        void act('pin', () => setPhonePin(value), t('phone.pin.saved')).then((ok) => { if (ok) setEditingPin(false); });
    };

    const status = talkStatus(settings);
    const knownVoice = !voices || voices.some((v) => v.id === settings.voice);

    return (
        <div className="phone-talk">
            <p className="settings-hint">{t('phone.talk.explain')}</p>
            <p className="settings-hint">{t('phone.talk.callIn')}</p>

            <div className={`phone-talk-status is-${status.state}`} role="status">
                {status.state === 'ready' && <><Check size={13} aria-hidden="true" /><span>{t('phone.talk.ready')}</span></>}
                {status.state === 'off' && <span>{t('phone.talk.off')}</span>}
                {status.state === 'missing' && (
                    <ul>
                        {status.needsPin && <li>{t('phone.talk.needsPin')}</li>}
                        {status.needsAccess && <li>{t('phone.talk.needsAccess')}</li>}
                    </ul>
                )}
            </div>

            <label className="setting-row">
                <span>{t('phone.talk.toggle')}</span>
                <span className="toggle-switch">
                    <input
                        type="checkbox"
                        checked={settings.talk_on_calls}
                        disabled={disabled}
                        onChange={() => void save({ talk_on_calls: !settings.talk_on_calls })}
                        aria-label={t('phone.talk.toggle')}
                    />
                    <span className="toggle-track" />
                </span>
            </label>

            <div className="phone-field">
                <div className="settings-field">
                    <span className="settings-label">{t('phone.pin.label')}</span>
                    <div className="phone-actions">
                        <span className="settings-hint">{t(settings.pin_set ? 'phone.pin.set' : 'phone.pin.unset')}</span>
                        {!editingPin && (
                            <button type="button" className="btn-secondary phone-btn" disabled={disabled} onClick={() => { setEditingPin(true); setPinError(null); }}>
                                {t(settings.pin_set ? 'phone.pin.change' : 'phone.pin.setButton')}
                            </button>
                        )}
                        {!editingPin && settings.pin_set && (
                            <button type="button" className="btn-secondary phone-btn" disabled={disabled} onClick={() => void act('pin', clearPhonePin, t('phone.pin.removed'))}>
                                {t('phone.pin.remove')}
                            </button>
                        )}
                    </div>
                </div>
            </div>

            {editingPin && (
                <form className="phone-form phone-pin" onSubmit={(e) => { e.preventDefault(); savePin(); }}>
                    <div className="settings-field">
                        <label className="settings-label" htmlFor="phone-pin">{t('phone.pin.new')}</label>
                        <input
                            id="phone-pin"
                            className="settings-input phone-code-input"
                            type="password"
                            inputMode="numeric"
                            autoComplete="new-password"
                            maxLength={8}
                            dir="ltr"
                            value={pin}
                            onChange={(e) => setPin(e.target.value.replace(/\D/g, ''))}
                        />
                        <span className="settings-hint">{t('phone.pin.hint')}</span>
                    </div>
                    <div className="phone-actions">
                        <button type="submit" className="btn-primary phone-btn" disabled={disabled}>
                            {busy === 'pin' && <Loader2 size={13} className="spin" />}
                            {t('phone.pin.save')}
                        </button>
                        <button type="button" className="btn-secondary phone-btn" disabled={disabled} onClick={() => { setEditingPin(false); setPin(''); setPinError(null); }}>
                            {t('phone.code.cancel')}
                        </button>
                    </div>
                    {pinError && <p className="settings-error" role="alert">{pinError}</p>}
                </form>
            )}

            <div className="phone-field">
                <div className="settings-field">
                    <label className="settings-label" htmlFor="phone-voice">{t('phone.talk.voice')}</label>
                    <div className="read-aloud-voice-row">
                        <select
                            id="phone-voice"
                            className="settings-input"
                            value={settings.voice}
                            disabled={disabled || !voices}
                            onChange={(e) => { stopPreview(); void save({ voice: e.target.value }); }}
                        >
                            {!knownVoice && <option value={settings.voice} lang="en">{settings.voice}</option>}
                            {!voices && <option value={settings.voice} lang="en">{settings.voice}</option>}
                            {voices && GROUPS.map((g) => (
                                <optgroup key={`${g.accent}-${g.gender}`} label={t.bare(`readAloud.group.${g.accent}.${g.gender}`)}>
                                    {voices.filter((v) => v.accent === g.accent && v.gender === g.gender).map((v) => (
                                        <option key={v.id} value={v.id} lang="en">{v.name}</option>
                                    ))}
                                </optgroup>
                            ))}
                        </select>
                        <button type="button" className="btn-secondary phone-btn" aria-pressed={previewing !== null} disabled={disabled} onClick={() => void preview()}>
                            {previewing === 'loading' ? <Loader2 size={13} className="spin" /> : previewing ? <Square size={13} /> : <Volume2 size={13} />}
                            {previewing ? t('phone.talk.stopPreview') : t('phone.talk.preview')}
                        </button>
                    </div>
                    {voicesError && <p className="settings-error" role="alert">{t('readAloud.settings.loadFailed', { reason: voicesError })}</p>}
                    {previewError && <p className="settings-error" role="alert">{t('phone.talk.previewFailed', { reason: previewError })}</p>}
                </div>
            </div>
        </div>
    );
}
