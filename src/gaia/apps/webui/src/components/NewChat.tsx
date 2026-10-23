// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronRight, FileText, Folder, X } from 'lucide-react';
import { Composer } from './Composer';
import { ModelChip } from './ModelChip';
import { PermissionModeChip } from './PermissionModeChip';
import { PrivateChip } from './PrivateChip';
import { StartCards } from './StartCards';
import { useAttachments } from '../hooks/useAttachments';
import { useWarmUp, type WarmUpProgress } from '../hooks/useWarmUp';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { displayModelName, useModelNames, useModelStore } from '../stores/modelStore';
import { setVisibleInterval } from '../utils/visibleInterval';
import { STARTING_REASON } from '../utils/constants';
import { log } from '../utils/logger';
import { deviceLabel } from '../utils/modelLabels';
import type { StartCard, StartScreen } from '../types';
import './NewChat.css';

interface NewChatProps {
    /** Creates the chat and sends the first message; rejects with the reason it could not. */
    onSend: (text: string) => Promise<void>;
    /** Text to start the message box with, e.g. a task picked at the end of setup. */
    initialText?: string;
    /** Changes each time New chat is chosen, so the box takes focus even when already shown. */
    focusRequest?: number;
}

/** How often the new-chat cards are asked for again while they are unavailable. */
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

/** No suggestions: one plain sentence, a retry, and the full error behind Details. */
function SuggestionsUnavailable({ detail, onRetry }: { detail: string; onRetry: () => void }) {
    const [open, setOpen] = useState(false);
    return (
        <div className="new-chat-note">
            <p>
                GAIA&rsquo;s assistant didn&rsquo;t start, so it can&rsquo;t suggest tasks yet.{' '}
                <button type="button" className="link-btn" onClick={onRetry}>Try again</button>
            </p>
            <button type="button" className="new-chat-details" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
                <ChevronRight size={12} className={open ? 'is-open' : ''} aria-hidden="true" /> Details
            </button>
            {open && <pre className="new-chat-details-text">{detail}</pre>}
        </div>
    );
}

/** The start card that asks about the library's documents. */
const DOCUMENT_CARD = 'document-brief';

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
                <ul className="new-chat-doc-list" aria-label="Documents for this chat">
                    {attached.map((d) => (
                        <li key={d.id} className="new-chat-doc">
                            <FileText size={12} aria-hidden="true" />
                            <span>{d.filename}</span>
                            <button
                                type="button"
                                className="new-chat-doc-remove"
                                onClick={() => removeDraftDocument(d.id)}
                                aria-label={`Don't attach ${d.filename}`}
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
                        ? `${n} more document${n !== 1 ? 's' : ''} in your library. `
                        : `Your library has ${n} document${n !== 1 ? 's' : ''}; this chat can search only the ones you attach. `}
                    <button type="button" className="link-btn" onClick={attachWholeLibrary}>
                        Attach {n === 1 ? 'it' : `all ${n}`}
                    </button>
                    {n > 1 && (
                        <>
                            {' · '}
                            <button type="button" className="link-btn" onClick={() => setShowDocLibrary(true)}>
                                Choose
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
    const openSettings = useChatStore((s) => s.openSettings);
    return (
        <div className="new-chat-setup-failed" role="alert">
            <p className="new-chat-setup-failed-title">GAIA&rsquo;s first-run setup didn&rsquo;t finish, so a chat on this PC&rsquo;s model will fail.</p>
            <p className="new-chat-setup-failed-error">{error}</p>
            <p>
                Use a cloud model, or pick a smaller one.{' '}
                <button type="button" className="link-btn" onClick={() => openSettings('model')}>Choose a model</button>
            </p>
        </div>
    );
}

/** An empty chat: a greeting, the composer, tasks to start from. Created on the first message. */
export function NewChat({ onSend, initialText = '', focusRequest = 0 }: NewChatProps) {
    const starting = useChatStore((s) => s.systemStatus?.init_state === 'initializing');
    // Moot once chats run in the cloud or a local model has loaded after all.
    const setupError = useChatStore((s) => (s.systemStatus?.model_loaded ? null : s.systemStatus?.setup_error ?? null));
    const remote = useModelStore((s) => !!s.active?.remote);
    const [text, setText] = useState(initialText);
    const [error, setError] = useState<string | null>(null);
    const [sending, setSending] = useState(false);
    const [start, setStart] = useState<StartScreen | null>(null);
    const [startError, setStartError] = useState<string | null>(null);
    const inputRef = useRef<HTMLTextAreaElement | null>(null);
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
                log.api.warn('New-chat suggestions unavailable', err);
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

    const pick = useCallback((card: StartCard) => {
        // The card counts the library's documents; without them attached the chat has none.
        if (card.skill === DOCUMENT_CARD) attachWholeLibrary();
        if (!card.needs_input) {
            void send(attachments.compose(card.prompt));
            return;
        }
        setText(card.prompt);
        inputRef.current?.focus();
    }, [attachments, send]);

    const folder = start?.working_folder;

    return (
        <main className="new-chat" aria-label="New chat">
            <div className="new-chat-inner">
                <h1 className="new-chat-greeting">{greeting()}</h1>
                {setupError && !remote && <SetupFailed error={setupError} />}
                <Composer
                    value={text}
                    onChange={setText}
                    onSubmit={submit}
                    attachments={attachments}
                    disabledReason={starting ? STARTING_REASON : sending ? 'Starting the chat…' : null}
                    autoFocus
                    inputRef={inputRef}
                    leftControls={<><PermissionModeChip sessionId={null} /><PrivateChip disabled={sending} /></>}
                    rightControls={<ModelChip />}
                />
                {folder && (
                    <div className="new-chat-meta">
                        <span
                            className="new-chat-folder"
                            title={folder.gaia_folder
                                ? `GAIA works in its own folder, ${folder.path}. Attach a file or start GAIA from a project folder to work there.`
                                : `GAIA works in ${folder.path}`}
                        >
                            <Folder size={13} aria-hidden="true" />
                            <span>{folder.name}</span>
                        </span>
                    </div>
                )}
                <LibraryDocuments />
                {warming !== null && !starting && (
                    <p className="new-chat-warmup" role="status">{warmUpLine(warming)}</p>
                )}
                {error && <p className="new-chat-error" role="alert">{error}</p>}
                {start && <StartCards cards={start.cards} onPick={pick} disabled={starting || sending} />}
                {startError && <SuggestionsUnavailable detail={startError} onRetry={() => loadStart()} />}
            </div>
        </main>
    );
}
