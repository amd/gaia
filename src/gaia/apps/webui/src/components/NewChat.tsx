// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronRight, Folder } from 'lucide-react';
import { Composer } from './Composer';
import { ModelChip } from './ModelChip';
import { PermissionModeChip } from './PermissionModeChip';
import { StartCards } from './StartCards';
import { useAttachments } from '../hooks/useAttachments';
import { useWarmUp, type WarmUpProgress } from '../hooks/useWarmUp';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { displayModelName } from '../stores/modelStore';
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
}

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

/** An empty chat: a greeting, the composer, tasks to start from. Created on the first message. */
export function NewChat({ onSend, initialText = '' }: NewChatProps) {
    const starting = useChatStore((s) => s.systemStatus?.init_state === 'initializing');
    const [text, setText] = useState(initialText);
    const [error, setError] = useState<string | null>(null);
    const [sending, setSending] = useState(false);
    const [start, setStart] = useState<StartScreen | null>(null);
    const [startError, setStartError] = useState<string | null>(null);
    const inputRef = useRef<HTMLTextAreaElement | null>(null);
    const attachments = useAttachments();
    const warming = useWarmUp();

    const live = useRef(true);
    const loadStart = useCallback(() => {
        setStartError(null);
        api.getStartScreen()
            .then((s) => {
                if (!live.current) return;
                setStart(s);
                if (s.skills_error) setStartError(s.skills_error);
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
                {warming !== null && !starting && (
                    <p className="new-chat-warmup" role="status">{warmUpLine(warming)}</p>
                )}
                {error && <p className="new-chat-error" role="alert">{error}</p>}
                {start && <StartCards cards={start.cards} onPick={pick} disabled={starting || sending} />}
                {startError && <SuggestionsUnavailable detail={startError} onRetry={loadStart} />}
            </div>
        </main>
    );
}
