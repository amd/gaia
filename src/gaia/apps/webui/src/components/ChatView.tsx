// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useCallback, useState, useMemo, type CSSProperties } from 'react';
import { Activity, Edit3, Download, Upload, ArrowDown, FolderSearch, Clock } from 'lucide-react';
import { MessageBubble, cleanToolCallContent, isErrorContent, turnEnding } from './MessageBubble';
import { toolRowText } from './AgentActivity';
import { agentEventToStep } from '../utils/streamSteps';
import { applyStatusEvent, selectToolStatus } from '../utils/statusEvents';
import { TOOL_CALL_JSON_SAFETY_RE, carryMessageKeys, fromApiMessages, isEchoOfFailedTool, keepFailedReply, onlyFailureAfter, persistedQuestion, policyReceiptAnchor, stripStreamingEnvelope } from '../utils/chatStream';
import { useChatStore } from '../stores/chatStore';
import { useReadAloudStore } from '../stores/readAloudStore';
import { readsAloudWhenDone } from '../utils/autoReadAloud';
import { useReadAloudSettings } from '../hooks/useReadAloudSettings';
import { RecordingProgress } from './RecordingProgress';
import { ensureModel, modelKnownReady } from '../stores/modelNeededStore';
import { useNotificationStore, selectSessionPermissionPrompt } from '../stores/notificationStore';
import { policyAlertNotification, showPermissionRequest, toolResultUpdates } from '../utils/toolEvents';
import * as api from '../services/api';
import { isStarting } from '../services/statusPoll';
import { refreshActiveRuns } from '../services/activeRuns';
import { claimView, dispatchNext, idleByPoll, isBusyError, markActivity, requeueRefused, type QueuedSend, type SendOutcome } from '../services/queueDispatch';
import { log } from '../utils/logger';
import { isMediaFile } from '../utils/mediaFiles';
import { bugReportUrl } from './UnsupportedFeature';
import { classifyChatError, isStreamInterrupted, mayStillFinish, MAX_STREAM_RECONNECTS, RECONNECT_DELAYS_MS, RUN_LOST_TEXT } from '../utils/streamErrors';
import { isAuthRequiredMessage } from './email/EmailConnectCta';
import { connectCardProviders, hasConnectCard } from './render/ConnectAccountCard';
import { ConnectorRetryBanner } from './ConnectorRetryBanner';
import { answerQuestion, QuestionGoneError, resolveTypedAnswer, waitingQuestionId } from './render/NeedsInputCard';
import type { Message, StreamEvent, AgentStep, RenderCardData, ContextUsage } from '../types';

import './ChatView.css';
import { Composer } from './Composer';
import { ModelChip } from './ModelChip';
import { SessionFolderChip } from './FolderChip';
import { PermissionModeChip } from './PermissionModeChip';
import { PermissionPrompt } from './PermissionPrompt';
import { QueuedMessages } from './QueuedMessages';
import { ContextMeter, ContextWarning } from './ContextMeter';
import { SpendMeter } from './SpendMeter';
import { RetryWithMenu } from './RetryWithMenu';
import { AnswerComparison } from './AnswerComparison';
import * as messageQueue from '../utils/messageQueue';
import type { QueuedMessage } from '../utils/messageQueue';
import { SessionPanel, SessionPanelToggle } from './session-panel/SessionPanel';
import { PrivateChatButton } from './PrivateChatButton';
import { SessionDocsBar } from './SessionDocsBar';
import { ResourceMonitor } from './ResourceMonitor';
import { OfflineNotice } from './session-panel/OfflineNotice';
import { ExportErrorBar } from './ExportErrorBar';
import { useSessionPanelStore } from '../stores/sessionPanelStore';
import { useResourceMonitorStore } from '../stores/resourceMonitorStore';
import { useAttachments } from '../hooks/useAttachments';
import { useAnswerComparison } from '../hooks/useAnswerComparison';
import { useContextUsage } from '../hooks/useContextUsage';
import { useJumpToMessage } from '../hooks/useJumpToMessage';
import { useFollowScroll } from '../hooks/useFollowScroll';
import { useChatExport } from '../hooks/useChatExport';
import { CLAIMS_ESCAPE, isEscapeClaimed } from '../hooks/useEscapeToClose';
import { usePolicyToast } from '../hooks/usePolicyToast';
import { FLAGSHIP_AGENT_ID } from '../utils/newTask';
import { isPlaceholderTitle } from '../utils/sessionTitle';
import { lastSentMessage } from '../utils/editLast';
import { addDroppedFiles, showAddedDocuments } from '../utils/chatDocuments';
import { requestErrorParam } from '../utils/requestErrors';
import { MAX_SESSION_TITLE_CHARS, STARTING_REASON } from '../utils/constants';
import { scrollBehavior } from '../utils/motion';
import { setVisibleInterval } from '../utils/visibleInterval';
import { ModelServerDownNotice } from './ModelServerDownNotice';
import { useChatDraft } from '../hooks/useSavedDraft';
import { useOpeningMessage } from '../hooks/useOpeningMessage';
import { getT, useT } from '../i18n/useT';

/** Events that only come after the agent got past a permission prompt. */
const RESOLVES_PROMPTS = new Set(['tool_start', 'tool_end', 'tool_result']);
const RECONNECTING_PREFIX = 'Connection lost. Reconnecting';

/** How long Stop waits for the server's closing `done` before the client closes the turn itself. */
export const STOP_GRACE_MS = 15_000;

export { QUEUE_BUSY_WAIT_MS } from '../services/queueDispatch';

const NO_QUEUE: QueuedMessage[] = [];
const NO_STEPS: AgentStep[] = [];

/** Notices by server message id: a refetch replaces messages, so they cannot live on them alone. */
const historyNotices = new Map<number, string>();

/** One quiet line saying older turns left the model's view. */
function HistoryNotice({ text }: { text: string }) {
    return <div className="history-notice" role="note">{text}</div>;
}

function LibraryNotices({ notices }: { notices: string[] }) {
    return (
        <div className="library-notices" role="note">
            {notices.map((notice, index) => <div key={`${index}-${notice}`} dir="auto">{notice}</div>)}
        </div>
    );
}

interface ChatViewProps {
    sessionId: string;
}

interface SendOptions {
    /** Re-subscribe to a run already in flight server-side. */
    attach?: boolean;
    /** A message from the queue: already composed, and the composer stays as the user left it. */
    queued?: boolean;
    /** For a queued message: told once whether the server took it. */
    onQueuedOutcome?: (outcome: SendOutcome) => void;
    /** Re-attach attempt number for a stream that went silent. */
    reconnect?: number;
    /** Opens the chat, so it goes ahead of anything queued. */
    opening?: boolean;
}

export function ChatView({ sessionId }: ChatViewProps) {
    const t = useT();
    const {
        sessions, messages, setMessages, addMessage, removeMessage, removeMessagesFrom, updateSessionInList,
        isStreaming, streamingContent, setStreaming, setStreamContent, clearStreamContent,
        agentSteps, addAgentStep, updateLastAgentStep, appendThinkingContent, updateLastToolStep, clearAgentSteps,
        cards, appendCard, clearCards,
        setDocuments, setShowFileBrowser, isLoadingMessages, setLoadingMessages,
        systemStatus,
    } = useChatStore();

    const addNotification = useNotificationStore((s) => s.addNotification);
    const notificationPanelVisible = useNotificationStore((s) => s.showPanel);
    const setNotificationPanelVisible = useNotificationStore((s) => s.setShowPanel);
    const setNotificationTypeFilter = useNotificationStore((s) => s.setTypeFilter);
    const pendingPrompt = useChatStore((s) => (s.pendingPrompt?.sessionId === sessionId ? s.pendingPrompt : null));
    const liveStatus = useChatStore((s) => s.liveStatus);
    const toolStatus = useChatStore(selectToolStatus);
    const awaitingUser = useChatStore((s) => s.awaitingUser);
    // Other events can arrive while a question waits; it still waits on the user.
    const hasLiveQuestion = useChatStore((s) => Object.values(s.liveQuestions).some((q) => q.sessionId === sessionId));
    // While GAIA waits on a question, the message box answers it instead of queueing a new message.
    const answeringId = useChatStore((s) => waitingQuestionId(s.liveQuestions, sessionId));
    // An unanswered prompt means GAIA is waiting on the user, whatever event arrived last.
    const hasOpenPrompt = useNotificationStore((s) => selectSessionPermissionPrompt(sessionId)(s) !== null);

    const session = sessions.find((s) => s.id === sessionId);
    const [input, setInput] = useChatDraft(sessionId, session?.private);
    const [editingTitle, setEditingTitle] = useState(false);
    const [titleDraft, setTitleDraft] = useState('');
    const [isDragOver, setIsDragOver] = useState(false);
    const [showScrollBtn, setShowScrollBtn] = useState(false);
    // Store agent steps snapshot for completed messages
    const attach = useAttachments();
    const [deletingMsgId, setDeletingMsgId] = useState<number | null>(null);
    const { policyToast, showPolicyToast, dismissPolicyToast } = usePolicyToast();
    const [pendingReceiptScroll, setPendingReceiptScroll] = useState<string | null>(null);
    const { exportChat, exportError, clearExportError } = useChatExport(sessionId, session?.title);
    // Why the last rename, private toggle, drop or detach failed.
    const [actionError, setActionError] = useState<string | null>(null);
    useEffect(() => setActionError(null), [sessionId]);
    const handleDetachFailed = useCallback((name: string, err: unknown) => {
        setActionError(t('chat.detachFailed', { name, error: requestErrorParam(err) }));
    }, [t]);
    // A chat made by a retired agent (chat/doc/file) can be read but not continued.
    const retiredAgent = session?.agent_type && session.agent_type !== FLAGSHIP_AGENT_ID ? session.agent_type : null;

    // Smooth streaming exit — snapshot last content so fade-out shows real text
    const [streamEnding, setStreamEnding] = useState(false);
    const [liveHistoryNotice, setLiveHistoryNotice] = useState<string | null>(null);
    const lastStreamContentRef = useRef('');
    const lastAgentStepsRef = useRef<AgentStep[]>([]);
    // Render keys that outlive a message's temporary id, and answers already on screen as the live bubble.
    const messageKeysRef = useRef(new Map<number, number>());
    const settledIdsRef = useRef(new Set<number>());
    const lastCardsRef = useRef<RenderCardData[]>([]);
    const prevStreamingRef = useRef(false);
    // Continuously snapshot the streaming state so we have it when streaming ends
    useEffect(() => {
        if (streamingContent) lastStreamContentRef.current = streamingContent;
    }, [streamingContent]);
    useEffect(() => {
        if (agentSteps.length > 0) lastAgentStepsRef.current = agentSteps.map(s => ({ ...s, active: false }));
    }, [agentSteps]);
    useEffect(() => {
        if (cards.length > 0) lastCardsRef.current = [...cards];
    }, [cards]);
    // Auto-read needs the saved choice before the first reply finishes.
    useReadAloudSettings();
    useEffect(() => {
        if (!isStreaming && prevStreamingRef.current) {
            setStreamEnding(true);
            const timer = setTimeout(() => {
                setStreamEnding(false);
                lastStreamContentRef.current = '';
                lastAgentStepsRef.current = [];
                lastCardsRef.current = [];
            }, 350);
            return () => clearTimeout(timer);
        }
        prevStreamingRef.current = isStreaming;
    }, [isStreaming]);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const messagesScrollRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);
    // Custom-caret tracking removed (was a 10×18 red glowing block that
    // blinked once per second — too distracting for an always-visible
    // text input). Native browser caret is plenty.
    const abortRef = useRef<AbortController | null>(null);
    /** When this view's own stream last ended; gates re-attaching (#5247). */
    const streamEndedAtRef = useRef(0);
    /** The view closed a turn the server never confirmed stopping; don't reopen it. */
    const gaveUpOnRunRef = useRef(false);
    /** Reconnects spent on the current turn's stream. */
    const reconnectsRef = useRef(0);
    /** A re-attach waiting out its delay; cleared when the chat changes or the turn stops. */
    const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    // Stop keeps the stream open until the server's closing `done` arrives.
    const [stopping, setStopping] = useState(false);
    const stopRequestedRef = useRef(false);
    const stopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const closeStoppedTurnLocallyRef = useRef<(() => void) | null>(null);
    const stepIdRef = useRef(0);
    /** The agent's own step number for the live turn, from its `step` events. */
    const toolOccurredRef = useRef(false);
    const sendMessageRef = useRef<(text?: string, options?: SendOptions) => Promise<boolean>>(async () => false);
    const checkingModelRef = useRef(false);
    // Messages typed while a reply runs, kept per chat in the store, and whether the user stopped that reply.
    const queue = useChatStore((s) => s.messageQueues[sessionId] ?? NO_QUEUE);
    const setQueue = useCallback(
        (fn: (items: QueuedMessage[]) => QueuedMessage[]) => useChatStore.getState().updateQueue(sessionId, fn),
        [sessionId],
    );
    const userStoppedRef = useRef(false);
    /** The running reply said up front that it can't take a message mid-turn. */
    const [followupsUnavailable, setFollowupsUnavailable] = useState(false);
    /** This view saw its chat's last turn end, so the queue need not wait for a poll to say so. */
    const observedEndRef = useRef(false);
    /** A queued message's send that the server has not yet taken; leaving the chat must not abort it. */
    const queuedSendPendingRef = useRef(false);
    const streamingRef = useRef(isStreaming);
    streamingRef.current = isStreaming;
    // Set briefly after a queued send found the chat still busy, so it waits before trying again.
    const queueHold = useChatStore((s) => !!s.queueHolds[sessionId]);

    // ── Streaming chunk buffer ──────────────────────────────────────
    // Buffer SSE chunks in a ref and flush to the store via rAF.
    // This limits React re-renders to ~60fps instead of once per chunk
    // (which can be hundreds/sec), dramatically reducing DOM mutations
    // and eliminating extension-triggered "runtime.lastError" floods.
    const streamBufferRef = useRef('');
    const streamRafRef = useRef<number | null>(null);
    const scrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    /** Timestamp of the last auto-scroll (used for throttling). */
    const lastScrollRef = useRef(0);
    /** True when the user is at (or near) the bottom of the messages list.
     *  Auto-scroll only fires when this is true, so scrolling up to read
     *  earlier messages won't be interrupted by new streaming content. */
    const isNearBottomRef = useRef(true);
    const { userScrolling, clearUserScroll, listProps: followListProps } =
        useFollowScroll(messagesScrollRef, isNearBottomRef, (dist) => setShowScrollBtn(dist > 200));

    // True once the user has navigated to another session. The store's
    // currentSessionId flips synchronously on switch (the displayed view lags
    // ~220ms behind it), so an in-flight stream callback can detect it's now
    // writing for a background session and bail before mutating shared state
    // (#1580).
    const isStale = useCallback(
        () => useChatStore.getState().currentSessionId !== sessionId,
        [sessionId],
    );

    const flushStreamBuffer = useCallback(() => {
        streamRafRef.current = null;
        if (isStale()) return; // don't flush into the now-active session's store
        if (streamBufferRef.current) {
            setStreamContent(streamBufferRef.current);
        }
    }, [setStreamContent, isStale]);

    // Load messages on mount, then poll for external changes (MCP, API)
    const lastMsgCountRef = useRef<number>(0);
    // Messages sent from this view; a history load that started before one must not replace it.
    const sentRef = useRef<number>(0);

    useEffect(() => {
        log.chat.info(`ChatView mounted for session=${sessionId}, loading messages...`);
        const t = log.chat.time();
        setLoadingMessages(true);
        let cancelled = false;
        let loading = false;

        const loadMessages = async (isInitial = false) => {
            if (cancelled || loading) return;
            loading = true;
            const sentBefore = sentRef.current;
            try {
                if (!isInitial) {
                    if (useChatStore.getState().isStreaming) return;
                    const total = await api.getMessageCount(sessionId);
                    if (cancelled || total === lastMsgCountRef.current || useChatStore.getState().isStreaming) return;
                }
                const data = await api.getMessages(sessionId);
                if (cancelled) return;
                const msgs = fromApiMessages(data.messages);
                if (isInitial && sentRef.current !== sentBefore) {
                    // A message went out while this loaded; the turn's refetch, or after an error the next poll, syncs the list.
                    lastMsgCountRef.current = msgs.length;
                } else if (isInitial) {
                    setMessages(msgs);
                    lastMsgCountRef.current = msgs.length;
                    log.chat.timed(`Loaded ${msgs.length} message(s) for session=${sessionId}`, t);
                } else if (msgs.length !== lastMsgCountRef.current && !useChatStore.getState().isStreaming) {
                    // New messages from external source (MCP, API) — refresh
                    log.chat.info(`Messages changed externally: ${lastMsgCountRef.current} -> ${msgs.length}`);
                    const shown = useChatStore.getState().messages;
                    carryMessageKeys(shown, msgs, messageKeysRef.current);
                    setMessages(keepFailedReply(shown, msgs));
                    lastMsgCountRef.current = msgs.length;
                }
            } catch (err) {
                if (cancelled) return;
                if (isInitial) {
                    log.chat.error(`Failed to load messages for session=${sessionId}`, err);
                    setMessages([]);
                }
            } finally {
                loading = false;
                if (!cancelled && isInitial) setLoadingMessages(false);
            }
        };

        loadMessages(true);

        // Poll every 3s for messages added by external tools (MCP API, etc.); not while hidden.
        const stopPoll = setVisibleInterval(() => loadMessages(false), 3_000);
        return () => {
            cancelled = true;
            stopPoll();
        };
    }, [sessionId, setMessages, setLoadingMessages]);

    // Load indexed documents on mount (so context bar is always up to date)
    useEffect(() => {
        api.listDocuments()
            .then((data) => setDocuments(data.documents || []))
            .catch((err) => log.doc.error('Could not load the document list', err));
    }, [setDocuments]);

    const mountedRef = useRef(true);
    const initializing = isStarting(systemStatus);
    useEffect(() => {
        mountedRef.current = true;
        return () => { mountedRef.current = false; };
    }, []);
    const { openingRef, openingShown, rows, isUnsent, openingKey } = useOpeningMessage({
        sessionId, pendingPrompt, messages, messageCount: session?.message_count ?? 0,
        isLoadingMessages, initializing, mountedRef, send: sendMessageRef,
    });

    // Auto-scroll (throttled) — scrolls at most once per 100ms while
    // streaming, and also schedules a trailing scroll so the final chunk
    // is never missed.  Only fires when the user hasn't scrolled away.
    useEffect(() => {
        if (!isNearBottomRef.current) return;

        const now = Date.now();
        const elapsed = now - lastScrollRef.current;
        const THROTTLE_MS = 100;

        const doScroll = () => {
            // Re-check: the user may have scrolled up since this was scheduled.
            if (!isNearBottomRef.current || userScrolling()) return;
            lastScrollRef.current = Date.now();
            // Instant: a smooth follow still in flight would override the reader's wheel.
            messagesEndRef.current?.scrollIntoView({ behavior: 'instant' });
        };

        if (elapsed >= THROTTLE_MS) {
            // Enough time passed — scroll immediately
            doScroll();
        }

        // Always schedule a trailing scroll so the very last update is caught
        if (scrollTimerRef.current) clearTimeout(scrollTimerRef.current);
        scrollTimerRef.current = setTimeout(doScroll, THROTTLE_MS);
    }, [messages, streamingContent, agentSteps]);

    // Focus input
    useEffect(() => { inputRef.current?.focus(); }, [sessionId]);

    useEffect(() => { userStoppedRef.current = false; }, [sessionId]);

    const { contextUsage, contextUnavailable, refreshContext, showContextUsage } = useContextUsage(sessionId, isStale);
    const { comparison, comparing, comparingRef, handleRetryWith, handleKeepAnswer, handleCloseComparison, closeForNewTurn } =
        useAnswerComparison({ sessionId, isStreaming, isStale, setMessages, refreshContext });

    // Abort active stream and clean up timers when component unmounts / session changes
    useEffect(() => {
        return () => {
            // A turn left running keeps the chat busy until a later poll says otherwise.
            if (streamingRef.current) markActivity(sessionId);
            // Aborting a queued send the server has not answered could lose it; it finishes on its own.
            if (abortRef.current && !queuedSendPendingRef.current) {
                abortRef.current.abort();
            }
            if (abortRef.current) {
                abortRef.current = null;
            }
            if (stopTimerRef.current) {
                clearTimeout(stopTimerRef.current);
                stopTimerRef.current = null;
            }
            stopRequestedRef.current = false;
            closeStoppedTurnLocallyRef.current = null;
            setStopping(false);
            if (streamRafRef.current !== null) {
                cancelAnimationFrame(streamRafRef.current);
                streamRafRef.current = null;
            }
            if (scrollTimerRef.current) {
                clearTimeout(scrollTimerRef.current);
                scrollTimerRef.current = null;
            }
            if (reconnectTimerRef.current) {
                clearTimeout(reconnectTimerRef.current);
                reconnectTimerRef.current = null;
            }
            dismissPolicyToast();
            streamBufferRef.current = '';
            // Clear this view's live turn now, before the next view's effects re-attach theirs.
            useChatStore.getState().resetStreaming();
        };
    }, [sessionId]);

    // Scroll to the receipt once the notification panel has rendered it.
    useEffect(() => {
        if (!pendingReceiptScroll || !notificationPanelVisible) return;
        document
            .getElementById(policyReceiptAnchor(pendingReceiptScroll))
            ?.scrollIntoView({ behavior: scrollBehavior(), block: 'center' });
        setPendingReceiptScroll(null);
    }, [pendingReceiptScroll, notificationPanelVisible]);

    const panelWidth = useSessionPanelStore((s) => s.width);
    const revealPanelSection = useSessionPanelStore((s) => s.reveal);
    const showResources = useResourceMonitorStore((s) => s.visible);
    const setShowResources = useResourceMonitorStore((s) => s.setVisible);
    const resourcesToggleRef = useRef<HTMLButtonElement>(null);
    const closeResources = useCallback(() => {
        setShowResources(false);
        // The close button unmounts with the strip; keep keyboard focus nearby.
        resourcesToggleRef.current?.focus();
    }, [setShowResources]);

    const jumpToMessage = useJumpToMessage(messagesScrollRef, isNearBottomRef);

    const scrollToBottom = useCallback(() => {
        isNearBottomRef.current = true;
        clearUserScroll();
        messagesEndRef.current?.scrollIntoView({ behavior: scrollBehavior() });
    }, [clearUserScroll]);

    // Stop: ask the server to cancel, but keep reading the stream. The run
    // outlives the connection (#1580) and closes with a `done` carrying the
    // persisted turn, steps included — the user must be able to audit what ran.
    const handleStop = useCallback(() => {
        if (stopRequestedRef.current) return;
        stopRequestedRef.current = true;
        userStoppedRef.current = true;
        setStopping(true);
        useNotificationStore.getState().dismissSessionPrompts(sessionId);
        useChatStore.getState().closeSessionQuestions(sessionId);
        log.stream.warn('User stopped generation; waiting for the server to close the turn');
        // A 404 here usually means the run just finished and its `done` is in
        // flight; the grace timer below closes the turn if nothing arrives.
        api.cancelStream(sessionId).catch((err) => {
            log.stream.error('Stop request failed; waiting for the stream to close', err);
        });
        // Between a lost stream and its re-attach there is no stream to wait on.
        if (reconnectTimerRef.current) {
            clearTimeout(reconnectTimerRef.current);
            reconnectTimerRef.current = null;
            closeStoppedTurnLocallyRef.current?.();
            return;
        }
        stopTimerRef.current = setTimeout(() => {
            log.stream.error(
                `No closing event ${STOP_GRACE_MS}ms after Stop; closing the turn locally (session=${sessionId})`,
            );
            closeStoppedTurnLocallyRef.current?.();
        }, STOP_GRACE_MS);
    }, [sessionId]);

    // Escape stops the reply. Ctrl+K is App's.
    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            // Dialogs, menus, the rename field and the permission prompt claim Escape first.
            if (e.key === 'Escape' && isStreaming && !isEscapeClaimed(e)) {
                e.preventDefault();
                handleStop();
            }
        };
        window.addEventListener('keydown', handler);
        return () => window.removeEventListener('keydown', handler);
    }, [isStreaming, handleStop]);

    /**
     * With no text, sends the composer's draft and empties the box; given text is sent as is and the box is left alone.
     * Resolves false when the message was neither sent nor queued.
     */
    const sendMessage = useCallback(async (givenText?: string, options?: SendOptions): Promise<boolean> => {
        // attach=true re-subscribes to a run already in flight server-side
        // (revisiting a backgrounded session, #1580). It reuses the entire
        // stream-event handling below but skips composing/sending a new turn:
        // no optimistic user message, no input/attachment handling, and the
        // controller comes from api.attachToRun instead of api.sendMessageStream.
        const reattach = options?.attach === true;
        const fromQueue = options?.queued === true;
        // reconnect=N re-attaches a stream that went silent, replacing the dead one still marked live.
        const reconnect = reattach ? options?.reconnect ?? 0 : 0;
        const fromComposer = !reattach && givenText === undefined;
        const text = (fromComposer ? input : givenText ?? '').trim();
        const hasAttachments = fromComposer && attach.hasUploaded;
        const hasImage = fromComposer && attach.hasImage;
        const takeDraft = () => {
            if (!fromComposer) return text;
            const composed = attach.compose(text);
            setInput('');
            attach.clear();
            inputRef.current?.focus();
            return composed;
        };

        // User just sent a message — re-pin scroll to the bottom so the
        // new message and streaming response are visible.
        isNearBottomRef.current = true;

        // The store, not this render's copy: a resend re-reads the status just before it calls.
        const isInitializing = isStarting(useChatStore.getState().systemStatus);
        const notTaken = (outcome: SendOutcome) => { if (fromQueue) options?.onQueuedOutcome?.(outcome); };
        if (!reattach && retiredAgent) {
            log.chat.warn(`Send blocked: chat ${sessionId} belongs to the retired "${retiredAgent}" agent`);
            notTaken({ kind: 'failed', error: `This chat's "${retiredAgent}" agent was retired, so it cannot take new messages.` });
            return false;
        }
        // Waiting messages go first, so a new one joins the end of the line.
        const waiting = !options?.opening && (useChatStore.getState().messageQueues[sessionId] ?? NO_QUEUE).length > 0;
        if (reattach) {
            // Don't double-attach if a stream is already live in this view.
            if (isStreaming && !reconnect) return false;
        } else if (!fromQueue && (text || hasAttachments) && !isInitializing && (isStreaming || comparingRef.current || waiting)) {
            // Its own turn by default: a model handed a message mid-turn may finish without answering it.
            const queued = takeDraft();
            setQueue((q) => messageQueue.enqueue(q, queued, 'queued'));
            return true;
        } else if ((!text && !hasAttachments) || isStreaming || isInitializing) {
            notTaken({ kind: 'busy' });
            if (!text && !hasAttachments) log.chat.debug('Send blocked: empty message');
            if (isStreaming) log.chat.debug('Send blocked: already streaming');
            if (isInitializing) log.chat.debug('Send blocked: system initializing');
            return false;
        }

        // Asked here so a send that was going to be queued or refused asks nothing.
        if (!reattach && !fromQueue && !(modelKnownReady('chat') && (!hasImage || modelKnownReady('vision')))) {
            if (checkingModelRef.current) return false;
            checkingModelRef.current = true;
            try {
                if (!(await ensureModel('chat')) || (hasImage && !(await ensureModel('vision')))) return false;
            } finally {
                checkingModelRef.current = false;
            }
        }

        // Build message text with attachment references
        let messageText = text;
        let optimisticUser: Message | null = null;
        if (!reattach) {
            messageText = takeDraft();

            log.chat.info(`Sending message to session=${sessionId}`, { length: messageText.length, preview: messageText.slice(0, 80) });

            closeForNewTurn();

            // Optimistic user message
            const userMsg: Message = {
                id: Date.now(),
                session_id: sessionId,
                role: 'user',
                content: messageText,
                created_at: new Date().toISOString(),
                rag_sources: null,
            };
            addMessage(userMsg);
            optimisticUser = userMsg;
            sentRef.current += 1;
        } else {
            log.chat.info(`Re-attaching to background run for session=${sessionId}${reconnect ? ` (reconnect ${reconnect})` : ''}`);
        }
        if (!reconnect) reconnectsRef.current = 0;

        // Start streaming
        setStreaming(true);
        clearStreamContent();
        clearAgentSteps();
        // Also covers attach-replay dedupe — attachToRun replays all events
        // from the start, so stale cards would double up without this.
        clearCards();
        stepIdRef.current = 0;
        toolOccurredRef.current = false;
        if (reconnect) {
            useChatStore.getState().setLiveStatus(`${RECONNECTING_PREFIX} (attempt ${reconnect} of ${MAX_STREAM_RECONNECTS})…`);
        }

        log.stream.info('Starting agent stream...');
        const streamStart = log.stream.time();

        let fullContent = '';
        let historyNotice: string | undefined;
        setLiveHistoryNotice(null);
        let doneHandled = false;
        observedEndRef.current = false;
        if (!reattach) setFollowupsUnavailable(false);
        let streamController: AbortController | null = null;
        let queuedSettled = !fromQueue;
        queuedSendPendingRef.current = fromQueue;
        const settleQueued = (outcome: SendOutcome) => {
            if (queuedSettled) return;
            queuedSettled = true;
            queuedSendPendingRef.current = false;
            options?.onQueuedOutcome?.(outcome);
            // The view closed while the server answered; the run goes on without it.
            if (outcome.kind === 'accepted' && isStale()) streamController?.abort();
        };
        // The server names a new chat after its first question when it takes it.
        let titleShown = reattach || (!!session && !isPlaceholderTitle(session.title));
        const accepted = () => {
            settleQueued({ kind: 'accepted' });
            if (titleShown) return;
            titleShown = true;
            api.getSession(sessionId)
                .then((s) => updateSessionInList(sessionId, { title: s.title }))
                .catch((err) => log.chat.warn('Could not refresh the chat title', err));
        };
        streamBufferRef.current = '';
        stopRequestedRef.current = false;
        setStopping(false);

        const endStopWait = () => {
            if (stopTimerRef.current) {
                clearTimeout(stopTimerRef.current);
                stopTimerRef.current = null;
            }
            stopRequestedRef.current = false;
            closeStoppedTurnLocallyRef.current = null;
            setStopping(false);
        };

        const snapshotTurn = () => {
            const { agentSteps: steps, cards: liveCards } = useChatStore.getState();
            return {
                steps: steps.map((s) => ({ ...s, active: false })),
                cards: [...liveCards],
            };
        };

        // Used only when the server never closes a stopped turn: keep what
        // the user saw, and label it unconfirmed rather than pretend it ended.
        closeStoppedTurnLocallyRef.current = () => {
            if (doneHandled) return;
            doneHandled = true;
            gaveUpOnRunRef.current = true;
            streamEndedAtRef.current = Date.now();
            endStopWait();
            settleQueued({ kind: 'failed', error: 'Stopped before GAIA confirmed it got this message.' });
            abortRef.current?.abort();
            abortRef.current = null;
            if (streamRafRef.current !== null) {
                cancelAnimationFrame(streamRafRef.current);
                streamRafRef.current = null;
            }
            const content = streamBufferRef.current || useChatStore.getState().streamingContent;
            streamBufferRef.current = '';
            if (isStale()) return;
            const { steps, cards: cardsSnapshot } = snapshotTurn();
            addMessage({
                id: Date.now() + 1,
                session_id: sessionId,
                role: 'assistant',
                content,
                created_at: new Date().toISOString(),
                rag_sources: null,
                agentSteps: steps.length > 0 ? steps : undefined,
                cards: cardsSnapshot.length > 0 ? cardsSnapshot : undefined,
                stopState: 'unconfirmed',
            });
            setStreaming(false);
            clearStreamContent();
            clearAgentSteps();
            clearCards();
        };

        const streamCallbacks: api.StreamCallbacks = {
            onConnected: () => {
                if (isStale()) return;
                const store = useChatStore.getState();
                if (store.liveStatus === api.SEND_WAITING_NOTICE_TEXT || store.liveStatus?.startsWith(RECONNECTING_PREFIX)) {
                    store.setLiveStatus(null);
                }
            },
            onChunk: (event) => {
                accepted();
                if (isStale()) return; // stop writing after a session switch (#1580)
                if (useChatStore.getState().awaitingUser) useChatStore.getState().setAwaitingUser(false);
                const content = event.content || '';
                if (content) useNotificationStore.getState().dismissSessionPrompts(sessionId);
                if (content) {
                    // 'answer' events carry the full final text (not a delta),
                    // so replace rather than append to avoid doubling content.
                    if (event.type === 'answer') {
                        fullContent = content;
                    } else {
                        // A tool between text chunks starts a new paragraph. Clear the flag on
                        // the first chunk either way, or it splits the answer after its first word.
                        if (toolOccurredRef.current) {
                            if (fullContent.length > 0) fullContent += '\n\n';
                            toolOccurredRef.current = false;
                        }
                        fullContent += content;
                        // The answer has begun, so the reasoning before it is over.
                        const steps = useChatStore.getState().agentSteps;
                        const last = steps[steps.length - 1];
                        if (last?.type === 'thinking' && last.active) {
                            updateLastAgentStep({ active: false, latencyMs: Date.now() - last.timestamp });
                        }
                    }
                    // Safety net: strip any tool-call JSON that leaked past the
                    // backend SSE filter (see sse_handler.py _TOOL_CALL_JSON_RE).
                    let cleaned = fullContent.replace(TOOL_CALL_JSON_SAFETY_RE, '').trim();
                    // Strip LLM JSON envelope ({"thought":"...", "answer":"..."} format).
                    // This handles the case where the entire response is wrapped in JSON.
                    cleaned = stripStreamingEnvelope(cleaned);
                    // During streaming, suppress trailing incomplete JSON blocks
                    // (e.g. {"thought":"partial... that haven't closed yet)
                    if (cleaned) {
                        const trailingBrace = cleaned.lastIndexOf('{');
                        if (trailingBrace > -1) {
                            const tail = cleaned.slice(trailingBrace);
                            if (/^\{\s*"(?:thought|answer|goal|tool)"/.test(tail) &&
                                (tail.match(/\{/g) || []).length > (tail.match(/\}/g) || []).length) {
                                cleaned = cleaned.slice(0, trailingBrace).trim();
                            }
                        }
                    }
                    // Buffer chunks and flush to store at most once per frame (~60fps)
                    // instead of triggering a React re-render on every single SSE chunk
                    streamBufferRef.current = cleaned;
                    if (streamRafRef.current === null) {
                        streamRafRef.current = requestAnimationFrame(flushStreamBuffer);
                    }
                }
            },
            onAgentEvent: (event) => {
                // The client's own "waiting to start" notice is not the server taking the message.
                if (!(event.type === 'status' && event.phase === 'waiting')) accepted();
                // The queue is this chat's, so it stays current even after the view moved on.
                if (event.type === 'followup_taken' && event.text) {
                    const takenText = event.text;
                    setQueue((q) => messageQueue.taken(q, takenText));
                    if (isStale()) return;
                    addMessage({
                        id: Date.now(),
                        session_id: sessionId,
                        role: 'user',
                        content: takenText,
                        created_at: new Date().toISOString(),
                        rag_sources: null,
                    });
                    return;
                }
                if (event.type === 'followup_unavailable') {
                    if (!isStale()) setFollowupsUnavailable(true);
                    return;
                }
                if (event.type === 'followup_returned') {
                    const texts = event.texts ?? [];
                    setQueue((q) => messageQueue.returned(q, texts));
                    return;
                }
                // Ignore events from a stream the user navigated away from so its
                // steps don't leak into the new session's view (#1580).
                if (isStale()) return;
                if (event.type === 'context_usage') {
                    showContextUsage(event as unknown as ContextUsage);
                    return;
                }
                // The agent emits nothing while it blocks on the user, so the next event ends the wait.
                const waitsOnUser = event.type === 'permission_request' || event.type === 'needs_input';
                if (useChatStore.getState().awaitingUser !== waitsOnUser) {
                    useChatStore.getState().setAwaitingUser(waitsOnUser);
                }
                // Permission request — shown inline above the composer. Calls the
                // user already allowed "always" never get here: the backend skips them.
                // The run moved past any prompt still open, so a replayed one is already resolved.
                if (RESOLVES_PROMPTS.has(event.type)) {
                    useNotificationStore.getState().dismissSessionPrompts(sessionId);
                }
                if (event.type === 'permission_request') {
                    showPermissionRequest(event, sessionId);
                    return;
                }

                // ── Stateless confirmation card (email /query, #2109 D1) ──
                // Informational: the sidecar run is already over, so this is
                // a chat-flow card via the generic #2108 mechanism — NEVER
                // the blocking PermissionPrompt/confirm_id path above. Only
                // {action, summary} cross over; no run_id passthrough.
                if (event.type === 'needs_confirmation') {
                    appendCard({
                        render: 'needs_confirmation',
                        data: {
                            action: typeof event.action === 'string' ? event.action : '',
                            summary: typeof event.summary === 'string' ? event.summary : '',
                        },
                    });
                    return;
                }

                // ── Mid-run question (#2595) — answerable, non-terminal: the
                // agent blocks server-side until NeedsInputCard posts an
                // answer via POST /chat/user-input, then the run continues.
                if (event.type === 'needs_input') {
                    if (!event.request_id) {
                        console.error('[ChatView] needs_input event missing request_id, ignoring');
                        return;
                    }
                    useChatStore.getState().openQuestion(
                        event.request_id,
                        sessionId,
                        typeof event.timeout_seconds === 'number' ? event.timeout_seconds : null,
                        {
                            options: Array.isArray(event.options) ? event.options : [],
                            allowFreeText: event.allow_free_text !== false,
                            sensitive: Boolean(event.sensitive),
                        },
                    );
                    appendCard({
                        render: 'needs_input',
                        data: {
                            session_id: sessionId,
                            request_id: event.request_id,
                            question: typeof event.question === 'string' ? event.question : '',
                            options: Array.isArray(event.options) ? event.options : [],
                            allow_free_text: event.allow_free_text !== false,
                            sensitive: Boolean(event.sensitive),
                        },
                    });
                    return;
                }

                if (event.type === 'policy_alert') {
                    const notification = policyAlertNotification(event, sessionId, `policy-${Date.now()}-${stepIdRef.current + 1}`);
                    addNotification(notification);
                    const step = agentEventToStep(event, stepIdRef);
                    if (step) addAgentStep(step);
                    showPolicyToast({ tool: notification.tool, receiptId: event.receipt_id });
                    return;
                }

                // Tool completion updates the last TOOL step (not just the last step,
                // since thinking/status events may have been interleaved during execution)
                // The call is over, so the live line must stop naming it; the model has the turn again.
                if ((event.type === 'tool_end' || event.type === 'tool_result')
                    && useChatStore.getState().livePhase === 'tool') {
                    useChatStore.getState().setLiveStatus(null);
                }
                if (event.type === 'tool_end') {
                    updateLastToolStep({ active: false, success: event.success !== false });
                    return;
                }
                if (event.type === 'tool_result') {
                    // Structured card (#2108): a non-empty render key mounts a
                    // registered card component against event.data.
                    if (typeof event.render === 'string' && event.render.length > 0) {
                        appendCard({ render: event.render, data: event.data });
                    }
                    updateLastToolStep(toolResultUpdates(event));
                    return;
                }
                // Tool args update the last TOOL step with detail
                if (event.type === 'tool_args') {
                    // A call with no arguments keeps its tool_start label, not "{}".
                    const hasArgs = !!event.args && Object.keys(event.args).length > 0;
                    const detail = event.detail || (hasArgs ? JSON.stringify(event.args) : '');
                    updateLastToolStep({
                        ...(detail ? { detail } : {}),
                        ...(event.narration ? { narration: event.narration } : {}),
                    });
                    const store = useChatStore.getState();
                    const tool = [...store.agentSteps].reverse().find((s) => s.type === 'tool');
                    if (event.narration && tool?.active && store.livePhase === 'tool') {
                        store.setLiveStatus(toolRowText(tool, getT()), 'tool');
                    }
                    return;
                }

                // A load_skill call is already its own row; other pickups get one.
                if (event.type === 'skill_use') {
                    useChatStore.getState().addSkillStep(
                        event.how === 'model' ? null : agentEventToStep(event, stepIdRef),
                    );
                    return;
                }

                // The text streamed so far narrated the call that follows: it leaves the answer for the timeline.
                if (event.type === 'commentary') {
                    fullContent = '';
                    toolOccurredRef.current = false;
                    streamBufferRef.current = '';
                    setStreamContent('');
                    const step = agentEventToStep(event, stepIdRef);
                    if (step) addAgentStep(step);
                    return;
                }

                // Not the answer: it was reasoning (a lone </think>), or was cut off (status; say so).
                if (event.retracts_answer) {
                    fullContent = '';
                    toolOccurredRef.current = false;
                    streamBufferRef.current = '';
                    setStreamContent('');
                    if (event.type === 'status') {
                        const step = agentEventToStep(
                            { type: 'status', status: 'warning', message: getT()('live.answerRestarted') } as StreamEvent,
                            stepIdRef,
                        );
                        if (step) addAgentStep(step);
                        return;
                    }
                }

                // One "Thinking" step per turn. appendThinkingContent() appends
                // inside a single set(), so racing chunks never lose text.
                if (event.type === 'thinking') {
                    const currentSteps = useChatStore.getState().agentSteps;
                    const lastStep = currentSteps[currentSteps.length - 1];
                    if (lastStep && lastStep.type === 'thinking') {
                        appendThinkingContent(event.content || '');
                        return;
                    }
                    // First thinking step or after a non-thinking step - create it
                    const step = agentEventToStep(event, stepIdRef);
                    if (step) addAgentStep(step);
                    return;
                }

                if (event.type === 'status') {
                    if (event.status === 'history_cut') {
                        historyNotice = (event.message || '').trim() || undefined;
                        setLiveHistoryNotice(historyNotice ?? null);
                        return;
                    }
                    applyStatusEvent(event, stepIdRef);
                    return;
                }

                // The step counter measures the harness; the timeline shows the work.
                if (event.type === 'step') return;

                if (event.type === 'agent_error' && isEchoOfFailedTool(event, useChatStore.getState().agentSteps)) {
                    return;
                }
                const step = agentEventToStep(event, stepIdRef);
                if (step) {
                    addAgentStep(step);
                    if (event.type === 'tool_start') {
                        toolOccurredRef.current = true;
                        // The model's phase ended with the call; say a tool runs, not the model.
                        useChatStore.getState().setLiveStatus(toolRowText(step, getT()), 'tool');
                    }
                }
            },
            onDone: (event) => {
                accepted();
                useNotificationStore.getState().dismissSessionPrompts(sessionId);
                useChatStore.getState().closeSessionQuestions(sessionId);
                if (doneHandled) return;
                doneHandled = true;
                // Label from the server's report, not the click: a Stop that
                // lands as the run finishes on its own leaves a normal answer.
                // A synthesized done (no message_id) means the stream closed unreported.
                const stopState: Message['stopState'] = event.cancelled === true
                    ? 'stopped'
                    : stopRequestedRef.current && event.message_id == null ? 'unconfirmed' : undefined;
                endStopWait();

                // Cancel any pending rAF flush — we have the final content
                if (streamRafRef.current !== null) {
                    cancelAnimationFrame(streamRafRef.current);
                    streamRafRef.current = null;
                }
                streamBufferRef.current = '';

                // Even for a backgrounded chat: these are library and chat-list state.
                showAddedDocuments(sessionId, event.documents_added ?? []);

                // A completion for a backgrounded session is persisted
                // server-side, so don't touch the shared store (#1580).
                if (isStale()) return;

                // `done` is the run's last event, so it is no longer running.
                streamEndedAtRef.current = Date.now();
                observedEndRef.current = true;
                const store = useChatStore.getState();
                store.setRunningSessions(store.runningSessionIds.filter((id) => id !== sessionId));

                const content = event.content || fullContent;
                log.chat.timed(`Agent response complete: ${content.length} chars${stopState ? ` (${stopState})` : ''}`, streamStart);

                // Snapshot agent steps and streaming cards (#2108) for the completed message
                const { steps: stepsSnapshot, cards: cardsSnapshot } = snapshotTurn();

                const hasPolicyAlert = stepsSnapshot.some((s) => s.type === 'policy_alert');
                if (content || hasPolicyAlert || stopState) {
                    // Update msg count ref so poll doesn't re-fetch what we just added
                    lastMsgCountRef.current = useChatStore.getState().messages.length + 1;
                    const assistantMsg: Message = {
                        id: event.message_id || Date.now() + 1,
                        session_id: sessionId,
                        role: 'assistant',
                        content,
                        created_at: new Date().toISOString(),
                        rag_sources: null,
                        agentSteps: stepsSnapshot.length > 0 ? stepsSnapshot : undefined,
                        stats: event.stats || undefined,
                        turnSummary: event.turn_summary || undefined,
                        cards: cardsSnapshot.length > 0 ? cardsSnapshot : undefined,
                        stopState,
                        historyNotice,
                        libraryNotices: event.library_notices,
                        failure: event.failed ? event.failure : undefined,
                    };
                    if (historyNotice) historyNotices.set(assistantMsg.id, historyNotice);
                    settledIdsRef.current.add(assistantMsg.id);
                    addMessage(assistantMsg);
                    const readAloud = useReadAloudStore.getState();
                    if (readAloud.settings?.auto && readsAloudWhenDone(assistantMsg)) {
                        void readAloud.read(`${sessionId}:${assistantMsg.id}`, cleanToolCallContent(content));
                    }
                }

                setStreaming(false);
                clearStreamContent();
                clearAgentSteps();
                clearCards();

                // Refocus input so user can immediately type the next message
                if (inputRef.current) inputRef.current.focus();

                // Refresh messages from DB to replace optimistic IDs with real
                // DB IDs.  Without this, delete/resend on user messages fails
                // because the optimistic Date.now() ID doesn't match the DB's
                // auto-increment ID.
                setTimeout(() => {
                    api.getMessages(sessionId)
                        .then((data) => {
                            // A queued message may already be streaming; its own end refetches.
                            if (isStale() || useChatStore.getState().isStreaming) return;
                            // Only cards a tool step persisted come back from the
                            // backend; keep the live ones (e.g. confirmations) by
                            // id, else role+content.
                            // The server's saved stop wins over a stop it never confirmed.
                            const prevMsgs = useChatStore.getState().messages;
                            const prevWithCards = prevMsgs.filter((m) => m.cards && m.cards.length > 0);
                            const msgs: Message[] = fromApiMessages(data.messages).map((m) => {
                                const prev = prevWithCards.find(
                                    (p) => p.id === m.id || (p.role === m.role && p.content === m.content),
                                );
                                return {
                                    ...m,
                                    cards: prev?.cards ?? m.cards,
                                    stopState: m.stopState ?? prevMsgs.find((p) => p.id === m.id)?.stopState,
                                    failure: m.failure ?? prevMsgs.find((p) => p.id === m.id)?.failure,
                                };
                            });
                            carryMessageKeys(prevMsgs, msgs, messageKeysRef.current);
                            setMessages(msgs);
                            lastMsgCountRef.current = msgs.length;
                            if (reattach && !content && msgs[msgs.length - 1]?.role === 'user') {
                                addMessage({
                                    id: Date.now() + 2,
                                    session_id: sessionId,
                                    role: 'assistant',
                                    content: RUN_LOST_TEXT,
                                    created_at: new Date().toISOString(),
                                    rag_sources: null,
                                });
                            }
                        })
                        .catch((err) => log.chat.error(`Could not refresh messages for session=${sessionId}`, err));
                }, 300);
            },
            onError: (err) => {
                const interrupted = isStreamInterrupted(err);
                // A queued message the server never took goes back in line, or shows as failed with a retry.
                // So does a direct send a busy chat refused.
                const requeued = queuedSettled && !reattach && !interrupted && !!optimisticUser
                    && requeueRefused(sessionId, messageText, err);
                if (!queuedSettled || requeued) {
                    if (interrupted) {
                        accepted();
                    } else {
                        settleQueued(isBusyError(err)
                            ? { kind: 'busy' }
                            : { kind: 'failed', error: classifyChatError(err).text });
                        streamEndedAtRef.current = Date.now();
                        endStopWait();
                        if (streamRafRef.current !== null) {
                            cancelAnimationFrame(streamRafRef.current);
                            streamRafRef.current = null;
                        }
                        streamBufferRef.current = '';
                        if (isStale()) return;
                        log.chat.info(`Queued message for session=${sessionId} not taken: ${err.message}`);
                        if (optimisticUser) removeMessage(optimisticUser.id);
                        observedEndRef.current = true;
                        setStreaming(false);
                        clearStreamContent();
                        clearAgentSteps();
                        clearCards();
                        return;
                    }
                }
                // Stop already asked the server to end the turn; don't reopen it.
                if (interrupted && stopRequestedRef.current && !doneHandled) {
                    log.chat.warn(`Stream for session=${sessionId} was lost while stopping; closing the turn locally`);
                    closeStoppedTurnLocallyRef.current?.();
                    return;
                }
                // This view is gone; the next view of this chat re-attaches the run itself.
                if (interrupted && !mountedRef.current) return;
                // A dead connection is not a failed turn: re-attach, which replays the run so far.
                if (interrupted && !doneHandled && !isStale() && reconnectsRef.current < MAX_STREAM_RECONNECTS) {
                    const attempt = ++reconnectsRef.current;
                    const delay = RECONNECT_DELAYS_MS[attempt - 1] ?? 0;
                    log.chat.warn(`Stream for session=${sessionId} lost (${err.name}); reconnecting in ${delay}ms (${attempt}/${MAX_STREAM_RECONNECTS})`);
                    useChatStore.getState().setLiveStatus(`${RECONNECTING_PREFIX} (attempt ${attempt} of ${MAX_STREAM_RECONNECTS})…`);
                    const reattach = () => {
                        reconnectTimerRef.current = null;
                        if (!mountedRef.current || isStale() || stopRequestedRef.current) return;
                        sendMessageRef.current(undefined, { attach: true, reconnect: attempt });
                    };
                    if (delay > 0) reconnectTimerRef.current = setTimeout(reattach, delay);
                    else reattach();
                    return;
                }
                streamEndedAtRef.current = Date.now();
                useNotificationStore.getState().dismissSessionPrompts(sessionId);
                useChatStore.getState().closeSessionQuestions(sessionId);
                const wasStopping = stopRequestedRef.current;
                endStopWait();
                // Cancel any pending rAF flush
                if (streamRafRef.current !== null) {
                    cancelAnimationFrame(streamRafRef.current);
                    streamRafRef.current = null;
                }
                streamBufferRef.current = '';

                // A real error event arriving on a backgrounded stream isn't
                // the active view's concern, so don't surface it (#1580).
                if (isStale()) return;

                if (!interrupted) observedEndRef.current = true;
                log.chat.error(`Chat error for session=${sessionId}`, err);

                // An error while stopping must not wipe the steps the user is auditing.
                if (wasStopping) {
                    const { steps, cards: cardsSnapshot } = snapshotTurn();
                    addMessage({
                        id: Date.now() + 2,
                        session_id: sessionId,
                        role: 'assistant',
                        content: fullContent,
                        created_at: new Date().toISOString(),
                        rag_sources: null,
                        agentSteps: steps.length > 0 ? steps : undefined,
                        cards: cardsSnapshot.length > 0 ? cardsSnapshot : undefined,
                        stopState: 'stopped',
                        stopError: err.message || 'Unknown error',
                    });
                    // The server persisted its own row for this turn; don't let the poll swap ours out.
                    lastMsgCountRef.current = useChatStore.getState().messages.length;
                    setStreaming(false);
                    clearStreamContent();
                    clearAgentSteps();
                    clearCards();
                    return;
                }
                // Plain cause + next step; the raw text stays in the log above.
                const { kind, text } = classifyChatError(err);
                const errorContent = kind === 'stream'
                    ? text
                    : text + '\n\n---\n*Unexpected error?* '
                        + `[Report it on GitHub](${bugReportUrl((err.message || '').slice(0, 80))})`;
                const errMsg: Message = {
                    id: Date.now() + 2,
                    session_id: sessionId,
                    role: 'assistant',
                    content: errorContent,
                    created_at: new Date().toISOString(),
                    rag_sources: null,
                    failed: !mayStillFinish(err),
                    failureText: err.message || undefined,
                };
                addMessage(errMsg);
                setStreaming(false);
                clearStreamContent();
                clearAgentSteps();
                clearCards();
            },
        };

        const controller = reattach
            ? api.attachToRun(sessionId, streamCallbacks)
            : api.sendMessageStream(sessionId, messageText, streamCallbacks);

        streamController = controller;
        abortRef.current = controller;
        return true;
    }, [input, attach, isStreaming, sessionId, session, retiredAgent, addMessage, removeMessage, setMessages, setStreaming, flushStreamBuffer, clearStreamContent, updateSessionInList, addAgentStep, updateLastAgentStep, appendThinkingContent, updateLastToolStep, clearAgentSteps, appendCard, clearCards, addNotification, isStale]);

    // Keep ref in sync so event listeners always call the latest sendMessage
    sendMessageRef.current = sendMessage;

    // Re-attach to a turn still running server-side (#1580), whenever this view
    // isn't streaming it: on open, on reload, and after leaving a chat that was
    // still streaming. The replay brings back a pending permission prompt (#5247).
    // A poll sent before this view's last stream ended can't trigger it, so a
    // finished turn is never re-attached.
    const runningHere = useChatStore((s) => s.runningSessionIds.includes(sessionId));
    const pollIsNewer = useChatStore((s) => s.activeRunsAt > streamEndedAtRef.current);
    useEffect(() => { void refreshActiveRuns(); }, [sessionId]);
    useEffect(() => {
        if (!runningHere) {
            gaveUpOnRunRef.current = false;
            return;
        }
        if (!pollIsNewer || isStreaming || gaveUpOnRunRef.current) return;
        log.chat.info(`Resuming live view of background run for session=${sessionId}`);
        sendMessageRef.current(undefined, { attach: true });
    }, [sessionId, runningHere, pollIsNewer, isStreaming]);

    // Listen for programmatic message dispatches from rich-content
    // components (currently the EmailPreScanCard's Approve / Reply
    // buttons). Wired as a window-level CustomEvent rather than prop
    // drilling so any embedded component can reach the active session
    // without ChatView having to know about it ahead of time.
    useEffect(() => {
        const handler = (evt: Event) => {
            const ce = evt as CustomEvent<{ text?: string }>;
            const text = ce.detail?.text;
            if (typeof text === 'string' && text.trim()) {
                sendMessageRef.current(text);
            }
        };
        window.addEventListener('gaia:send-message', handler);
        return () => window.removeEventListener('gaia:send-message', handler);
    }, []);

    // Refocus input when streaming ends (textarea is disabled during streaming,
    // which causes the browser to drop focus — restore it so the user can
    // immediately type the next message without clicking).
    useEffect(() => {
        if (!isStreaming && inputRef.current) {
            inputRef.current.focus();
        }
    }, [isStreaming]);

    // Delete a single message
    const handleDeleteMessage = useCallback(async (messageId: number) => {
        if (isStreaming) return;
        log.chat.info(`Deleting message ${messageId} from session=${sessionId}`);
        // Animate first, then remove after 250ms
        setDeletingMsgId(messageId);
        setTimeout(async () => {
            removeMessage(messageId);
            setDeletingMsgId(null);
            try {
                await api.deleteMessage(sessionId, messageId);
            } catch (err) {
                log.chat.error(`Failed to delete message ${messageId}`, err);
                // Reload messages on error to restore accurate state
                api.getMessages(sessionId)
                    .then((data) => {
                        if (isStale()) return;
                        setMessages(fromApiMessages(data.messages));
                    })
                    .catch((reloadErr) => log.chat.error(`Could not reload messages for session=${sessionId}`, reloadErr));
            }
        }, 250);
    }, [sessionId, isStreaming, removeMessage, setMessages, isStale]);

    // Resend a user message, or its edited text: delete it and everything below, then send.
    const resendingRef = useRef(false);
    // True once the text went out or waits in the queue; false when nothing was sent and nothing deleted.
    const handleResendMessage = useCallback(async (message: Message, failedReply?: Message, editedText?: string): Promise<boolean> => {
        if (isStreaming || message.role !== 'user' || resendingRef.current) return false;
        resendingRef.current = true;
        const text = editedText ?? message.content;
        log.chat.info(`Resending message ${message.id} from session=${sessionId}`, { preview: text.slice(0, 80) });

        const notify = (detail: string) => addNotification({
            id: `resend-failed-${sessionId}-${Date.now()}`,
            type: 'error',
            agentId: sessionId,
            agentName: 'GAIA',
            title: t('chatError.resendFailed'),
            message: detail,
            timestamp: Date.now(),
            read: false,
            dismissed: false,
            priority: 'high',
            sessionId,
        });

        try {
            // The old turn is deleted below, so first make sure the new one can start.
            if (isStarting(useChatStore.getState().systemStatus)) {
                // Read while a restarted backend booted, the status may be stale.
                const status = await api.getSystemStatus();
                useChatStore.getState().setSystemStatus(status);
                if (isStarting(status)) {
                    notify(t('chatError.resendStarting'));
                    return false;
                }
            }
            if (!modelKnownReady('chat') && !(await ensureModel('chat'))) return false;
            // The shown id may still be the temporary one; delete the server's own row.
            const data = await api.getMessages(sessionId);
            if (isStale()) return false;
            const server = fromApiMessages(data.messages);
            const row = persistedQuestion(server, useChatStore.getState().messages, message);
            if (row && failedReply && !onlyFailureAfter(server, row, failedReply)) {
                // Answered since it failed here: show that answer instead of deleting it.
                log.chat.info(`Message ${message.id} was answered since it failed; not resending`);
                setMessages(server);
                lastMsgCountRef.current = server.length;
                return false;
            }
            if (row) await api.deleteMessagesFrom(sessionId, row.id);
            if (isStale()) {
                // The question is gone from the server; the background dispatcher asks it again,
                // unless the chat itself was deleted meanwhile.
                const { sessions, pendingDeleteIds } = useChatStore.getState();
                if (sessions.some((s) => s.id === sessionId) && !pendingDeleteIds.includes(sessionId)) {
                    setQueue((q) => messageQueue.enqueue(q, text, 'queued'));
                }
                return true;
            }
        } catch (err) {
            log.chat.error(`Could not resend message ${message.id} in session=${sessionId}`, err);
            const cause = err instanceof Error ? classifyChatError(err) : null;
            const lost = cause?.kind === 'connection' || cause?.kind === 'timeout';
            notify(lost ? cause.text : t('chatError.resendStale'));
            return false;
        } finally {
            resendingRef.current = false;
        }

        removeMessagesFrom(message.id);
        // Its old turn is gone from the server: if it can't go now, it waits in line, never vanishes.
        let sent = false;
        try {
            sent = await sendMessageRef.current(text);
        } catch (err) {
            log.chat.error(`Could not resend message ${message.id} in session=${sessionId}; queued it`, err);
        }
        if (!sent) setQueue((q) => messageQueue.enqueue(q, text, 'queued'));
        return true;
    }, [sessionId, isStreaming, removeMessagesFrom, setMessages, setQueue, isStale, addNotification, t]);

    // This view sends its chat's queue while open; the background dispatcher does it otherwise.
    useEffect(() => claimView(sessionId), [sessionId]);
    const idleHere = useChatStore(() => idleByPoll(sessionId));
    const sendQueued = useCallback<QueuedSend>((_sid, text) => new Promise<SendOutcome>((resolve) => {
        sendMessageRef.current(text, { queued: true, onQueuedOutcome: resolve });
    }), []);

    // Between turns: send the next queued message, or after a Stop hand them all back to the composer.
    const busy = isStreaming || comparing;
    useEffect(() => {
        if (busy) return;
        if (userStoppedRef.current) {
            userStoppedRef.current = false;
            const texts = queue.map((m) => m.text);
            if (texts.length) {
                setQueue(() => []);
                setInput((prev) => [prev.trim(), ...texts].filter(Boolean).join('\n\n'));
            }
            return;
        }
        // The opening message goes first.
        if (queue.length === 0 || queueHold || isLoadingMessages || pendingPrompt || openingRef.current || retiredAgent || initializing) return;
        // Only once this chat's turn is over: seen ending here, or reported idle by a poll since.
        if (isStale() || (!observedEndRef.current && !idleHere)) return;
        void dispatchNext(sessionId, sendQueued, () => mountedRef.current && !isStale() && !useChatStore.getState().isStreaming && !comparingRef.current);
    }, [busy, queue, queueHold, idleHere, isLoadingMessages, pendingPrompt, retiredAgent, initializing, sessionId, sendQueued, isStale]);

    // The meter shows what the next message will carry, so re-measure after each reply.
    const wasBusyRef = useRef(false);
    useEffect(() => {
        if (wasBusyRef.current && !busy) refreshContext();
        wasBusyRef.current = busy;
    }, [busy, refreshContext]);

    // The user chose to fold a queued message into the running reply.
    const handleSteer = useCallback((id: number) => {
        const item = queue.find((m) => m.id === id && m.state === 'queued');
        if (!item || !isStreaming) return;
        setQueue((q) => messageQueue.promote(q, id, 'queued', 'sending'));
        api.sendFollowUp(sessionId, item.text)
            .then(() => setQueue((q) => messageQueue.promote(q, id, 'sending', 'delivered')))
            .catch((err) => {
                log.chat.info(`Follow-up not taken by the running reply; it stays queued: ${err instanceof Error ? err.message : err}`);
                setQueue((q) => messageQueue.steerRefused(q, id));
            });
    }, [queue, isStreaming, sessionId, setQueue]);

    // Attachments can't ride on an answer, so a message with files still queues.
    const answerFromComposer = useCallback(async () => {
        const question = answeringId ? useChatStore.getState().liveQuestions[answeringId] : undefined;
        const text = input.trim();
        if (!answeringId || !question || !text || attach.hasUploaded) {
            void sendMessage();
            return;
        }
        const answer = resolveTypedAnswer(text, question);
        if (!answer) {
            setActionError(t('composer.pickAnOption'));
            return;
        }
        setActionError(null);
        setInput('');
        try {
            await answerQuestion(sessionId, answeringId, answer.value, answer.shown);
        } catch (err) {
            // Back in the box, so nothing typed is lost; an expired question says so in its card.
            setInput(text);
            if (!(err instanceof QuestionGoneError)) {
                setActionError(t('composer.answerFailed', { error: requestErrorParam(err) }));
            }
        }
    }, [answeringId, input, attach, sendMessage, setInput, sessionId, t]);

    // Retry the last question after a connector was connected (#2119). Resends
    // the last user message, which also drops the stale connector-error reply.
    const handleRetryLast = useCallback(() => {
        for (let i = messages.length - 1; i >= 0; i--) {
            if (messages[i].role === 'user') {
                handleResendMessage(messages[i]);
                return;
            }
        }
    }, [messages, handleResendMessage]);

    // Try again on a failed reply: resend the question it answers.
    const handleTryAgain = useCallback((reply: Message) => {
        const at = messages.indexOf(reply);
        for (let i = at - 1; i >= 0; i--) {
            if (messages[i].role === 'user') {
                handleResendMessage(messages[i], reply);
                return;
            }
        }
    }, [messages, handleResendMessage]);

    // Try again on a finished answer: ask its question afresh, with the same model.
    const handleRegenerate = useCallback((reply: Message) => {
        const at = messages.indexOf(reply);
        for (let i = at - 1; i >= 0; i--) {
            if (messages[i].role === 'user') {
                void handleResendMessage(messages[i]);
                return;
            }
        }
    }, [messages, handleResendMessage]);

    const [editingId, setEditingId] = useState<number | null>(null);
    // The edited text outlives the editor: a running turn hides it, and a resend that didn't go out reopens it.
    const editDraftRef = useRef<string | null>(null);
    const startEdit = useCallback((id: number) => {
        editDraftRef.current = null;
        setEditingId(id);
    }, []);
    const handleEditLast = useCallback(() => {
        const last = lastSentMessage(messages);
        if (last) startEdit(last.id);
    }, [messages, startEdit]);
    const handleEditCancel = useCallback(() => {
        editDraftRef.current = null;
        setEditingId(null);
        inputRef.current?.focus();
    }, []);
    const handleEdit = useCallback(async (message: Message, text: string) => {
        editDraftRef.current = text;
        // The editor stays open until the edit has gone out or waits in the queue.
        if (await handleResendMessage(message, undefined, text)) {
            editDraftRef.current = null;
            setEditingId((id) => (id === message.id ? null : id));
        }
    }, [handleResendMessage]);

    // Continue a stopped reply: the partial answer stays in the chat history the model reads.
    const handleContinue = useCallback(() => {
        void sendMessageRef.current(t('msg.continuePrompt'));
    }, [t]);

    // Title editing
    const startEditTitle = () => {
        setTitleDraft(session?.title || '');
        setEditingTitle(true);
    };

    const saveTitle = async () => {
        const title = titleDraft.trim().slice(0, MAX_SESSION_TITLE_CHARS).trim();
        // Untouched draft: never cut an old over-long title the user didn't edit.
        if (title && titleDraft !== session?.title && title !== session?.title) {
            setActionError(null);
            try {
                // Explicit rename — pin against the auto-retitler (#2165).
                await api.updateSession(sessionId, { title, title_is_custom: true });
                updateSessionInList(sessionId, { title });
            } catch (err) {
                log.chat.error(`Could not rename chat ${sessionId}`, err);
                setActionError(t('chat.renameFailed', { error: requestErrorParam(err) }));
            }
        }
        setEditingTitle(false);
    };

    // Toggle private (incognito) mode
    const handleTogglePrivate = useCallback(async () => {
        if (!sessionId) return;
        setActionError(null);
        try {
            const updated = await api.toggleSessionPrivacy(sessionId);
            updateSessionInList(sessionId, { private: updated.private });
        } catch (err) {
            log.ui.warn('Failed to toggle private mode', err);
            setActionError(t('chat.privateFailed', { error: requestErrorParam(err) }));
        }
    }, [sessionId, updateSessionInList, t]);

    // Drag & drop: see addDroppedFiles.
    const handleDrop = useCallback(async (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragOver(false);
        if (e.dataTransfer.files.length === 0) return;

        const dropped = Array.from(e.dataTransfer.files);
        // A recording is transcribed by the agent, not indexed as a document.
        const recordings = dropped.filter((file) => isMediaFile(file.name));
        if (recordings.length > 0) attach.addFiles(recordings);
        const documents = dropped.filter((file) => !isMediaFile(file.name));
        if (documents.length === 0) return;

        if (!(await ensureModel('embedding'))) return;
        setActionError(null);
        const failures = await addDroppedFiles(sessionId, documents);
        if (failures.length > 0) {
            setActionError(failures.map(({ name, stage, error }) => t(
                stage === 'attach' ? 'chat.dropAttachFailed' : 'chat.dropUploadFailed',
                { name, error: requestErrorParam(error) },
            )).join(' '));
        }
    }, [sessionId, attach, t]);

    const handleDragOver = (e: React.DragEvent) => { e.preventDefault(); e.stopPropagation(); setIsDragOver(true); };
    const handleDragLeave = (e: React.DragEvent) => { e.preventDefault(); e.stopPropagation(); setIsDragOver(false); };

    // Stale connector-reply cue (#2119): when the newest reply was a connector
    // auth-required error, offer to re-run once the account is connected.
    const lastMessage = messages.length > 0 ? messages[messages.length - 1] : undefined;
    const lastConnectProviders = useMemo(() => connectCardProviders(lastMessage), [lastMessage]);
    const lastIsConnectorError = !isStreaming
        && !isLoadingMessages
        && lastMessage?.role === 'assistant'
        // A card speaks for itself: the reply's wording is read only without one.
        && (hasConnectCard(lastMessage) ? lastConnectProviders.length > 0 : isAuthRequiredMessage(lastMessage.content));

    // Pre-compute per-message latency: time from preceding user message to each
    // assistant message. O(N) single pass, avoids repeated backward scans in render.
    const latencyByMsgId = useMemo(() => {
        const map = new Map<number, number>();
        let lastUserTime: number | undefined;
        for (const msg of messages) {
            if (msg.role === 'user' && msg.created_at) {
                lastUserTime = new Date(msg.created_at).getTime();
            } else if (msg.role === 'assistant' && msg.created_at && lastUserTime !== undefined) {
                const assistTime = new Date(msg.created_at).getTime();
                if (assistTime > lastUserTime) map.set(msg.id, assistTime - lastUserTime);
                lastUserTime = undefined;
            }
        }
        return map;
    }, [messages]);

    const composerBlocked = retiredAgent
        ? `This chat used the retired "${retiredAgent}" agent and is read-only. Start a new chat.`
        : isStarting(systemStatus)
            ? STARTING_REASON
            : null;

    return (
        <main
            className={`task-view ${isDragOver ? 'drag-active' : ''}`}
            style={{ '--session-panel-w': `${panelWidth}px` } as CSSProperties}
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            aria-label={session?.title || 'Chat'}
        >
            <header className="task-header">
                <div className="task-header-left">
                    {editingTitle ? (
                        <input
                            className="title-edit"
                            value={titleDraft}
                            maxLength={MAX_SESSION_TITLE_CHARS}
                            onChange={(e) => setTitleDraft(e.target.value)}
                            onBlur={saveTitle}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter') saveTitle();
                                if (e.key === 'Escape') { e.preventDefault(); setEditingTitle(false); }
                            }}
                            {...CLAIMS_ESCAPE}
                            autoFocus
                            aria-label="Chat title"
                            dir="auto"
                        />
                    ) : (
                        <button type="button" className="task-title" onClick={startEditTitle} title={t('row.rename')}>
                            <span>{isPlaceholderTitle(session?.title) ? t('sidebar.newChat') : session?.title}</span>
                            <Edit3 size={12} aria-hidden="true" className="task-title-edit" />
                        </button>
                    )}
                </div>
                <div className="task-header-right">
                    <button
                        ref={resourcesToggleRef}
                        className="btn-icon-sm task-header-resources"
                        onClick={() => setShowResources(!showResources)}
                        aria-pressed={showResources}
                        aria-label={t('resources.toggle')}
                        title={t(showResources ? 'resources.hide' : 'resources.show')}
                    >
                        <Activity size={15} aria-hidden="true" />
                    </button>
                    <PrivateChatButton isPrivate={!!session?.private} onToggle={handleTogglePrivate} />
                    <button className="btn-icon-sm" onClick={() => setShowFileBrowser(true)} title="Browse files on this PC" aria-label="Browse files on this PC">
                        <FolderSearch size={15} />
                    </button>
                    <button className="btn-icon-sm" onClick={exportChat} title="Export as Markdown" aria-label="Export chat">
                        <Download size={15} />
                    </button>
                    <SessionPanelToggle messages={messages} busy={isStreaming} />
                </div>
            </header>

            {showResources && <ResourceMonitor onClose={closeResources} />}

            <SessionDocsBar sessionId={sessionId} onDetachFailed={handleDetachFailed} />

            <div className="messages-scroll" ref={messagesScrollRef} {...followListProps}>
                <div className="messages-column">
                    {isLoadingMessages && !openingShown && (
                        <div className="skeleton-messages" aria-label="Loading messages">
                            {[0, 1, 2].map((i) => (
                                <div key={i} className="skeleton-msg">
                                    <div className="skeleton-lines">
                                        <div className="skeleton-line" />
                                        <div className="skeleton-line" />
                                        {i !== 2 && <div className="skeleton-line" />}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    {rows.map((msg, idx) => {
                        // The streaming bubble shows the just-finished answer while it fades out.
                        const isStreamEndingMsg = streamEnding
                            && msg.role === 'assistant'
                            && idx === messages.length - 1;
                        if (isStreamEndingMsg) return null;
                        const notice = msg.role === 'assistant'
                            ? msg.historyNotice ?? historyNotices.get(msg.id)
                            : undefined;
                        const cutOff = msg.role === 'assistant' && turnEnding(msg.content, msg.stopState) === 'interrupted';
                        const stopped = msg.role === 'assistant' && msg.stopState === 'stopped' && !msg.stopError;
                        const canTryAgain = (msg.failed === true || cutOff || stopped) && !busy && !retiredAgent && !streamEnding
                            && idx === messages.length - 1;
                        const canRetry = !canTryAgain && !busy && !comparison && !retiredAgent && !streamEnding
                            && msg.role === 'assistant' && idx === messages.length - 1
                            && !msg.stopState && !msg.failed && !isErrorContent(msg.content);
                        const latency = msg.stats?.seconds != null ? msg.stats.seconds * 1000 : latencyByMsgId.get(msg.id);
                        const unsent = isUnsent(msg);
                        return (
                            <div
                                key={openingKey(msg) ?? messageKeysRef.current.get(msg.id) ?? msg.id}
                                className={deletingMsgId === msg.id ? 'msg-deleting' : settledIdsRef.current.has(messageKeysRef.current.get(msg.id) ?? msg.id) ? 'msg-settled' : undefined}
                                data-message-id={msg.id}
                                data-role={msg.role}
                            >
                                {notice && <HistoryNotice text={notice} />}
                                {msg.libraryNotices && msg.libraryNotices.length > 0 && (
                                    <LibraryNotices notices={msg.libraryNotices} />
                                )}
                                <MessageBubble
                                    message={msg}
                                    agentSteps={msg.role === 'assistant' ? msg.agentSteps : undefined}
                                    onDelete={!busy && !unsent ? handleDeleteMessage : undefined}
                                    onResend={!busy && !unsent && msg.role === 'user' && !retiredAgent ? handleResendMessage : undefined}
                                    onEditStart={!busy && !unsent && msg.role === 'user' && !retiredAgent ? () => startEdit(msg.id) : undefined}
                                    editing={editingId === msg.id && !busy}
                                    editDraft={editingId === msg.id ? editDraftRef.current : null}
                                    onEditDraft={(text: string) => { editDraftRef.current = text; }}
                                    onEdit={handleEdit}
                                    onEditCancel={handleEditCancel}
                                    onRegenerate={canRetry && !unsent ? () => handleRegenerate(msg) : undefined}
                                    extraActions={canRetry ? (
                                        <RetryWithMenu
                                            answeredBy={msg.stats?.model ?? session?.effective_model}
                                            onPick={(model) => handleRetryWith(msg, model)}
                                        />
                                    ) : undefined}
                                    onTryAgain={canTryAgain ? () => handleTryAgain(msg) : undefined}
                                    onContinue={canTryAgain && stopped ? handleContinue : undefined}
                                    latest={msg.role === 'assistant' && idx === messages.length - 1}
                                    latencyMs={latency}
                                />
                                {msg.failure === 'model_server_down' && idx === messages.length - 1 && !busy && (
                                    <ModelServerDownNotice />
                                )}
                                {comparison?.messageId === msg.id && (
                                    <AnswerComparison
                                        original={msg}
                                        originalModel={msg.stats?.model ?? session?.effective_model}
                                        originalLatencyMs={latency}
                                        state={comparison.state}
                                        keeping={comparison.keeping}
                                        onKeep={(which) => { void handleKeepAnswer(which); }}
                                        onClose={handleCloseComparison}
                                    />
                                )}
                            </div>
                        );
                    })}

                    {(isStreaming || streamEnding) && liveHistoryNotice && <HistoryNotice text={liveHistoryNotice} />}
                    {isStreaming && <RecordingProgress />}
                    {(isStreaming || streamEnding || !!openingShown) && (
                        <div className={`streaming-bubble ${streamEnding ? 'stream-ending' : 'stream-active'}`}>
                            <MessageBubble
                                message={{
                                    id: -1,
                                    session_id: sessionId,
                                    role: 'assistant',
                                    content: (isStreaming ? streamingContent : lastStreamContentRef.current) || '',
                                    created_at: '',
                                    rag_sources: null,
                                }}
                                isStreaming={isStreaming || !!openingShown}
                                agentSteps={isStreaming ? agentSteps : openingShown ? NO_STEPS : lastAgentStepsRef.current}
                                agentStepsActive={isStreaming && agentSteps.some(s => s.active)}
                                cards={isStreaming ? cards : lastCardsRef.current}
                                liveStatus={isStreaming ? liveStatus : null}
                                toolStatus={isStreaming ? toolStatus : null}
                                awaitingUser={isStreaming && (awaitingUser || hasLiveQuestion || hasOpenPrompt)}
                            />
                        </div>
                    )}
                    <div ref={messagesEndRef} />
                </div>
            </div>

            <SessionPanel
                sessionId={sessionId}
                messages={messages}
                busy={busy}
                onUsage={showContextUsage}
                onJumpToMessage={jumpToMessage}
            />

            {showScrollBtn && (
                <button className="scroll-bottom-btn" onClick={scrollToBottom} title="Scroll to bottom" aria-label="Scroll to bottom">
                    <ArrowDown size={16} />
                </button>
            )}

            {isDragOver && (
                <div className="drag-overlay">
                    <Upload size={32} strokeWidth={1.5} aria-hidden="true" />
                    <span>Drop files to index them for this chat</span>
                </div>
            )}

            {policyToast && (
                <div className="toast policy-alert-toast" role="alert">
                    <span>Blocked: {policyToast.tool} is restricted by policy.</span>
                    {policyToast.receiptId ? (
                        <button
                            type="button"
                            className="policy-alert-toast-link"
                            onClick={() => {
                                const receiptId = policyToast.receiptId;
                                if (!receiptId) return;
                                setNotificationTypeFilter('policy_alert');
                                setNotificationPanelVisible(true);
                                setPendingReceiptScroll(receiptId);
                            }}
                        >
                            View receipt
                        </button>
                    ) : (
                        <span className="policy-alert-toast-missing">Receipt unavailable</span>
                    )}
                </div>
            )}

            <div className="input-area">
                <div className="input-column">
                    <ExportErrorBar message={exportError} onDismiss={clearExportError} dismissLabel={t('chat.dismissExportError')} />
                    <ExportErrorBar message={actionError} onDismiss={() => setActionError(null)} dismissLabel={t('chat.dismissError')} />
                    {lastIsConnectorError && lastMessage && (
                        <ConnectorRetryBanner content={lastMessage.content} providers={lastConnectProviders} onRetry={handleRetryLast} />
                    )}
                    <PermissionPrompt sessionId={sessionId} />
                    <ContextWarning usage={contextUsage} />
                    <OfflineNotice sessionId={sessionId} />
                    {pendingPrompt && initializing && (
                        <ul className="queued-messages" aria-label={t('composer.heldLabel')} aria-live="polite">
                            <li className="queued-message is-queued">
                                <Clock size={12} aria-hidden="true" />
                                <span className="queued-message-text" title={pendingPrompt.text}>{pendingPrompt.text}</span>
                                <span className="queued-message-state">{t('composer.heldUntilStarted')}</span>
                            </li>
                        </ul>
                    )}
                    <QueuedMessages
                        items={queue}
                        onRemove={(id) => setQueue((q) => messageQueue.remove(q, id))}
                        onRetry={(id) => setQueue((q) => messageQueue.promote(q, id, 'failed', 'queued'))}
                        onSteer={isStreaming && !stopping && !followupsUnavailable ? handleSteer : undefined}
                        replying={isStreaming}
                        busyRetry={!!queueHold}
                    />
                    <Composer
                        value={input}
                        onChange={setInput}
                        onSubmit={answeringId ? answerFromComposer : () => sendMessage()}
                        answering={!!answeringId}
                        onStop={handleStop}
                        streaming={isStreaming}
                        stopping={stopping}
                        queueWhileStreaming
                        disabledReason={composerBlocked}
                        attachments={attach}
                        inputRef={inputRef}
                        onEditLast={!busy && !retiredAgent ? handleEditLast : undefined}
                        placeholder={answeringId
                            ? t('composer.answerQuestion')
                            : isStreaming
                            ? t('composer.queueNext')
                            : messages.length === 0 ? t('composer.placeholder') : t('composer.reply')}
                        leftControls={<PermissionModeChip sessionId={sessionId} disabled={!!retiredAgent} />}
                        rightControls={(
                            <>
                                <ContextMeter
                                    usage={contextUsage}
                                    unavailable={contextUnavailable}
                                    sessionId={sessionId}
                                    busy={busy}
                                    onUsage={showContextUsage}
                                    onDetails={() => revealPanelSection('context')}
                                />
                                <SpendMeter sessionId={sessionId} busy={busy} />
                                <SessionFolderChip sessionId={sessionId} onOpen={() => revealPanelSection('environment')} />
                                <ModelChip />
                            </>
                        )}
                    />
                </div>
            </div>
        </main>
    );
}
