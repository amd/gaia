// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useState } from 'react';
import { Copy, Check, AlertTriangle } from 'lucide-react';
import type { Message } from '../types';
import './MessageBubble.css';

interface MessageBubbleProps {
    message: Message;
    isStreaming?: boolean;
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
        lower.includes('is the gaia chat server running?') ||
        lower.includes('connection refused') ||
        lower.includes('failed to fetch')
    );
}

export function MessageBubble({ message, isStreaming }: MessageBubbleProps) {
    const isError = message.role === 'assistant' && isErrorContent(message.content);

    return (
        <div className={`msg msg-${message.role} ${isError ? 'msg-error' : ''}`}>
            <div className="msg-inner">
                <div className={`msg-role ${message.role === 'user' ? 'role-user' : 'role-assistant'}`}>
                    {message.role === 'user' ? 'You' : 'GAIA'}
                </div>
                <div className="msg-body">
                    {isError && (
                        <div className="error-banner">
                            <AlertTriangle size={14} />
                            <span>Something went wrong</span>
                        </div>
                    )}
                    <RenderedContent content={message.content} />
                    {isStreaming && <span className="cursor" />}
                </div>
            </div>
        </div>
    );
}

/** Minimal markdown renderer. */
function RenderedContent({ content }: { content: string }) {
    if (!content) return null;

    // Split on code blocks first
    const parts = content.split(/(```[\s\S]*?```)/g);

    return (
        <>
            {parts.map((part, i) => {
                if (part.startsWith('```')) {
                    return <CodeBlock key={i} raw={part} />;
                }
                return <InlineContent key={i} text={part} />;
            })}
        </>
    );
}

function CodeBlock({ raw }: { raw: string }) {
    const match = raw.match(/^```(\w*)\n?([\s\S]*?)```$/);
    const lang = match?.[1] || '';
    const code = match?.[2]?.trimEnd() || raw.slice(3, -3);

    const [copied, setCopied] = useState(false);

    const handleCopy = useCallback(() => {
        navigator.clipboard.writeText(code);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
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

function InlineContent({ text }: { text: string }) {
    // Convert markdown-ish to simple spans
    const lines = text.split('\n');

    return (
        <div className="inline-content">
            {lines.map((line, i) => (
                <InlineLine key={i} line={line} isLast={i === lines.length - 1} />
            ))}
        </div>
    );
}

function InlineLine({ line, isLast }: { line: string; isLast: boolean }) {
    if (!line.trim()) return isLast ? null : <br />;

    // Headers
    if (line.startsWith('### ')) return <h4 className="md-h4">{line.slice(4)}</h4>;
    if (line.startsWith('## ')) return <h3 className="md-h3">{line.slice(3)}</h3>;
    if (line.startsWith('# ')) return <h2 className="md-h2">{line.slice(2)}</h2>;

    // List items
    if (/^[*\-] /.test(line)) {
        return <div className="md-li">&bull; {renderInline(line.slice(2))}</div>;
    }
    if (/^\d+\. /.test(line)) {
        const num = line.match(/^(\d+)\./)?.[1];
        return <div className="md-li">{num}. {renderInline(line.replace(/^\d+\.\s*/, ''))}</div>;
    }

    return <p className="md-p">{renderInline(line)}</p>;
}

/** Render bold, italic, inline code, links. */
function renderInline(text: string): React.ReactNode {
    // Split on inline code first to avoid processing inside code
    const parts = text.split(/(`[^`]+`)/g);

    return parts.map((part, i) => {
        if (part.startsWith('`') && part.endsWith('`')) {
            return <code key={i} className="inline-code">{part.slice(1, -1)}</code>;
        }
        // Bold
        let processed: string = part;
        const elements: React.ReactNode[] = [];

        // Simple approach: just return text with bold/italic
        const boldParts = processed.split(/(\*\*[^*]+\*\*)/g);
        return boldParts.map((bp, j) => {
            if (bp.startsWith('**') && bp.endsWith('**')) {
                return <strong key={`${i}-${j}`}>{bp.slice(2, -2)}</strong>;
            }
            // Italic
            const italicParts = bp.split(/(\*[^*]+\*)/g);
            return italicParts.map((ip, k) => {
                if (ip.startsWith('*') && ip.endsWith('*')) {
                    return <em key={`${i}-${j}-${k}`}>{ip.slice(1, -1)}</em>;
                }
                return <span key={`${i}-${j}-${k}`}>{ip}</span>;
            });
        });
    });
}
