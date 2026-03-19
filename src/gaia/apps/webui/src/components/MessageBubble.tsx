// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React, { useCallback, useRef, useState, useEffect, useMemo } from 'react';
import { Copy, Check, AlertTriangle, Trash2, RefreshCw, FolderOpen } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import rehypeRaw from 'rehype-raw';
import remarkGfm from 'remark-gfm';
import { AgentActivity } from './AgentActivity';
import * as api from '../services/api';
import { log } from '../utils/logger';
import gaiaRobot from '../assets/gaia-robot.png';
import type { Message, AgentStep } from '../types';
import './MessageBubble.css';

interface MessageBubbleProps {
    message: Message;
    isStreaming?: boolean;
    /** Show a solid terminal cursor at the end of the message (even when not streaming). */
    showTerminalCursor?: boolean;
    /** Agent steps to display inside this message bubble. */
    agentSteps?: AgentStep[];
    /** Whether agent steps are currently active (streaming). */
    agentStepsActive?: boolean;
    /** Called when user clicks the delete button. */
    onDelete?: (messageId: number) => void;
    /** Called when user clicks the resend button (user messages only). */
    onResend?: (message: Message) => void;
}



/** Immediate "Thinking..." with blinking dots while waiting for LLM. */
function LoadingMessage() {
    return (
        <div className="loading-message">
            <span>Thinking</span>
            <span className="thinking-dots"><span>.</span><span>.</span><span>.</span></span>
        </div>
    );
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

/**
 * Safety-net regex to strip raw tool-call JSON from rendered message content.
 *
 * Primary filtering happens server-side in sse_handler.py (see _TOOL_CALL_JSON_RE
 * and _TOOL_CALL_JSON_SUB_RE). This frontend regex is a secondary safety net for
 * messages that were persisted before the backend filter was in place, or in case
 * any tool-call JSON leaks through. Keep in sync with the server-side pattern.
 */
const TOOL_CALL_JSON_RE = /\s*\{\s*"?tool"?\s*:\s*"[^"]+"\s*,\s*"?tool_args"?\s*:\s*\{[^}]*\}\s*\}/g;

/**
 * Strip raw tool-call JSON and other LLM noise from message content.
 * LLMs sometimes emit the tool call as text before the agent framework
 * intercepts it. They also sometimes output trailing code fences,
 * thinking tags, or JSON thought blocks. This function cleans all of that.
 */
/**
 * Find and remove/extract LLM JSON blocks from output.
 * Detects {"thought":...}, {"answer":...}, {"tool":...} patterns.
 * Blocks with "tool"/"tool_args" are removed entirely.
 * Blocks with "answer" have the answer text extracted and kept.
 * Blocks with "thought" only are removed (shown in agent activity).
 */
function cleanLLMJsonBlocks(text: string): string {
    // Markers that indicate an LLM JSON block we should process
    const MARKERS = ['"thought"', '"answer"', '"tool"'];
    let result = '';
    let i = 0;

    while (i < text.length) {
        const braceIdx = text.indexOf('{', i);
        if (braceIdx === -1) { result += text.slice(i); break; }

        // Check if this brace starts an LLM JSON block
        const lookAhead = text.slice(braceIdx, braceIdx + 50);
        const isLLMBlock = MARKERS.some((m) => lookAhead.includes(m));
        if (!isLLMBlock) {
            result += text.slice(i, braceIdx + 1);
            i = braceIdx + 1;
            continue;
        }

        // Found a potential LLM JSON block — find matching closing brace
        result += text.slice(i, braceIdx);
        let depth = 0;
        let j = braceIdx;
        for (; j < text.length; j++) {
            if (text[j] === '{') depth++;
            else if (text[j] === '}') { depth--; if (depth === 0) break; }
        }
        if (depth !== 0) { break; } // suppress partial/unclosed JSON block

        const block = text.slice(braceIdx, j + 1);
        try {
            const parsed = JSON.parse(block);
            if (parsed.answer) {
                // Extract the answer content — this is the useful text
                result += parsed.answer;
            }
            // thought-only and tool/tool_args blocks are dropped silently
        } catch {
            // JSON.parse failed — LLM likely emitted literal newlines inside string
            // values (e.g. {"thought":"...", "goal":"...", "answer":"line1\nline2"}).
            // Find the "answer" field anywhere in the block and extract its content.
            const answerKeyIdx = block.indexOf('"answer"');
            if (answerKeyIdx !== -1) {
                const colonIdx = block.indexOf(':', answerKeyIdx + 8);
                if (colonIdx !== -1) {
                    let start = colonIdx + 1;
                    while (start < block.length && /\s/.test(block[start])) start++;
                    if (start < block.length && block[start] === '"') {
                        let content = block.slice(start + 1);
                        // Strip closing "} or trailing " from JSON envelope
                        if (content.endsWith('"}')) {
                            content = content.slice(0, -2);
                        } else if (content.endsWith('"')) {
                            content = content.slice(0, -1);
                        }
                        result += content.replace(/\\"/g, '"').replace(/\\n/g, '\n').replace(/\\\\/g, '\\');
                    }
                }
            }
            // Blocks without "answer" (thought-only, tool blocks) are dropped silently
        }
        i = j + 1;
    }
    return result;
}

/** Known programming language identifiers that should keep code block rendering. */
const KNOWN_CODE_LANGS = new Set([
    'python', 'py', 'javascript', 'js', 'typescript', 'ts', 'java', 'c', 'cpp',
    'csharp', 'cs', 'go', 'rust', 'ruby', 'rb', 'php', 'swift', 'kotlin',
    'scala', 'r', 'perl', 'lua', 'bash', 'sh', 'zsh', 'powershell', 'ps1',
    'sql', 'html', 'css', 'scss', 'sass', 'less', 'xml', 'json', 'yaml',
    'yml', 'toml', 'ini', 'csv', 'markdown', 'md', 'dockerfile', 'docker',
    'makefile', 'cmake', 'nginx', 'apache', 'graphql', 'proto', 'protobuf',
    'jsx', 'tsx', 'vue', 'svelte', 'dart', 'elixir', 'ex', 'erlang',
    'haskell', 'hs', 'ocaml', 'ml', 'fsharp', 'fs', 'clojure', 'clj',
    'lisp', 'scheme', 'racket', 'zig', 'nim', 'crystal', 'julia',
    'matlab', 'octave', 'fortran', 'cobol', 'pascal', 'delphi', 'ada',
    'assembly', 'asm', 'nasm', 'wasm', 'solidity', 'sol', 'verilog', 'vhdl',
    'text', 'txt', 'plaintext', 'diff', 'patch', 'log',
]);

/**
 * Strip bogus code fences from LLM output.
 *
 * Local LLMs (especially Qwen-Coder) frequently wrap prose responses in
 * fenced code blocks with fake 1-2 char "languages" like ```i or ```a.
 * This strips fences whose language tag is NOT a known programming language,
 * unwrapping the content back to plain markdown. Real code blocks
 * (```python, ```bash, etc.) are preserved.
 */
function stripBogusCodeFences(text: string): string {
    // Match fenced blocks: ```<lang>\n...\n```
    // Replace bogus ones (unknown lang) with just their inner content
    return text.replace(
        /```(\w*)[ \t]*\n([\s\S]*?)```/g,
        (_match, lang: string, inner: string) => {
            const langLower = lang.toLowerCase();
            // Keep fences with known code languages
            if (langLower && KNOWN_CODE_LANGS.has(langLower)) {
                return _match; // Preserve the original fenced block
            }
            // Strip the fence — return inner content as plain markdown
            return inner.trim();
        },
    );
}

function cleanToolCallContent(content: string): string {
    if (!content) return content;
    let cleaned = content;

    // Remove all tool-call JSON blocks from the content
    cleaned = cleaned.replace(TOOL_CALL_JSON_RE, '');

    // Remove/extract LLM JSON blocks (thought, answer, tool) from output.
    // These have nested braces so we use a brace-depth parser instead of regex.
    // Blocks with "answer" have their answer text extracted; others are removed.
    cleaned = cleanLLMJsonBlocks(cleaned);

    // Remove <think>...</think> tags that some models output
    cleaned = cleaned.replace(/<think>[\s\S]*?<\/think>/g, '');

    // Fix double-escaped newlines/tabs from LLM output.
    // Some models output literal "\n" (two chars) instead of actual newlines,
    // which breaks markdown rendering. Only unescape when there are many
    // literal \n sequences compared to real newlines (avoids breaking code blocks).
    const literalNewlines = (cleaned.match(/\\n/g) || []).length;
    const realNewlines = (cleaned.match(/\n/g) || []).length;
    if (literalNewlines > 2 && literalNewlines > realNewlines * 2) {
        cleaned = cleaned.replace(/\\n/g, '\n');
        cleaned = cleaned.replace(/\\"/g, '"');
    }

    // Strip bogus code fences AFTER all other cleaning — catches fences
    // that were inside JSON answer blocks or other wrappers. Only removes
    // fences with unknown/fake language tags; preserves real code blocks.
    cleaned = stripBogusCodeFences(cleaned).trim();

    return cleaned;
}

/** Format a timestamp as relative time ("2m ago") or absolute for older messages. */
function formatMsgTime(iso: string): string {
    if (!iso) return '';
    const d = new Date(iso);
    const now = new Date();
    const diff = now.getTime() - d.getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

export function MessageBubble({ message, isStreaming, showTerminalCursor, agentSteps, agentStepsActive, onDelete, onResend }: MessageBubbleProps) {
    const isError = message.role === 'assistant' && isErrorContent(message.content);
    // Memoize the expensive LLM content cleaning (brace-depth parser) so it
    // doesn't re-run on every render — only when message content changes.
    const cleanedContent = useMemo(
        () => cleanToolCallContent(message.content),
        [message.content],
    );
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
        if (navigator.clipboard?.writeText) {
            navigator.clipboard.writeText(message.content).catch(() => {});
        } else {
            // Fallback for non-HTTPS contexts (common for localhost)
            const textarea = document.createElement('textarea');
            textarea.value = message.content;
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
                    <div className="msg-header-left">
                        {message.role === 'assistant' && (
                            <>
                                <div className="msg-avatar msg-avatar-assistant" aria-hidden="true">
                                    <img src={gaiaRobot} alt="" />
                                </div>
                                <div className="msg-role role-assistant">GAIA</div>
                            </>
                        )}
                        {message.created_at && (
                            <span className="msg-timestamp">{formatMsgTime(message.created_at)}</span>
                        )}
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
                    {/* Loading message: typed by red cursor while waiting for LLM */}
                    {message.role === 'assistant' && isStreaming && !cleanedContent && (!agentSteps || agentSteps.length === 0) && (
                        <LoadingMessage />
                    )}
                    <RenderedContent content={cleanedContent} showCursor={(isStreaming || showTerminalCursor) && !!cleanedContent && !agentStepsActive} />
                    {message.role === 'assistant' && message.stats && !isStreaming && message.stats.tokens_per_second > 0 && (
                        <div className="msg-stats">
                            <span>{message.stats.tokens_per_second} tok/s</span>
                            <span>{message.stats.output_tokens} tokens</span>
                            {message.stats.time_to_first_token != null && (
                                <span>{(message.stats.time_to_first_token * 1000).toFixed(0)}ms TTFT</span>
                            )}
                        </div>
                    )}
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
// ── File Path Linkification ──────────────────────────────────────────────

/** Regex to detect Windows file paths like C:\Users\... or C:/Users/... */
const WIN_PATH_RE = /[A-Z]:[\\\/](?:[^\s*?"<>|,;)}\]]+[\\\/])*[^\s*?"<>|,;)}\]]*\.\w{1,5}/gi;
/** Regex to detect Windows directory paths like C:\Users\...\folder\ */
const WIN_DIR_RE = /[A-Z]:[\\\/](?:[^\s*?"<>|,;)}\]]+[\\\/])+/gi;

function FilePathLink({ path }: { path: string }) {
    const handleClick = (e: React.MouseEvent) => {
        e.preventDefault();
        api.openFileOrFolder(path).catch((err) => {
            log.ui.error('Failed to open path', err);
        });
    };
    return (
        <span
            className="file-path-link"
            onClick={handleClick}
            title={`Open in file explorer: ${path}`}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleClick(e as unknown as React.MouseEvent); } }}
        >
            <FolderOpen size={12} className="file-path-icon" />
            {path}
        </span>
    );
}

/** Split text into segments, replacing file paths with clickable links. */
function linkifyFilePaths(text: string): React.ReactNode {
    // Combine both regexes: match files first, then directories
    const combined = new RegExp(`(${WIN_PATH_RE.source}|${WIN_DIR_RE.source})`, 'gi');
    const parts: React.ReactNode[] = [];
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = combined.exec(text)) !== null) {
        // Add text before the match
        if (match.index > lastIndex) {
            parts.push(text.slice(lastIndex, match.index));
        }
        parts.push(<FilePathLink key={match.index} path={match[0]} />);
        lastIndex = combined.lastIndex;
    }

    // No paths found — return original text
    if (parts.length === 0) return text;

    // Add remaining text
    if (lastIndex < text.length) {
        parts.push(text.slice(lastIndex));
    }
    return <>{parts}</>;
}

/**
 * Recursively process React children, replacing string children with
 * linkified file paths. This is needed because react-markdown v9 does
 * not support a `text` component override.
 */
function linkifyChildren(children: React.ReactNode): React.ReactNode {
    return React.Children.map(children, (child) =>
        typeof child === 'string' ? linkifyFilePaths(child) : child
    );
}

function RenderedContent({ content, showCursor }: { content: string; showCursor?: boolean }) {
    if (!content && !showCursor) return null;
    if (!content && showCursor) return <span className="cursor" />;

    return (
        <div className="md-content">
            <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeRaw]}
                components={{
                    // Code block vs inline code detection.
                    // react-markdown calls `code` for both inline `code` and
                    // fenced ```code``` blocks. Fenced blocks are wrapped in
                    // <pre><code>, inline in just <code>. We use our `pre`
                    // override to render fenced blocks as CodeBlock, so the
                    // `code` component only handles inline code.
                    code({ className, children, ...props }) {
                        // If we get here, it's inline code (fenced blocks are
                        // handled by the `pre` override below).
                        return (
                            <code className="inline-code" {...props}>
                                {children}
                            </code>
                        );
                    },
                    // Fenced code blocks: react-markdown wraps them in <pre><code>.
                    // Extract the language and code text, render as CodeBlock.
                    pre({ children }) {
                        // children is <code className="language-xxx">...</code>
                        const codeChild = React.Children.toArray(children)[0];
                        if (React.isValidElement(codeChild) && (codeChild.type === 'code' || (codeChild.props as any)?.className !== undefined || typeof (codeChild.props as any)?.children === 'string')) {
                            const codeProps = codeChild.props as any;
                            const className = codeProps?.className || '';
                            const match = /language-(\w+)/.exec(className);
                            const codeString = String(codeProps?.children || '').replace(/\n$/, '');
                            return (
                                <CodeBlock
                                    lang={match?.[1] || ''}
                                    code={codeString}
                                />
                            );
                        }
                        // Fallback: render as-is
                        return <pre>{children}</pre>;
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
                    // Paragraphs — linkify file paths in text children
                    p({ children }) {
                        return <p className="md-p">{linkifyChildren(children)}</p>;
                    },
                    // Headers
                    h1({ children }) {
                        return <h2 className="md-h2">{linkifyChildren(children)}</h2>;
                    },
                    h2({ children }) {
                        return <h3 className="md-h3">{linkifyChildren(children)}</h3>;
                    },
                    h3({ children }) {
                        return <h4 className="md-h4">{linkifyChildren(children)}</h4>;
                    },
                    // Lists
                    ul({ children }) {
                        return <ul className="md-ul">{children}</ul>;
                    },
                    ol({ children }) {
                        return <ol className="md-ol">{children}</ol>;
                    },
                    li({ children }) {
                        return <li className="md-li">{linkifyChildren(children)}</li>;
                    },
                    // Blockquote
                    blockquote({ children }) {
                        return <blockquote className="md-blockquote">{linkifyChildren(children)}</blockquote>;
                    },
                    // Horizontal rule
                    hr() {
                        return <hr className="md-hr" />;
                    },
                    // Table cells
                    td({ children }) {
                        return <td>{linkifyChildren(children)}</td>;
                    },
                    th({ children }) {
                        return <th>{linkifyChildren(children)}</th>;
                    },
                }}
            >
                {content}
            </ReactMarkdown>
            {showCursor && <span className="cursor" />}
        </div>
    );
}
