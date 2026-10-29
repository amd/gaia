// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A generated music track or sound effect, playable inline in the chat. */

import { useCallback, useEffect, useRef, useState } from 'react';
import { AudioLines, FolderOpen, Music, Pause, Play } from 'lucide-react';
import * as api from '../services/api';
import { log } from '../utils/logger';
import type { GeneratedAudio } from '../types';
import {
    audioKindLabel, audioModelLabel, bestDuration, fileNameOf, formatAudioDuration, isPlayableWavPath,
} from '../utils/generatedAudio';
import './GeneratedAudioCard.css';

export const AUDIO_GONE_ERROR = "This file is no longer there. It may have been moved or deleted. Open its folder to look for it.";
export const AUDIO_OUT_OF_SCOPE_ERROR = "GAIA plays and opens files only inside your home folder. Open this one from your file manager; its full path is in the file name's tooltip.";
export const AUDIO_LOAD_ERROR = "Couldn't load this audio. Use Open to play it in your audio app.";
export const NOT_A_WAV_ERROR = "This result isn't a playable WAV file.";

/** The folder holding `path`, for "Open its folder" when the file itself is gone. */
export function parentFolder(path: string): string {
    const cut = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
    return cut > 0 ? path.slice(0, cut) : path;
}

/** Why the player could not load the file, asked of the server rather than guessed. */
async function diagnoseLoadFailure(path: string): Promise<string> {
    try {
        const { files } = await api.getFileStatus([path]);
        const exists = files[0]?.exists;
        if (exists === false) return AUDIO_GONE_ERROR;
        if (exists == null) return AUDIO_OUT_OF_SCOPE_ERROR;
    } catch (err) {
        log.ui.warn('Could not check why generated audio failed to load', err);
    }
    return AUDIO_LOAD_ERROR;
}

function errorText(err: unknown): string {
    if (err instanceof api.ApiError) return err.detail || err.message;
    return err instanceof Error ? err.message : String(err);
}

export function GeneratedAudioCard({ audio }: { audio: GeneratedAudio }) {
    const audioRef = useRef<HTMLAudioElement>(null);
    const [playing, setPlaying] = useState(false);
    const [position, setPosition] = useState(0);
    const [measured, setMeasured] = useState<number | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const loadFailed = loadError !== null;
    const [actionError, setActionError] = useState<string | null>(null);

    const playable = isPlayableWavPath(audio.path);
    const kind = audioKindLabel(audio.kind);
    const Icon = audio.kind === 'sfx' ? AudioLines : Music;
    const name = typeof audio.path === 'string' ? fileNameOf(audio.path) : '';
    const duration = bestDuration(audio, measured);
    const durationText = formatAudioDuration(duration);
    const model = audioModelLabel(audio.model);
    const prompt = typeof audio.prompt === 'string' ? audio.prompt.trim() : '';

    useEffect(() => () => audioRef.current?.pause(), []);

    const toggle = useCallback(() => {
        const el = audioRef.current;
        if (!el) return;
        if (el.paused) {
            // A pause during the load rejects play() with AbortError; the file is fine.
            el.play().catch((err: unknown) => {
                if ((err as { name?: string } | null)?.name !== 'AbortError') setLoadError(AUDIO_LOAD_ERROR);
            });
        } else {
            el.pause();
        }
    }, []);

    const act = (request: Promise<unknown>) => {
        setActionError(null);
        request.catch((err) => setActionError(`Couldn't open ${name}: ${errorText(err)}`));
    };

    const meta = [kind, model, durationText].filter(Boolean).join(' · ');

    return (
        <figure className="gen-audio" aria-label={`${kind}: ${name || 'generated audio'}`}>
            <div className="gen-audio__row">
                {playable && !loadFailed ? (
                    <button
                        type="button"
                        className="gen-audio__play"
                        onClick={toggle}
                        aria-label={playing ? `Pause ${name}` : `Play ${name}`}
                    >
                        {playing ? <Pause size={14} aria-hidden="true" /> : <Play size={14} aria-hidden="true" />}
                    </button>
                ) : (
                    <Icon size={16} className="gen-audio__icon" aria-hidden="true" />
                )}
                <div className="gen-audio__text">
                    <span className="gen-audio__name" title={typeof audio.path === 'string' ? audio.path : undefined}>{name || 'Generated audio'}</span>
                    <span className="gen-audio__meta">{meta}</span>
                </div>
                {playable && !loadFailed && (
                    <span className="gen-audio__time" aria-live="off">
                        {formatAudioDuration(position) || '0:00'}{durationText ? ` / ${durationText}` : ''}
                    </span>
                )}
            </div>
            {playable && !loadFailed && duration !== null && (
                <input
                    type="range"
                    className="gen-audio__seek"
                    min={0}
                    max={duration}
                    step={0.1}
                    value={Math.min(position, duration)}
                    aria-label={`Seek in ${name}`}
                    onChange={(e) => {
                        const el = audioRef.current;
                        const t = Number(e.target.value);
                        if (el && Number.isFinite(t)) el.currentTime = t;
                        setPosition(t);
                    }}
                />
            )}
            {prompt && <figcaption className="gen-audio__prompt" title={prompt}>{prompt}</figcaption>}
            {!playable && <p className="gen-audio__error" role="alert">{NOT_A_WAV_ERROR}</p>}
            {playable && loadFailed && <p className="gen-audio__error" role="alert">{loadError}</p>}
            {playable && (
                <div className="gen-audio__actions">
                    {loadError !== AUDIO_GONE_ERROR && loadError !== AUDIO_OUT_OF_SCOPE_ERROR && (
                        <button type="button" className="gen-audio__action" onClick={() => act(api.openFileInApp(audio.path))}>
                            Open
                        </button>
                    )}
                    {loadError === AUDIO_OUT_OF_SCOPE_ERROR ? null : loadError === AUDIO_GONE_ERROR ? (
                        <button type="button" className="gen-audio__action" onClick={() => act(api.openFileOrFolder(parentFolder(audio.path), false))}>
                            <FolderOpen size={11} aria-hidden="true" /> Open its folder
                        </button>
                    ) : (
                        <button type="button" className="gen-audio__action" onClick={() => act(api.openFileOrFolder(audio.path, true))}>
                            <FolderOpen size={11} aria-hidden="true" /> Show in folder
                        </button>
                    )}
                </div>
            )}
            {actionError && <p className="gen-audio__error" role="alert">{actionError}</p>}
            {playable && (
                <audio
                    ref={audioRef}
                    preload="metadata"
                    src={api.audioFileUrl(audio.path)}
                    onLoadedMetadata={(e) => {
                        const d = e.currentTarget.duration;
                        if (Number.isFinite(d) && d > 0) setMeasured(d);
                    }}
                    onTimeUpdate={(e) => setPosition(e.currentTarget.currentTime)}
                    onPlay={() => setPlaying(true)}
                    onPause={() => setPlaying(false)}
                    onEnded={() => { setPlaying(false); setPosition(0); }}
                    onError={() => {
                        setPlaying(false);
                        setLoadError(AUDIO_LOAD_ERROR);
                        diagnoseLoadFailure(audio.path).then(setLoadError);
                    }}
                />
            )}
        </figure>
    );
}

/** Every track this turn generated, once each, above the answer. */
export function GeneratedAudioList({ items }: { items: GeneratedAudio[] }) {
    if (items.length === 0) return null;
    return (
        <div className="gen-audio-list">
            {items.map((audio) => <GeneratedAudioCard key={audio.path} audio={audio} />)}
        </div>
    );
}
