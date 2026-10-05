// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useCallback, useState, useMemo } from 'react';
import { Edit3, Download, Upload, ArrowDown, FileText, FolderSearch, CheckCircle2, X, EyeOff } from 'lucide-react';
import { MessageBubble } from './MessageBubble';
import { useChatStore } from '../stores/chatStore';
import { useNotificationStore, PATH_ACCESS_TOOL, pathAccessQuestion, pathAccessTitle, isPathAccessFollowUp } from '../stores/notificationStore';
import type { GaiaNotification } from '../types/agent';
import * as api from '../services/api';
import { log } from '../utils/logger';
import { bugReportUrl } from './UnsupportedFeature';
import { isAuthRequiredMessage } from './email/EmailConnectCta';
import { ConnectorRetryBanner } from './ConnectorRetryBanner';
import type { Message, StreamEvent, AgentStep, RenderCardData } from '../types';

import './ChatView.css';
import { Composer } from './Composer';
import { ModelChip } from './ModelChip';
import { PermissionModeChip } from './PermissionModeChip';
import { PermissionPrompt } from './PermissionPrompt';
import { useAttachments } from '../hooks/useAttachments';
import { FLAGSHIP_AGENT_ID } from '../utils/newTask';

/**
 * Safety-net regex to strip raw tool-call JSON from streaming content.
 *
 * Primary filtering happens server-side in sse_handler.py (see _TOOL_CALL_JSON_RE).
 * This frontend regex is a secondary safety net in case any tool-call JSON leaks
 * through the SSE stream. The canonical pattern is defined in sse_handler.py;
 * keep this in sync if the server-side pattern changes.
 */
const TOOL_CALL_JSON_SAFETY_RE = /\s*\{\s*"?(?:tool|thought|goal)"?\s*:\s*"[^"]*"[^}]*(?:"?tool_args"?\s*:\s*\{[^}]*\})?\s*\}/g;

/** How long Stop waits for the server's closing `done` before the client closes the turn itself. */
export const STOP_GRACE_MS = 15_000;

/**
 * Strip the LLM JSON envelope from streamed/accumulated content.
 * Handles responses like {"thought":"...", "goal":"...", "answer":"<content>"}
 * where the entire response is wrapped in a structured JSON object.
 * During streaming, progressively reveals the answer content as it arrives.
 */
function stripStreamingEnvelope(text: string): string {
    const trimmed = text.trim();
    if (!trimmed.startsWith('{')) return text;
    if (!trimmed.includes('"thought"') && !trimmed.includes('"answer"') && !trimmed.includes('"goal"')) return text;

    // Try full JSON parse first (works when message is complete and well-formed)
    try {
        const parsed = JSON.parse(trimmed);
        if (parsed.answer !== undefined) return String(parsed.answer);
    } catch {
        // Incomplete or malformed JSON (literal newlines in values) — fall through
    }

    // Find "answer" field and extract content progressively
    const answerIdx = trimmed.indexOf('"answer"');
    if (answerIdx === -1) return ''; // Still in thought/goal — suppress during streaming

    const colonIdx = trimmed.indexOf(':', answerIdx + 8);
    if (colonIdx === -1) return '';
    let start = colonIdx + 1;
    while (start < trimmed.length && /\s/.test(trimmed[start])) start++;
    if (start >= trimmed.length || trimmed[start] !== '"') return '';

    let content = trimmed.slice(start + 1);
    // Strip closing "} if the JSON envelope is complete
    if (content.endsWith('"}')) content = content.slice(0, -2);
    else if (content.endsWith('"')) content = content.slice(0, -1);

    return content.replace(/\\"/g, '"').replace(/\\n/g, '\n').replace(/\\\\/g, '\\');
}

/** Map an SSE agent event to an AgentStep for the UI. */
function agentEventToStep(event: StreamEvent, stepIdRef: React.MutableRefObject<number>): AgentStep | null {
    const id = ++stepIdRef.current;
    const ts = Date.now();

    switch (event.type) {
        case 'thinking':
            return {
                id, type: 'thinking', label: 'Thinking',
                detail: event.content, active: true, timestamp: ts,
            };
        case 'tool_start':
            return {
                id, type: 'tool',
                // Label is determined by AgentActivity based on tool name
                label: 'Using tool',
                tool: event.tool,
                detail: event.detail,
                active: true, timestamp: ts,
                mcpServer: event.mcp_server,
            };
        case 'plan':
            return {
                id, type: 'plan', label: 'Created plan',
                planSteps: event.steps, active: false,
                success: true, timestamp: ts,
            };
        case 'step':
            return {
                id, type: 'status',
                label: `Step ${event.step}${event.total ? ` of ${event.total}` : ''}`,
                active: true, timestamp: ts,
            };
        case 'status':
            if (event.message?.startsWith('Agent: ')) return null;
            return {
                id, type: 'status',
                label: event.message || event.status || 'Working',
                active: event.status === 'working',
                timestamp: ts,
            };
        case 'agent_error':
            return {
                id, type: 'error', label: 'Error',
                detail: event.content, success: false,
                active: false, timestamp: ts,
            };
        case 'policy_alert': {
            const toolName = event.tool || 'unknown tool';
            const reason =
                event.reason ||
                event.message ||
                event.content ||
                'Tool execution was blocked by governance policy.';
            return {
                id,
                type: 'policy_alert',
                label: `Policy blocked ${toolName}`,
                detail: reason,
                tool: toolName,
                decision: event.decision || 'BLOCK',
                reason,
                ruleIds: event.rule_ids ?? [],
                policyVersion: event.policy_version,
                receiptId: event.receipt_id,
                success: false,
                active: false,
                timestamp: ts,
            };
        }
        default:
            return null;
    }
}

function policyReceiptAnchor(receiptId: string): string {
    return `policy-receipt-${encodeURIComponent(receiptId)}`;
}

interface ChatViewProps {
    sessionId: string;
}

export function ChatView({ sessionId }: ChatViewProps) {
    const {
        sessions, messages, setMessages, addMessage, removeMessage, removeMessagesFrom, updateSessionInList,
        isStreaming, streamingContent, setStreaming, setStreamContent, clearStreamContent,
        agentSteps, addAgentStep, updateLastAgentStep, appendThinkingContent, updateLastToolStep, clearAgentSteps,
        cards, appendCard, clearCards,
        documents, setDocuments, setShowDocLibrary, setShowFileBrowser, isLoadingMessages, setLoadingMessages,
        systemStatus,
    } = useChatStore();

    const addNotification = useNotificationStore((s) => s.addNotification);
    const setNotificationPanelVisible = useNotificationStore((s) => s.setShowPanel);
    const setNotificationTypeFilter = useNotificationStore((s) => s.setTypeFilter);
    const pendingPrompt = useChatStore((s) => s.pendingPrompt);
    const liveStatus = useChatStore((s) => s.liveStatus);

    const session = sessions.find((s) => s.id === sessionId);
    const sessionDocIds = new Set(session?.document_ids ?? []);
    const sessionDocs = documents.filter(d => sessionDocIds.has(d.id));
    const [input, setInput] = useState('');
    const [editingTitle, setEditingTitle] = useState(false);
    const [titleDraft, setTitleDraft] = useState('');
    const [isDragOver, setIsDragOver] = useState(false);
    const [showScrollBtn, setShowScrollBtn] = useState(false);
    // Store agent steps snapshot for completed messages
    const attach = useAttachments();
    const [docsExpanded, setDocsExpanded] = useState(false);
    const [deletingMsgId, setDeletingMsgId] = useState<number | null>(null);
    const [policyToast, setPolicyToast] = useState<{ tool: string; receiptId?: string } | null>(null);
    // A chat made by a retired agent (chat/doc/file) can be read but not continued.
    const retiredAgent = session?.agent_type && session.agent_type !== FLAGSHIP_AGENT_ID ? session.agent_type : null;

    // Smooth streaming exit — snapshot last content so fade-out shows real text
    const [streamEnding, setStreamEnding] = useState(false);
    const lastStreamContentRef = useRef('');
    const lastAgentStepsRef = useRef<AgentStep[]>([]);
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
    // Stop keeps the stream open until the server's closing `done` arrives.
    const [stopping, setStopping] = useState(false);
    const stopRequestedRef = useRef(false);
    const stopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const closeStoppedTurnLocallyRef = useRef<(() => void) | null>(null);
    const stepIdRef = useRef(0);
    const toolOccurredRef = useRef(false);
    const sendMessageRef = useRef<(text?: string, options?: { attach?: boolean }) => void>(() => {});

    // ── Streaming chunk buffer ──────────────────────────────────────
    // Buffer SSE chunks in a ref and flush to the store via rAF.
    // This limits React re-renders to ~60fps instead of once per chunk
    // (which can be hundreds/sec), dramatically reducing DOM mutations
    // and eliminating extension-triggered "runtime.lastError" floods.
    const streamBufferRef = useRef('');
    const streamRafRef = useRef<number | null>(null);
    const policyToastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const scrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    /** Timestamp of the last auto-scroll (used for throttling). */
    const lastScrollRef = useRef(0);
    /** True when the user is at (or near) the bottom of the messages list.
     *  Auto-scroll only fires when this is true, so scrolling up to read
     *  earlier messages won't be interrupted by new streaming content. */
    const isNearBottomRef = useRef(true);

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
    const msgPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const lastMsgCountRef = useRef<number>(0);

    useEffect(() => {
        log.chat.info(`ChatView mounted for session=${sessionId}, loading messages...`);
        const t = log.chat.time();
        setLoadingMessages(true);
        let cancelled = false;
        let loading = false;

        const loadMessages = async (isInitial = false) => {
            if (cancelled || loading) return;
            loading = true;
            try {
                if (!isInitial) {
                    if (useChatStore.getState().isStreaming) return;
                    const total = await api.getMessageCount(sessionId);
                    if (cancelled || total === lastMsgCountRef.current || useChatStore.getState().isStreaming) return;
                }
                const data = await api.getMessages(sessionId);
                if (cancelled) return;
                const msgs = (data.messages || []).map((m: any) => ({
                    ...m,
                    // Map snake_case agent_steps from API to camelCase agentSteps
                    agentSteps: m.agentSteps || m.agent_steps || undefined,
                    // Map inference_stats from API to stats field
                    stats: m.stats || m.inference_stats || undefined,
                }));
                if (isInitial) {
                    setMessages(msgs);
                    lastMsgCountRef.current = msgs.length;
                    log.chat.timed(`Loaded ${msgs.length} message(s) for session=${sessionId}`, t);
                } else if (msgs.length !== lastMsgCountRef.current && !useChatStore.getState().isStreaming) {
                    // New messages from external source (MCP, API) — refresh
                    log.chat.info(`Messages changed externally: ${lastMsgCountRef.current} -> ${msgs.length}`);
                    setMessages(msgs);
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

        // Poll every 3s for messages added by external tools (MCP API, etc.)
        msgPollRef.current = setInterval(() => loadMessages(false), 3_000);
        return () => {
            cancelled = true;
            if (msgPollRef.current) clearInterval(msgPollRef.current);
        };
    }, [sessionId, setMessages, setLoadingMessages]);

    // Load indexed documents on mount (so context bar is always up to date)
    useEffect(() => {
        api.listDocuments()
            .then((data) => setDocuments(data.documents || []))
            .catch(() => {});
    }, [setDocuments]);

    // Send a prompt queued before this view mounted (a new chat's first
    // message, or "Ask GAIA" from the file browser).
    const mountedRef = useRef(true);
    useEffect(() => {
        mountedRef.current = true;
        return () => { mountedRef.current = false; };
    }, []);
    useEffect(() => {
        const { pendingPrompt: pending, isLoadingMessages: loading } = useChatStore.getState();
        // Wait for the history load, or it would replace the message just sent.
        if (!pending || loading) return;
        useChatStore.getState().setPendingPrompt(null);
        // A timer, not rAF: rAF never fires while the window is hidden, and
        // clearing the prompt re-runs this effect, so no cleanup may cancel it.
        setTimeout(() => {
            if (mountedRef.current) sendMessageRef.current(pending);
        }, 0);
    }, [pendingPrompt, isLoadingMessages]);

    // Auto-scroll (throttled) — scrolls at most once per 100ms while
    // streaming, and also schedules a trailing scroll so the final chunk
    // is never missed.  Only fires when the user hasn't scrolled away.
    useEffect(() => {
        if (!isNearBottomRef.current) return;

        const now = Date.now();
        const elapsed = now - lastScrollRef.current;
        const THROTTLE_MS = 100;

        const doScroll = () => {
            lastScrollRef.current = Date.now();
            messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
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

    // Abort active stream and clean up timers when component unmounts / session changes
    useEffect(() => {
        return () => {
            if (abortRef.current) {
                abortRef.current.abort();
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
            if (policyToastTimerRef.current) {
                clearTimeout(policyToastTimerRef.current);
                policyToastTimerRef.current = null;
            }
            setPolicyToast(null);
            streamBufferRef.current = '';
        };
    }, [sessionId]);

    // Track scroll position — drives both the scroll-to-bottom button and
    // the isNearBottom flag that gates auto-scroll during streaming.
    const handleScroll = useCallback(() => {
        const el = messagesScrollRef.current;
        if (!el) return;
        const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
        isNearBottomRef.current = distFromBottom <= 80;
        setShowScrollBtn(distFromBottom > 200);
    }, []);

    const scrollToBottom = useCallback(() => {
        isNearBottomRef.current = true;
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, []);

    // Stop: ask the server to cancel, but keep reading the stream. The run
    // outlives the connection (#1580) and closes with a `done` carrying the
    // persisted turn, steps included — the user must be able to audit what ran.
    const handleStop = useCallback(() => {
        if (stopRequestedRef.current) return;
        stopRequestedRef.current = true;
        setStopping(true);
        useNotificationStore.getState().dismissSessionPrompts(sessionId);
        log.stream.warn('User stopped generation; waiting for the server to close the turn');
        // A 404 here usually means the run just finished and its `done` is in
        // flight; the grace timer below closes the turn if nothing arrives.
        api.cancelStream(sessionId).catch((err) => {
            log.stream.error('Stop request failed; waiting for the stream to close', err);
        });
        stopTimerRef.current = setTimeout(() => {
            log.stream.error(
                `No closing event ${STOP_GRACE_MS}ms after Stop; closing the turn locally (session=${sessionId})`,
            );
            closeStoppedTurnLocallyRef.current?.();
        }, STOP_GRACE_MS);
    }, [sessionId]);

    // Global keyboard shortcuts: Escape → stop streaming, Ctrl+K → focus sidebar search
    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            // An open permission prompt claims Escape first (it denies).
            if (e.key === 'Escape' && isStreaming && !e.defaultPrevented) {
                e.preventDefault();
                handleStop();
            }
            if (e.key === 'k' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                window.dispatchEvent(new CustomEvent('gaia:focus-search'));
            }
        };
        window.addEventListener('keydown', handler);
        return () => window.removeEventListener('keydown', handler);
    }, [isStreaming, handleStop]);

    // Send message
    const sendMessage = useCallback(async (overrideText?: string, options?: { attach?: boolean }) => {
        // attach=true re-subscribes to a run already in flight server-side
        // (revisiting a backgrounded session, #1580). It reuses the entire
        // stream-event handling below but skips composing/sending a new turn:
        // no optimistic user message, no input/attachment handling, and the
        // controller comes from api.attachToRun instead of api.sendMessageStream.
        const reattach = options?.attach === true;
        const text = (overrideText || input).trim();
        const hasAttachments = attach.hasUploaded;

        // User just sent a message — re-pin scroll to the bottom so the
        // new message and streaming response are visible.
        isNearBottomRef.current = true;

        const isInitializing = systemStatus?.init_state === 'initializing';
        if (!reattach && retiredAgent) {
            log.chat.warn(`Send blocked: chat ${sessionId} belongs to the retired "${retiredAgent}" agent`);
            return;
        }
        if (reattach) {
            // Don't double-attach if a stream is already live in this view.
            if (isStreaming) return;
        } else if ((!text && !hasAttachments) || isStreaming || isInitializing) {
            if (!text && !hasAttachments) log.chat.debug('Send blocked: empty message');
            if (isStreaming) log.chat.debug('Send blocked: already streaming');
            if (isInitializing) log.chat.debug('Send blocked: system initializing');
            return;
        }

        // Build message text with attachment references
        let messageText = text;
        if (!reattach) {
            messageText = attach.compose(text);

            log.chat.info(`Sending message to session=${sessionId}`, { length: messageText.length, preview: messageText.slice(0, 80) });

            setInput('');
            inputRef.current?.focus();

            attach.clear();

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
        } else {
            log.chat.info(`Re-attaching to background run for session=${sessionId}`);
        }

        // Start streaming
        setStreaming(true);
        clearStreamContent();
        clearAgentSteps();
        // Also covers attach-replay dedupe — attachToRun replays all events
        // from the start, so stale cards would double up without this.
        clearCards();
        stepIdRef.current = 0;
        toolOccurredRef.current = false;

        log.stream.info('Starting agent stream...');
        const streamStart = log.stream.time();

        let fullContent = '';
        let doneHandled = false;
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
            endStopWait();
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
            onChunk: (event) => {
                if (isStale()) return; // stop writing after a session switch (#1580)
                const content = event.content || '';
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
                // Ignore events from a stream the user navigated away from so its
                // steps don't leak into the new session's view (#1580).
                if (isStale()) return;
                // Permission request — shown inline above the composer. Calls the
                // user already allowed "always" never get here: the backend skips them.
                if (event.type === 'permission_request') {
                    const toolName = event.tool || '';
                    const { addNotification: addNotif, notifications } = useNotificationStore.getState();
                    const isPathAccess = toolName === PATH_ACCESS_TOOL;
                    const followUp = isPathAccess && isPathAccessFollowUp(notifications, sessionId, event.args);
                    addNotif({
                        id: event.confirm_id ?? `perm-${Date.now()}`,
                        type: 'permission_request',
                        agentId: sessionId,
                        sessionId,
                        agentName: 'GAIA',
                        title: isPathAccess ? pathAccessTitle(event.args, followUp) : `Allow ${toolName}?`,
                        message: isPathAccess
                            ? pathAccessQuestion(event.args, followUp)
                            : `The agent wants to execute: ${toolName}`,
                        timestamp: Date.now(),
                        read: false,
                        dismissed: false,
                        priority: 'high',
                        tool: toolName,
                        toolArgs: event.args as Record<string, unknown> | undefined,
                        confirmId: event.confirm_id,
                        alwaysScope: typeof event.always_scope === 'string' ? event.always_scope : undefined,
                        // No timeout from the backend means it waits; don't invent one.
                        timeoutSeconds: event.timeout_seconds,
                    });
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
                    const toolName = event.tool || 'unknown tool';
                    const reason =
                        event.reason ||
                        event.message ||
                        event.content ||
                        'Tool execution was blocked by governance policy.';
                    const notification: GaiaNotification = {
                        id: event.receipt_id ?? `policy-${Date.now()}-${stepIdRef.current + 1}`,
                        type: 'policy_alert',
                        agentId: sessionId,
                        agentName: 'GAIA',
                        title: `Blocked: ${toolName} is restricted by policy.`,
                        message: reason,
                        timestamp: Date.now(),
                        read: false,
                        dismissed: false,
                        priority: 'critical',
                        tool: toolName,
                        decision: event.decision || 'BLOCK',
                        reason,
                        ruleIds: event.rule_ids ?? [],
                        policyVersion: event.policy_version,
                        receiptId: event.receipt_id,
                    };
                    addNotification(notification);
                    const step = agentEventToStep(event, stepIdRef);
                    if (step) addAgentStep(step);
                    setPolicyToast({ tool: toolName, receiptId: event.receipt_id });
                    if (policyToastTimerRef.current) clearTimeout(policyToastTimerRef.current);
                    policyToastTimerRef.current = setTimeout(() => setPolicyToast(null), 5200);
                    return;
                }

                // Tool completion updates the last TOOL step (not just the last step,
                // since thinking/status events may have been interleaved during execution)
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
                    const updates: Partial<AgentStep> = {
                        result: event.summary || event.title || 'Done',
                        active: false,
                        success: event.success !== false,
                        latencyMs: event.latency_ms,
                    };
                    // Pass through structured command output if available
                    if (event.command_output) {
                        updates.commandOutput = {
                            command: event.command_output.command,
                            stdout: event.command_output.stdout,
                            stderr: event.command_output.stderr,
                            returnCode: event.command_output.return_code,
                            cwd: event.command_output.cwd,
                            durationSeconds: event.command_output.duration_seconds,
                            truncated: event.command_output.truncated,
                        };
                    }
                    // Pass through retrieval chunks if available
                    if (event.result_data?.chunks && event.result_data.chunks.length > 0) {
                        updates.retrievalChunks = event.result_data.chunks.map((c) => ({
                            id: c.id,
                            source: c.source,
                            sourcePath: c.sourcePath,
                            page: c.page,
                            score: c.score,
                            preview: c.preview,
                            content: c.content,
                        }));
                    }
                    // Pass through file list if available
                    if (event.result_data?.type === 'file_list' &&
                        (event.result_data as any).files?.length > 0) {
                        updates.fileList = {
                            files: (event.result_data as any).files,
                            total: (event.result_data as any).total ??
                                (event.result_data as any).files.length,
                        };
                    }
                    updateLastToolStep(updates);
                    return;
                }
                // Tool args update the last TOOL step with detail
                if (event.type === 'tool_args') {
                    updateLastToolStep({
                        detail: event.detail || JSON.stringify(event.args),
                    });
                    return;
                }

                // ── Consolidate thinking events ──────────────────────────
                // Instead of creating a new step for every thought, update
                // the existing thinking step so we get ONE "Thinking" entry
                // that shows the latest thought, not a massive stream.
                // Uses appendThinkingContent() which atomically reads the
                // current detail and appends inside a single set() call,
                // preventing stale-read races that can lose accumulated text.
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

                // ── Consolidate status events ────────────────────────────
                // Working/info status messages are progress indicators.
                // Consolidate consecutive ones into a single entry.
                if (event.type === 'status') {
                    const status = event.status;
                    const msg = (event.message || '').trim();
                    if (event.phase && msg) useChatStore.getState().setLiveStatus(msg);
                    // Skip "Executing <tool>" messages - redundant with tool_start
                    if (msg.toLowerCase().startsWith('executing ')) return;
                    if (status === 'working' || status === 'warning' || status === 'info') {
                        const currentSteps = useChatStore.getState().agentSteps;
                        const lastStep = currentSteps[currentSteps.length - 1];
                        // Consolidate with previous status step (but NOT thinking —
                        // overwriting a thinking step's detail would discard all
                        // accumulated thinking text).
                        if (lastStep && lastStep.type === 'status' && lastStep.active) {
                            updateLastAgentStep({
                                label: msg || 'Working',
                                detail: msg,
                            });
                            return;
                        }
                        // If the last step is thinking, update only the label
                        // so the summary bar shows the status, but preserve the
                        // accumulated thinking detail.
                        if (lastStep && lastStep.type === 'thinking' && lastStep.active) {
                            updateLastAgentStep({ label: msg || 'Thinking' });
                            return;
                        }
                        const step = agentEventToStep(event, stepIdRef);
                        if (step) addAgentStep(step);
                    }
                    return;
                }

                if (event.type === 'step') {
                    return; // Step headers are redundant with actual tool/thinking steps
                }

                const step = agentEventToStep(event, stepIdRef);
                if (step) {
                    addAgentStep(step);
                    if (event.type === 'tool_start') {
                        toolOccurredRef.current = true;
                        // The model's phase ended with the call; the tool card says what runs now.
                        useChatStore.getState().setLiveStatus(null);
                    }
                }
            },
            onDone: (event) => {
                useNotificationStore.getState().dismissSessionPrompts(sessionId);
                if (doneHandled) return;
                doneHandled = true;
                const stopped = stopRequestedRef.current;
                endStopWait();

                // Cancel any pending rAF flush — we have the final content
                if (streamRafRef.current !== null) {
                    cancelAnimationFrame(streamRafRef.current);
                    streamRafRef.current = null;
                }
                streamBufferRef.current = '';

                // A completion for a backgrounded session is persisted
                // server-side, so don't touch the shared store (#1580).
                if (isStale()) return;

                // `done` is the run's last event, so it is no longer running.
                const store = useChatStore.getState();
                store.setRunningSessions(store.runningSessionIds.filter((id) => id !== sessionId));

                const content = event.content || fullContent;
                log.chat.timed(`Agent response complete: ${content.length} chars${stopped ? ' (stopped)' : ''}`, streamStart);

                // Snapshot agent steps and streaming cards (#2108) for the completed message
                const { steps: stepsSnapshot, cards: cardsSnapshot } = snapshotTurn();

                const hasPolicyAlert = stepsSnapshot.some((s) => s.type === 'policy_alert');
                if (content || hasPolicyAlert || stopped) {
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
                        cards: cardsSnapshot.length > 0 ? cardsSnapshot : undefined,
                        stopState: stopped ? 'stopped' : undefined,
                    };
                    addMessage(assistantMsg);
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
                            if (isStale()) return;
                            // The backend doesn't persist cards, so this replace
                            // would drop them — merge from the pre-refetch
                            // in-memory messages by id, else role+content.
                            // #2109 replaces this merge with steps-derived hydration.
                            // stopState isn't persisted either; carry it by id.
                            const prevMsgs = useChatStore.getState().messages;
                            const prevWithCards = prevMsgs.filter((m) => m.cards && m.cards.length > 0);
                            const msgs: Message[] = (data.messages || []).map((m: any) => {
                                const prev = prevWithCards.find(
                                    (p) => p.id === m.id || (p.role === m.role && p.content === m.content),
                                );
                                return {
                                    ...m,
                                    agentSteps: m.agentSteps || m.agent_steps || undefined,
                                    stats: m.stats || m.inference_stats || undefined,
                                    cards: prev?.cards,
                                    stopState: prevMsgs.find((p) => p.id === m.id)?.stopState,
                                };
                            });
                            setMessages(msgs);
                            lastMsgCountRef.current = msgs.length;
                        })
                        .catch((err) => log.chat.error(`Could not refresh messages for session=${sessionId}`, err));
                }, 300);

                // Auto-title on first message
                // Skip client-side auto-title when re-attaching (no user text in
                // hand and the run's lifecycle already titles server-side, #1580).
                if (!reattach && session && session.title === 'New Task') {
                    const autoTitle = text.slice(0, 50) + (text.length > 50 ? '...' : '');
                    // Not a user choice — leave unpinned so the server-side
                    // LLM titler can still replace it (#2165).
                    api.updateSession(sessionId, { title: autoTitle, title_is_custom: false })
                        .then(() => updateSessionInList(sessionId, { title: autoTitle }))
                        .catch((err) => log.chat.error('Auto-title failed', err));
                }
            },
            onError: (err) => {
                useNotificationStore.getState().dismissSessionPrompts(sessionId);
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

                log.chat.error(`Chat error for session=${sessionId}`, err);
                // Provide a user-friendly error message based on the error type
                // Each error includes a GitHub link for reporting issues
                let errorContent: string;
                const msg = err.message || '';
                const issueFooter = '\n\n---\n*Unexpected error?* '
                    + `[Report it on GitHub](${bugReportUrl(msg.slice(0, 80))})`;

                if (msg.includes('Lemonade') || msg.includes('LLM') || msg.includes('Could not get response')) {
                    errorContent =
                        'The model server did not answer. Open Settings → Model to check it, '
                        + 'then send your message again.\n\n'
                        + `Details: ${msg}` + issueFooter;
                } else if (err instanceof TypeError || msg.includes('fetch') || msg.includes('Failed to fetch') || msg.includes('NetworkError')) {
                    errorContent =
                        'Lost the connection to GAIA. Check that GAIA is still running, then send '
                        + 'your message again.' + issueFooter;
                } else if (msg.includes('timed out') || msg.includes('timeout') || msg.includes('Timeout')) {
                    errorContent =
                        'The request timed out. Try a shorter question, or a faster model in '
                        + 'Settings → Model.' + issueFooter;
                } else {
                    errorContent = `Error: ${msg}` + issueFooter;
                }
                const errMsg: Message = {
                    id: Date.now() + 2,
                    session_id: sessionId,
                    role: 'assistant',
                    content: errorContent,
                    created_at: new Date().toISOString(),
                    rag_sources: null,
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

        abortRef.current = controller;
    }, [input, attach, isStreaming, sessionId, session, retiredAgent, addMessage, setMessages, setStreaming, flushStreamBuffer, clearStreamContent, updateSessionInList, addAgentStep, updateLastAgentStep, appendThinkingContent, updateLastToolStep, clearAgentSteps, appendCard, clearCards, addNotification, isStale]);

    // Keep ref in sync so event listeners always call the latest sendMessage
    sendMessageRef.current = sendMessage;

    // Re-attach to an in-flight background run on mount (#1580). When the
    // user revisits a session whose turn is still running server-side, hook
    // back into its live stream so progress resumes in the view instead of
    // sitting static until the run finishes. Once per session mount; the
    // attach path no-ops if a stream is already live here.
    const reattachedRef = useRef(false);
    useEffect(() => {
        reattachedRef.current = false;
    }, [sessionId]);
    useEffect(() => {
        const attemptAttach = () => {
            if (reattachedRef.current) return;
            if (useChatStore.getState().isStreaming) return;
            reattachedRef.current = true;
            log.chat.info(`Resuming live view of background run for session=${sessionId}`);
            sendMessageRef.current(undefined, { attach: true });
        };
        // Fast path: the global poll already knows this session is running.
        if (useChatStore.getState().runningSessionIds.includes(sessionId)) {
            attemptAttach();
            return;
        }
        // Otherwise confirm once with the backend on mount.
        let cancelled = false;
        api.getActiveRuns()
            .then(({ session_ids }) => {
                if (cancelled) return;
                if (session_ids.includes(sessionId)) attemptAttach();
            })
            .catch(() => { /* non-critical — sidebar spinner still signals running */ });
        return () => { cancelled = true; };
    }, [sessionId]);

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
                        setMessages(data.messages || []);
                    })
                    .catch(() => {});
            }
        }, 250);
    }, [sessionId, isStreaming, removeMessage, setMessages, isStale]);

    // Resend a user message: delete it and everything below, then re-send
    const handleResendMessage = useCallback(async (message: Message) => {
        if (isStreaming || message.role !== 'user') return;
        const text = message.content;
        log.chat.info(`Resending message ${message.id} from session=${sessionId}`, { preview: text.slice(0, 80) });

        // Optimistic removal of this message and all below
        removeMessagesFrom(message.id);

        try {
            await api.deleteMessagesFrom(sessionId, message.id);
        } catch (err) {
            log.chat.error(`Failed to delete messages from ${message.id}`, err);
            // Reload messages on error
            api.getMessages(sessionId)
                .then((data) => {
                    if (isStale()) return;
                    setMessages(data.messages || []);
                })
                .catch(() => {});
            return;
        }

        // Re-send the same message text
        sendMessage(text);
    }, [sessionId, isStreaming, removeMessagesFrom, setMessages, sendMessage, isStale]);

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



    // Title editing
    const startEditTitle = () => {
        setTitleDraft(session?.title || '');
        setEditingTitle(true);
    };

    const saveTitle = async () => {
        if (titleDraft.trim() && titleDraft !== session?.title) {
            // Explicit rename — pin against the auto-retitler (#2165).
            await api.updateSession(sessionId, { title: titleDraft.trim(), title_is_custom: true });
            updateSessionInList(sessionId, { title: titleDraft.trim() });
        }
        setEditingTitle(false);
    };

    // Toggle private (incognito) mode
    const handleTogglePrivate = useCallback(async () => {
        if (!sessionId) return;
        try {
            const updated = await api.toggleSessionPrivacy(sessionId);
            updateSessionInList(sessionId, { private: updated.private });
        } catch (err) {
            log.ui.warn('Failed to toggle private mode', err);
        }
    }, [sessionId, updateSessionInList]);

    // Export
    const handleExport = async () => {
        try {
            const data = await api.exportSession(sessionId);
            const blob = new Blob([data.content], { type: 'text/markdown' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            // Sanitize title for use as filename: remove path separators and special chars
            const safeTitle = (session?.title || 'chat').replace(/[/\\:*?"<>|]/g, '_').slice(0, 100);
            a.download = `${safeTitle}.md`;
            a.click();
            URL.revokeObjectURL(url);
        } catch (err) {
            log.chat.error('Export failed', err);
        }
    };

    /** Detach a document from this session via the context bar (does not delete from library). */
    const handleRemoveDocument = useCallback(async (e: React.MouseEvent, docId: string) => {
        e.stopPropagation(); // Don't trigger the context bar click
        const doc = documents.find((d) => d.id === docId);
        log.doc.info(`Detaching document from session: ${doc?.filename || docId}`);
        try {
            await api.detachDocument(sessionId, docId);
            // Read fresh state after await to avoid stale-closure bugs when
            // the user removes multiple documents in quick succession.
            const freshSession = useChatStore.getState().sessions.find(s => s.id === sessionId);
            const remaining = (freshSession?.document_ids ?? []).filter(id => id !== docId);
            updateSessionInList(sessionId, { document_ids: remaining });
            // Auto-collapse when 3 or fewer session docs remain after removal
            if (remaining.length <= 3) setDocsExpanded(false);
            log.doc.info(`Detached document from session: ${doc?.filename || docId}`);
        } catch (err) {
            log.doc.error(`Failed to detach document: ${doc?.filename || docId}`, err);
        }
    }, [documents, sessionId, updateSessionInList]);

    // Drag & drop — uploads the file blob directly rather than relying on
    // File.path (which is browser-undefined and was removed in Electron 32+).
    // Dropped files index quietly and auto-attach to the current session;
    // the Document Library panel is NOT forced open so the drop doesn't
    // interrupt the chat view. Users can still open the library manually
    // to see progress for large files. See issue #728.
    const handleDrop = useCallback(async (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragOver(false);
        if (e.dataTransfer.files.length === 0) return;

        for (const file of Array.from(e.dataTransfer.files)) {
            log.doc.info(`Dropped on chat view: ${file.name}`);
            try {
                const doc = await api.uploadDocumentBlob(file);
                if (sessionId && doc?.id) {
                    try {
                        await api.attachDocument(sessionId, doc.id);
                        const freshSession = useChatStore.getState().sessions.find(s => s.id === sessionId);
                        const existing = freshSession?.document_ids ?? [];
                        if (!existing.includes(doc.id)) {
                            updateSessionInList(sessionId, { document_ids: [...existing, doc.id] });
                        }
                        log.doc.info(`Auto-attached "${file.name}" to session ${sessionId}`);
                    } catch (attachErr) {
                        log.doc.warn(`Could not attach dropped document to session: ${attachErr}`);
                    }
                }
            } catch (err) {
                log.doc.error(`Upload failed: ${file.name}`, err);
            }
        }
    }, [sessionId, updateSessionInList]);

    const handleDragOver = (e: React.DragEvent) => { e.preventDefault(); e.stopPropagation(); setIsDragOver(true); };
    const handleDragLeave = (e: React.DragEvent) => { e.preventDefault(); e.stopPropagation(); setIsDragOver(false); };

    // Stale connector-reply cue (#2119): when the newest reply was a connector
    // auth-required error, offer to re-run once the account is connected.
    const lastMessage = messages.length > 0 ? messages[messages.length - 1] : undefined;
    const lastIsConnectorError = !isStreaming
        && !isLoadingMessages
        && lastMessage?.role === 'assistant'
        && isAuthRequiredMessage(lastMessage.content);

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
        : systemStatus?.init_state === 'initializing'
            ? 'GAIA is starting…'
            : null;

    return (
        <main
            className={`task-view ${isDragOver ? 'drag-active' : ''}`}
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
                            onChange={(e) => setTitleDraft(e.target.value)}
                            onBlur={saveTitle}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter') saveTitle();
                                if (e.key === 'Escape') setEditingTitle(false);
                            }}
                            autoFocus
                            aria-label="Chat title"
                        />
                    ) : (
                        <button type="button" className="task-title" onClick={startEditTitle} title="Rename">
                            <span>{session?.title || 'New chat'}</span>
                            <Edit3 size={12} aria-hidden="true" className="task-title-edit" />
                        </button>
                    )}
                </div>
                <div className="task-header-right">
                    <button
                        className={`btn-icon-sm${session?.private ? ' active' : ''}`}
                        onClick={handleTogglePrivate}
                        title={session?.private ? 'Private: this chat is not saved to memory' : 'Make private (not saved to memory)'}
                        aria-label={session?.private ? 'Private chat on' : 'Make chat private'}
                        aria-pressed={!!session?.private}
                    >
                        <EyeOff size={15} />
                    </button>
                    <button className="btn-icon-sm" onClick={() => setShowFileBrowser(true)} title="Browse files on this PC" aria-label="Browse files on this PC">
                        <FolderSearch size={15} />
                    </button>
                    <button className="btn-icon-sm" onClick={handleExport} title="Export as Markdown" aria-label="Export chat">
                        <Download size={15} />
                    </button>
                </div>
            </header>

            {sessionDocs.length > 0 && (() => {
                const sorted = [...sessionDocs].sort((a, b) => {
                    const aTime = a.last_accessed_at || a.indexed_at || '';
                    const bTime = b.last_accessed_at || b.indexed_at || '';
                    return bTime.localeCompare(aTime);
                });
                const visibleDocs = docsExpanded ? sorted : sorted.slice(0, 3);
                const hiddenCount = sorted.length - 3;
                return (
                    <div
                        className={`doc-context-bar${docsExpanded ? ' doc-context-expanded' : ''}`}
                        aria-label={`${sessionDocs.length} indexed document${sessionDocs.length !== 1 ? 's' : ''}`}
                    >
                        <CheckCircle2 size={12} className="doc-context-icon" aria-hidden="true" />
                        <button type="button" className="doc-context-label" onClick={() => setShowDocLibrary(true)} title="Manage documents">
                            {sessionDocs.length} indexed
                        </button>
                        <div className={`doc-context-pills${docsExpanded ? ' doc-context-pills-expanded' : ''}`}>
                            {visibleDocs.map((d) => (
                                <span key={d.id} className="doc-pill" title={d.filepath || d.filename}>
                                    <FileText size={9} className="doc-pill-icon" aria-hidden="true" />
                                    <span className="doc-pill-name">{d.filename}</span>
                                    <button
                                        className="doc-pill-remove"
                                        onClick={(e) => handleRemoveDocument(e, d.id)}
                                        aria-label={`Remove ${d.filename} from this chat`}
                                    >
                                        <X size={10} />
                                    </button>
                                </span>
                            ))}
                            {!docsExpanded && hiddenCount > 0 && (
                                <button className="doc-pill-more" onClick={(e) => { e.stopPropagation(); setDocsExpanded(true); }}>
                                    +{hiddenCount} more
                                </button>
                            )}
                            {docsExpanded && hiddenCount > 0 && (
                                <button className="doc-pill-collapse" onClick={(e) => { e.stopPropagation(); setDocsExpanded(false); }}>
                                    show less
                                </button>
                            )}
                        </div>
                    </div>
                );
            })()}

            <div className="messages-scroll" ref={messagesScrollRef} onScroll={handleScroll}>
                <div className="messages-column">
                    {isLoadingMessages && (
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

                    {messages.map((msg, idx) => {
                        // The streaming bubble shows the just-finished answer while it fades out.
                        const isStreamEndingMsg = streamEnding
                            && msg.role === 'assistant'
                            && idx === messages.length - 1;
                        if (isStreamEndingMsg) return null;
                        return (
                            <div key={msg.id} className={deletingMsgId === msg.id ? 'msg-deleting' : undefined}>
                                <MessageBubble
                                    message={msg}
                                    agentSteps={msg.role === 'assistant' ? msg.agentSteps : undefined}
                                    onDelete={!isStreaming ? handleDeleteMessage : undefined}
                                    onResend={!isStreaming && msg.role === 'user' && !retiredAgent ? handleResendMessage : undefined}
                                    latencyMs={latencyByMsgId.get(msg.id)}
                                />
                            </div>
                        );
                    })}

                    {(isStreaming || streamEnding) && (
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
                                isStreaming={isStreaming}
                                agentSteps={isStreaming ? agentSteps : lastAgentStepsRef.current}
                                agentStepsActive={isStreaming && agentSteps.some(s => s.active)}
                                cards={isStreaming ? cards : lastCardsRef.current}
                                liveStatus={isStreaming ? liveStatus : null}
                            />
                        </div>
                    )}
                    <div ref={messagesEndRef} />
                </div>
            </div>

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
                                window.setTimeout(() => {
                                    document
                                        .getElementById(policyReceiptAnchor(receiptId))
                                        ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                }, 80);
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
                    {lastIsConnectorError && lastMessage && (
                        <ConnectorRetryBanner content={lastMessage.content} onRetry={handleRetryLast} />
                    )}
                    <PermissionPrompt sessionId={sessionId} />
                    <Composer
                        value={input}
                        onChange={setInput}
                        onSubmit={() => sendMessage()}
                        onStop={handleStop}
                        streaming={isStreaming}
                        stopping={stopping}
                        disabledReason={composerBlocked}
                        attachments={attach}
                        inputRef={inputRef}
                        placeholder={messages.length === 0 ? 'Ask GAIA anything' : 'Reply to GAIA'}
                        leftControls={<PermissionModeChip sessionId={sessionId} disabled={!!retiredAgent} />}
                        rightControls={<ModelChip disabled={isStreaming} />}
                    />
                </div>
            </div>
        </main>
    );
}
