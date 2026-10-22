// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { Composer } from './Composer';
import { FolderChip } from './FolderChip';
import { ModelChip } from './ModelChip';
import { NpuBackdrop } from './NpuBackdrop';
import { PermissionModeChip } from './PermissionModeChip';
import { useAttachments } from '../hooks/useAttachments';
import { useWarmUp, type WarmUpProgress } from '../hooks/useWarmUp';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { displayModelName, useModelNames } from '../stores/modelStore';
import { setVisibleInterval } from '../utils/visibleInterval';
import { STARTING_REASON } from '../utils/constants';
import { log } from '../utils/logger';
import { durationMs, prefersReducedMotion } from '../utils/motion';
import { deviceLabel } from '../utils/modelLabels';
import type { StartScreen } from '../types';
import './NewChat.css';

interface NewChatProps {
    /** Creates the chat and sends the first message; rejects with the reason it could not. */
    onSend: (text: string) => Promise<void>;
    /** Text to start the message box with, e.g. a task picked at the end of setup. */
    initialText?: string;
    /** Changes each time New chat is chosen, so the box takes focus even when already shown. */
    focusRequest?: number;
}

/** How often the start screen is asked for again while the assistant is unavailable. */
export const START_RETRY_MS = 15_000;

export function greeting(now: Date = new Date()): string {
    const h = now.getHours();
    const part = h < 5 ? 'evening' : h < 12 ? 'morning' : h < 18 ? 'afternoon' : 'evening';
    return `Good ${part}. What should we work on?`;
}

export function warmUpLine({ seconds, model, device }: WarmUpProgress): string {
    if (!model) return `Getting GAIA ready… ${seconds}s`;
    const where = deviceLabel(device);
    return `Loading ${displayModelName(model)}${where ? ` on ${where}` : ''}… ${seconds}s`;
}

/** The assistant didn't start: one plain sentence, a retry, and the full error behind Details. */
function AssistantUnavailable({ detail, onRetry }: { detail: string; onRetry: () => void }) {
    const [open, setOpen] = useState(false);
    return (
        <div className="new-chat-note">
            <p>
                GAIA&rsquo;s assistant didn&rsquo;t start yet.{' '}
                <button type="button" className="link-btn" onClick={onRetry}>Try again</button>
            </p>
            <button type="button" className="new-chat-details" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
                <ChevronRight size={12} className={open ? 'is-open' : ''} aria-hidden="true" /> Details
            </button>
            {open && <pre className="new-chat-details-text">{detail}</pre>}
        </div>
    );
}

/** An empty chat: a greeting and the composer over the NPU backdrop. Created on the first message. */
export function NewChat({ onSend, initialText = '', focusRequest = 0 }: NewChatProps) {
    const starting = useChatStore((s) => s.systemStatus?.init_state === 'initializing');
    const download = useChatStore((s) => s.systemStatus?.download_progress ?? null);
    const [text, setText] = useState(initialText);
    const [error, setError] = useState<string | null>(null);
    const [sending, setSending] = useState(false);
    const [start, setStart] = useState<StartScreen | null>(null);
    const [startError, setStartError] = useState<string | null>(null);
    const inputRef = useRef<HTMLTextAreaElement | null>(null);
    const innerRef = useRef<HTMLDivElement | null>(null);
    const composerRef = useRef<HTMLDivElement | null>(null);
    const attachments = useAttachments();
    const warming = useWarmUp();
    useModelNames();

    const live = useRef(true);
    // A background retry leaves the current reason up until there is a new answer.
    const loadStart = useCallback((retrying = false) => {
        if (!retrying) setStartError(null);
        api.getStartScreen()
            .then((s) => {
                if (!live.current) return;
                setStart(s);
                setStartError(s.skills_error ?? null);
            })
            .catch((err) => {
                log.api.warn('New-chat start screen unavailable', err);
                if (live.current) setStartError(err instanceof Error ? err.message : String(err));
            });
    }, []);

    useEffect(() => {
        live.current = true;
        loadStart();
        return () => { live.current = false; };
    }, [loadStart]);

    // The assistant may still be starting: keep asking while the reason is on screen.
    useEffect(() => {
        if (!startError) return;
        return setVisibleInterval(() => loadStart(true), START_RETRY_MS);
    }, [startError, loadStart]);

    const send = useCallback(async (message: string) => {
        if (!message || sending) return;
        setSending(true);
        setError(null);
        try {
            await onSend(message);
            setText('');
            attachments.clear();
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setSending(false);
        }
    }, [attachments, onSend, sending]);

    const submit = useCallback(() => send(attachments.compose(text.trim())), [attachments, send, text]);

    useEffect(() => {
        if (focusRequest) inputRef.current?.focus();
    }, [focusRequest]);

    // Typing drops the composer to the bottom, where the chat will continue.
    const docked = text.length > 0 || sending;
    const innerTop = useRef<{ top: number; docked: boolean } | null>(null);
    useLayoutEffect(() => {
        const el = innerRef.current;
        if (!el) return;
        // Measure from where it sits now, not mid-way through a previous move.
        if (typeof el.getAnimations === 'function') el.getAnimations().forEach((a) => a.cancel());
        const top = el.getBoundingClientRect().top;
        const before = innerTop.current;
        innerTop.current = { top, docked };
        if (!before || before.docked === docked || prefersReducedMotion() || typeof el.animate !== 'function') return;
        const easing = getComputedStyle(document.documentElement).getPropertyValue('--ease').trim();
        if (!easing) throw new Error('Motion token --ease is not set; check styles/index.css.');
        el.animate(
            [{ transform: `translateY(${before.top - top}px)` }, { transform: 'none' }],
            { duration: durationMs('--duration-spring'), easing },
        );
    });

    const downloading = download?.state === 'starting' || download?.state === 'downloading';
    const activity = sending ? 'sending' : downloading ? 'download' : starting || warming !== null ? 'loading' : 'idle';
    const folder = start?.working_folder;

    return (
        <main className={`new-chat${docked ? ' is-docked' : ''}`} aria-label="New chat">
            <NpuBackdrop
                activity={activity}
                progress={(download?.percent ?? 0) / 100}
                pulse={text.length}
                docked={docked}
                contentRef={innerRef}
                composerRef={composerRef}
            />
            <div className="new-chat-inner" ref={innerRef}>
                <h1 className="new-chat-greeting" aria-hidden={docked}>{greeting()}</h1>
                <div ref={composerRef}>
                    <Composer
                        value={text}
                        onChange={setText}
                        onSubmit={submit}
                        attachments={attachments}
                        disabledReason={starting ? STARTING_REASON : sending ? 'Starting the chat…' : null}
                        autoFocus
                        inputRef={inputRef}
                        leftControls={<PermissionModeChip sessionId={null} />}
                        rightControls={(
                            <>
                                {folder && <FolderChip path={folder.path} name={folder.name} gaiaFolder={folder.gaia_folder} />}
                                <ModelChip />
                            </>
                        )}
                    />
                </div>
                {warming !== null && !starting && (
                    <p className="new-chat-warmup" role="status">{warmUpLine(warming)}</p>
                )}
                {error && <p className="new-chat-error" role="alert">{error}</p>}
                {startError && <AssistantUnavailable detail={startError} onRetry={() => loadStart()} />}
            </div>
        </main>
    );
}
