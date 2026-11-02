// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ChevronRight, FileText, X } from 'lucide-react';
import { Composer } from './Composer';
import { FolderChip } from './FolderChip';
import { ModelChip } from './ModelChip';
import { NpuBackdrop } from './NpuBackdrop';
import { PermissionModeChip } from './PermissionModeChip';
import { PrivateChip } from './PrivateChip';
import { useAttachments } from '../hooks/useAttachments';
import { useWarmUp, type WarmUpProgress } from '../hooks/useWarmUp';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { displayModelName, useModelNames, useModelStore } from '../stores/modelStore';
import * as messageQueue from '../utils/messageQueue';
import { setVisibleInterval } from '../utils/visibleInterval';
import { log } from '../utils/logger';
import { durationMs, prefersReducedMotion } from '../utils/motion';
import { deviceLabel } from '../utils/modelLabels';
import type { StartScreen } from '../types';
import './NewChat.css';
import { useSavedDraft } from '../hooks/useSavedDraft';
import { NEW_CHAT_DRAFT_KEY } from '../utils/savedDraft';
import { ref, type MessageKey } from '../i18n/registry';
import { tEn, useT, type Translate } from '../i18n/useT';

interface NewChatProps {
    /**
     * Creates the chat and sends the first message; rejects with the reason it could not.
     * Resolves false when the chat was made but the message was not sent (it says why itself).
     */
    onSend: (text: string) => Promise<boolean | void>;
    /** Text to start the message box with, e.g. a task picked at the end of setup. */
    initialText?: string;
    /** Changes each time New chat is chosen, so the box takes focus even when already shown. */
    focusRequest?: number;
}

/** How often the start screen is asked for again while the assistant is unavailable. */
export const START_RETRY_MS = 15_000;

export function greeting(now: Date = new Date(), t: Translate = tEn): string {
    const h = now.getHours();
    const part = h < 5 ? 'evening' : h < 12 ? 'morning' : h < 18 ? 'afternoon' : 'evening';
    return t(`newChat.greeting.${part}`);
}

const DEVICE_KEYS: Record<string, MessageKey> = { gpu: 'device.gpu', npu: 'device.npu', cpu: 'device.cpu' };

export function warmUpLine({ seconds, model, device }: WarmUpProgress, t: Translate = tEn): string {
    if (!model) return t('newChat.warmup.ready', { s: seconds });
    const name = displayModelName(model);
    const where = device ? DEVICE_KEYS[device] : undefined;
    if (where) return t('newChat.warmup.loadingOn', { model: name, where: ref(where), s: seconds });
    // A device the labels don't know yet still reads in English rather than vanishing.
    const raw = deviceLabel(device);
    return raw
        ? t('newChat.warmup.loadingOn', { model: name, where: raw, s: seconds })
        : t('newChat.warmup.loading', { model: name, s: seconds });
}

/** The assistant didn't start: one plain sentence, a retry, and the full error behind Details. */
function AssistantUnavailable({ detail, onRetry }: { detail: string; onRetry: () => void }) {
    const t = useT();
    const [open, setOpen] = useState(false);
    return (
        <div className="new-chat-note">
            <p>
                {t('newChat.assistantUnavailable')}{' '}
                <button type="button" className="link-btn" onClick={onRetry}>{t('newChat.tryAgain')}</button>
            </p>
            <button type="button" className="new-chat-details" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
                <ChevronRight size={12} className={open ? 'is-open' : ''} aria-hidden="true" /> {t('newChat.details')}
            </button>
            {open && <pre className="new-chat-details-text">{detail}</pre>}
        </div>
    );
}

/** Hold every indexed library document for the chat about to be created. */
function attachWholeLibrary() {
    const { documents, draftDocumentIds, addDraftDocuments } = useChatStore.getState();
    addDraftDocuments(documents
        .filter((d) => d.indexing_status === 'complete' && !draftDocumentIds.includes(d.id))
        .map((d) => d.id));
}

/**
 * A chat searches only the documents attached to it: show what this one will
 * get, and offer the rest of the library.
 */
function LibraryDocuments() {
    const t = useT();
    const documents = useChatStore((s) => s.documents);
    const draftIds = useChatStore((s) => s.draftDocumentIds);
    const removeDraftDocument = useChatStore((s) => s.removeDraftDocument);
    const setShowDocLibrary = useChatStore((s) => s.setShowDocLibrary);
    const attached = documents.filter((d) => draftIds.includes(d.id));
    const offered = documents.filter((d) => d.indexing_status === 'complete' && !draftIds.includes(d.id));
    if (!attached.length && !offered.length) return null;
    const n = offered.length;
    return (
        <div className="new-chat-docs">
            {attached.length > 0 && (
                <ul className="new-chat-doc-list" aria-label={t('newChat.docs.forChat')}>
                    {attached.map((d) => (
                        <li key={d.id} className="new-chat-doc">
                            <FileText size={12} aria-hidden="true" />
                            <span dir="auto">{d.filename}</span>
                            <button
                                type="button"
                                className="new-chat-doc-remove"
                                onClick={() => removeDraftDocument(d.id)}
                                aria-label={t('newChat.docs.dontAttach', { name: d.filename })}
                            >
                                <X size={12} aria-hidden="true" />
                            </button>
                        </li>
                    ))}
                </ul>
            )}
            {n > 0 && (
                <p className="new-chat-note">
                    {attached.length
                        ? t.plural('newChat.docs.more', n)
                        : t.plural('newChat.docs.library', n)}{' '}
                    <button type="button" className="link-btn" onClick={attachWholeLibrary}>
                        {n === 1 ? t('newChat.docs.attachIt') : t('newChat.docs.attachAll', { n })}
                    </button>
                    {n > 1 && (
                        <>
                            {' · '}
                            <button type="button" className="link-btn" onClick={() => setShowDocLibrary(true)}>
                                {t('newChat.docs.choose')}
                            </button>
                        </>
                    )}
                </p>
            )}
        </div>
    );
}

/** First-run setup failed: say so before the first message fails with the same error. */
function SetupFailed({ error }: { error: string }) {
    const t = useT();
    const openSettings = useChatStore((s) => s.openSettings);
    return (
        <div className="new-chat-setup-failed" role="alert">
            <p className="new-chat-setup-failed-title">{t('newChat.setupFailed.title')}</p>
            <p className="new-chat-setup-failed-error">{error}</p>
            <p>
                {t('newChat.setupFailed.hint')}{' '}
                <button type="button" className="link-btn" onClick={() => openSettings('model')}>{t('newChat.setupFailed.choose')}</button>
            </p>
        </div>
    );
}

/** An empty chat: a greeting and the composer over the NPU backdrop. Created on the first message. */
export function NewChat({ onSend, initialText = '', focusRequest = 0 }: NewChatProps) {
    const t = useT();
    const starting = useChatStore((s) => s.systemStatus?.init_state === 'initializing');
    const download = useChatStore((s) => s.systemStatus?.download_progress ?? null);
    // Moot once chats run in the cloud or a local model has loaded after all.
    const setupError = useChatStore((s) => (s.systemStatus?.model_loaded ? null : s.systemStatus?.setup_error ?? null));
    const remote = useModelStore((s) => !!s.active?.remote);
    const [text, setText] = useSavedDraft(NEW_CHAT_DRAFT_KEY, initialText);
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

    useEffect(() => {
        api.listDocuments()
            .then((data) => useChatStore.getState().setDocuments(data.documents || []))
            .catch((err) => log.api.warn('Document library unavailable; not offering its documents', err));
    }, []);

    // The assistant may still be starting: keep asking while the reason is on screen.
    useEffect(() => {
        if (!startError) return;
        return setVisibleInterval(() => loadStart(true), START_RETRY_MS);
    }, [startError, loadStart]);

    // Set before any re-render, so keypresses landing in the same tick are held too.
    const sendingRef = useRef(false);
    // Sent while the chat was still being created: they wait in its queue, never dropped.
    const heldRef = useRef<string[]>([]);
    const send = useCallback(async (message: string, draft = '') => {
        if (!message) return;
        if (sendingRef.current) {
            // As typed: the attachments still belong to the first message.
            heldRef.current.push(draft || message);
            setText('');
            return;
        }
        sendingRef.current = true;
        setSending(true);
        setError(null);
        // Cleared now, so anything typed while the chat is created is a new message.
        setText('');
        try {
            const sent = await onSend(message);
            attachments.clear();
            const held = heldRef.current.splice(0);
            const sessionId = useChatStore.getState().currentSessionId;
            if (held.length > 0 && sessionId) {
                // The chat is open now; if its first message never went out, the rest wait for Send again.
                const add = (items: messageQueue.QueuedMessage[], text: string) => sent === false
                    ? [...items, { ...messageQueue.item(text, 'failed'), error: 'The first message was not sent.' }]
                    : messageQueue.enqueue(items, text, 'queued');
                useChatStore.getState().updateQueue(sessionId, (q) => held.reduce(add, q));
            }
        } catch (err) {
            // Nothing was sent, so every message goes back in the box.
            const held = heldRef.current.splice(0);
            setText([draft || message, ...held].join('\n\n'));
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            sendingRef.current = false;
            setSending(false);
        }
    }, [attachments, onSend]);

    const submit = useCallback(() => send(attachments.compose(text.trim()), text.trim()), [attachments, send, text]);

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
        <main className={`new-chat${docked ? ' is-docked' : ''}`} aria-label={t('newChat.label')}>
            <NpuBackdrop
                activity={activity}
                progress={(download?.percent ?? 0) / 100}
                pulse={text.length}
                docked={docked}
                contentRef={innerRef}
                composerRef={composerRef}
            />
            <div className="new-chat-inner" ref={innerRef}>
                <h1 className="new-chat-greeting" aria-hidden={docked}>{greeting(new Date(), t)}</h1>
                {setupError && !remote && <SetupFailed error={setupError} />}
                <div ref={composerRef}>
                    <Composer
                        value={text}
                        onChange={setText}
                        onSubmit={submit}
                        attachments={attachments}
                        disabledReason={starting ? t('composer.starting') : null}
                        autoFocus
                        inputRef={inputRef}
                        leftControls={<><PermissionModeChip sessionId={null} /><PrivateChip disabled={sending} /></>}
                        rightControls={<ModelChip />}
                    />
                </div>
                <div className="new-chat-meta">
                    {folder && <FolderChip folder={folder} disabled={starting || sending} />}
                </div>
                <LibraryDocuments />
                {warming !== null && !starting && (
                    <p className="new-chat-warmup" role="status">{warmUpLine(warming, t)}</p>
                )}
                {error && <p className="new-chat-error" role="alert">{error}</p>}
                {startError && <AssistantUnavailable detail={startError} onRetry={() => loadStart()} />}
            </div>
        </main>
    );
}
