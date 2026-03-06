// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useCallback, useState } from 'react';
import { Edit3, Paperclip, Download, Send, Upload, MessageSquare, Square, ArrowDown, Lock } from 'lucide-react';
import { MessageBubble } from './MessageBubble';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import type { Message } from '../types';
import './ChatView.css';

const EMPTY_SUGGESTIONS = [
    'Summarize a document for me',
    'Write a Python script',
    'Explain a concept simply',
    'Help me brainstorm ideas',
];

interface ChatViewProps {
    sessionId: string;
}

export function ChatView({ sessionId }: ChatViewProps) {
    const {
        sessions, messages, setMessages, addMessage, updateSessionInList,
        isStreaming, streamingContent, setStreaming, appendStreamContent, clearStreamContent,
        setShowDocLibrary, isLoadingMessages, setLoadingMessages,
    } = useChatStore();

    const session = sessions.find((s) => s.id === sessionId);
    const [input, setInput] = useState('');
    const [editingTitle, setEditingTitle] = useState(false);
    const [titleDraft, setTitleDraft] = useState('');
    const [isDragOver, setIsDragOver] = useState(false);
    const [showScrollBtn, setShowScrollBtn] = useState(false);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const messagesScrollRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);
    const abortRef = useRef<AbortController | null>(null);

    // Load messages on mount
    useEffect(() => {
        setLoadingMessages(true);
        api.getMessages(sessionId)
            .then((data) => setMessages(data.messages || []))
            .catch(() => setMessages([]))
            .finally(() => setLoadingMessages(false));
    }, [sessionId, setMessages, setLoadingMessages]);

    // Listen for external send-prompt events (from WelcomeScreen suggestions)
    useEffect(() => {
        const handler = (e: Event) => {
            const prompt = (e as CustomEvent).detail?.prompt;
            if (prompt) {
                setInput(prompt);
                // Trigger send on next tick so input is set
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
    }, [messages, streamingContent]);

    // Focus input
    useEffect(() => { inputRef.current?.focus(); }, [sessionId]);

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
        if (abortRef.current) {
            abortRef.current.abort();
            abortRef.current = null;
        }
        if (streamingContent) {
            const assistantMsg: Message = {
                id: Date.now() + 1,
                session_id: sessionId,
                role: 'assistant',
                content: streamingContent,
                created_at: new Date().toISOString(),
                rag_sources: null,
            };
            addMessage(assistantMsg);
        }
        setStreaming(false);
        clearStreamContent();
    }, [sessionId, streamingContent, addMessage, setStreaming, clearStreamContent]);

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
        if (!text || isStreaming) return;

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

        let fullContent = '';
        let doneHandled = false;

        const controller = api.sendMessageStream(
            sessionId,
            text,
            // onChunk
            (event) => {
                if (event.content) {
                    fullContent += event.content;
                    appendStreamContent(event.content);
                }
            },
            // onDone
            (event) => {
                if (doneHandled) return;
                doneHandled = true;
                const content = event.content || fullContent;
                if (content) {
                    const assistantMsg: Message = {
                        id: event.message_id || Date.now() + 1,
                        session_id: sessionId,
                        role: 'assistant',
                        content,
                        created_at: new Date().toISOString(),
                        rag_sources: null,
                    };
                    addMessage(assistantMsg);
                }
                setStreaming(false);
                clearStreamContent();

                // Auto-title on first message
                if (session && session.title === 'New Chat') {
                    const autoTitle = text.slice(0, 50) + (text.length > 50 ? '...' : '');
                    api.updateSession(sessionId, { title: autoTitle })
                        .then(() => updateSessionInList(sessionId, { title: autoTitle }))
                        .catch(() => {});
                }
            },
            // onError
            (err) => {
                const errMsg: Message = {
                    id: Date.now() + 2,
                    session_id: sessionId,
                    role: 'assistant',
                    content: `Error: ${err.message}. Is the GAIA Chat server running?`,
                    created_at: new Date().toISOString(),
                    rag_sources: null,
                };
                addMessage(errMsg);
                setStreaming(false);
                clearStreamContent();
            },
        );

        abortRef.current = controller;
    }, [input, isStreaming, sessionId, session, addMessage, setStreaming, appendStreamContent, clearStreamContent, updateSessionInList]);

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
            a.download = `${session?.title || 'chat'}.md`;
            a.click();
            URL.revokeObjectURL(url);
        } catch (err) {
            console.error('Export failed:', err);
        }
    };

    // Drag & drop - upload files directly
    const handleDrop = useCallback(async (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragOver(false);
        if (e.dataTransfer.files.length > 0) {
            setShowDocLibrary(true);
            // Upload each dropped file by path (works in Electron where file.path is available)
            for (const file of Array.from(e.dataTransfer.files)) {
                const filepath = (file as any).path || file.name;
                try {
                    await api.uploadDocumentByPath(filepath);
                } catch (err) {
                    console.error('Failed to upload dropped file:', err);
                }
            }
        }
    }, [setShowDocLibrary]);

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragOver(true);
    };

    const handleDragLeave = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragOver(false);
    };

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
                    <MessageBubble key={msg.id} message={msg} />
                ))}
                {isStreaming && streamingContent && (
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
                {isStreaming && !streamingContent && (
                    <div className="typing-row">
                        <span className="typing-label">GAIA</span>
                        <div className="typing-dots">
                            <span /><span /><span />
                        </div>
                    </div>
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
