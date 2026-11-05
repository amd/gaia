// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useLayoutEffect, useRef, type ReactNode, type RefObject } from 'react';
import { ArrowUp, AudioLines, FileText, Plus, Square, X } from 'lucide-react';
import type { UseAttachments } from '../hooks/useAttachments';
import { useDictation } from '../hooks/useDictation';
import { useT } from '../i18n/useT';
import { DictationNotice, MicButton } from './voice/MicButton';
import { LevelMeter } from './voice/LevelMeter';
import './Composer.css';

interface ComposerProps {
    value: string;
    onChange: (value: string) => void;
    onSubmit: () => void;
    onStop?: () => void;
    streaming?: boolean;
    /** Stop was pressed and the run is winding down. */
    stopping?: boolean;
    /** While streaming, Enter still submits: the message is queued for the next turn. */
    queueWhileStreaming?: boolean;
    /** Blocks typing and sending, with the reason shown as the placeholder. */
    disabledReason?: string | null;
    attachments: UseAttachments;
    placeholder?: string;
    autoFocus?: boolean;
    inputRef?: RefObject<HTMLTextAreaElement | null>;
    /** Small inline controls on the left of the bottom row (permission mode). */
    leftControls?: ReactNode;
    /** Small inline controls before the send button (model). */
    rightControls?: ReactNode;
    /** Opens voice mode; the send button becomes the voice button while the box is empty. */
    onVoice?: () => void;
}

const MAX_INPUT_HEIGHT = 240;

/** The most a message may carry; the backend refuses more (`ChatRequest.message` in ui/models.py). */
export const MAX_MESSAGE_CHARS = 100_000;

export function Composer({
    value, onChange, onSubmit, onStop, streaming = false, stopping = false, queueWhileStreaming = false, disabledReason, attachments,
    placeholder, autoFocus, inputRef, leftControls, rightControls, onVoice,
}: ComposerProps) {
    const t = useT();
    const ownRef = useRef<HTMLTextAreaElement | null>(null);
    const textareaRef = inputRef ?? ownRef;
    const fileRef = useRef<HTMLInputElement>(null);
    const disabled = !!disabledReason;
    const hasContent = value.trim().length > 0 || attachments.hasUploaded;
    // What is sent: the text plus a link for each attached file.
    const sentLength = attachments.compose(value.trim()).length;
    const tooLong = sentLength > MAX_MESSAGE_CHARS;
    const canSend = !disabled && (!streaming || queueWhileStreaming) && !attachments.uploading && hasContent && !tooLong;

    const shownPlaceholder = disabledReason || placeholder || t('composer.placeholder');

    // Dictation: the words go in where the cursor was when it started.
    const cursorRef = useRef<[number, number] | null>(null);
    const valueRef = useRef(value);
    valueRef.current = value;
    const dictation = useDictation((heard) => {
        const current = valueRef.current;
        const [from, to] = cursorRef.current ?? [current.length, current.length];
        const before = current.slice(0, from);
        const after = current.slice(to);
        const lead = before && !/\s$/.test(before) ? ' ' : '';
        const trail = after && !/^\s/.test(after) ? ' ' : '';
        const caret = before.length + lead.length + heard.length;
        onChange(`${before}${lead}${heard}${trail}${after}`);
        requestAnimationFrame(() => {
            const el = textareaRef.current;
            if (!el) return;
            el.focus();
            el.setSelectionRange(caret, caret);
        });
    });
    const recording = dictation.state !== 'idle';
    // Where the cursor last was in the box; clicking the mic moves focus off it.
    const caretRef = useRef<[number, number] | null>(null);
    const rememberCaret = (e: React.SyntheticEvent<HTMLTextAreaElement>) => {
        caretRef.current = [e.currentTarget.selectionStart, e.currentTarget.selectionEnd];
    };
    const startDictation = useCallback(() => {
        const end = value.length;
        const [from, to] = caretRef.current ?? [end, end];
        cursorRef.current = [Math.min(from, end), Math.min(to, end)];
        dictation.start();
    }, [dictation, value.length]);

    // Enter or Esc stops dictation; Enter does not send.
    const stopDictation = dictation.stop;
    useEffect(() => {
        if (!recording) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key !== 'Enter' && e.key !== 'Escape') return;
            e.preventDefault();
            e.stopPropagation();
            stopDictation();
        };
        window.addEventListener('keydown', onKey, true);
        return () => window.removeEventListener('keydown', onKey, true);
    }, [recording, stopDictation]);
    const showVoice = !!onVoice && !streaming && !hasContent && !recording;
    const elapsed = `${Math.floor(dictation.elapsed / 60)}:${String(dictation.elapsed % 60).padStart(2, '0')}`;

    // Grow with the text, up to a cap; shrink back when it is cleared.
    // Chrome counts the placeholder in scrollHeight, and a narrow first layout
    // wraps it, so re-measure on placeholder and width changes too.
    useLayoutEffect(() => {
        const el = textareaRef.current;
        if (!el) return;
        const fit = () => {
            el.style.height = 'auto';
            el.style.height = `${Math.min(el.scrollHeight, MAX_INPUT_HEIGHT)}px`;
        };
        fit();
        if (typeof ResizeObserver === 'undefined') return;
        let width = el.clientWidth;
        const observer = new ResizeObserver(() => {
            if (el.clientWidth === width) return;
            width = el.clientWidth;
            fit();
        });
        observer.observe(el);
        return () => observer.disconnect();
    }, [value, shownPlaceholder, textareaRef]);

    const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (canSend) onSubmit();
        }
    }, [canSend, onSubmit]);

    const handleDrop = useCallback((e: React.DragEvent) => {
        if (!e.dataTransfer?.files?.length) return;
        e.preventDefault();
        e.stopPropagation();
        attachments.addFiles(Array.from(e.dataTransfer.files));
    }, [attachments]);

    return (
        <div
            className={`composer${disabled ? ' composer-disabled' : ''}${recording ? ' is-recording' : ''}`}
            onDrop={handleDrop}
            onDragOver={(e) => { if (e.dataTransfer?.types?.includes('Files')) e.preventDefault(); }}
        >
            {attachments.attachments.length > 0 && (
                <ul className="composer-attachments" aria-label={t('composer.attached')}>
                    {attachments.attachments.map((a) => (
                        <li key={a.id} className={`composer-attachment${a.error ? ' has-error' : ''}`}>
                            {a.isImage && a.url
                                ? <img src={a.url} alt="" className="composer-attachment-thumb" />
                                : <FileText size={14} aria-hidden="true" />}
                            <span className="composer-attachment-name" title={a.name} dir="auto">{a.name}</span>
                            {a.uploading && <span className="composer-attachment-state">{t('composer.uploading')}</span>}
                            {a.error && <span className="composer-attachment-state" role="alert" title={a.error}>{a.error}</span>}
                            <button
                                type="button"
                                className="composer-icon-btn"
                                onClick={() => attachments.remove(a.id)}
                                aria-label={t('composer.remove', { name: a.name })}
                            >
                                <X size={12} />
                            </button>
                        </li>
                    ))}
                </ul>
            )}
            {recording && (
                <div className="composer-livebox" dir="auto" aria-live="polite" aria-label={t('dictation.live')}>
                    {value && <span>{value} </span>}
                    {dictation.committed && <span>{dictation.committed} </span>}
                    <span className="composer-livebox-partial">{dictation.partial}</span>
                    <span className="composer-livebox-cursor" aria-hidden="true" />
                </div>
            )}
            <textarea
                ref={textareaRef}
                hidden={recording}
                className="composer-input"
                value={value}
                onChange={(e) => onChange(e.target.value)}
                onKeyDown={handleKeyDown}
                onPaste={(e) => { attachments.handlePaste(e); }}
                onSelect={rememberCaret}
                placeholder={shownPlaceholder}
                rows={1}
                disabled={disabled}
                autoFocus={autoFocus}
                aria-label={t('composer.message')}
                dir="auto"
            />
            <DictationNotice dictation={dictation} />
            {tooLong && (
                <p className="composer-limit-error" role="alert">
                    {t('composer.tooLong', { sent: sentLength.toLocaleString(), max: MAX_MESSAGE_CHARS.toLocaleString() })}
                    {' '}{t('composer.tooLongHint')}
                </p>
            )}
            <div className="composer-row">
                <div className="composer-row-left">
                    <button
                        type="button"
                        className="composer-icon-btn"
                        onClick={() => fileRef.current?.click()}
                        disabled={disabled}
                        aria-label={t('composer.attach')}
                        title={t('composer.attach')}
                    >
                        <Plus size={16} />
                    </button>
                    <input
                        ref={fileRef}
                        type="file"
                        multiple
                        hidden
                        onChange={(e) => {
                            if (e.target.files) attachments.addFiles(Array.from(e.target.files));
                            e.target.value = '';
                        }}
                    />
                    <MicButton dictation={dictation} onStart={startDictation} disabled={disabled} />
                    {recording && (
                        <span className="composer-rec">
                            <LevelMeter level={dictation.level} className="composer-rec-wave" />
                            <bdi dir="ltr" className="composer-rec-time">{elapsed}</bdi>
                        </span>
                    )}
                    {!recording && leftControls}
                </div>
                <div className="composer-row-right">
                    {rightControls}
                    {streaming && queueWhileStreaming && hasContent && (
                        <button
                            type="button"
                            className="composer-send"
                            onClick={onSubmit}
                            disabled={!canSend}
                            aria-label={t('composer.queue')}
                            title={t('composer.queueTitle')}
                        >
                            <ArrowUp size={16} />
                        </button>
                    )}
                    {streaming ? (
                        <button
                            type="button"
                            className="composer-send is-stop"
                            onClick={onStop}
                            disabled={stopping}
                            aria-label={t('composer.stop')}
                            title={stopping ? t('composer.stopping') : t.hint(t('composer.stop'), 'Esc')}
                        >
                            <Square size={12} fill="currentColor" />
                        </button>
                    ) : showVoice ? (
                        <button
                            type="button"
                            className="composer-send is-voice"
                            onClick={onVoice}
                            disabled={disabled}
                            aria-label={t('voice.open')}
                            title={t('voice.open')}
                        >
                            <AudioLines size={16} />
                        </button>
                    ) : (
                        <button
                            type="button"
                            className="composer-send"
                            onClick={onSubmit}
                            disabled={!canSend || recording}
                            aria-label={t('composer.send')}
                            title={t.hint(t('composer.send'), 'Enter')}
                        >
                            <ArrowUp size={16} />
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}
