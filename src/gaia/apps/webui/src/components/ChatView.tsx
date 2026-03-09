// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useCallback, useState } from 'react';
import { Edit3, Paperclip, Download, Send, Upload, MessageSquare, Square, ArrowDown, Lock, FileText } from 'lucide-react';
import { MessageBubble } from './MessageBubble';
import { AgentActivity } from './AgentActivity';
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
                label: event.detail ? `Running command` : `Using tool`,
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
        sessions, messages, setMessages, addMessage, updateSessionInList,
        isStreaming, streamingContent, setStreaming, appendStreamContent, clearStreamContent,
        agentSteps, addAgentStep, updateLastAgentStep, clearAgentSteps,
        documents, setDocuments, setShowDocLibrary, isLoadingMessages, setLoadingMessages,
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

    // Load messages on mount
    useEffect(() => {
        log.chat.info(`ChatView mounted for session=${sessionId}, loading messages...`);
        const t = log.chat.time();
        setLoadingMessages(true);
        api.getMessages(sessionId)
            .then((data) => {
                const msgs = data.messages || [];
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

    // Auto-scroll
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages, streamingContent, agentSteps]);

    // Focus input
    useEffect(() => { inputRef.current?.focus(); }, [sessionId]);

    // Abort active stream when component unmounts (e.g., switching sessions)
    useEffect(() => {
        return () => {
            if (abortRef.current) {
                abortRef.current.abort();
                abortRef.current = null;
            }
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
        if (streamingContent) {
            log.stream.info(`Saving partial response (${streamingContent.length} chars)`);
            const assistantMsg: Message = {
                id: Date.now() + 1,
                session_id: sessionId,
                role: 'assistant',
                content: streamingContent,
                created_at: new Date().toISOString(),
                rag_sources: null,
                agentSteps: agentSteps.length > 0 ? [...agentSteps] : undefined,
            };
            addMessage(assistantMsg);
        }
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

        const controller = api.sendMessageStream(sessionId, text, {
            onChunk: (event) => {
                const content = event.content || '';
                if (content) {
                    fullContent += content;
                    appendStreamContent(content);
                }
            },
            onAgentEvent: (event) => {
                // Tool completion updates the last tool step
                if (event.type === 'tool_end') {
                    updateLastAgentStep({ active: false, success: event.success !== false });
                    return;
                }
                if (event.type === 'tool_result') {
                    updateLastAgentStep({
                        result: event.summary || event.title || 'Done',
                        active: false,
                        success: event.success !== false,
                    });
                    return;
                }
                // Filter out noisy status events - only show substantive activity
                if (event.type === 'status') {
                    return; // Status events are internal bookkeeping, not user-facing
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
    }, [input, isStreaming, sessionId, session, addMessage, setStreaming, appendStreamContent, clearStreamContent, updateSessionInList, addAgentStep, updateLastAgentStep, clearAgentSteps]);

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
                        {/* Show collapsed agent steps above assistant messages */}
                        {msg.role === 'assistant' && msg.agentSteps && msg.agentSteps.length > 0 && (
                            <AgentActivity
                                steps={msg.agentSteps}
                                isActive={false}
                                variant="summary"
                            />
                        )}
                        <MessageBubble message={msg} />
                    </div>
                ))}

                {/* Active agent activity during streaming */}
                {isStreaming && (
                    <>
                        <AgentActivity
                            steps={agentSteps}
                            isActive={true}
                            variant="inline"
                        />
                        {streamingContent && (
                            <MessageBubble
                                message={{
                                    id: -1,
                                    session_id: sessionId,
                                    role: 'assistant',
                                    content: streamingContent,
                                    created_at: '',
                                    rag_sources: null,
                                }}
                                isStreaming
                            />
                        )}
                    </>
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
