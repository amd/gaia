// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronRight, Folder, X } from 'lucide-react';
import { Composer } from './Composer';
import { ModelChip } from './ModelChip';
import { PermissionModeChip } from './PermissionModeChip';
import { StartCards } from './StartCards';
import { FolderPicker } from './session-panel/FolderPicker';
import { useAttachments } from '../hooks/useAttachments';
import { useWarmUp, type WarmUpProgress } from '../hooks/useWarmUp';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { displayModelName, useModelNames } from '../stores/modelStore';
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

function baseName(path: string): string {
    const parts = path.split(/[\\/]/).filter(Boolean);
    return parts[parts.length - 1] ?? path;
}

/**
 * The folder the new chat will work in, and a way to pick another: the OS
 * dialog in the desktop app, a path box and browser in a plain browser.
 */
function WorkingFolderChip({ folder }: { folder: StartScreen['working_folder'] | undefined }) {
    const picked = useChatStore((s) => s.draftWorkingFolder);
    const setPicked = useChatStore((s) => s.setDraftWorkingFolder);
    const [browsing, setBrowsing] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const choose = useCallback(async () => {
        setError(null);
        const native = window.gaiaAPI?.dialog?.pickFolder;
        if (!native) {
            setBrowsing(true);
            return;
        }
        try {
            const path = await native();
            if (path) setPicked(path);
        } catch (err) {
            setError(`Couldn't open the folder picker: ${err instanceof Error ? err.message : String(err)}`);
        }
    }, [setPicked]);

    if (!folder && !picked) return null;
    const title = picked
        ? `GAIA works in ${picked} in this chat. Click to pick another folder.`
        : folder?.gaia_folder
            ? `GAIA works in its own folder, ${folder.path}. Click to pick a folder for this chat.`
            : `GAIA works in ${folder?.path}. Click to pick another folder for this chat.`;
    return (
        <div className="new-chat-meta">
            <button type="button" className="new-chat-folder" title={title} aria-label={title} onClick={() => void choose()}>
                <Folder size={13} aria-hidden="true" />
                <span>{picked ? baseName(picked) : folder?.name}</span>
            </button>
            {picked && (
                <button
                    type="button"
                    className="btn-icon-sm"
                    onClick={() => setPicked(null)}
                    aria-label="Use the default folder"
                    title="Use the default folder"
                >
                    <X size={12} />
                </button>
            )}
            {error && <span className="new-chat-error" role="alert">{error}</span>}
            {browsing && (
                <FolderPicker
                    title="Choose a folder"
                    label="Choose the folder this chat works in"
                    note="This chat will read and write in the folder you pick, and run commands there."
                    onPick={(path) => { setPicked(path); setBrowsing(false); }}
                    onClose={() => setBrowsing(false)}
                />
            )}
        </div>
    );
}

/** An empty chat: a greeting, the composer, tasks to start from. Created on the first message. */
export function NewChat({ onSend, initialText = '', focusRequest = 0 }: NewChatProps) {
    const starting = useChatStore((s) => s.systemStatus?.init_state === 'initializing');
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
                <Composer
                    value={text}
                    onChange={setText}
                    onSubmit={submit}
                    attachments={attachments}
                    disabledReason={starting ? STARTING_REASON : sending ? 'Starting the chat…' : null}
                    autoFocus
                    inputRef={inputRef}
                    leftControls={<PermissionModeChip sessionId={null} />}
                    rightControls={<ModelChip />}
                />
                <WorkingFolderChip folder={folder} />
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
