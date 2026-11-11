// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The voice screen's own settings: volume, voice, speed and the wake word, changed without leaving it. */

import { useEffect, useRef, useState } from 'react';
import { Settings2 } from 'lucide-react';
import { useT } from '../../i18n/useT';
import { getSpeechSettings, updateSpeechSettings, type SpeechSettings } from '../../services/speechApi';
import { useVoiceStore } from '../../stores/voiceStore';
import { log } from '../../utils/logger';
import { setVolume, setWakeWord, voiceVolume } from '../../voice/controller';

const SPEEDS = [80, 90, 100, 110, 125, 150];

export function VoiceQuickSettings() {
    const t = useT();
    const [open, setOpen] = useState(false);
    const [speech, setSpeech] = useState<SpeechSettings | null>(null);
    const [failed, setFailed] = useState(false);
    const [volume, setVolumeState] = useState(() => Math.round(voiceVolume() * 100));
    const wakeWord = useVoiceStore((s) => s.status?.wake_word !== false);
    const pushToTalk = useVoiceStore((s) => s.pushToTalk);
    const boxRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!open) return;
        setFailed(false);
        getSpeechSettings().then(setSpeech, (err: unknown) => {
            log.ui.warn('Voice settings could not be read', err);
            setFailed(true);
        });
        const away = (e: PointerEvent) => {
            if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
        };
        document.addEventListener('pointerdown', away);
        return () => document.removeEventListener('pointerdown', away);
    }, [open]);

    const save = (change: Partial<Pick<SpeechSettings, 'voice' | 'speed'>>) => {
        setFailed(false);
        updateSpeechSettings(change).then(setSpeech, (err: unknown) => {
            log.ui.warn('Voice settings could not be saved', err);
            setFailed(true);
        });
    };

    return (
        <div className="vs-set" ref={boxRef}>
            <button
                type="button"
                className="vs-tbtn"
                aria-expanded={open}
                aria-haspopup="dialog"
                aria-label={t('voice.quick.open')}
                title={t('voice.quick.open')}
                onClick={() => setOpen(!open)}
            >
                <Settings2 size={16} />
            </button>
            {open && (
                <div className="vs-set-panel" role="dialog" aria-label={t('voice.quick.open')}>
                    <label className="vs-set-row">
                        <span>{t('voice.quick.volume')}</span>
                        <input
                            type="range"
                            min={0}
                            max={100}
                            step={5}
                            value={volume}
                            onChange={(e) => {
                                const next = Number(e.target.value);
                                setVolumeState(next);
                                setVolume(next / 100);
                            }}
                        />
                        <bdi dir="ltr" className="vs-set-val">{volume}%</bdi>
                    </label>
                    <label className="vs-set-row">
                        <span>{t('readAloud.settings.voice')}</span>
                        <select value={speech?.voice ?? ''} disabled={!speech} onChange={(e) => save({ voice: e.target.value })}>
                            {(speech?.voices ?? []).map((v) => (
                                <option key={v.id} value={v.id}>
                                    {v.name} · {t.bare(`readAloud.group.${v.accent}.${v.gender}`)}
                                </option>
                            ))}
                        </select>
                    </label>
                    <label className="vs-set-row">
                        <span>{t('voice.quick.speed')}</span>
                        <select value={speech?.speed ?? 100} disabled={!speech} onChange={(e) => save({ speed: Number(e.target.value) })}>
                            {[...new Set([...SPEEDS, speech?.speed ?? 100])].sort((a, b) => a - b).map((s) => (
                                <option key={s} value={s}>{s}%</option>
                            ))}
                        </select>
                    </label>
                    {!pushToTalk && (
                        <label className="vs-set-row is-check">
                            <span>{t('voice.settings.wakeWord')}</span>
                            <input type="checkbox" checked={wakeWord} onChange={() => setWakeWord(!wakeWord)} />
                        </label>
                    )}
                    <p className="vs-set-note">{t('voice.quick.note')}</p>
                    {failed && <p className="vs-set-error" role="alert">{t('voice.quick.failed')}</p>}
                </div>
            )}
        </div>
    );
}
