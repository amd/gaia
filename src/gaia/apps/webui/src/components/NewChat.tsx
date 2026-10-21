// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronRight, Folder } from 'lucide-react';
import { Composer } from './Composer';
import { ModelChip } from './ModelChip';
import { PermissionModeChip } from './PermissionModeChip';
import { StartCards } from './StartCards';
import { useAttachments } from '../hooks/useAttachments';
import { useGreeting, type Greeting } from '../hooks/useGreeting';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { displayModelName, useModelNames } from '../stores/modelStore';
import { setVisibleInterval } from '../utils/visibleInterval';
import { STARTING_REASON } from '../utils/constants';
import { log } from '../utils/logger';
import { deviceLabel } from '../utils/modelLabels';
import gaiaRobot from '../assets/gaia-robot.png';
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

type Loading = Extract<Greeting, { kind: 'loading' }>;

/** The start-up step the agent is on, e.g. "Loading Gemma 4 E4B on your GPU… 3s". */
export function loadingLine({ phase, model, device, seconds }: Loading, bootTask: string | null = null): string {
    let step: string;
    if (phase === 'waiting') step = 'Waiting for another chat to finish';
    else if (phase === 'building') step = 'Starting the agent';
    else if (phase === 'priming') step = 'Reading GAIA’s instructions';
    else if (phase === 'loading' && model) {
        const where = deviceLabel(device);
        step = `Loading ${displayModelName(model)}${where ? ` on ${where}` : ''}`;
    } else if (phase === 'loading') step = 'Loading the model';
    else step = bootTask ?? 'Starting GAIA';
    return `${step}… ${seconds}s`;
}

/** Where the agent's greeting goes: its loading state, its words, or why it has none. */
function AgentGreeting({ greeting, bootTask, onRetry }: { greeting: Greeting; bootTask: string | null; onRetry: () => void }) {
    if (greeting.kind === 'loading') {
        return (
            <div className="new-chat-loading is-live" role="status">
                <img className="new-chat-loading-robot" src={gaiaRobot} alt="" width={40} height={40} />
                <p className="new-chat-loading-line">{loadingLine(greeting, bootTask)}</p>
            </div>
        );
    }
    if (greeting.kind === 'failed') {
        return (
            <p className="new-chat-greeting-error" role="alert">
                {greeting.message}{' '}
                <button type="button" className="link-btn" onClick={onRetry}>Try again</button>
            </p>
        );
    }
    return <h1 className="new-chat-greeting" aria-busy={greeting.kind === 'writing'}>{greeting.text}</h1>;
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

/** An empty chat: the agent's greeting, the composer, tasks to start from. Created on the first message. */
export function NewChat({ onSend, initialText = '', focusRequest = 0 }: NewChatProps) {
    const starting = useChatStore((s) => s.systemStatus?.init_state === 'initializing');
    const [text, setText] = useState(initialText);
    const [error, setError] = useState<string | null>(null);
    const [sending, setSending] = useState(false);
    const [start, setStart] = useState<StartScreen | null>(null);
    const [startError, setStartError] = useState<string | null>(null);
    const inputRef = useRef<HTMLTextAreaElement | null>(null);
    const attachments = useAttachments();
    const { greeting, retry: retryGreeting } = useGreeting();
    const bootTask = useChatStore((s) => s.systemStatus?.init_tasks?.find((t) => t.status === 'running')?.name ?? null);
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
                <AgentGreeting greeting={greeting} bootTask={bootTask} onRetry={retryGreeting} />
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
                {error && <p className="new-chat-error" role="alert">{error}</p>}
                {start && <StartCards cards={start.cards} onPick={pick} disabled={starting || sending} />}
                {startError && <SuggestionsUnavailable detail={startError} onRetry={() => loadStart()} />}
            </div>
        </main>
    );
}
