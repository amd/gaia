// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Voice mode: the shape and one state word, captions, and "What GAIA did" beside
 * it (a bottom sheet on a phone). Kept minimal on purpose: no helper text.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Check, Info, Keyboard, List, Mic, MicOff, Pause, Square, Volume2, Wrench, X } from 'lucide-react';
import { useEscapeToClose } from '../../hooks/useEscapeToClose';
import { useT } from '../../i18n/useT';
import { useVoiceStore, type VoiceLogRow, type VoicePhase } from '../../stores/voiceStore';
import {
    closeVoice, dismissPhoneNotice, holdEnd, holdStart, primaryAction, setCaptions, toggleMute, voiceLevels,
} from '../../voice/controller';
import { RenderedContent } from '../MessageBubble';
import { VoiceErrorCard } from './VoiceErrorCard';
import { VoiceViz, type VizState } from './VoiceViz';
import './VoiceScreen.css';

/** Close voice mode and put the cursor in the message box. */
function backToChat(): void {
    closeVoice();
    requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('.composer-input')?.focus());
}

const SHIMMERS: ReadonlySet<VoicePhase> = new Set(['warming', 'listening', 'thinking', 'speaking']);

function vizState(phase: VoicePhase, failed: boolean): VizState {
    if (failed || phase === 'ptt_idle') return 'paused';
    return phase;
}

function BigIcon({ phase }: { phase: VoicePhase }) {
    if (phase === 'listening') return <Pause size={24} fill="currentColor" strokeWidth={0} />;
    if (phase === 'thinking' || phase === 'speaking') return <Square size={22} fill="currentColor" strokeWidth={0} />;
    return <Mic size={24} />;
}

function Captions() {
    const t = useT();
    const heard = useVoiceStore((s) => s.heard);
    const partial = useVoiceStore((s) => s.heardPartial);
    const caption = useVoiceStore((s) => s.caption);
    const on = useVoiceStore((s) => s.captions);
    const words = caption ? caption.text.split(' ') : [];
    return (
        <div className="vs-caps" data-off={on ? undefined : ''} aria-hidden={!on}>
            <div className={`vs-you${partial ? ' is-partial' : ''}`} dir="auto">{heard}</div>
            <div className="vs-said" dir="auto" aria-label={caption ? t('voice.lead') : undefined}>
                {words.map((w, i) => (
                    <span key={i} className={`vs-w${i < (caption?.lit ?? 0) ? ' is-on' : ''}`}>{w}{i < words.length - 1 ? ' ' : ''}</span>
                ))}
            </div>
        </div>
    );
}

function LogRow({ row }: { row: VoiceLogRow }) {
    if (row.kind === 'answer') {
        return <div className="vs-result"><RenderedContent content={row.text} /></div>;
    }
    const icon = row.kind === 'user' ? <Mic size={14} /> : row.kind === 'status' ? <Volume2 size={14} /> : row.kind === 'tool' ? <Wrench size={14} /> : <Info size={14} />;
    return (
        <div className={`vs-row is-${row.kind}`}>
            <span className="vs-row-ic" aria-hidden="true">{icon}</span>
            {row.kind === 'tool' ? (
                <div className="vs-row-body">
                    <span className="vs-call">
                        <span className="vs-call-fn">
                            <span className="vs-call-st" aria-hidden="true">
                                {row.done ? (row.ok === false ? <X size={13} className="is-failed" /> : <Check size={13} className="is-ok" />) : <i className="vs-spin" />}
                            </span>
                            <bdi dir="ltr">{row.tool}</bdi>
                        </span>
                        {row.label && <span className="vs-call-args"><bdi>{row.label}</bdi></span>}
                        {row.result && <span className={`vs-call-res${row.ok === false ? ' is-failed' : ''}`}><bdi>{row.result}</bdi></span>}
                    </span>
                    {row.image && <img className="vs-shot" src={`/api/files/image?path=${encodeURIComponent(row.image)}`} alt="" />}
                </div>
            ) : (
                <div className="vs-row-body"><bdi>{row.text}</bdi></div>
            )}
        </div>
    );
}

function WorkLog({ sheet, onClose }: { sheet: boolean; onClose?: () => void }) {
    const t = useT();
    const log = useVoiceStore((s) => s.log);
    const tools = useVoiceStore((s) => s.tools);
    const listRef = useRef<HTMLDivElement>(null);
    useEffect(() => {
        const el = listRef.current;
        if (el) el.scrollTop = el.scrollHeight;
    }, [log]);
    return (
        <>
            <div className="vs-log-head" onClick={sheet ? onClose : undefined}>
                {sheet && <div className="vs-grab" aria-hidden="true" />}
                <div className="vs-log-title">
                    <span>{t('voice.did')}</span>
                    <bdi dir="ltr" className="vs-count">{tools}</bdi>
                    {sheet && (
                        <button type="button" className="vs-icon-btn vs-sheet-close" onClick={onClose} aria-label={t('settings.close')}>
                            <X size={16} />
                        </button>
                    )}
                </div>
            </div>
            <div className="vs-loglist" ref={listRef}>
                {log.map((row) => <LogRow key={row.id} row={row} />)}
            </div>
        </>
    );
}

/** `prompt`: the chat's permission card, shown here while voice mode covers the composer. */
export function VoiceScreen({ phone, prompt }: { phone: boolean; prompt?: ReactNode }) {
    const t = useT();
    const phase = useVoiceStore((s) => s.phase);
    const error = useVoiceStore((s) => s.error);
    const status = useVoiceStore((s) => s.status);
    const captions = useVoiceStore((s) => s.captions);
    const hasLog = useVoiceStore((s) => s.log.length > 0);
    const tools = useVoiceStore((s) => s.tools);
    const pushToTalk = useVoiceStore((s) => s.pushToTalk);
    const phoneNotice = useVoiceStore((s) => s.phoneNotice);
    const [sheet, setSheet] = useState(false);
    const bigRef = useRef<HTMLButtonElement>(null);

    useEscapeToClose(true, closeVoice);
    useEffect(() => { bigRef.current?.focus(); }, []);

    // Push-to-talk: hold Space.
    useEffect(() => {
        if (!pushToTalk) return;
        const down = (e: KeyboardEvent) => {
            if (e.code !== 'Space' || e.repeat) return;
            e.preventDefault();
            holdStart();
        };
        const up = (e: KeyboardEvent) => {
            if (e.code !== 'Space') return;
            e.preventDefault();
            holdEnd();
        };
        window.addEventListener('keydown', down);
        window.addEventListener('keyup', up);
        return () => {
            window.removeEventListener('keydown', down);
            window.removeEventListener('keyup', up);
        };
    }, [pushToTalk]);

    const failed = !!error;
    const shownPhase: VoicePhase = failed ? 'paused' : phase;
    const standby = useVoiceStore((s) => s.standby);
    const muted = useVoiceStore((s) => s.muted);
    const stateKey = `voice.state.${standby && shownPhase === 'listening' ? 'standby' : shownPhase}` as const;
    const level = shownPhase === 'speaking' ? voiceLevels.speech : voiceLevels.mic;
    const englishOnly = !!status && !status.speaks;
    const holdable = pushToTalk && !failed && (phase === 'ptt_idle' || phase === 'listening');
    const bigLabel = t(`voice.big.${holdable ? 'ptt_idle' : shownPhase}`);

    const big = (
        <button
            ref={bigRef}
            type="button"
            className={`vs-big${holdable && phase === 'listening' ? ' is-held' : ''}`}
            disabled={!failed && phase === 'warming'}
            aria-label={bigLabel}
            title={bigLabel}
            onClick={holdable ? undefined : primaryAction}
            onPointerDown={holdable ? (e) => { e.currentTarget.setPointerCapture?.(e.pointerId); holdStart(); } : undefined}
            onPointerUp={holdable ? holdEnd : undefined}
            onPointerCancel={holdable ? holdEnd : undefined}
        >
            <BigIcon phase={holdable ? 'ptt_idle' : shownPhase} />
        </button>
    );

    const ccButton = (
        <button
            type="button"
            className="vs-tbtn"
            aria-pressed={captions}
            aria-label={t('voice.captions')}
            title={t('voice.captions')}
            disabled={englishOnly}
            onClick={() => setCaptions(!captions)}
        >
            <bdi dir="ltr">CC</bdi>
        </button>
    );

    return (
        <div
            className={`voice-screen${phone ? ' is-phone' : ''}${hasLog && !phone ? ' has-log' : ''}`}
            role="dialog"
            aria-modal="true"
            aria-label={t('voice.open')}
        >
            <div className="vs-main">
                <div className="vs-top">
                    <span className="vs-pill"><i aria-hidden="true" />{t(phone ? 'voice.wherePhone' : 'voice.where')}</span>
                    <span className="vs-acts">
                        {!pushToTalk && (
                            <button
                                type="button"
                                className="vs-tbtn"
                                aria-pressed={muted}
                                aria-label={t(muted ? 'voice.unmute' : 'voice.mute')}
                                title={t(muted ? 'voice.unmute' : 'voice.mute')}
                                disabled={failed}
                                onClick={toggleMute}
                            >
                                {muted ? <MicOff size={16} /> : <Mic size={16} />}
                            </button>
                        )}
                        {ccButton}
                        {!phone && (
                            <button type="button" className="vs-tbtn" onClick={closeVoice} aria-label={t('voice.close')} title={t.hint(t('voice.close'), 'Esc')}>
                                <X size={16} />
                            </button>
                        )}
                    </span>
                </div>
                <div className="vs-center">
                    <VoiceViz className="vs-viz" state={vizState(phase, failed)} level={level} />
                    <div
                        className={`vs-state is-${shownPhase}${SHIMMERS.has(shownPhase) ? ' is-live' : ''}`}
                        data-word={t.bare(stateKey)}
                        aria-live="polite"
                    >
                        {t.bare(stateKey)}
                    </div>
                </div>
                <div className="vs-lower">
                    {englishOnly && !failed && <div className="vs-note" role="note">{t('voice.englishOnly')}</div>}
                    {prompt && <div className="vs-prompt">{prompt}</div>}
                    {failed ? <VoiceErrorCard /> : <Captions />}
                </div>
                {phone && hasLog && (
                    <button type="button" className="vs-logbar" onClick={() => setSheet(true)} aria-expanded={sheet}>
                        <List size={14} aria-hidden="true" />
                        <span>{t('voice.did')}</span>
                        <bdi dir="ltr" className="vs-count">{tools}</bdi>
                    </button>
                )}
                <div className="vs-ctl">
                    {phone && (
                        <button type="button" className="vs-side" onClick={backToChat} aria-label={t('voice.toChat')} title={t('voice.toChat')}>
                            <Keyboard size={20} />
                        </button>
                    )}
                    {big}
                    {phone && (
                        <button type="button" className="vs-side" onClick={closeVoice} aria-label={t('voice.close')} title={t('voice.close')}>
                            <X size={20} />
                        </button>
                    )}
                </div>
            </div>
            {phone ? (
                <aside className={`vs-sheet${sheet ? ' is-open' : ''}`} aria-hidden={!sheet} aria-label={t('voice.did')}>
                    <WorkLog sheet onClose={() => setSheet(false)} />
                </aside>
            ) : (
                <aside className="vs-log" aria-label={t('voice.did')} hidden={!hasLog}>
                    <WorkLog sheet={false} />
                </aside>
            )}
            {phone && phoneNotice && (
                <div className="vs-toast" role="status">
                    <span>{t('voice.phoneNotice')}</span>
                    <button type="button" onClick={dismissPhoneNotice}>{t('voice.gotIt')}</button>
                </div>
            )}
        </div>
    );
}
