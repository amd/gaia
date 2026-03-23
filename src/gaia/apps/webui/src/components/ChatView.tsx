// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useCallback, useState } from 'react';
import { Edit3, Paperclip, Download, Send, Upload, MessageSquare, Square, ArrowDown, Lock, FileText, FolderSearch, CheckCircle2, X, Link } from 'lucide-react';
import { MessageBubble } from './MessageBubble';
import { useChatStore } from '../stores/chatStore';
import { useNotificationStore, ALWAYS_ALLOW_TOOLS_KEY } from '../stores/notificationStore';
import type { GaiaNotification } from '../types/agent';
import * as api from '../services/api';
import { log } from '../utils/logger';
import { getSessionHash } from '../utils/format';
import { bugReportUrl } from './UnsupportedFeature';
import type { Message, StreamEvent, AgentStep, Attachment } from '../types';
import './ChatView.css';

const EMPTY_SUGGESTIONS = [
    'Summarize a document',
    'Find a file on my computer',
    'Analyze a spreadsheet',
    'Show my recent files',
];

/**
 * Safety-net regex to strip raw tool-call JSON from streaming content.
 *
 * Primary filtering happens server-side in sse_handler.py (see _TOOL_CALL_JSON_RE).
 * This frontend regex is a secondary safety net in case any tool-call JSON leaks
 * through the SSE stream. The canonical pattern is defined in sse_handler.py;
 * keep this in sync if the server-side pattern changes.
 */
const TOOL_CALL_JSON_SAFETY_RE = /\s*\{\s*"?(?:tool|thought|goal)"?\s*:\s*"[^"]*"[^}]*(?:"?tool_args"?\s*:\s*\{[^}]*\})?\s*\}/g;

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
        default:
            return null;
    }
}

interface ChatViewProps {
    sessionId: string;
}

export function ChatView({ sessionId }: ChatViewProps) {
    const {
        sessions, messages, setMessages, addMessage, removeMessage, removeMessagesFrom, updateSessionInList,
        isStreaming, streamingContent, setStreaming, setStreamContent, clearStreamContent,
        agentSteps, addAgentStep, updateLastAgentStep, appendThinkingContent, updateLastToolStep, clearAgentSteps,
        documents, setDocuments, setShowDocLibrary, setShowFileBrowser, isLoadingMessages, setLoadingMessages,
        systemStatus,
    } = useChatStore();

    const { addNotification } = useNotificationStore();

    const session = sessions.find((s) => s.id === sessionId);
    const [input, setInput] = useState('');
    const [editingTitle, setEditingTitle] = useState(false);
    const [titleDraft, setTitleDraft] = useState('');
    const [isDragOver, setIsDragOver] = useState(false);
    const [showScrollBtn, setShowScrollBtn] = useState(false);
    // Store agent steps snapshot for completed messages
    const [completedSteps, setCompletedSteps] = useState<AgentStep[]>([]);
    const [attachments, setAttachments] = useState<Attachment[]>([]);
    const [docsExpanded, setDocsExpanded] = useState(false);
    const [deletingMsgId, setDeletingMsgId] = useState<number | null>(null);
    // Smooth streaming exit — snapshot last content so fade-out shows real text
    const [streamEnding, setStreamEnding] = useState(false);
    const lastStreamContentRef = useRef('');
    const lastAgentStepsRef = useRef<AgentStep[]>([]);
    const prevStreamingRef = useRef(false);
    // Continuously snapshot the streaming state so we have it when streaming ends
    useEffect(() => {
        if (streamingContent) lastStreamContentRef.current = streamingContent;
    }, [streamingContent]);
    useEffect(() => {
        if (agentSteps.length > 0) lastAgentStepsRef.current = agentSteps.map(s => ({ ...s, active: false }));
    }, [agentSteps]);
    useEffect(() => {
        if (!isStreaming && prevStreamingRef.current) {
            setStreamEnding(true);
            const timer = setTimeout(() => {
                setStreamEnding(false);
                lastStreamContentRef.current = '';
                lastAgentStepsRef.current = [];
            }, 350);
            return () => clearTimeout(timer);
        }
        prevStreamingRef.current = isStreaming;
    }, [isStreaming]);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const messagesScrollRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);
    const abortRef = useRef<AbortController | null>(null);
    const stepIdRef = useRef(0);
    const toolOccurredRef = useRef(false);
    const sendMessageRef = useRef<(text?: string) => void>(() => {});

    // ── Streaming chunk buffer ──────────────────────────────────────
    // Buffer SSE chunks in a ref and flush to the store via rAF.
    // This limits React re-renders to ~60fps instead of once per chunk
    // (which can be hundreds/sec), dramatically reducing DOM mutations
    // and eliminating extension-triggered "runtime.lastError" floods.
    const streamBufferRef = useRef('');
    const rafRef = useRef<number | null>(null);
    const scrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    /** Timestamp of the last auto-scroll (used for throttling). */
    const lastScrollRef = useRef(0);
    /** True when the user is at (or near) the bottom of the messages list.
     *  Auto-scroll only fires when this is true, so scrolling up to read
     *  earlier messages won't be interrupted by new streaming content. */
    const isNearBottomRef = useRef(true);

    const flushStreamBuffer = useCallback(() => {
        rafRef.current = null;
        if (streamBufferRef.current) {
            setStreamContent(streamBufferRef.current);
        }
    }, [setStreamContent]);

    // Load messages on mount, then poll for external changes (MCP, API)
    const msgPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const lastMsgCountRef = useRef<number>(0);

    useEffect(() => {
        log.chat.info(`ChatView mounted for session=${sessionId}, loading messages...`);
        const t = log.chat.time();
        setLoadingMessages(true);

        const loadMessages = (isInitial = false) => {
            api.getMessages(sessionId)
                .then((data) => {
                    const msgs = (data.messages || []).map((m: any) => ({
                        ...m,
                        // Map snake_case agent_steps from API to camelCase agentSteps
                        agentSteps: m.agentSteps || m.agent_steps || undefined,
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
                })
                .catch((err) => {
                    if (isInitial) {
                        log.chat.error(`Failed to load messages for session=${sessionId}`, err);
                        setMessages([]);
                    }
                })
                .finally(() => { if (isInitial) setLoadingMessages(false); });
        };

        loadMessages(true);

        // Poll every 3s for messages added by external tools (MCP API, etc.)
        msgPollRef.current = setInterval(() => loadMessages(false), 3_000);
        return () => {
            if (msgPollRef.current) clearInterval(msgPollRef.current);
        };
    }, [sessionId, setMessages, setLoadingMessages]);

    // Load indexed documents on mount (so context bar is always up to date)
    useEffect(() => {
        api.listDocuments()
            .then((data) => setDocuments(data.documents || []))
            .catch(() => {});
    }, [setDocuments]);

    // Consume pending prompt from store (set by WelcomeScreen suggestions).
    // This replaces the fragile event-dispatch-with-setTimeout pattern —
    // the prompt is stored in Zustand before the session is created, so
    // ChatView reliably picks it up on mount regardless of render timing.
    useEffect(() => {
        const pending = useChatStore.getState().pendingPrompt;
        if (pending) {
            log.chat.info(`Consuming pending prompt: "${pending.slice(0, 60)}"`);
            useChatStore.getState().setPendingPrompt(null);
            setInput(pending);
            // Defer send to next tick so React finishes mount
            requestAnimationFrame(() => sendMessageRef.current(pending));
        }
    }, [sessionId]);

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
            if (rafRef.current !== null) {
                cancelAnimationFrame(rafRef.current);
                rafRef.current = null;
            }
            if (scrollTimerRef.current) {
                clearTimeout(scrollTimerRef.current);
                scrollTimerRef.current = null;
            }
            streamBufferRef.current = '';
            // Revoke any attachment blob URLs to prevent memory leaks
            setAttachments(prev => {
                prev.forEach(a => { if (a.url) URL.revokeObjectURL(a.url); });
                return [];
            });
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

    // Stop streaming — reads fresh state from store to avoid stale closures
    const handleStop = useCallback(() => {
        log.stream.warn('User stopped generation');
        if (abortRef.current) {
            abortRef.current.abort();
            abortRef.current = null;
        }
        // Cancel any pending rAF flush
        if (rafRef.current !== null) {
            cancelAnimationFrame(rafRef.current);
            rafRef.current = null;
        }
        // Use the buffer (most up-to-date) or fall back to store content
        const storeState = useChatStore.getState();
        const content = streamBufferRef.current || storeState.streamingContent;
        if (content) {
            log.stream.info(`Saving partial response (${content.length} chars)`);
            const currentSteps = storeState.agentSteps;
            const assistantMsg: Message = {
                id: Date.now() + 1,
                session_id: sessionId,
                role: 'assistant',
                content,
                created_at: new Date().toISOString(),
                rag_sources: null,
                agentSteps: currentSteps.length > 0 ? [...currentSteps] : undefined,
            };
            addMessage(assistantMsg);
        }
        streamBufferRef.current = '';
        setStreaming(false);
        clearStreamContent();
        setCompletedSteps([]);
        clearAgentSteps();
    }, [sessionId, addMessage, setStreaming, clearStreamContent, clearAgentSteps]);

    // Global keyboard shortcuts: Escape → stop streaming, Ctrl+K → focus sidebar search
    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            if (e.key === 'Escape' && isStreaming) {
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

    // Auto-resize textarea
    const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        setInput(e.target.value);
        const el = e.target;
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 200) + 'px';
    };

    // Handle clipboard paste (screenshots)
    const handlePaste = useCallback(async (e: React.ClipboardEvent) => {
        const items = e.clipboardData?.items;
        if (!items) return;

        const imageItems: DataTransferItem[] = [];
        for (const item of Array.from(items)) {
            if (item.type.startsWith('image/')) {
                imageItems.push(item);
            }
        }

        if (imageItems.length === 0) return; // Let normal text paste happen

        e.preventDefault(); // Prevent pasting image as text

        for (const item of imageItems) {
            const file = item.getAsFile();
            if (!file) continue;

            const id = `attach-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
            const previewUrl = URL.createObjectURL(file);
            const attachment: Attachment = {
                id,
                file,
                name: file.name || `screenshot-${new Date().toISOString().slice(0, 19).replace(/[T:]/g, '-')}.png`,
                url: previewUrl,
                uploading: true,
                uploaded: false,
                isImage: true,
            };

            setAttachments(prev => [...prev, attachment]);
            log.chat.info(`Pasted image: ${attachment.name} (${file.size} bytes)`);

            // Upload in background
            try {
                const result = await api.uploadFile(file);
                setAttachments(prev => prev.map(a =>
                    a.id === id ? { ...a, uploading: false, uploaded: true, serverUrl: result.url } : a
                ));
                log.chat.info(`Upload complete: ${attachment.name} -> ${result.url}`);
            } catch (err) {
                log.chat.error(`Upload failed: ${attachment.name}`, err);
                setAttachments(prev => prev.map(a =>
                    a.id === id ? { ...a, uploading: false, error: 'Upload failed' } : a
                ));
            }
        }
    }, []);

    // Handle file drop on input area
    const handleInputDrop = useCallback(async (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragOver(false);

        const files = e.dataTransfer?.files;
        if (!files || files.length === 0) return;

        for (const file of Array.from(files)) {
            const isImage = file.type.startsWith('image/');
            const id = `attach-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
            const previewUrl = isImage ? URL.createObjectURL(file) : '';

            const attachment: Attachment = {
                id,
                file,
                name: file.name,
                url: previewUrl,
                uploading: true,
                uploaded: false,
                isImage,
            };

            setAttachments(prev => [...prev, attachment]);
            log.chat.info(`Dropped file: ${file.name} (${file.size} bytes, image=${isImage})`);

            // Upload in background
            try {
                const result = await api.uploadFile(file);
                setAttachments(prev => prev.map(a =>
                    a.id === id ? { ...a, uploading: false, uploaded: true, serverUrl: result.url } : a
                ));
                log.chat.info(`Upload complete: ${file.name} -> ${result.url}`);
            } catch (err) {
                log.chat.error(`Upload failed: ${file.name}`, err);
                setAttachments(prev => prev.map(a =>
                    a.id === id ? { ...a, uploading: false, error: 'Upload failed' } : a
                ));
            }
        }
    }, []);

    // Remove an attachment
    const removeAttachment = useCallback((id: string) => {
        setAttachments(prev => {
            const attachment = prev.find(a => a.id === id);
            if (attachment?.url) URL.revokeObjectURL(attachment.url);
            return prev.filter(a => a.id !== id);
        });
    }, []);

    // Send message
    const sendMessage = useCallback(async (overrideText?: string) => {
        const text = (overrideText || input).trim();
        const hasAttachments = attachments.length > 0 && attachments.some(a => a.uploaded);

        // User just sent a message — re-pin scroll to the bottom so the
        // new message and streaming response are visible.
        isNearBottomRef.current = true;

        if ((!text && !hasAttachments) || isStreaming) {
            if (!text && !hasAttachments) log.chat.debug('Send blocked: empty message');
            if (isStreaming) log.chat.debug('Send blocked: already streaming');
            return;
        }

        // Build message text with attachment references
        let messageText = text;
        const uploadedAttachments = attachments.filter(a => a.uploaded && a.serverUrl);
        if (uploadedAttachments.length > 0) {
            const attachmentLines = uploadedAttachments.map(a => {
                if (a.isImage) {
                    return `![${a.name}](${a.serverUrl})`;
                }
                return `[${a.name}](${a.serverUrl})`;
            }).join('\n');
            messageText = messageText
                ? `${messageText}\n\n${attachmentLines}`
                : attachmentLines;
        }

        log.chat.info(`Sending message to session=${sessionId}`, { length: messageText.length, preview: messageText.slice(0, 80) });

        setInput('');
        if (inputRef.current) {
            inputRef.current.style.height = 'auto';
            inputRef.current.focus();
        }

        // Clear attachments
        setAttachments(prev => {
            prev.forEach(a => { if (a.url) URL.revokeObjectURL(a.url); });
            return [];
        });

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

        // Start streaming
        setStreaming(true);
        clearStreamContent();
        clearAgentSteps();
        setCompletedSteps([]);
        stepIdRef.current = 0;
        toolOccurredRef.current = false;

        log.stream.info('Starting agent stream...');
        const streamStart = log.stream.time();

        let fullContent = '';
        let doneHandled = false;
        streamBufferRef.current = '';

        const controller = api.sendMessageStream(sessionId, messageText, {
            onChunk: (event) => {
                const content = event.content || '';
                if (content) {
                    // 'answer' events carry the full final text (not a delta),
                    // so replace rather than append to avoid doubling content.
                    if (event.type === 'answer') {
                        fullContent = content;
                    } else {
                        // If a tool just ran between text chunks, add a paragraph separator
                        if (toolOccurredRef.current && fullContent.length > 0) {
                            fullContent += '\n\n';
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
                    if (rafRef.current === null) {
                        rafRef.current = requestAnimationFrame(flushStreamBuffer);
                    }
                }
            },
            onAgentEvent: (event) => {
                // ── Tool confirmation popup ──────────────────────────────
                if (event.type === 'tool_confirm') {
                    if (!event.confirm_id) {
                        console.error('[ChatView] tool_confirm event missing confirm_id, ignoring');
                        return;
                    }
                    const toolName = event.tool || '';
                    const alwaysAllowed: string[] = JSON.parse(
                        localStorage.getItem(ALWAYS_ALLOW_TOOLS_KEY) || '[]'
                    );
                    if (alwaysAllowed.includes(toolName)) {
                        // Auto-approve without showing the modal
                        api.confirmToolExecution(sessionId, event.confirm_id, 'allow', false).catch(
                            (err) => console.error('[ChatView] auto-confirm failed:', err)
                        );
                        return;
                    }
                    // Show the PermissionPrompt modal via notificationStore
                    const notification: GaiaNotification = {
                        id: event.confirm_id,
                        type: 'permission_request',
                        agentId: 'chat',
                        agentName: 'GAIA',
                        title: `Allow ${toolName}?`,
                        message: `The agent wants to execute: ${toolName}`,
                        timestamp: Date.now(),
                        read: false,
                        dismissed: false,
                        priority: 'high',
                        tool: toolName,
                        toolArgs: event.args as Record<string, unknown> | undefined,
                        timeoutSeconds: event.timeout_seconds ?? 60,
                    };
                    addNotification(notification);
                    return;
                }

                // Permission request — check always-allow list, then push to
                // notification store for the PermissionPrompt overlay.
                if (event.type === 'permission_request') {
                    const toolName = event.tool || '';
                    const alwaysAllowed: string[] = JSON.parse(
                        localStorage.getItem(ALWAYS_ALLOW_TOOLS_KEY) || '[]'
                    );
                    if (alwaysAllowed.includes(toolName)) {
                        api.confirmTool(sessionId, true).catch(
                            (err) => console.error('[ChatView] auto-confirm failed:', err)
                        );
                        return;
                    }
                    const { addNotification: addNotif } = useNotificationStore.getState();
                    addNotif({
                        id: event.confirm_id ?? `perm-${Date.now()}`,
                        type: 'permission_request',
                        agentId: sessionId,
                        agentName: 'GAIA',
                        title: `Allow ${toolName}?`,
                        message: `The agent wants to execute: ${toolName}`,
                        timestamp: Date.now(),
                        read: false,
                        dismissed: false,
                        priority: 'high',
                        tool: toolName,
                        toolArgs: event.args as Record<string, unknown> | undefined,
                        timeoutSeconds: event.timeout_seconds ?? 60,
                    });
                    return;
                }

                // Tool completion updates the last TOOL step (not just the last step,
                // since thinking/status events may have been interleaved during execution)
                if (event.type === 'tool_end') {
                    updateLastToolStep({ active: false, success: event.success !== false });
                    return;
                }
                if (event.type === 'tool_result') {
                    const updates: Partial<AgentStep> = {
                        result: event.summary || event.title || 'Done',
                        active: false,
                        success: event.success !== false,
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
                    }
                }
            },
            onDone: (event) => {
                if (doneHandled) return;
                doneHandled = true;

                // Cancel any pending rAF flush — we have the final content
                if (rafRef.current !== null) {
                    cancelAnimationFrame(rafRef.current);
                    rafRef.current = null;
                }
                streamBufferRef.current = '';

                const content = event.content || fullContent;
                log.chat.timed(`Agent response complete: ${content.length} chars`, streamStart);

                // Snapshot agent steps for the completed message
                const stepsSnapshot = useChatStore.getState().agentSteps.map((s) => ({
                    ...s, active: false,
                }));

                if (content) {
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
                    };
                    addMessage(assistantMsg);
                }

                setCompletedSteps(stepsSnapshot);
                setStreaming(false);
                clearStreamContent();
                clearAgentSteps();

                // Refocus input so user can immediately type the next message
                if (inputRef.current) inputRef.current.focus();

                // Refresh messages from DB to replace optimistic IDs with real
                // DB IDs.  Without this, delete/resend on user messages fails
                // because the optimistic Date.now() ID doesn't match the DB's
                // auto-increment ID.
                setTimeout(() => {
                    api.getMessages(sessionId)
                        .then((data) => {
                            const msgs = (data.messages || []).map((m: any) => ({
                                ...m,
                                agentSteps: m.agentSteps || m.agent_steps || undefined,
                            }));
                            setMessages(msgs);
                            lastMsgCountRef.current = msgs.length;
                        })
                        .catch(() => {});
                }, 300);

                // Auto-title on first message
                if (session && session.title === 'New Task') {
                    const autoTitle = text.slice(0, 50) + (text.length > 50 ? '...' : '');
                    api.updateSession(sessionId, { title: autoTitle })
                        .then(() => updateSessionInList(sessionId, { title: autoTitle }))
                        .catch((err) => log.chat.error('Auto-title failed', err));
                }
            },
            onError: (err) => {
                // Cancel any pending rAF flush
                if (rafRef.current !== null) {
                    cancelAnimationFrame(rafRef.current);
                    rafRef.current = null;
                }
                streamBufferRef.current = '';

                log.chat.error(`Chat error for session=${sessionId}`, err);
                // Provide a user-friendly error message based on the error type
                // Each error includes a GitHub link for reporting issues
                let errorContent: string;
                const msg = err.message || '';
                const issueFooter = '\n\n---\n*Unexpected error?* '
                    + `[Report it on GitHub](${bugReportUrl(msg.slice(0, 80))})`;

                if (msg.includes('Lemonade') || msg.includes('LLM') || msg.includes('Could not get response')) {
                    errorContent =
                        'Could not reach the LLM server. Make sure Lemonade Server is running:\n\n' +
                        '```\nlemonade-server serve\n```\n\n' +
                        'Then try sending your message again.' + issueFooter;
                } else if (err instanceof TypeError || msg.includes('fetch') || msg.includes('Failed to fetch') || msg.includes('NetworkError')) {
                    errorContent =
                        'Cannot connect to the GAIA Agent UI server. Make sure the backend is running:\n\n' +
                        '```\ngaia chat --ui\n```' + issueFooter;
                } else if (msg.includes('500')) {
                    errorContent =
                        'The server encountered an error. This usually means Lemonade Server is not running or the model failed to load.\n\n' +
                        'Start Lemonade Server with:\n```\nlemonade-server serve\n```' + issueFooter;
                } else if (msg.includes('timed out') || msg.includes('timeout') || msg.includes('Timeout')) {
                    errorContent =
                        'The request timed out. The query may be too complex — try breaking it into simpler questions.\n\n' +
                        'If Lemonade Server is running but responses are slow, the model may need more resources.' + issueFooter;
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
            },
        });

        abortRef.current = controller;
    }, [input, attachments, isStreaming, sessionId, session, addMessage, setMessages, setStreaming, flushStreamBuffer, clearStreamContent, updateSessionInList, addAgentStep, updateLastAgentStep, appendThinkingContent, updateLastToolStep, clearAgentSteps]);

    // Keep ref in sync so event listeners always call the latest sendMessage
    sendMessageRef.current = sendMessage;

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
                    .then((data) => setMessages(data.messages || []))
                    .catch(() => {});
            }
        }, 250);
    }, [sessionId, isStreaming, removeMessage, setMessages]);

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
                .then((data) => setMessages(data.messages || []))
                .catch(() => {});
            return;
        }

        // Re-send the same message text
        sendMessage(text);
    }, [sessionId, isStreaming, removeMessagesFrom, setMessages, sendMessage]);

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    };

    // Session hash link copy
    const [hashCopied, setHashCopied] = useState(false);
    const handleCopyHash = useCallback((e: React.MouseEvent) => {
        e.preventDefault();
        const hash = getSessionHash(sessionId);
        const url = `${window.location.origin}${window.location.pathname}#${hash}`;
        navigator.clipboard.writeText(url).then(() => {
            log.ui.info(`Copied session link: ${url}`);
            setHashCopied(true);
            setTimeout(() => setHashCopied(false), 1500);
        }).catch(() => {
            log.ui.warn('Clipboard write failed');
        });
    }, [sessionId]);

    // Title editing
    const startEditTitle = () => {
        setTitleDraft(session?.title || '');
        setEditingTitle(true);
    };

    const saveTitle = async () => {
        if (titleDraft.trim() && titleDraft !== session?.title) {
            await api.updateSession(sessionId, { title: titleDraft.trim() });
            updateSessionInList(sessionId, { title: titleDraft.trim() });
        }
        setEditingTitle(false);
    };

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

    /** Remove a document from the index directly from the context bar. */
    const handleRemoveDocument = useCallback(async (e: React.MouseEvent, docId: string) => {
        e.stopPropagation(); // Don't trigger the context bar click
        const doc = documents.find((d) => d.id === docId);
        log.doc.info(`Removing document from context bar: ${doc?.filename || docId}`);
        try {
            await api.deleteDocument(docId);
            const remaining = documents.filter((d) => d.id !== docId);
            setDocuments(remaining);
            // Auto-collapse when 3 or fewer docs remain
            if (remaining.length <= 3) setDocsExpanded(false);
            log.doc.info(`Removed document: ${doc?.filename || docId}`);
        } catch (err) {
            log.doc.error(`Failed to remove document: ${doc?.filename || docId}`, err);
        }
    }, [documents, setDocuments]);

    // Drag & drop
    const handleDrop = useCallback(async (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragOver(false);
        if (e.dataTransfer.files.length > 0) {
            setShowDocLibrary(true);
            for (const file of Array.from(e.dataTransfer.files)) {
                const filepath = (file as any).path || file.name;
                try {
                    await api.uploadDocumentByPath(filepath);
                } catch (err) {
                    log.doc.error(`Upload failed: ${filepath}`, err);
                }
            }
        }
    }, [setShowDocLibrary]);

    const handleDragOver = (e: React.DragEvent) => { e.preventDefault(); e.stopPropagation(); setIsDragOver(true); };
    const handleDragLeave = (e: React.DragEvent) => { e.preventDefault(); e.stopPropagation(); setIsDragOver(false); };

    const handleSuggestionClick = (text: string) => {
        setInput(text);
        sendMessage(text);
    };

    const showEmptyState = !isLoadingMessages && messages.length === 0 && !isStreaming;

    return (
        <main
            className={`chat-view ${isDragOver ? 'drag-active' : ''}`}
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
        >
            {/* Header */}
            <header className="chat-header">
                <div className="chat-header-left">
                    {editingTitle ? (
                        <input
                            className="title-edit"
                            value={titleDraft}
                            onChange={(e) => setTitleDraft(e.target.value)}
                            onBlur={saveTitle}
                            onKeyDown={(e) => e.key === 'Enter' && saveTitle()}
                            autoFocus
                            aria-label="Edit chat title"
                        />
                    ) : (
                        <>
                            <h3 className="chat-title">{session?.title || 'Chat'}</h3>
                            <button className="btn-icon-sm" onClick={startEditTitle} title="Rename" aria-label="Rename chat">
                                <Edit3 size={13} />
                            </button>
                        </>
                    )}
                </div>
                <div className="chat-header-right">
                    <a
                        className={`session-hash-badge ${hashCopied ? 'copied' : ''}`}
                        href={`#${getSessionHash(sessionId)}`}
                        onClick={handleCopyHash}
                        title={hashCopied ? 'Copied!' : `Copy session link #${getSessionHash(sessionId)}`}
                        aria-label={`Copy link for session ${getSessionHash(sessionId)}`}
                    >
                        <Link size={10} />
                        <span>#{getSessionHash(sessionId)}</span>
                    </a>
                    <span className={`model-badge ${!systemStatus?.model_loaded ? 'no-model' : ''}`}>{systemStatus?.model_loaded || 'No model loaded'}</span>
                    <button className="btn-icon-sm" onClick={() => setShowDocLibrary(true)} title="Documents" aria-label="Attach documents">
                        <Paperclip size={15} />
                    </button>
                    <button className="btn-icon-sm" onClick={() => setShowFileBrowser(true)} title="Browse files" aria-label="Browse files">
                        <FolderSearch size={15} />
                    </button>
                    <button className="btn-icon-sm" onClick={handleExport} title="Export" aria-label="Export chat">
                        <Download size={15} />
                    </button>
                </div>
            </header>

            {/* Indexed documents context bar */}
            {documents.length > 0 && (() => {
                // Sort by most recently accessed (last_accessed_at), falling back to indexed_at
                const sorted = [...documents].sort((a, b) => {
                    const aTime = a.last_accessed_at || a.indexed_at || '';
                    const bTime = b.last_accessed_at || b.indexed_at || '';
                    return bTime.localeCompare(aTime);
                });
                const visibleDocs = docsExpanded ? sorted : sorted.slice(0, 3);
                const hiddenCount = sorted.length - 3;
                return (
                    <div
                        className={`doc-context-bar${docsExpanded ? ' doc-context-expanded' : ''}`}
                        aria-label={`${documents.length} indexed document${documents.length !== 1 ? 's' : ''}`}
                    >
                        <CheckCircle2 size={12} className="doc-context-icon" />
                        <span
                            className="doc-context-label"
                            onClick={() => setShowDocLibrary(true)}
                            title="Click to manage documents"
                            role="button"
                            tabIndex={0}
                        >
                            {documents.length} indexed
                        </span>
                        <div className={`doc-context-pills${docsExpanded ? ' doc-context-pills-expanded' : ''}`}>
                            {visibleDocs.map((d) => (
                                <span key={d.id} className="doc-pill" title={d.filepath || d.filename}>
                                    <FileText size={9} className="doc-pill-icon" />
                                    <span className="doc-pill-name">{d.filename}</span>
                                    <button
                                        className="doc-pill-remove"
                                        onClick={(e) => handleRemoveDocument(e, d.id)}
                                        title={`Remove ${d.filename} from index`}
                                        aria-label={`Remove ${d.filename}`}
                                    >
                                        <X size={10} />
                                    </button>
                                </span>
                            ))}
                            {!docsExpanded && hiddenCount > 0 && (
                                <button
                                    className="doc-pill-more"
                                    onClick={(e) => { e.stopPropagation(); setDocsExpanded(true); }}
                                    title="Show all indexed files"
                                    aria-label={`Show ${hiddenCount} more files`}
                                >
                                    +{hiddenCount} more
                                </button>
                            )}
                            {docsExpanded && hiddenCount > 0 && (
                                <button
                                    className="doc-pill-collapse"
                                    onClick={(e) => { e.stopPropagation(); setDocsExpanded(false); }}
                                    title="Show fewer files"
                                    aria-label="Collapse file list"
                                >
                                    show less
                                </button>
                            )}
                        </div>
                    </div>
                );
            })()}

            {/* Messages */}
            <div className="messages-scroll" ref={messagesScrollRef} onScroll={handleScroll}>
                {isLoadingMessages && (
                    <div className="skeleton-messages" aria-label="Loading messages">
                        {[0, 1, 2].map((i) => (
                            <div key={i} className="skeleton-msg">
                                <div className="skeleton-header">
                                    <div className="skeleton-avatar" />
                                    <div className="skeleton-role" />
                                </div>
                                <div className="skeleton-lines">
                                    <div className="skeleton-line" />
                                    <div className="skeleton-line" />
                                    {i !== 2 && <div className="skeleton-line" />}
                                </div>
                            </div>
                        ))}
                    </div>
                )}

                {showEmptyState && (
                    <div className="empty-chat">
                        <div className="empty-chat-icon">
                            <MessageSquare size={36} strokeWidth={1.2} />
                        </div>
                        <h4 className="empty-chat-title">What can I help you with?</h4>
                        <p className="empty-chat-desc">
                            Ask about your documents, search files, or analyze data &mdash; powered by local AI.
                        </p>
                        <div className="empty-chat-suggestions">
                            {EMPTY_SUGGESTIONS.map((s) => (
                                <button
                                    key={s}
                                    className="empty-chat-chip"
                                    onClick={() => handleSuggestionClick(s)}
                                >
                                    {s}
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                {messages.map((msg, idx) => {
                    // Show a solid terminal cursor on the last assistant message
                    // (only when not actively streaming — the streaming bubble has its own cursor)
                    const isLastAssistant = !isStreaming && !streamEnding
                        && msg.role === 'assistant'
                        && messages.slice(idx + 1).every((m) => m.role !== 'assistant');
                    // During stream-ending, skip rendering the just-completed
                    // assistant message entirely — the streaming bubble shows it.
                    // This prevents the flash/jump when transitioning.
                    const isStreamEndingMsg = streamEnding
                        && msg.role === 'assistant'
                        && idx === messages.length - 1;
                    if (isStreamEndingMsg) return null;
                    return (
                        <div key={msg.id} className={deletingMsgId === msg.id ? 'msg-deleting' : undefined}>
                            <MessageBubble
                                message={msg}
                                showTerminalCursor={isLastAssistant}
                                agentSteps={msg.role === 'assistant' ? msg.agentSteps : undefined}
                                onDelete={!isStreaming ? handleDeleteMessage : undefined}
                                onResend={!isStreaming && msg.role === 'user' ? handleResendMessage : undefined}
                            />
                        </div>
                    );
                })}

                {/* Active streaming message with agent activity inside */}
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
                            showTerminalCursor={streamEnding}
                            agentSteps={isStreaming ? agentSteps : lastAgentStepsRef.current}
                            agentStepsActive={isStreaming && agentSteps.some(s => s.active)}
                        />
                    </div>
                )}
                <div ref={messagesEndRef} />
            </div>

            {/* Scroll to bottom */}
            {showScrollBtn && (
                <button className="scroll-bottom-btn" onClick={scrollToBottom} title="Scroll to bottom" aria-label="Scroll to bottom">
                    <ArrowDown size={16} />
                </button>
            )}

            {/* Drag overlay */}
            {isDragOver && (
                <div className="drag-overlay">
                    <Upload size={32} strokeWidth={1.5} />
                    <span>Drop files to index</span>
                </div>
            )}

            {/* Input */}
            <div className="input-area">
                <div
                    className={`input-box ${attachments.length > 0 ? 'has-attachments' : ''}`}
                    onDrop={handleInputDrop}
                    onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
                >
                    <div className="input-content">
                        {/* Attachment previews */}
                        {attachments.length > 0 && (
                            <div className="attachment-strip">
                                {attachments.map(a => (
                                    <div key={a.id} className={`attachment-preview ${a.error ? 'attachment-error' : ''}`}>
                                        {a.isImage && a.url ? (
                                            <img src={a.url} alt={a.name} className="attachment-thumb" />
                                        ) : (
                                            <div className="attachment-file-icon">
                                                <FileText size={16} />
                                            </div>
                                        )}
                                        <span className="attachment-name" title={a.name}>
                                            {a.name.length > 20 ? a.name.slice(0, 17) + '...' : a.name}
                                        </span>
                                        {a.uploading && <span className="attachment-spinner" />}
                                        {a.error && <span className="attachment-error-text">{a.error}</span>}
                                        <button
                                            className="attachment-remove"
                                            onClick={() => removeAttachment(a.id)}
                                            title="Remove"
                                            aria-label={`Remove ${a.name}`}
                                        >
                                            <X size={12} />
                                        </button>
                                    </div>
                                ))}
                            </div>
                        )}
                        <textarea
                            ref={inputRef}
                            className="msg-input"
                            value={input}
                            onChange={handleInputChange}
                            onKeyDown={handleKeyDown}
                            onPaste={handlePaste}
                            placeholder="Type a message or paste an image... (Shift+Enter for new line)"
                            rows={1}
                            disabled={isStreaming}
                            aria-label="Message input"
                        />
                    </div>
                    {!isStreaming && <span className="input-cursor" aria-hidden="true" />}
                    <div className="input-btns">
                        <button className="btn-icon-sm" onClick={() => setShowDocLibrary(true)} title="Upload document" aria-label="Upload document">
                            <Upload size={15} />
                        </button>
                        <button className="btn-icon-sm" onClick={() => setShowFileBrowser(true)} title="Browse files" aria-label="Browse files">
                            <FolderSearch size={15} />
                        </button>
                        {isStreaming ? (
                            <button
                                className="stop-btn"
                                onClick={handleStop}
                                title="Stop generating"
                                aria-label="Stop generating"
                            >
                                <Square size={14} />
                            </button>
                        ) : (
                            <button
                                className="send-btn"
                                onClick={() => sendMessage()}
                                disabled={!input.trim() && !attachments.some(a => a.uploaded)}
                                title="Send (Enter)"
                                aria-label="Send message"
                            >
                                <Send size={16} />
                            </button>
                        )}
                    </div>
                </div>
                <div className="input-footer">
                    <span className="input-footer-item">
                        <Lock size={10} />
                        <span>100% local &amp; private</span>
                    </span>
                    <span className="input-footer-sep" />
                    <span className="input-footer-item">
                        <kbd className="kbd-hint">Enter</kbd>
                        <span>send</span>
                    </span>
                    <span className="input-footer-item">
                        <kbd className="kbd-hint">Shift+Enter</kbd>
                        <span>new line</span>
                    </span>
                    {isStreaming && (
                        <span className="input-footer-item">
                            <kbd className="kbd-hint">Esc</kbd>
                            <span>stop</span>
                        </span>
                    )}
                    <span className="input-footer-sep" />
                    <span className="input-footer-item">
                        <kbd className="kbd-hint">Ctrl+K</kbd>
                        <span>search</span>
                    </span>
                    <span className="input-footer-item">
                        <kbd className="kbd-hint">Ctrl+V</kbd>
                        <span>paste image</span>
                    </span>
                </div>
            </div>
        </main>
    );
}
