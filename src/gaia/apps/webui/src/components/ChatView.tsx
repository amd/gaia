// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useCallback, useState } from 'react';
import { Edit3, Paperclip, Download, Send, Upload, MessageSquare, Square, ArrowDown, Lock, FileText, FolderSearch } from 'lucide-react';
import { MessageBubble } from './MessageBubble';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import { log } from '../utils/logger';
import type { Message, StreamEvent, AgentStep } from '../types';
import './ChatView.css';

const EMPTY_SUGGESTIONS = [
    'Summarize a document for me',
    'Write a Python script',
    'Explain a concept simply',
    'Help me brainstorm ideas',
];

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
        agentSteps, addAgentStep, updateLastAgentStep, clearAgentSteps,
        documents, setDocuments, setShowDocLibrary, setShowFileBrowser, isLoadingMessages, setLoadingMessages,
    } = useChatStore();

    const session = sessions.find((s) => s.id === sessionId);
    const [input, setInput] = useState('');
    const [editingTitle, setEditingTitle] = useState(false);
    const [titleDraft, setTitleDraft] = useState('');
    const [isDragOver, setIsDragOver] = useState(false);
    const [showScrollBtn, setShowScrollBtn] = useState(false);
    // Store agent steps snapshot for completed messages
    const [completedSteps, setCompletedSteps] = useState<AgentStep[]>([]);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const messagesScrollRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);
    const abortRef = useRef<AbortController | null>(null);
    const stepIdRef = useRef(0);

    // ── Streaming chunk buffer ──────────────────────────────────────
    // Buffer SSE chunks in a ref and flush to the store via rAF.
    // This limits React re-renders to ~60fps instead of once per chunk
    // (which can be hundreds/sec), dramatically reducing DOM mutations
    // and eliminating extension-triggered "runtime.lastError" floods.
    const streamBufferRef = useRef('');
    const rafRef = useRef<number | null>(null);
    const scrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const flushStreamBuffer = useCallback(() => {
        rafRef.current = null;
        if (streamBufferRef.current) {
            setStreamContent(streamBufferRef.current);
        }
    }, [setStreamContent]);

    // Load messages on mount
    useEffect(() => {
        log.chat.info(`ChatView mounted for session=${sessionId}, loading messages...`);
        const t = log.chat.time();
        setLoadingMessages(true);
        api.getMessages(sessionId)
            .then((data) => {
                const msgs = (data.messages || []).map((m: any) => ({
                    ...m,
                    // Map snake_case agent_steps from API to camelCase agentSteps
                    agentSteps: m.agentSteps || m.agent_steps || undefined,
                }));
                setMessages(msgs);
                log.chat.timed(`Loaded ${msgs.length} message(s) for session=${sessionId}`, t);
            })
            .catch((err) => {
                log.chat.error(`Failed to load messages for session=${sessionId}`, err);
                setMessages([]);
            })
            .finally(() => setLoadingMessages(false));
    }, [sessionId, setMessages, setLoadingMessages]);

    // Load indexed documents on mount (so context bar is always up to date)
    useEffect(() => {
        api.listDocuments()
            .then((data) => setDocuments(data.documents || []))
            .catch(() => {});
    }, [setDocuments]);

    // Listen for external send-prompt events (from WelcomeScreen suggestions)
    useEffect(() => {
        const handler = (e: Event) => {
            const prompt = (e as CustomEvent).detail?.prompt;
            if (prompt) {
                log.chat.info(`Received gaia:send-prompt event: "${prompt.slice(0, 60)}"`);
                setInput(prompt);
                setTimeout(() => sendMessage(prompt), 50);
            }
        };
        window.addEventListener('gaia:send-prompt', handler);
        return () => window.removeEventListener('gaia:send-prompt', handler);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [sessionId]);

    // Auto-scroll (debounced to avoid excessive DOM mutations during streaming)
    useEffect(() => {
        if (scrollTimerRef.current) clearTimeout(scrollTimerRef.current);
        scrollTimerRef.current = setTimeout(() => {
            messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
        }, 80);
    }, [messages, streamingContent, agentSteps]);

    // Focus input
    useEffect(() => { inputRef.current?.focus(); }, [sessionId]);

    // Abort active stream and clean up timers when component unmounts
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
        };
    }, [sessionId]);

    // Track scroll position for scroll-to-bottom button
    const handleScroll = useCallback(() => {
        const el = messagesScrollRef.current;
        if (!el) return;
        const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
        setShowScrollBtn(distFromBottom > 200);
    }, []);

    const scrollToBottom = useCallback(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, []);

    // Stop streaming
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
        const content = streamBufferRef.current || streamingContent;
        if (content) {
            log.stream.info(`Saving partial response (${content.length} chars)`);
            const assistantMsg: Message = {
                id: Date.now() + 1,
                session_id: sessionId,
                role: 'assistant',
                content,
                created_at: new Date().toISOString(),
                rag_sources: null,
                agentSteps: agentSteps.length > 0 ? [...agentSteps] : undefined,
            };
            addMessage(assistantMsg);
        }
        streamBufferRef.current = '';
        setStreaming(false);
        clearStreamContent();
        setCompletedSteps([]);
        clearAgentSteps();
    }, [sessionId, streamingContent, agentSteps, addMessage, setStreaming, clearStreamContent, clearAgentSteps]);

    // Auto-resize textarea
    const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        setInput(e.target.value);
        const el = e.target;
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 200) + 'px';
    };

    // Send message
    const sendMessage = useCallback(async (overrideText?: string) => {
        const text = (overrideText || input).trim();
        if (!text || isStreaming) {
            if (!text) log.chat.debug('Send blocked: empty message');
            if (isStreaming) log.chat.debug('Send blocked: already streaming');
            return;
        }

        log.chat.info(`Sending message to session=${sessionId}`, { length: text.length, preview: text.slice(0, 80) });

        setInput('');
        if (inputRef.current) inputRef.current.style.height = 'auto';

        // Optimistic user message
        const userMsg: Message = {
            id: Date.now(),
            session_id: sessionId,
            role: 'user',
            content: text,
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

        log.stream.info('Starting agent stream...');
        const streamStart = log.stream.time();

        let fullContent = '';
        let doneHandled = false;
        streamBufferRef.current = '';

        const controller = api.sendMessageStream(sessionId, text, {
            onChunk: (event) => {
                const content = event.content || '';
                if (content) {
                    fullContent += content;
                    // Filter raw tool-call JSON that LLMs sometimes emit as text.
                    // This is a frontend safety net (backend also filters these).
                    const cleaned = fullContent.replace(
                        /^\s*\{"?\s*tool"?\s*:\s*"[^"]+"\s*,\s*"?tool_args"?\s*:\s*\{.*\}\s*\}\s*$/s,
                        ''
                    ).trim();
                    // Buffer chunks and flush to store at most once per frame (~60fps)
                    // instead of triggering a React re-render on every single SSE chunk
                    streamBufferRef.current = cleaned;
                    if (rafRef.current === null) {
                        rafRef.current = requestAnimationFrame(flushStreamBuffer);
                    }
                }
            },
            onAgentEvent: (event) => {
                // Tool completion updates the last tool step
                if (event.type === 'tool_end') {
                    updateLastAgentStep({ active: false, success: event.success !== false });
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
                    updateLastAgentStep(updates);
                    return;
                }
                // Tool args update the last tool step with detail
                if (event.type === 'tool_args') {
                    updateLastAgentStep({
                        detail: event.detail || JSON.stringify(event.args),
                    });
                    return;
                }

                // ── Consolidate thinking events ──────────────────────────
                // Instead of creating a new step for every thought, update
                // the existing thinking step so we get ONE "Thinking" entry
                // that shows the latest thought, not a massive stream.
                if (event.type === 'thinking') {
                    const currentSteps = useChatStore.getState().agentSteps;
                    const lastStep = currentSteps[currentSteps.length - 1];
                    if (lastStep && lastStep.type === 'thinking') {
                        // Update the existing thinking step with new content
                        updateLastAgentStep({
                            detail: event.content,
                            active: true,
                        });
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
                        // Consolidate with previous status/thinking step
                        if (lastStep && (lastStep.type === 'status' || lastStep.type === 'thinking') && lastStep.active) {
                            updateLastAgentStep({
                                label: msg || 'Working',
                                detail: msg,
                            });
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
                if (step) addAgentStep(step);
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
                    const assistantMsg: Message = {
                        id: event.message_id || Date.now() + 1,
                        session_id: sessionId,
                        role: 'assistant',
                        content,
                        created_at: new Date().toISOString(),
                        rag_sources: null,
                        agentSteps: stepsSnapshot.length > 0 ? stepsSnapshot : undefined,
                    };
                    addMessage(assistantMsg);
                }

                setCompletedSteps(stepsSnapshot);
                setStreaming(false);
                clearStreamContent();
                clearAgentSteps();

                // Auto-title on first message
                if (session && session.title === 'New Chat') {
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
                let errorContent: string;
                const msg = err.message || '';
                if (msg.includes('Lemonade') || msg.includes('LLM') || msg.includes('Could not get response')) {
                    errorContent =
                        'Could not reach the LLM server. Make sure Lemonade Server is running:\n\n' +
                        '```\nlemonade-server serve\n```\n\n' +
                        'Then try sending your message again.';
                } else if (err instanceof TypeError || msg.includes('fetch') || msg.includes('Failed to fetch') || msg.includes('NetworkError')) {
                    errorContent =
                        'Cannot connect to the GAIA Agent UI server. Make sure the backend is running:\n\n' +
                        '```\ngaia chat --ui\n```';
                } else if (msg.includes('500')) {
                    errorContent =
                        'The server encountered an error. This usually means Lemonade Server is not running or the model failed to load.\n\n' +
                        'Start Lemonade Server with:\n```\nlemonade-server serve\n```';
                } else {
                    errorContent = `Error: ${msg}`;
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
    }, [input, isStreaming, sessionId, session, addMessage, setStreaming, flushStreamBuffer, clearStreamContent, updateSessionInList, addAgentStep, updateLastAgentStep, clearAgentSteps]);

    // Delete a single message
    const handleDeleteMessage = useCallback(async (messageId: number) => {
        if (isStreaming) return;
        log.chat.info(`Deleting message ${messageId} from session=${sessionId}`);
        // Optimistic removal
        removeMessage(messageId);
        try {
            await api.deleteMessage(sessionId, messageId);
        } catch (err) {
            log.chat.error(`Failed to delete message ${messageId}`, err);
            // Reload messages on error to restore accurate state
            api.getMessages(sessionId)
                .then((data) => setMessages(data.messages || []))
                .catch(() => {});
        }
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
                    <span className="model-badge">{session?.model || 'Local LLM'}</span>
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
            {documents.length > 0 && (
                <button
                    className="doc-context-bar"
                    onClick={() => setShowDocLibrary(true)}
                    title="Click to manage documents"
                    aria-label={`${documents.length} indexed document${documents.length !== 1 ? 's' : ''}`}
                >
                    <FileText size={12} className="doc-context-icon" />
                    <span className="doc-context-label">
                        {documents.length} document{documents.length !== 1 ? 's' : ''} indexed
                    </span>
                    <span className="doc-context-names">
                        {documents.slice(0, 3).map((d) => d.filename).join(', ')}
                        {documents.length > 3 && ` +${documents.length - 3} more`}
                    </span>
                </button>
            )}

            {/* Messages */}
            <div className="messages-scroll" ref={messagesScrollRef} onScroll={handleScroll}>
                {isLoadingMessages && (
                    <div className="loading-spinner" aria-label="Loading messages" />
                )}

                {showEmptyState && (
                    <div className="empty-chat">
                        <div className="empty-chat-icon">
                            <MessageSquare size={36} strokeWidth={1.2} />
                        </div>
                        <h4 className="empty-chat-title">Start the conversation</h4>
                        <p className="empty-chat-desc">
                            Ask anything &mdash; your data stays on this device.
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

                {messages.map((msg) => (
                    <div key={msg.id}>
                        <MessageBubble
                            message={msg}
                            agentSteps={msg.role === 'assistant' ? msg.agentSteps : undefined}
                            onDelete={!isStreaming ? handleDeleteMessage : undefined}
                            onResend={!isStreaming && msg.role === 'user' ? handleResendMessage : undefined}
                        />
                    </div>
                ))}

                {/* Active streaming message with agent activity inside */}
                {isStreaming && (
                    <MessageBubble
                        message={{
                            id: -1,
                            session_id: sessionId,
                            role: 'assistant',
                            content: streamingContent || '',
                            created_at: '',
                            rag_sources: null,
                        }}
                        isStreaming
                        agentSteps={agentSteps}
                        agentStepsActive={true}
                    />
                )}
                <div ref={messagesEndRef} />
            </div>

            {/* Scroll to bottom */}
            {showScrollBtn && !isStreaming && (
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
                <div className="input-box">
                    <textarea
                        ref={inputRef}
                        className="msg-input"
                        value={input}
                        onChange={handleInputChange}
                        onKeyDown={handleKeyDown}
                        placeholder="Type a message... (Shift+Enter for new line)"
                        rows={1}
                        disabled={isStreaming}
                        aria-label="Message input"
                    />
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
                                disabled={!input.trim()}
                                title="Send (Enter)"
                                aria-label="Send message"
                            >
                                <Send size={16} />
                            </button>
                        )}
                    </div>
                </div>
                <div className="input-footer">
                    <Lock size={10} />
                    <span>Your data never leaves this device</span>
                </div>
            </div>
        </main>
    );
}
