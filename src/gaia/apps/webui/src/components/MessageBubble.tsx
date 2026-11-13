// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React, { createContext, useCallback, useContext, useId, useLayoutEffect, useRef, useState, useEffect, useMemo } from 'react';
import type { Components } from 'react-markdown';
import { Copy, Check, AlertTriangle, Trash2, RefreshCw, RotateCw, Play, Pencil, FolderOpen, Square, FileText, ScrollText } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { SAFE_DISALLOWED_ELEMENTS, safeUrlTransform } from '../utils/markdown';
import remarkGfm from 'remark-gfm';
import remarkWindowsPaths from '../utils/remarkWindowsPaths';
import { remarkSoftBreaks } from '../utils/remarkSoftBreaks';
import { AgentActivity } from './AgentActivity';
import { EmailConnectCta, isAuthRequiredMessage } from './email/EmailConnectCta';
import { RenderCard } from './render/RenderCard';
import * as api from '../services/api';
import { log } from '../utils/logger';
import { AnswerStats, type TurnEnding } from './AnswerStats';
import { displayModelName, useInferencePlace, useModelNames } from '../stores/modelStore';
import { MODEL_WAIT_HINT_S, isModelWaitLabel } from '../utils/modelWait';
import { localizeLiveStatus } from '../utils/liveStatus';
import type { Message, AgentStep, RenderCardData } from '../types';
import { TurnChanges } from './TurnChanges';
import { ReadAloudButton, ReadAloudError, ReadAloudNote } from './ReadAloudButton';
import { TroubleshootingReport, namesFailureRecord, type ReportClasses } from './TroubleshootingReport';
import { CODE_COLLAPSE_LINES, CODE_PREVIEW_LINES, ExpandToggle, countLines, isLongUserMessage, lineCount, sizeLabel } from './LongContent';
import { dateLocale, useT, type Translate } from '../i18n/useT';
import type { MessageKey } from '../i18n/registry';
import { useChatStore } from '../stores/chatStore';
import { useLogsStore } from '../stores/logsStore';
import { CLAIMS_ESCAPE } from '../hooks/useEscapeToClose';
import { dropAnswerDrafts } from '../utils/answerDrafts';
import {
    buildCitationIndex, docForToken, findCitations, findPages,
    type CitationIndex, type FoundPage, type LibraryDoc,
} from '../utils/citations';
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

/** The answer an agent writes when a model call ends its turn (`_model_failure_answer` in agent.py). */
const MODEL_FAILURE_RE = /^The model (?:call failed|stopped responding) at step \d+, so this request is unfinished\./;

export function isModelFailure(content: string): boolean {
    return MODEL_FAILURE_RE.test(content.trimStart());
}

const CHAT_REPORT_CLASSES: ReportClasses = {
    root: 'msg-report',
    button: 'msg-try-again',
    hint: 'msg-report-hint',
    error: 'msg-report-error',
};

/** Detect if message content looks like an error. */
export function isErrorContent(content: string): boolean {
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

const squash = (text: string) => text.replace(/\s+/g, ' ').trim();

/**
 * Drop an error step the answer is, or ends with, word for word. A run that
 * fails before answering saves its error as the whole answer (`echoed`); one
 * that dies mid-answer saves what it wrote, then the error, which then reads
 * below that text rather than above it. An answer that only quotes a tool's
 * error keeps the step, as does one ending with an error that isn't the
 * turn's last step.
 */
export function dropErrorsEchoedInAnswer(steps: AgentStep[], content: string): { steps: AgentStep[]; echoed: boolean } {
    const answer = squash(content);
    if (!answer) return { steps, echoed: false };
    const tail = content.trimEnd();
    const last = steps[steps.length - 1];
    let echoed = false;
    const kept = steps.filter((s) => {
        if (s.type !== 'error' || !s.detail?.trim()) return true;
        if (squash(s.detail) === answer) {
            echoed = true;
            return false;
        }
        // The server appends the error that ended the turn as its own paragraph.
        return s !== last || !tail.endsWith(`\n\n${s.detail.trim()}`);
    });
    return { steps: kept, echoed };
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
 * Keys that belong to the agent's JSON envelope. An object made up only of
 * these is machinery; an object that mixes them with anything else is content.
 */
const ENVELOPE_KEYS = new Set(['thought', 'answer', 'tool', 'tool_args', 'goal']);

/** An envelope always opens with one of its own keys — cheap gate before parsing. */
const ENVELOPE_OPENER_RE = /^\{\s*"(thought|answer|tool|tool_args|goal)"\s*:/;

/** Index of the `}` matching the `{` at `start`, or -1 if unbalanced. Braces inside JSON strings don't count. */
function findMatchingBrace(text: string, start: number): number {
    let depth = 0;
    let inString = false;
    let escaped = false;
    for (let i = start; i < text.length; i++) {
        const ch = text[i];
        if (inString) {
            if (escaped) escaped = false;
            else if (ch === '\\') escaped = true;
            else if (ch === '"') inString = false;
            continue;
        }
        if (ch === '"') inString = true;
        else if (ch === '{') depth++;
        else if (ch === '}') {
            depth--;
            if (depth === 0) return i;
        }
    }
    return -1;
}

/**
 * Pull the `answer` string out of a block JSON.parse rejected — models emit
 * literal newlines inside string values, which is not valid JSON.
 * Returns null when the block carries no `answer` field.
 */
function extractAnswerField(block: string): string | null {
    const answerKeyIdx = block.indexOf('"answer"');
    if (answerKeyIdx === -1) return null;
    const colonIdx = block.indexOf(':', answerKeyIdx + 8);
    if (colonIdx === -1) return null;
    let start = colonIdx + 1;
    while (start < block.length && /\s/.test(block[start])) start++;
    if (start >= block.length || block[start] !== '"') return null;
    let content = block.slice(start + 1);
    if (content.endsWith('"}')) content = content.slice(0, -2);
    else if (content.endsWith('"')) content = content.slice(0, -1);
    return content.replace(/\\"/g, '"').replace(/\\n/g, '\n').replace(/\\\\/g, '\\');
}

type EnvelopeVerdict =
    | { action: 'drop' }
    | { action: 'answer'; text: string }
    | { action: 'keep' };

/**
 * Decide what a `{...}` region is. Only a complete agent envelope is removed or
 * unwrapped; anything a user or model could have written as a JSON example —
 * including one with a `tool` key — is kept verbatim.
 */
function classifyEnvelope(block: string): EnvelopeVerdict {
    let parsed: unknown;
    try {
        parsed = JSON.parse(block);
    } catch {
        const answer = extractAnswerField(block);
        if (answer !== null) return { action: 'answer', text: answer };
        // A malformed tool call is still machinery; anything else stays visible.
        if (block.includes('"tool_args"')) return { action: 'drop' };
        return { action: 'keep' };
    }

    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return { action: 'keep' };
    }
    const obj = parsed as Record<string, unknown>;
    const keys = Object.keys(obj);
    const envelopeOnly = keys.length > 0 && keys.every((k) => ENVELOPE_KEYS.has(k));

    if (typeof obj.tool === 'string' && obj.tool_args !== undefined) return { action: 'drop' };
    if (typeof obj.answer === 'string' && envelopeOnly) return { action: 'answer', text: obj.answer };
    if (typeof obj.thought === 'string' && obj.answer === undefined && envelopeOnly) {
        return { action: 'drop' };
    }
    return { action: 'keep' };
}

/**
 * Remove agent JSON envelopes from assistant output, keeping their `answer` text.
 *
 * Primary filtering is server-side (sse_handler.py); this is the safety net for
 * history persisted before that filter existed. A finished message is never
 * truncated: an unbalanced `{` (a shell one-liner, a code snippet) emits the
 * remaining text verbatim. Only while streaming is an unclosed envelope held
 * back, so a tool call does not flash on screen before its closing brace lands.
 */
function cleanLLMJsonBlocks(text: string, streaming: boolean): string {
    let result = '';
    let i = 0;

    while (i < text.length) {
        const braceIdx = text.indexOf('{', i);
        if (braceIdx === -1) {
            result += text.slice(i);
            break;
        }
        if (!ENVELOPE_OPENER_RE.test(text.slice(braceIdx, braceIdx + 40))) {
            result += text.slice(i, braceIdx + 1);
            i = braceIdx + 1;
            continue;
        }
        const closeIdx = findMatchingBrace(text, braceIdx);
        if (closeIdx === -1) {
            result += streaming ? text.slice(i, braceIdx) : text.slice(i);
            break;
        }
        result += text.slice(i, braceIdx);

        const block = text.slice(braceIdx, closeIdx + 1);
        const verdict = classifyEnvelope(block);
        if (verdict.action === 'answer') result += verdict.text;
        else if (verdict.action === 'keep') result += block;
        i = closeIdx + 1;
    }
    return result;
}

/**
 * Real language tags. Only consulted for 1-2 character tags, so genuine short
 * languages (c, r, go, js) keep their fence while a stray ```i does not.
 */
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
    // Retired fence-payload tags (#2109 cutover): cards now arrive via
    // tool_result.render, never fences. Kept ONLY so pre-cutover session
    // history shows those payloads as fenced JSON code blocks, not bare prose.
    'email_pre_scan', 'email-pre-scan',
]);

/**
 * Unwrap fences whose language tag is obvious garbage.
 *
 * Local LLMs (especially Qwen-Coder) sometimes wrap prose in a fence tagged
 * with a single stray letter (```i, ```a). Only those are unwrapped: a tag of
 * 1-2 characters that is not a real language. Every other tag keeps its fence,
 * including an empty one — a tag list can never keep up with what people write
 * (shell, console, mermaid, jsonc, env, hcl), and unwrapping a real block turns
 * `# comment` into a heading and swallows `<tag>`.
 */
function stripBogusCodeFences(text: string): string {
    return text.replace(
        /```(\w*)[ \t]*\n([\s\S]*?)```/g,
        (match, lang: string, inner: string) => {
            const langLower = lang.toLowerCase();
            const isStrayLetter =
                langLower.length > 0 && langLower.length <= 2 && !KNOWN_CODE_LANGS.has(langLower);
            return isStrayLetter ? inner.trim() : match;
        },
    );
}

/**
 * A trailing "Verification: unverified ..." line is the agent's no-op scope
 * note on a turn that ran no checks — the same disclaimer under every chat
 * answer. "verified" / "partially verified" lines report real check results
 * and stay. Mirrors the TUI's StripVerificationScope.
 */
const UNVERIFIED_SCOPE_RE = /\n{1,2}Verification: unverified[^\n]*\s*$/;

function stripUnverifiedScope(content: string): string {
    let text = content;
    for (;;) {
        const stripped = text.replace(UNVERIFIED_SCOPE_RE, '');
        if (stripped === text) return text;
        text = stripped;
    }
}

export function cleanToolCallContent(content: string, streaming = false): string {
    if (!content) return content;
    content = stripUnverifiedScope(content);
    let cleaned = content;

    // Remove all tool-call JSON blocks from the content
    cleaned = cleaned.replace(TOOL_CALL_JSON_RE, '');

    // Remove/extract LLM JSON blocks (thought, answer, tool) from output.
    // These have nested braces so we use a brace-depth parser instead of regex.
    // Blocks with "answer" have their answer text extracted; others are removed.
    cleaned = cleanLLMJsonBlocks(cleaned, streaming);

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

    return escapeBareNumberLines(cleaned);
}

/**
 * A line that is only a number and a period ("391.") parses as an empty ordered
 * list item, which shows the marker and hides the number. Escaping the period
 * keeps it as text; fenced code is left alone.
 */
export function escapeBareNumberLines(text: string): string {
    let inFence = false;
    return text.split('\n').map((line) => {
        if (/^\s*(```|~~~)/.test(line)) inFence = !inFence;
        if (inFence) return line;
        return line.replace(/^(\s*\d+)\.(\s*)$/, '$1\\.$2');
    }).join('\n');
}

/** Format a full absolute timestamp for the stats tooltip. */
function formatFullTimestamp(iso: string, t: Translate): string {
    if (!iso) return '';
    return new Date(iso).toLocaleString(dateLocale(t), {
        month: 'short', day: 'numeric', year: 'numeric',
        hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true,
    });
}

/** What the server persists for a turn stopped before it wrote any text (_empty_answer_outcome). */
const SERVER_CANCELLED_TEXT = 'Cancelled.';

/** The notices that close a turn cut off before its answer (gaia/ui/restart_recovery.py), and their translations. */
const CUT_OFF_NOTICES: ReadonlyArray<readonly [string, MessageKey]> = [
    ['This answer was interrupted when GAIA closed. Send again to retry.', 'msg.cutOff.interrupted'],
    ['This answer was interrupted before it finished. Send again to retry.', 'msg.cutOff.cancelled'],
    ['This answer ended before GAIA could save a reply. Send again to retry.', 'msg.cutOff.noReply'],
];

/** restart_recovery.tools_ran_notice, the line the server puts just above a cut-off notice. */
const TOOLS_RAN_RE = /(?:^|\n\n)Before it stopped, it had started these tools: (.+)\.$/;

export interface CutOff {
    /** What the turn streamed before it was cut off. */
    body: string;
    notice: MessageKey;
    tools: string | null;
}

/** A reply the server closed with a cut-off notice, split so the notice shows in the UI's language. */
export function splitCutOff(content: string): CutOff | null {
    let text = content.trim();
    const hit = CUT_OFF_NOTICES.find(([notice]) => text.endsWith(notice));
    if (!hit) return null;
    text = text.slice(0, -hit[0].length).trimEnd();
    const tools = TOOLS_RAN_RE.exec(text);
    if (tools) text = text.slice(0, tools.index).trimEnd();
    return { body: text, notice: hit[1], tools: tools ? tools[1] : null };
}

/** How an assistant turn ended short of an answer, or undefined when it answered. */
export function turnEnding(content: string, stopState: Message['stopState'], failed = false): TurnEnding | undefined {
    if (stopState) return 'stopped';
    if (splitCutOff(content)) return 'interrupted';
    return failed ? 'failed' : undefined;
}

const EDITOR_MAX_HEIGHT = 320;

/** A sent message opened for editing: Enter sends it again, Escape puts it back. */
function MessageEditor({ original, draft, onDraft, onSend, onCancel }: {
    original: string;
    draft?: string | null;
    onDraft?: (text: string) => void;
    onSend: (text: string) => void;
    onCancel: () => void;
}) {
    const t = useT();
    const [text, setText] = useState(draft ?? original);
    const ref = useRef<HTMLTextAreaElement>(null);
    const changed = text.trim() !== '' && text !== original;

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
            if (changed) onSend(text);
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
                rows={1}
                dir="auto"
                {...CLAIMS_ESCAPE}
            />
            <div className="msg-edit-actions">
                <button type="button" className="btn-secondary msg-edit-btn" onClick={onCancel}>{t('msg.editCancel')}</button>
                <button type="button" className="btn-primary msg-edit-btn" onClick={() => onSend(text)} disabled={!changed}>
                    {t('msg.editSend')}
                </button>
            </div>
        </div>
    );
}

export function MessageBubble({ message, isStreaming, agentSteps, agentStepsActive, cards, onDelete, onResend, extraActions, onTryAgain, onContinue, onRegenerate, onEditStart, editing = false, editDraft = null, onEditDraft, onEdit, onEditCancel, laterCount = 0, latest = false, latencyMs, liveStatus, toolStatus, awaitingUser }: MessageBubbleProps) {
    const t = useT();
    useModelNames();
    const echo = useMemo(() => {
        if (message.role !== 'assistant' || !agentSteps) return { steps: agentSteps, echoed: false };
        const steps = dropAnswerDrafts(agentSteps, message.content);
        return isStreaming ? { steps, echoed: false } : dropErrorsEchoedInAnswer(steps, message.content);
    }, [agentSteps, message.content, message.role, isStreaming]);
    const isError = message.role === 'assistant' && (echo.echoed || message.failed === true || isErrorContent(message.content));
    const place = useInferencePlace();
    const showServerLog = useLogsStore((s) => s.show);
    const stopState = message.role === 'assistant'
        ? message.stopState ?? (message.content.trim() === SERVER_CANCELLED_TEXT ? 'stopped' : undefined)
        : undefined;
    const cutOff = useMemo(
        () => (message.role === 'assistant' && !isStreaming ? splitCutOff(message.content) : null),
        [message.content, message.role, isStreaming],
    );
    // What the user typed is never agent output — render it verbatim.
    // Memoized because the assistant path runs a brace-depth parser.
    const cleanedContent = useMemo(
        () => (message.role === 'user'
            ? message.content
            : cleanToolCallContent(cutOff ? cutOff.body : message.content, Boolean(isStreaming))),
        [message.content, message.role, isStreaming, cutOff],
    );
    const hasAnswerText = Boolean(cleanedContent.trim()) && !(stopState && cleanedContent.trim() === SERVER_CANCELLED_TEXT);
    const summaryFiles = message.turnSummary?.files;
    const writtenFiles = useMemo(() => summaryFiles?.map((f) => f.path) ?? [], [summaryFiles]);
    const readAloudId = `${message.session_id}:${message.id}`;
    const [copied, setCopied] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const collapsibleUser = message.role === 'user' && isLongUserMessage(message.content);
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
                    {isError && (
                        <div className="error-banner">
                            <AlertTriangle size={14} />
                            <span>{t('msg.error')}</span>
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
                        />
                    ) : collapsibleUser ? (
                        <>
                            <div
                                ref={userBodyRef}
                                id={userBodyId}
                                className={`msg-collapsible${userExpanded ? '' : ' is-collapsed'}`}
                            >
                                <RenderedContent content={cleanedContent} />
                            </div>
                            <ExpandToggle
                                expanded={userExpanded}
                                onToggle={toggleUserExpanded}
                                controls={userBodyId}
                                collapsedLabel={t.hint(t('msg.showAll'), sizeLabel(t, message.content))}
                                expandedLabel={t('msg.showLess')}
                                className="msg-expand-toggle"
                            />
                        </>
                    ) : !(stopState && cleanedContent.trim() === SERVER_CANCELLED_TEXT) && (
                        <RenderedContent content={cleanedContent} showCursor={isStreaming && !!cleanedContent && !agentStepsActive} isStreaming={isStreaming} writtenFiles={writtenFiles} />
                    )}
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
                    {/* A local model failed: what Lemonade and its backend wrote says why. */}
                    {(isError || (message.role === 'assistant' && isModelFailure(message.content))) && !isStreaming && place?.remote === false && (
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
                                <span className="msg-stats-ts" title={t('msg.timestamp')}>
                                    {formatFullTimestamp(message.created_at, t)}
                                </span>
                            )}
                            {message.stats?.retried && message.stats.model && (
                                <span title={`This answer was kept from Retry with… (${message.stats.model})`}>{displayModelName(message.stats.model)}</span>
                            )}
                            <AnswerStats latencyMs={latencyMs} stats={message.stats} ended={turnEnding(message.content, stopState, isError)} />
                        </div>
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

/** Regex to detect Windows file paths like C:\Users\... or C:/Users/... */
// Path separators are excluded from the segment class so each `(segment sep)`
// repetition has exactly one parse — the ambiguity CodeQL flagged as
// exponential-backtracking ReDoS (js/redos) on adversarial non-matching input.
// Exactly one separator between segments, so a last segment without an
// extension (`C:\Users\me\notes`) is kept as well as `...\a.txt` or `...\dir\`.
// A space stays inside a folder name a `\` follows (`Program Files (x86)\`) and
// inside a last name of at most three words ending in an extension (`Q3 report.txt`);
// otherwise it ends the path, so "C:\out\docs and look at a.pdf" stops at `docs`.
// A path in double quotes runs to the closing quote, spaces and all.
const WIN_PATH_RE = /(?<=["\u201c])[A-Z]:[\\/][^*?"<>|\r\n\u201c\u201d]+(?=["\u201d])|\b[A-Z]:[\\/](?=[^\s*?"<>|,;)}\]\\/])(?:[^\s*?"<>|,;)}\]\\/]+(?:(?: +(?:[^\s*?"<>|,;:)}\]\\/]+|\([^\s*?"<>|,;:)}\]\\/]+\)))+\\|[\\/]))*(?:(?:[^\s*?"<>|,;:.)}\]\\/]+ +){1,2}[^\s*?"<>|,;)}\]\\/]+\.[A-Za-z0-9]{1,5}\b|[^\s*?"<>|,;)}\]\\/]+)?/gi;
/** Sentence punctuation the path regex swallows: "see C:\a\b." ends at `b`. */
const PATH_TRAILING_PUNCT_RE = /[.:!']+$/;
/** An inline-code span that is one absolute path and nothing else, e.g. "Saved to `C:\out\answer.md`". */
const WIN_PATH_ONLY_RE = new RegExp(`^(?:${WIN_PATH_RE.source})$`, 'i');

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
            title={`Open in file explorer: ${path}`}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleClick(e as unknown as React.MouseEvent); } }}
        >
            <FolderOpen size={12} className="file-path-icon" />
            {label ?? path}
        </span>
    );
}

/** Each Windows path in `text`; a quoted one is kept exactly, a trailing dot and all. */
function findFilePaths(text: string): Array<{ start: number; path: string }> {
    return [...text.matchAll(new RegExp(WIN_PATH_RE.source, 'gi'))].map((m) => {
        const end = m.index + m[0].length;
        const quoted = '"“'.includes(text[m.index - 1] ?? '\n') && '"”'.includes(text[end] ?? '\n');
        return { start: m.index, path: quoted ? m[0] : m[0].replace(PATH_TRAILING_PUNCT_RE, '') };
    });
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

/** A cited library document; opens at `page` (the first page cited after it, when there is one). */
function CitationLink({ doc, page, t }: { doc: LibraryDoc; page?: number; t: Translate }) {
    return (
        <a
            className="citation-link"
            href={api.documentFileUrl(doc.id, page)}
            target="_blank"
            rel="noopener noreferrer"
            dir="ltr"
            title={page ? t('citation.openPage', { page }) : t('citation.open', { name: doc.filename })}
            data-doc-id={doc.id}
            data-page={page}
        >
            <FileText size={12} className="citation-icon" aria-hidden="true" />
            {doc.filename}
        </a>
    );
}

function PageLink({ doc, page, label, t }: { doc: LibraryDoc; page: number; label: string; t: Translate }) {
    return (
        <a
            className="citation-page"
            href={api.documentFileUrl(doc.id, page)}
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
function citeText(text: string, index: CitationIndex, t: Translate, carried: LibraryDoc | null): React.ReactNode {
    // A filename that ends a longer path stays inside that path's chip.
    const paths = findFilePaths(text);
    const citations = findCitations(index, text).filter(
        (c) => !paths.some((p) => p.start < c.start && c.start < p.start + p.path.length),
    );
    const parts: Array<{ start: number; end: number; node: React.ReactNode }> = [];
    const addPages = (doc: LibraryDoc, pages: FoundPage[], offset: number) => {
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
function inlineCodeDoc(index: CitationIndex, children: React.ReactNode): LibraryDoc | null {
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
    let carried: LibraryDoc | null = null;
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
    // Table cells
    td({ children }) {
        return <td dir="auto"><Linkified>{children}</Linkified></td>;
    },
    th({ children }) {
        return <th dir="auto"><Linkified>{children}</Linkified></th>;
    },
};

const NO_WRITTEN_FILES: readonly string[] = [];

export function RenderedContent({ content, showCursor, isStreaming = false, writtenFiles = NO_WRITTEN_FILES }: { content: string; showCursor?: boolean; isStreaming?: boolean; writtenFiles?: readonly string[] }) {
    const documents = useChatStore((s) => s.documents);
    const citations = useMemo(() => buildCitationIndex(documents), [documents]);
    if (!content && !showCursor) return null;
    if (!content && showCursor) return <span className="cursor" />;

    return (
        <StreamingContext.Provider value={isStreaming}>
        <CitationContext.Provider value={citations}>
        <WrittenFilesContext.Provider value={writtenFiles}>
        <div className="md-content">
            <ReactMarkdown
                remarkPlugins={[remarkGfm, remarkWindowsPaths, remarkSoftBreaks]}
                disallowedElements={[...SAFE_DISALLOWED_ELEMENTS]}
                urlTransform={safeUrlTransform}
                components={MARKDOWN_COMPONENTS}
            >
                {content}
            </ReactMarkdown>
            {showCursor && <span className="cursor" />}
        </div>
        </WrittenFilesContext.Provider>
        </CitationContext.Provider>
        </StreamingContext.Provider>
    );
}
