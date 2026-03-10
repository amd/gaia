// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useRef, useState, useEffect } from 'react';
import { Copy, Check, AlertTriangle, Trash2, RefreshCw } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AgentActivity } from './AgentActivity';
import type { Message, AgentStep } from '../types';
import './MessageBubble.css';

interface MessageBubbleProps {
    message: Message;
    isStreaming?: boolean;
    /** Agent steps to display inside this message bubble. */
    agentSteps?: AgentStep[];
    /** Whether agent steps are currently active (streaming). */
    agentStepsActive?: boolean;
    /** Called when user clicks the delete button. */
    onDelete?: (messageId: number) => void;
    /** Called when user clicks the resend button (user messages only). */
    onResend?: (message: Message) => void;
}

/** Detect if message content looks like an error. */
function isErrorContent(content: string): boolean {
    if (!content) return false;
    const lower = content.toLowerCase();
    return (
        lower.startsWith('error:') ||
        lower.startsWith('error -') ||
        lower.includes('traceback (most recent') ||
        lower.includes("object has no attribute") ||
        lower.includes('is lemonade server running') ||
        lower.includes('connection refused') ||
        lower.includes('failed to fetch')
    );
}

/** Regex to detect raw tool-call JSON that LLMs sometimes output as text. */
const TOOL_CALL_JSON_RE = /^\s*\{"?\s*tool"?\s*:\s*"[^"]+"\s*,\s*"?tool_args"?\s*:\s*\{.*\}\s*\}\s*$/s;

/**
 * Strip raw tool-call JSON from message content.
 * LLMs sometimes emit the tool call as text before the agent framework
 * intercepts it. This function removes that noise from the displayed message.
 */
function cleanToolCallContent(content: string): string {
    if (!content) return content;
    // Remove lines that are raw tool-call JSON
    const cleaned = content.replace(TOOL_CALL_JSON_RE, '').trim();
    return cleaned;
}

export function MessageBubble({ message, isStreaming, agentSteps, agentStepsActive, onDelete, onResend }: MessageBubbleProps) {
    const isError = message.role === 'assistant' && isErrorContent(message.content);
    const [copied, setCopied] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState(false);
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
        navigator.clipboard.writeText(message.content).catch(() => {
            // Fallback: clipboard API may be unavailable in non-secure contexts
        });
        setCopied(true);
        if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
        copyTimerRef.current = setTimeout(() => setCopied(false), 2000);
    }, [message.content]);

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

    const handleResend = useCallback(() => {
        onResend?.(message);
    }, [message, onResend]);

    return (
        <div className={`msg msg-${message.role} ${isError ? 'msg-error' : ''}`}>
            <div className="msg-inner">
                <div className="msg-header">
                    <div className={`msg-role ${message.role === 'user' ? 'role-user' : 'role-assistant'}`}>
                        {message.role === 'user' ? 'You' : 'GAIA'}
                    </div>
                    {!isStreaming && (
                        <div className="msg-actions">
                            {/* Resend button - user messages only */}
                            {message.role === 'user' && onResend && (
                                <button
                                    className="msg-action-btn"
                                    onClick={handleResend}
                                    title="Resend message"
                                    aria-label="Resend message"
                                >
                                    <RefreshCw size={12} />
                                </button>
                            )}
                            <button
                                className={`msg-copy ${copied ? 'copied' : ''}`}
                                onClick={handleCopy}
                                title={copied ? 'Copied!' : 'Copy message'}
                                aria-label={copied ? 'Copied to clipboard' : 'Copy message'}
                            >
                                {copied ? <Check size={12} /> : <Copy size={12} />}
                            </button>
                            {/* Delete button */}
                            {onDelete && (
                                <button
                                    className={`msg-action-btn msg-delete ${confirmDelete ? 'confirm' : ''}`}
                                    onClick={handleDelete}
                                    title={confirmDelete ? 'Click again to confirm' : 'Delete message'}
                                    aria-label={confirmDelete ? 'Confirm delete message' : 'Delete message'}
                                >
                                    <Trash2 size={12} />
                                </button>
                            )}
                        </div>
                    )}
                </div>
                <div className="msg-body">
                    {/* Agent activity inside the message bubble */}
                    {agentSteps && agentSteps.length > 0 && (
                        <AgentActivity
                            steps={agentSteps}
                            isActive={agentStepsActive ?? false}
                            variant={agentStepsActive ? 'inline' : 'summary'}
                        />
                    )}
                    {isError && (
                        <div className="error-banner">
                            <AlertTriangle size={14} />
                            <span>Something went wrong</span>
                        </div>
                    )}
                    <RenderedContent content={cleanToolCallContent(message.content)} />
                    {isStreaming && <span className="cursor" />}
                </div>
            </div>
        </div>
    );
}

/** Custom code block with copy button. */
function CodeBlock({ lang, code }: { lang: string; code: string }) {
    const [copied, setCopied] = useState(false);
    const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        return () => {
            if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
        };
    }, []);

    const handleCopy = useCallback(() => {
        navigator.clipboard.writeText(code).catch(() => {
            // Fallback: clipboard API may be unavailable in non-secure contexts
        });
        setCopied(true);
        if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
        copyTimerRef.current = setTimeout(() => setCopied(false), 2000);
    }, [code]);

    return (
        <div className="code-block">
            <div className="code-header">
                <span className="code-lang">{lang || 'code'}</span>
                <button
                    className={`code-copy ${copied ? 'copied' : ''}`}
                    onClick={handleCopy}
                    title={copied ? 'Copied!' : 'Copy'}
                    aria-label={copied ? 'Copied to clipboard' : 'Copy code'}
                >
                    {copied ? <Check size={13} /> : <Copy size={13} />}
                    <span>{copied ? 'Copied' : 'Copy'}</span>
                </button>
            </div>
            <pre><code>{code}</code></pre>
        </div>
    );
}

/** Markdown renderer using react-markdown with GFM support. */
function RenderedContent({ content }: { content: string }) {
    if (!content) return null;

    return (
        <div className="md-content">
            <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                    // Custom code block rendering with copy button
                    code({ className, children, ...props }) {
                        const match = /language-(\w+)/.exec(className || '');
                        const codeString = String(children).replace(/\n$/, '');

                        // Detect block code: has a language class or contains newlines
                        if (match || (codeString.includes('\n') && !props.style)) {
                            return (
                                <CodeBlock
                                    lang={match?.[1] || ''}
                                    code={codeString}
                                />
                            );
                        }
                        // Inline code
                        return (
                            <code className="inline-code" {...props}>
                                {children}
                            </code>
                        );
                    },
                    // Wrap <pre> to avoid double-wrapping with our CodeBlock
                    pre({ children }) {
                        return <>{children}</>;
                    },
                    // Custom table styling
                    table({ children }) {
                        return (
                            <div className="md-table-wrap">
                                <table className="md-table">{children}</table>
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
                    // Paragraphs
                    p({ children }) {
                        return <p className="md-p">{children}</p>;
                    },
                    // Headers
                    h1({ children }) {
                        return <h2 className="md-h2">{children}</h2>;
                    },
                    h2({ children }) {
                        return <h3 className="md-h3">{children}</h3>;
                    },
                    h3({ children }) {
                        return <h4 className="md-h4">{children}</h4>;
                    },
                    // Lists
                    ul({ children }) {
                        return <ul className="md-ul">{children}</ul>;
                    },
                    ol({ children }) {
                        return <ol className="md-ol">{children}</ol>;
                    },
                    li({ children }) {
                        return <li className="md-li">{children}</li>;
                    },
                    // Blockquote
                    blockquote({ children }) {
                        return <blockquote className="md-blockquote">{children}</blockquote>;
                    },
                    // Horizontal rule
                    hr() {
                        return <hr className="md-hr" />;
                    },
                }}
            >
                {content}
            </ReactMarkdown>
        </div>
    );
}
