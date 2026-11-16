// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React, { createContext, useCallback, useContext, useId, useLayoutEffect, useRef, useState, useEffect, useMemo } from 'react';
import type { Components } from 'react-markdown';
import { Copy, Check, AlertTriangle, Trash2, RefreshCw, RotateCw, Play, Pencil, FolderOpen, Square, FileText, ScrollText, Settings } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { SAFE_DISALLOWED_ELEMENTS, joinAttachmentLinks, safeUrlTransform, splitAttachmentLinks } from '../utils/markdown';
import remarkGfm from 'remark-gfm';
import remarkWindowsPaths from '../utils/remarkWindowsPaths';
import { remarkSoftBreaks } from '../utils/remarkSoftBreaks';
import { remarkNumericColumns } from '../utils/remarkNumericColumns';
import { AgentActivity } from './AgentActivity';
import { EmailConnectCta, isAuthRequiredMessage } from './email/EmailConnectCta';
import { RenderCard } from './render/RenderCard';
import * as api from '../services/api';
import { log } from '../utils/logger';
import { plainAnswerText } from '../utils/plainText';
import { AnswerStats } from './AnswerStats';
import { dropErrorsEchoedInAnswer, isErrorContent } from '../utils/answerErrors.core';
import { SERVER_CANCELLED_TEXT, splitCutOff, turnEnding } from '../utils/turnEnding.core';

export { dropErrorsEchoedInAnswer, isErrorContent, splitCutOff, turnEnding };
import { displayModelName, useInferencePlace, useModelNames } from '../stores/modelStore';
import { MODEL_WAIT_HINT_S, isModelWaitLabel } from '../utils/modelWait';
import { localizeLiveStatus } from '../utils/liveStatus';
import type { Message, AgentStep, RenderCardData } from '../types';
import { TurnChanges } from './TurnChanges';
import { ReadAloudButton, ReadAloudError, ReadAloudNote } from './ReadAloudButton';
import { TroubleshootingReport, namesFailureRecord, type ReportClasses } from './TroubleshootingReport';
import { CODE_COLLAPSE_LINES, CODE_PREVIEW_LINES, ExpandToggle, countLines, isLongUserMessage, lineCount, messageExcerpt, sizeLabel } from './LongContent';
import { dateLocale, useT, type Translate } from '../i18n/useT';
import { useChatStore } from '../stores/chatStore';
import { useLogsStore } from '../stores/logsStore';
import { CLAIMS_ESCAPE } from '../hooks/useEscapeToClose';
import { dropAnswerDrafts } from '../utils/answerDrafts';
import { describeFailedTurn, failedTurnDetails } from '../utils/failedTurn';
import { splitUploadedImages } from '../utils/messageImages';
import { ImageThumbs } from './ImageThumbs';
import {
    buildCitationIndex, docForToken, findCitations, findPages,
    type CitationIndex, type CitedDoc, type FoundPage,
} from '../utils/citations';
import { WIN_PATH_ONLY_RE, findFilePaths } from '../utils/windowsPaths';
import { cleanToolCallContent } from '../utils/toolCallContent';
export { cleanToolCallContent, escapeBareNumberLines } from '../utils/toolCallContent';
import './MessageBubble.css';

interface MessageBubbleProps {
    message: Message;
    isStreaming?: boolean;
    /** Agent steps to display inside this message bubble. */
    agentSteps?: AgentStep[];
    /** Whether agent steps are currently active (streaming). */
    agentStepsActive?: boolean;
    /** Live-streaming cards (issue #2108). `message.cards` wins when set —
     *  this prop carries in-flight cards for the synthetic streaming bubble,
     *  mirroring how `agentSteps` reaches it. */
    cards?: RenderCardData[];
    /** Called when user clicks the delete button. */
    onDelete?: (messageId: number) => void;
    /** Called when user clicks the resend button (user messages only). */
    onResend?: (message: Message) => void;
    /** More message actions, before Copy (e.g. Retry with…). */
    extraActions?: React.ReactNode;
    /** Shows a labelled Try again under a failed, cut-off or stopped reply, which asks the user to press it. */
    onTryAgain?: () => void;
    /** Asks the same question again with the same model (answers only). */
    onRegenerate?: () => void;
    /** Opens this message for editing (user messages only). */
    onEditStart?: (message: Message) => void;
    /** Whether this message is open for editing. */
    editing?: boolean;
    /** Text typed into the editor before it last closed; null starts from the message. */
    editDraft?: string | null;
    onEditDraft?: (text: string) => void;
    /** Sends the edited text in place of this message and everything after it. */
    onEdit?: (message: Message, text: string) => void;
    onEditCancel?: () => void;
    /** Later messages that Resend or Edit on this message delete for good; above zero, they ask first. */
    laterCount?: number;
    /** Shows Continue under a stopped reply that wrote something. */
    onContinue?: () => void;
    /** The chat's latest answer, whose actions stay in view. */
    latest?: boolean;
    /** Total wall-clock latency in ms (time from user message to response completion). */
    latencyMs?: number;
    /** A fix under the bubble explains the failure, so it drops its own banner. */
    fixShownBelow?: boolean;
    /** The model's current phase while streaming; null shows "Thinking...". */
    liveStatus?: string | null;
    /** What a running tool's own model calls are doing; shown on that tool's row. */
    toolStatus?: string | null;
    /** The run is blocked on the user's approval or answer; the indicator says so instead of timing the model. */
    awaitingUser?: boolean;
}



/**
 * Live status next to GAIA's name: the model's current phase ("Reading your request",
 * "Reasoning — 38 words so far"), or "Thinking..." until the first one arrives.
 * One calm line in the UI font; the whole label shows at once.
 */
function ThinkingIndicator({ active, label, awaitingUser = false }: { active: boolean; label?: string | null; awaitingUser?: boolean }) {
    const t = useT();
    const text = awaitingUser ? t('activity.waiting') : (label ? localizeLiveStatus(t, label) : t('msg.thinking'));
    const elapsed = useElapsedSeconds(active, text);
    // One clock for the whole silent wait: a repeated or retried phase must not restart it.
    const waitingOnModel = active && !awaitingUser && isModelWaitLabel(label);
    const modelWaitElapsed = useElapsedSeconds(waitingOnModel);
    const place = useInferencePlace();

    if (!active) return null;

    const showModelWaitHint = waitingOnModel && modelWaitElapsed >= MODEL_WAIT_HINT_S;

    return (
        <span className={`thinking-indicator${!awaitingUser ? ' is-live' : ''}`}>
            <span className="thinking-indicator-text">{text}</span>
            {!awaitingUser && elapsed >= PHASE_ELAPSED_MIN_S && (
                <span className="thinking-indicator-elapsed"> · <bdi dir="ltr">{elapsed}s</bdi></span>
            )}
            {showModelWaitHint && (
                <span className="thinking-indicator-hint" role="status">{t(place?.remote === false ? 'live.localModelWaitHint' : 'live.modelWaitHint')}</span>
            )}
        </span>
    );
}


/** A phase shorter than this needs no clock; past it, the count shows GAIA is still working. */
const PHASE_ELAPSED_MIN_S = 3;

/** Whole seconds `active` has held; resets when it drops or `key` changes. */
function useElapsedSeconds(active: boolean, key?: string): number {
    const [elapsed, setElapsed] = useState(0);
    useEffect(() => {
        setElapsed(0);
        if (!active) return;
        const started = Date.now();
        const t = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
        return () => clearInterval(t);
    }, [active, key]);
    return elapsed;
}

const CHAT_REPORT_CLASSES: ReportClasses = {
    root: 'msg-report',
    button: 'msg-try-again',
    hint: 'msg-report-hint',
    error: 'msg-report-error',
};

/** Format a full absolute timestamp for the stats tooltip. */
function formatFullTimestamp(iso: string, t: Translate): string {
    if (!iso) return '';
    return new Date(iso).toLocaleString(dateLocale(t), {
        month: 'short', day: 'numeric', year: 'numeric',
        hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true,
    });
}

/** "11:13 PM" today, "Yesterday, 4:10 PM", "Nov 9, 4:10 PM" this year, with the year before that. */
export function formatShortTimestamp(iso: string, t: Translate, now: Date = new Date()): string {
    if (!iso) return '';
    const when = new Date(iso);
    const locale = dateLocale(t);
    const time = when.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit' });
    const dayStart = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const daysAgo = Math.round((dayStart(now) - dayStart(when)) / 86_400_000);
    if (daysAgo === 0) return time;
    if (daysAgo === 1) return t('msg.yesterdayAt', { time });
    return when.toLocaleString(locale, {
        month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
        ...(when.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }),
    });
}

const EDITOR_MAX_HEIGHT = 320;

/** A sent message opened for editing: Enter sends it again, Escape puts it back. */
function MessageEditor({ original, draft, onDraft, onSend, onCancel, confirming = false }: {
    original: string;
    draft?: string | null;
    onDraft?: (text: string) => void;
    onSend: (text: string) => void;
    onCancel: () => void;
    /** The delete-later-messages question is open and owns the buttons. */
    confirming?: boolean;
}) {
    const t = useT();
    // Attachments stay out of the text box and go back on when the edit is sent.
    const { text: originalText, links } = useMemo(() => splitAttachmentLinks(original), [original]);
    const [text, setText] = useState(() => (draft != null ? splitAttachmentLinks(draft).text : originalText));
    const ref = useRef<HTMLTextAreaElement>(null);
    const changed = text.trim() !== '' && text !== originalText;
    const send = () => onSend(joinAttachmentLinks(text, links));

    useLayoutEffect(() => {
        const el = ref.current;
        if (!el) return;
        el.focus();
        el.setSelectionRange(el.value.length, el.value.length);
    }, []);

    useLayoutEffect(() => {
        const el = ref.current;
        if (!el) return;
        el.style.height = 'auto';
        el.style.height = `${Math.min(el.scrollHeight, EDITOR_MAX_HEIGHT)}px`;
    }, [text]);

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Escape') {
            e.preventDefault();
            onCancel();
        } else if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (changed && !confirming) send();
        }
    };

    return (
        <div className="msg-edit">
            <textarea
                ref={ref}
                className="msg-edit-input"
                value={text}
                onChange={(e) => { setText(e.target.value); onDraft?.(e.target.value); }}
                onKeyDown={handleKeyDown}
                aria-label={t('msg.editLabel')}
                readOnly={confirming}
                rows={1}
                dir="auto"
                {...CLAIMS_ESCAPE}
            />
            {links.length > 0 && (
                <ul className="msg-edit-attachments" aria-label={t('composer.attached')}>
                    {links.map((l, i) => (
                        <li key={i} className="msg-edit-attachment">
                            <FileText size={14} aria-hidden="true" />
                            <span className="msg-edit-attachment-name" title={l.name} dir="auto">{l.name}</span>
                        </li>
                    ))}
                </ul>
            )}
            {!confirming && (
                <div className="msg-edit-actions">
                    <button type="button" className="btn-secondary msg-edit-btn" onClick={onCancel}>{t('msg.editCancel')}</button>
                    <button type="button" className="btn-primary msg-edit-btn" onClick={send} disabled={!changed}>
                        {t('msg.editSend')}
                    </button>
                </div>
            )}
        </div>
    );
}

const SCHEDULE_RUN_MARKER = /^\[schedule-run\] Run #(\d+) \u00b7 (.+)$/;

export function MessageBubble({ message, isStreaming, agentSteps, agentStepsActive, cards, onDelete, onResend, extraActions, onTryAgain, onContinue, onRegenerate, onEditStart, editing = false, editDraft = null, onEditDraft, onEdit, onEditCancel, laterCount = 0, latest = false, latencyMs, fixShownBelow = false, liveStatus, toolStatus, awaitingUser }: MessageBubbleProps) {
    const t = useT();
    useModelNames();
    const echo = useMemo(() => {
        if (message.role !== 'assistant' || !agentSteps) return { steps: agentSteps, echoed: false, endedBy: null };
        const steps = dropAnswerDrafts(agentSteps, message.content);
        return isStreaming ? { steps, echoed: false, endedBy: null } : dropErrorsEchoedInAnswer(steps, message.content);
    }, [agentSteps, message.content, message.role, isStreaming]);
    const isError = message.role === 'assistant' && (echo.echoed || message.failed === true || isErrorContent(message.content));
    const place = useInferencePlace();
    const showServerLog = useLogsStore((s) => s.show);
    const openSettings = useChatStore((s) => s.openSettings);
    const jumpToSetting = useChatStore((s) => s.jumpToSetting);
    const failedTurn = useMemo(
        () => (isError && !isStreaming ? describeFailedTurn(message.content) : null),
        [isError, isStreaming, message.content],
    );
    const stopState = message.role === 'assistant'
        ? message.stopState ?? (message.content.trim() === SERVER_CANCELLED_TEXT ? 'stopped' : undefined)
        : undefined;
    const cutOff = useMemo(
        () => (message.role === 'assistant' && !isStreaming ? splitCutOff(message.content) : null),
        [message.content, message.role, isStreaming],
    );
    // The error that ended a partly written answer, shown apart from the answer.
    const endedBy = cutOff ? null : echo.endedBy;
    // What the user typed is never agent output — render it verbatim.
    // Memoized because the assistant path runs a brace-depth parser.
    const userParts = useMemo(
        () => (message.role === 'user' ? splitUploadedImages(message.content) : null),
        [message.content, message.role],
    );
    const cleanedContent = useMemo(
        () => {
            if (userParts) return userParts.text;
            const body = cutOff
                ? cutOff.body
                : endedBy ? message.content.trimEnd().slice(0, -endedBy.length) : message.content;
            return cleanToolCallContent(body, Boolean(isStreaming));
        },
        [message.content, userParts, isStreaming, cutOff, endedBy],
    );
    const hasAnswerText = Boolean(cleanedContent.trim()) && !(stopState && cleanedContent.trim() === SERVER_CANCELLED_TEXT);
    const summaryFiles = message.turnSummary?.files;
    const writtenFiles = useMemo(() => summaryFiles?.map((f) => f.path) ?? [], [summaryFiles]);
    const readAloudId = `${message.session_id}:${message.id}`;
    const [copied, setCopied] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const collapsibleUser = message.role === 'user' && isLongUserMessage(message.content);
    const excerpt = useMemo(() => (collapsibleUser ? messageExcerpt(message.content) : null), [collapsibleUser, message.content]);
    const [userExpanded, setUserExpanded] = useState(false);
    const userBodyId = useId();
    const userBodyRef = useRef<HTMLDivElement>(null);
    const scrollAfterCollapseRef = useRef(false);
    const toggleUserExpanded = useCallback(() => {
        if (userExpanded) scrollAfterCollapseRef.current = true;
        setUserExpanded(!userExpanded);
    }, [userExpanded]);
    useEffect(() => {
        // Collapsing from far down the page would leave the reader past the message.
        if (userExpanded || !scrollAfterCollapseRef.current) return;
        scrollAfterCollapseRef.current = false;
        userBodyRef.current?.scrollIntoView?.({ block: 'nearest' });
    }, [userExpanded]);
    const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const deleteTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // Clean up timers on unmount to avoid setState on unmounted component
    useEffect(() => {
        return () => {
            if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
            if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current);
        };
    }, []);

    const handleCopy = useCallback(() => {
        const text = message.role === 'assistant' ? plainAnswerText(cleanedContent) : message.content;
        if (navigator.clipboard?.writeText) {
            navigator.clipboard.writeText(text).catch(() => {});
        } else {
            // Fallback for non-HTTPS contexts (common for localhost)
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            document.body.removeChild(textarea);
        }
        setCopied(true);
        if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
        copyTimerRef.current = setTimeout(() => setCopied(false), 2000);
    }, [cleanedContent, message.content, message.role]);

    const handleDelete = useCallback(() => {
        if (!confirmDelete) {
            setConfirmDelete(true);
            if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current);
            deleteTimerRef.current = setTimeout(() => setConfirmDelete(false), 3000);
            return;
        }
        // Second click = confirmed
        setConfirmDelete(false);
        if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current);
        onDelete?.(message.id);
    }, [confirmDelete, message.id, onDelete]);

    const [pendingDrop, setPendingDrop] = useState<(() => void) | null>(null);
    const askBeforeDropping = useCallback((go: () => void) => {
        if (laterCount > 0) setPendingDrop(() => go);
        else go();
    }, [laterCount]);

    const handleResend = useCallback(() => {
        askBeforeDropping(() => onResend?.(message));
    }, [message, onResend, askBeforeDropping]);

    // Tooltip shown on hover anywhere over the bubble — full absolute
    // timestamp (e.g. "Apr 25, 2026, 11:02:04 PM"). Both user and assistant
    // bubbles get one; previously only the assistant had a visible
    // timestamp readout via msg-stats-ts.
    const hoverTimestamp = message.created_at
        ? t(message.role === 'user' ? 'msg.sent' : 'msg.replied', { time: formatFullTimestamp(message.created_at, t) })
        : undefined;

    const runMarker = message.role === 'system' ? SCHEDULE_RUN_MARKER.exec(message.content) : null;
    if (runMarker) {
        return <div className="msg-run-divider" role="separator">{t('msg.scheduledRun', { n: runMarker[1], time: runMarker[2] })}</div>;
    }

    return (
        <div
            className={`msg msg-${message.role} ${isError ? 'msg-error' : ''}${latest ? ' msg-latest' : ''}`}
            title={hoverTimestamp}
        >
            <div className="msg-inner">
                <div className="msg-header">
                    <div className="msg-header-left" />
                    {!isStreaming && !editing && (
                        <div className="msg-actions">
                            {message.role === 'user' && onEditStart && onEdit && (
                                <button
                                    type="button"
                                    className="msg-action-btn"
                                    onClick={() => onEditStart(message)}
                                    title={t('msg.edit')}
                                    aria-label={t('msg.edit')}
                                >
                                    <Pencil size={12} />
                                </button>
                            )}
                            {/* Resend button - user messages only */}
                            {message.role === 'user' && onResend && (
                                <button
                                    className="msg-action-btn"
                                    onClick={handleResend}
                                    title={t('msg.resend')}
                                    aria-label={t('msg.resend')}
                                >
                                    <RefreshCw size={12} />
                                </button>
                            )}
                            {message.role === 'assistant' && onRegenerate && (
                                <button
                                    type="button"
                                    className="msg-action-btn"
                                    onClick={onRegenerate}
                                    title={t('msg.tryAgain')}
                                    aria-label={t('msg.tryAgain')}
                                >
                                    <RotateCw size={12} />
                                </button>
                            )}
                            {extraActions}
                            {message.role === 'assistant' && !isError && hasAnswerText && (
                                <ReadAloudButton messageId={readAloudId} content={cleanedContent} />
                            )}
                            {hasAnswerText && (
                                <button
                                    className={`msg-copy ${copied ? 'copied' : ''}`}
                                    onClick={handleCopy}
                                    title={copied ? t('msg.copied') : t('msg.copy')}
                                    aria-label={copied ? t('msg.copiedAria') : t('msg.copy')}
                                >
                                    {copied ? <Check size={12} /> : <Copy size={12} />}
                                </button>
                            )}
                            {/* Delete button */}
                            {onDelete && (
                                <button
                                    className={`msg-action-btn msg-delete ${confirmDelete ? 'confirm' : ''}`}
                                    onClick={handleDelete}
                                    title={confirmDelete ? t('msg.confirmDelete') : t('msg.delete')}
                                    aria-label={confirmDelete ? t('msg.confirmDeleteAria') : t('msg.delete')}
                                >
                                    <Trash2 size={12} />
                                </button>
                            )}
                        </div>
                    )}
                </div>
                <div className="msg-body">
                    {/* The turn as a timeline; the live line sits where the next step will appear */}
                    {message.role === 'assistant' && ((echo.steps && echo.steps.length > 0) || isStreaming) && (
                        <AgentActivity
                            steps={echo.steps ?? []}
                            isActive={agentStepsActive ?? false}
                            running={!!isStreaming}
                            awaitingUser={awaitingUser}
                            toolStatus={isStreaming ? toolStatus : null}
                            answer={isStreaming ? undefined : cleanedContent}
                            liveLine={isStreaming && (
                                <ThinkingIndicator active={!!agentStepsActive || !cleanedContent} label={liveStatus} awaitingUser={awaitingUser} />
                            )}
                        />
                    )}
                    {isError && !fixShownBelow && (
                        <div className="error-banner">
                            <AlertTriangle size={14} />
                            <span>{t(failedTurn && failedTurn.kind !== 'unknown' ? `failedTurn.${failedTurn.kind}.title` : 'msg.error')}</span>
                        </div>
                    )}
                    {/* Structured cards (#2108) — finalized message.cards wins;
                        the prop is the live-streaming path. Always above the
                        markdown content. */}
                    {(message.cards ?? cards)?.map((card, i) => (
                        <RenderCard key={i} render={card.render} data={card.data} />
                    ))}
                    {/* The cursor is a streaming indicator, not decoration — it never
                        outlives the write. See docs/spec/gaia-design-language.mdx. */}
                    {editing && onEdit ? (
                        <MessageEditor
                            original={message.content}
                            draft={editDraft}
                            onDraft={onEditDraft}
                            onSend={(text) => askBeforeDropping(() => onEdit(message, text))}
                            onCancel={() => { setPendingDrop(null); onEditCancel?.(); }}
                            confirming={pendingDrop !== null && !isStreaming}
                        />
                    ) : collapsibleUser ? (
                        <>
                            {!userExpanded && excerpt ? (
                                <div ref={userBodyRef} id={userBodyId} className="msg-excerpt md-content">
                                    <div className="msg-excerpt-lines" dir="auto">{excerpt.head}</div>
                                    <div className="msg-excerpt-gap">{t.plural('msg.moreLines', excerpt.hidden)}</div>
                                    <div className="msg-excerpt-lines" dir="auto">{excerpt.tail}</div>
                                </div>
                            ) : (
                                <div
                                    ref={userBodyRef}
                                    id={userBodyId}
                                    className={`msg-collapsible${userExpanded ? '' : ' is-collapsed'}`}
                                >
                                    <RenderedContent content={cleanedContent} sessionId={message.session_id} />
                                </div>
                            )}
                            <ExpandToggle
                                expanded={userExpanded}
                                onToggle={toggleUserExpanded}
                                controls={userBodyId}
                                collapsedLabel={t.hint(t('msg.showAll'), sizeLabel(t, message.content))}
                                expandedLabel={t('msg.showLess')}
                                className="msg-expand-toggle"
                            />
                        </>
                    ) : failedTurn?.raw ? (
                        <>
                            <p className="msg-failed-lead">{t(`failedTurn.${failedTurn.kind}.body`)}</p>
                            <details className="msg-failed-details">
                                <summary>{t('failedTurn.details')}</summary>
                                <RenderedContent content={failedTurnDetails(cleanedContent)} />
                            </details>
                        </>
                    ) : !(stopState && cleanedContent.trim() === SERVER_CANCELLED_TEXT) && (
                        <RenderedContent content={cleanedContent} sessionId={message.session_id} showCursor={isStreaming && !!cleanedContent && !agentStepsActive} isStreaming={isStreaming} writtenFiles={writtenFiles} />
                    )}
                    {!editing && userParts && <ImageThumbs images={userParts.images} />}
                    {pendingDrop && !isStreaming && (
                        <div className="msg-drop-confirm" role="alertdialog" aria-labelledby={`${userBodyId}-drop`}>
                            <span id={`${userBodyId}-drop`}>{t.plural('msg.dropLater', laterCount)}</span>
                            <div className="msg-edit-actions">
                                <button type="button" className="btn-secondary msg-edit-btn" onClick={() => setPendingDrop(null)} autoFocus>
                                    {t('msg.editCancel')}
                                </button>
                                <button type="button" className="btn-primary msg-edit-btn" onClick={() => { setPendingDrop(null); pendingDrop(); }}>
                                    {t('msg.dropLaterGo')}
                                </button>
                            </div>
                        </div>
                    )}
                    {stopState && (
                        <div className={`msg-stopped${stopState === 'unconfirmed' || message.stopError ? ' is-unconfirmed' : ''}`} role="note">
                            <Square size={9} fill="currentColor" aria-hidden="true" />
                            <span>
                                {stopState === 'unconfirmed'
                                    ? t('msg.stoppedUnconfirmed')
                                    : message.stopError
                                        ? t('msg.stoppedError', { error: message.stopError })
                                        : t('msg.stopped')}
                            </span>
                        </div>
                    )}
                    {cutOff && (
                        <p className="msg-cut-off" role="note">
                            {t.compose((say) => (cutOff.tools
                                ? `${say('msg.cutOff.toolsRan', { tools: cutOff.tools })} ${say(cutOff.notice)}`
                                : say(cutOff.notice)))}
                        </p>
                    )}
                    {endedBy && (
                        <p className="msg-cut-off" role="note" dir="auto">{endedBy}</p>
                    )}
                    {onContinue && stopState && !isStreaming && cleanedContent.trim() && cleanedContent.trim() !== SERVER_CANCELLED_TEXT && (
                        <button type="button" className="msg-try-again" onClick={onContinue}>
                            <Play size={12} aria-hidden="true" />
                            {t('msg.continue')}
                        </button>
                    )}
                    {onTryAgain && !isStreaming && (
                        <button type="button" className="msg-try-again" onClick={onTryAgain}>
                            <RotateCw size={12} aria-hidden="true" />
                            {t('msg.tryAgain')}
                        </button>
                    )}
                    {failedTurn?.sections.map((section) => (
                        <button
                            key={section}
                            type="button"
                            className="msg-try-again"
                            onClick={() => {
                                const target = failedTurn?.targets[section];
                                if (target) jumpToSetting({ section, target });
                                else openSettings(section);
                            }}
                        >
                            <Settings size={12} aria-hidden="true" />
                            {t.compose((say) => say('failedTurn.openSettings', { tab: say(`settings.section.${section}`) }))}
                        </button>
                    ))}
                    {/* A local model failed: what Lemonade and its backend wrote says why. */}
                    {isError && !isStreaming && place?.remote === false && (
                        <button type="button" className="msg-try-again" onClick={() => showServerLog('model_server')}>
                            <ScrollText size={12} aria-hidden="true" />
                            {t('logs.serverLog')}
                        </button>
                    )}
                    {message.role === 'assistant' && !isStreaming && (namesFailureRecord(message.content) || namesFailureRecord(message.failureText ?? '')) && (
                        <TroubleshootingReport classes={CHAT_REPORT_CLASSES} note={t('msg.reportNote')} />
                    )}
                    {message.role === 'assistant'
                        && !isStreaming
                        && isAuthRequiredMessage(cleanedContent) && (
                        <EmailConnectCta content={cleanedContent} />
                    )}
                    {message.role === 'assistant' && !isStreaming && (message.stats || latencyMs != null || message.created_at) && (
                        <div className="msg-stats" aria-label={t('msg.about')}>
                            {message.created_at && (
                                <span className="msg-stats-ts" title={formatFullTimestamp(message.created_at, t)}>
                                    {formatShortTimestamp(message.created_at, t)}
                                </span>
                            )}
                            {message.stats?.retried && message.stats.model && (
                                <span title={t('msg.retriedWithTitle', { model: message.stats.model })}>{displayModelName(message.stats.model)}</span>
                            )}
                            {!message.stats?.retried && message.stats?.route?.model && (
                                <span className="msg-stats-route" title={t('msg.route', { reason: message.stats.route.reason ?? '' })}>
                                    {displayModelName(message.stats.route.model)}
                                </span>
                            )}
                            <AnswerStats latencyMs={latencyMs} stats={message.stats} ended={turnEnding(message.content, stopState, isError || Boolean(endedBy))} />
                        </div>
                    )}
                    {message.role === 'assistant' && !isStreaming && message.stats?.route?.degraded && message.stats.route.notice && (
                        <div className="msg-route-notice" role="status">{message.stats.route.notice}</div>
                    )}
                    {message.role === 'assistant' && !isStreaming && message.id > 0 && message.session_id && (
                        <TurnChanges sessionId={message.session_id} messageId={message.id} />
                    )}
                    {message.role === 'assistant' && <ReadAloudNote messageId={readAloudId} />}
                    {message.role === 'assistant' && <ReadAloudError messageId={readAloudId} />}
                </div>
            </div>
        </div>
    );
}

/** Whether the markdown being rendered is still streaming in. */
const StreamingContext = createContext(false);

/** Custom code block with copy button; a long one opens collapsed to a preview. */
function CodeBlock({ lang, code }: { lang: string; code: string }) {
    const t = useT();
    const [copied, setCopied] = useState(false);
    const [expanded, setExpanded] = useState(false);
    const streaming = useContext(StreamingContext);
    const bodyId = useId();
    const lines = countLines(code);
    // While streaming, cap as soon as the preview fills so the block never grows and then shrinks.
    const collapsible = lines > (streaming ? CODE_PREVIEW_LINES : CODE_COLLAPSE_LINES);
    const collapsed = collapsible && !expanded;
    const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        return () => {
            if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
        };
    }, []);

    const handleCopy = useCallback(() => {
        if (navigator.clipboard?.writeText) {
            navigator.clipboard.writeText(code).catch(() => {});
        } else {
            // Fallback for non-HTTPS contexts (common for localhost)
            const textarea = document.createElement('textarea');
            textarea.value = code;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            document.body.removeChild(textarea);
        }
        setCopied(true);
        if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
        copyTimerRef.current = setTimeout(() => setCopied(false), 2000);
    }, [code]);

    return (
        // Code reads left to right even inside an Arabic answer.
        <div className={`code-block${collapsed ? ' is-collapsed' : ''}`} dir="ltr">
            <div className="code-header">
                <span className="code-lang">{lang || 'code'}</span>
                <button
                    className={`code-copy ${copied ? 'copied' : ''}`}
                    onClick={handleCopy}
                    title={copied ? t('msg.copied') : t('code.copy')}
                    aria-label={copied ? t('msg.copiedAria') : t('code.copyAria')}
                >
                    {copied ? <Check size={13} /> : <Copy size={13} />}
                    <span>{copied ? t('code.copied') : t('code.copy')}</span>
                </button>
            </div>
            <pre id={bodyId}><code>{code}</code></pre>
            {collapsible && (
                <ExpandToggle
                    expanded={expanded}
                    onToggle={() => setExpanded((v) => !v)}
                    controls={bodyId}
                    collapsedLabel={t('code.expand', { size: lineCount(t, lines) })}
                    expandedLabel={t('code.collapse')}
                    className="code-expand-toggle"
                />
            )}
        </div>
    );
}

/** Markdown renderer using react-markdown with GFM support. */
// ── File Path Linkification ──────────────────────────────────────────────

/** Paths of the files this answer's turn wrote (`turn_summary.files`). */
const WrittenFilesContext = createContext<readonly string[]>([]);

/**
 * The written file an inline-code span names: its exact path, or a bare name
 * only one written file has. Two writes sharing a name stay plain code.
 */
function writtenFileFor(files: readonly string[], children: React.ReactNode): string | null {
    if (typeof children !== 'string' || files.length === 0) return null;
    const text = children.trim();
    if (!text) return null;
    if (files.includes(text)) return text;
    if (/[\\/]/.test(text)) return null;
    const name = text.toLowerCase();
    const matches = files.filter((f) => (f.split(/[\\/]/).pop() ?? '').toLowerCase() === name);
    return matches.length === 1 ? matches[0] : null;
}

function inlineCodePath(children: React.ReactNode): string | null {
    if (typeof children !== 'string') return null;
    const text = children.trim();
    return WIN_PATH_ONLY_RE.test(text) ? text : null;
}

function FilePathLink({ path, label }: { path: string; label?: string }) {
    const t = useT();
    const handleClick = (e: React.MouseEvent) => {
        e.preventDefault();
        api.openFileOrFolder(path).catch((err) => {
            log.ui.error('Failed to open path', err);
        });
    };
    return (
        <span
            className="file-path-link"
            dir="ltr"
            onClick={handleClick}
            title={t('msg.openInExplorer', { path })}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleClick(e as unknown as React.MouseEvent); } }}
        >
            <FolderOpen size={12} className="file-path-icon" />
            {label ?? path}
        </span>
    );
}

/** Split text into segments, replacing file paths with clickable links. */
function linkifyFilePaths(text: string): React.ReactNode {
    const parts: React.ReactNode[] = [];
    let lastIndex = 0;

    for (const { start, path } of findFilePaths(text)) {
        if (start > lastIndex) {
            parts.push(text.slice(lastIndex, start));
        }
        parts.push(<FilePathLink key={start} path={path} />);
        lastIndex = start + path.length;
    }

    // No paths found — return original text
    if (parts.length === 0) return text;

    // Add remaining text
    if (lastIndex < text.length) {
        parts.push(text.slice(lastIndex));
    }
    return <>{parts}</>;
}

/** The library's documents, as citations in an answer can name them. */
const CitationContext = createContext<CitationIndex>(buildCitationIndex([]));
/** The chat the answer belongs to, which a citation names to open its document. */
const CitationChatContext = createContext<string | null>(null);

/** A cited library document; opens at `page` (the first page cited after it, when there is one). */
function CitationLink({ doc, page, t }: { doc: CitedDoc; page?: number; t: Translate }) {
    const sessionId = useContext(CitationChatContext);
    return (
        <a
            className="citation-link"
            href={api.documentFileUrl(doc.id, sessionId, page)}
            target="_blank"
            rel="noopener noreferrer"
            dir="ltr"
            title={page ? t('citation.openPage', { page }) : t('citation.open', { name: doc.label })}
            data-doc-id={doc.id}
            data-page={page}
        >
            <FileText size={12} className="citation-icon" aria-hidden="true" />
            {doc.label}
        </a>
    );
}

function PageLink({ doc, page, label, t }: { doc: CitedDoc; page: number; label: string; t: Translate }) {
    const sessionId = useContext(CitationChatContext);
    return (
        <a
            className="citation-page"
            href={api.documentFileUrl(doc.id, sessionId, page)}
            target="_blank"
            rel="noopener noreferrer"
            title={t('citation.openPage', { page })}
            data-doc-id={doc.id}
            data-page={page}
        >
            {label}
        </a>
    );
}

/** Page numbers cited at the start of `text`, before it names another document. */
function leadingPages(index: CitationIndex, text: string): FoundPage[] {
    const next = findCitations(index, text)[0];
    return findPages(next ? text.slice(0, next.start) : text);
}

/**
 * A text run with its cited documents and pages linked, and any other Windows
 * path linked as before. `carried` is a document named just before this run
 * (in inline code), whose pages may open the run.
 */
function citeText(text: string, index: CitationIndex, t: Translate, carried: CitedDoc | null): React.ReactNode {
    // A filename that ends a longer path stays inside that path's chip.
    const paths = findFilePaths(text);
    const citations = findCitations(index, text).filter(
        (c) => !paths.some((p) => p.start < c.start && c.start < p.start + p.path.length),
    );
    const parts: Array<{ start: number; end: number; node: React.ReactNode }> = [];
    const addPages = (doc: CitedDoc, pages: FoundPage[], offset: number) => {
        for (const pg of pages) {
            const start = offset + pg.start;
            const end = offset + pg.end;
            parts.push({ start, end, node: <PageLink key={`p${start}`} doc={doc} page={pg.page} label={text.slice(start, end)} t={t} /> });
        }
    };
    if (carried) addPages(carried, findPages(text.slice(0, citations[0]?.start ?? text.length)), 0);
    citations.forEach((c, i) => {
        const tail = text.slice(c.end, citations[i + 1]?.start ?? text.length);
        const pages = findPages(tail);
        parts.push({ start: c.start, end: c.end, node: <CitationLink key={`c${c.start}`} doc={c.doc} page={pages[0]?.page} t={t} /> });
        addPages(c.doc, pages, c.end);
    });
    if (parts.length === 0) return linkifyFilePaths(text);
    parts.sort((a, b) => a.start - b.start);
    const out: React.ReactNode[] = [];
    let at = 0;
    for (const part of parts) {
        if (part.start > at) out.push(<React.Fragment key={`t${at}`}>{linkifyFilePaths(text.slice(at, part.start))}</React.Fragment>);
        out.push(part.node);
        at = part.end;
    }
    if (at < text.length) out.push(<React.Fragment key={`t${at}`}>{linkifyFilePaths(text.slice(at))}</React.Fragment>);
    return <>{out}</>;
}

/** The library document an inline-code span names (a filename or a path to one), or null. */
function inlineCodeDoc(index: CitationIndex, children: React.ReactNode): CitedDoc | null {
    return typeof children === 'string' ? docForToken(index, children) : null;
}

/**
 * Process a block's children: cite library documents and their pages, and link
 * other file paths. Needed because react-markdown v9 has no `text` override.
 * A filename in inline code hands its document to the text after it, where
 * "، الصفحتان 6–7" names the pages.
 */
function Linkified({ children }: { children?: React.ReactNode }) {
    const index = useContext(CitationContext);
    const t = useT();
    const items = React.Children.toArray(children);
    let carried: CitedDoc | null = null;
    const out = items.map((child, i) => {
        if (typeof child === 'string') {
            const node = citeText(child, index, t, carried);
            carried = null;
            return <React.Fragment key={`s${i}`}>{node}</React.Fragment>;
        }
        carried = null;
        if (React.isValidElement(child) && child.type === InlineCode) {
            const doc = inlineCodeDoc(index, (child.props as { children?: React.ReactNode }).children);
            if (doc) {
                const next = items[i + 1];
                const pages = typeof next === 'string' ? leadingPages(index, next) : [];
                carried = doc;
                return <CitationLink key={`k${i}`} doc={doc} page={pages[0]?.page} t={t} />;
            }
        }
        return child;
    });
    return <>{out}</>;
}

// `node` is the markdown AST; spread onto <code> it renders as node="[object Object]".
function InlineCode({ className: _className, children, node: _node, ...props }: React.ComponentProps<'code'> & { node?: unknown }) {
    const index = useContext(CitationContext);
    const writtenFiles = useContext(WrittenFilesContext);
    const t = useT();
    // A filename here outside a block Linkified handles (e.g. inside **bold**).
    const doc = inlineCodeDoc(index, children);
    if (doc) return <CitationLink doc={doc} t={t} />;
    const path = inlineCodePath(children);
    if (path) return <FilePathLink path={path} />;
    const written = writtenFileFor(writtenFiles, children);
    if (written) return <FilePathLink path={written} label={String(children).trim()} />;
    return (
        <code className="inline-code" dir="ltr" {...props}>
            {children}
        </code>
    );
}

// Module-level so each element type is stable: an inline object remounts every
// CodeBlock on each render, which would reset its expand state mid-stream.
const MARKDOWN_COMPONENTS: Components = {
    // Code block vs inline code detection.
    // react-markdown calls `code` for both inline `code` and
    // fenced ```code``` blocks. Fenced blocks are wrapped in
    // <pre><code>, inline in just <code>. We use our `pre`
    // override to render fenced blocks as CodeBlock, so the
    // `code` component only handles inline code.
    // If we get here, it's inline code (fenced blocks are handled by the `pre` override below).
    code: InlineCode,
    // Fenced code blocks: react-markdown wraps them in <pre><code>.
    // Extract the language and code text, render as CodeBlock.
    // (The fence→card mount for email_pre_scan was retired at the
    // #2109 cutover — cards arrive via tool_result.render now;
    // pre-cutover history renders those fences as code blocks.)
    pre({ children }) {
        // children is <code className="language-xxx">...</code>
        const codeChild = React.Children.toArray(children)[0];
        if (React.isValidElement(codeChild) && (codeChild.type === 'code' || (codeChild.props as any)?.className !== undefined || typeof (codeChild.props as any)?.children === 'string')) {
            const codeProps = codeChild.props as any;
            const className = codeProps?.className || '';
            const match = /language-([\w-]+)/.exec(className);
            const codeString = String(codeProps?.children || '').replace(/\n$/, '');
            const lang = match?.[1] || '';
            return (
                <CodeBlock
                    lang={lang}
                    code={codeString}
                />
            );
        }
        // Fallback: render as-is
        return <pre dir="ltr">{children}</pre>;
    },
    // Custom table styling
    table({ children }) {
        return (
            <div className="md-table-wrap">
                <table className="md-table" dir="auto">{children}</table>
            </div>
        );
    },
    // Links open in new tab
    a({ href, children }) {
        return (
            <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="md-link"
            >
                {children}
            </a>
        );
    },
    // Paragraphs — linkify file paths in text children
    p({ children }) {
        return <p className="md-p" dir="auto"><Linkified>{children}</Linkified></p>;
    },
    // Headers
    h1({ children }) {
        return <h2 className="md-h2" dir="auto"><Linkified>{children}</Linkified></h2>;
    },
    h2({ children }) {
        return <h3 className="md-h3" dir="auto"><Linkified>{children}</Linkified></h3>;
    },
    h3({ children }) {
        return <h4 className="md-h4" dir="auto"><Linkified>{children}</Linkified></h4>;
    },
    h4({ children }) {
        return <h5 className="md-h4" dir="auto"><Linkified>{children}</Linkified></h5>;
    },
    // Lists
    ul({ children }) {
        return <ul className="md-ul" dir="auto">{children}</ul>;
    },
    ol({ children, start }) {
        return <ol className="md-ol" start={start} dir="auto">{children}</ol>;
    },
    li({ children, className }) {
        return <li className={className ? `md-li ${className}` : 'md-li'} dir="auto"><Linkified>{children}</Linkified></li>;
    },
    // Blockquote
    blockquote({ children }) {
        return <blockquote className="md-blockquote" dir="auto"><Linkified>{children}</Linkified></blockquote>;
    },
    // Horizontal rule
    hr() {
        return <hr className="md-hr" />;
    },
    // Table cells set no dir: the table's dir="auto" skips any cell that has its own.
    // The markdown's own alignment arrives as `style`, a numbers column as `className`.
    td({ children, className, style }) {
        return <td className={className} style={style}><Linkified>{children}</Linkified></td>;
    },
    th({ children, className, style }) {
        return <th className={className} style={style}><Linkified>{children}</Linkified></th>;
    },
};

const NO_WRITTEN_FILES: readonly string[] = [];

export function RenderedContent({ content, sessionId = null, showCursor, isStreaming = false, writtenFiles = NO_WRITTEN_FILES }: { content: string; sessionId?: string | null; showCursor?: boolean; isStreaming?: boolean; writtenFiles?: readonly string[] }) {
    const documents = useChatStore((s) => s.documents);
    const citations = useMemo(() => buildCitationIndex(documents), [documents]);
    if (!content && !showCursor) return null;
    if (!content && showCursor) return <span className="cursor" />;

    return (
        <StreamingContext.Provider value={isStreaming}>
        <CitationContext.Provider value={citations}>
        <CitationChatContext.Provider value={sessionId}>
        <WrittenFilesContext.Provider value={writtenFiles}>
        <div className="md-content">
            <ReactMarkdown
                remarkPlugins={[remarkGfm, remarkNumericColumns, remarkWindowsPaths, remarkSoftBreaks]}
                disallowedElements={[...SAFE_DISALLOWED_ELEMENTS]}
                urlTransform={safeUrlTransform}
                components={MARKDOWN_COMPONENTS}
            >
                {content}
            </ReactMarkdown>
            {showCursor && <span className="cursor" />}
        </div>
        </WrittenFilesContext.Provider>
        </CitationChatContext.Provider>
        </CitationContext.Provider>
        </StreamingContext.Provider>
    );
}
