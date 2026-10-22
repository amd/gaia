// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Collapse rules for long user messages, code blocks and step outputs. The full text stays rendered and copyable. */

import React, { useId, useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import './LongContent.css';

/** A user message is collapsed past this many lines… */
export const USER_COLLAPSE_LINES = 15;
/** …or this many characters (one long pasted line has no newlines to count). */
export const USER_COLLAPSE_CHARS = 1500;
/** A code block is collapsed past this many lines, to a preview of CODE_PREVIEW_LINES. */
export const CODE_COLLAPSE_LINES = 25;
export const CODE_PREVIEW_LINES = 20;
/** A step output is capped past this many lines or characters. */
export const STEP_COLLAPSE_LINES = 25;
export const STEP_COLLAPSE_CHARS = 2500;

export function countLines(text: string): number {
    if (!text) return 0;
    return text.split('\n').length;
}

export function isLongUserMessage(text: string): boolean {
    return countLines(text) > USER_COLLAPSE_LINES || text.length > USER_COLLAPSE_CHARS;
}

/** "700 lines", or "60,000 characters" when the length is in a few very long lines. */
export function sizeLabel(text: string, lineThreshold = USER_COLLAPSE_LINES): string {
    const lines = countLines(text);
    if (lines > lineThreshold) return `${lines.toLocaleString('en-US')} lines`;
    return `${text.length.toLocaleString('en-US')} characters`;
}

interface ExpandToggleProps {
    expanded: boolean;
    onToggle: () => void;
    /** id of the region this button shows or hides. */
    controls: string;
    collapsedLabel: string;
    expandedLabel: string;
    className?: string;
}

export function ExpandToggle({ expanded, onToggle, controls, collapsedLabel, expandedLabel, className }: ExpandToggleProps) {
    return (
        <button
            type="button"
            className={`expand-toggle${className ? ` ${className}` : ''}`}
            onClick={onToggle}
            aria-expanded={expanded}
            aria-controls={controls}
        >
            {expanded ? <ChevronUp size={13} aria-hidden="true" /> : <ChevronDown size={13} aria-hidden="true" />}
            <span>{expanded ? expandedLabel : collapsedLabel}</span>
        </button>
    );
}

/** A tool output the agent shortened to its start and end (`elide_text` in tool_output.py). */
export interface TruncationEnvelope {
    head: string;
    tail: string;
    originalChars: number | null;
    omittedChars: number | null;
}

/** Undo JSON string escaping in a captured string body, dropping an escape cut in half. */
function decodeJsonString(body: string): string {
    const safe = body.replace(/\\(u[0-9a-fA-F]{0,3})?$/, '');
    try {
        return JSON.parse(`"${safe}"`) as string;
    } catch {
        return safe;
    }
}

function capturedNumber(text: string, key: string): number | null {
    const m = new RegExp(`"${key}"\\s*:\\s*(\\d+)`).exec(text);
    return m ? Number(m[1]) : null;
}

function capturedString(text: string, key: string): string {
    const m = new RegExp(`"${key}"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)`).exec(text);
    return m ? decodeJsonString(m[1]) : '';
}

/**
 * Read a truncation envelope out of a step result. A display cap can cut the
 * JSON short, so a result that opens like one is read field by field.
 */
export function parseTruncationEnvelope(text: string): TruncationEnvelope | null {
    const trimmed = text.trim();
    if (!trimmed.startsWith('{')) return null;
    let data: unknown;
    try {
        data = JSON.parse(trimmed);
    } catch {
        if (!/^\{\s*"truncated"\s*:\s*true\b/.test(trimmed) || !/"head"\s*:\s*"/.test(trimmed)) return null;
        return {
            head: capturedString(trimmed, 'head'),
            tail: capturedString(trimmed, 'tail'),
            originalChars: capturedNumber(trimmed, 'original_chars'),
            omittedChars: capturedNumber(trimmed, 'omitted_chars'),
        };
    }
    if (!data || typeof data !== 'object') return null;
    const env = data as Record<string, unknown>;
    if (env.truncated !== true || typeof env.head !== 'string') return null;
    return {
        head: env.head,
        tail: typeof env.tail === 'string' ? env.tail : '',
        originalChars: typeof env.original_chars === 'number' ? env.original_chars : null,
        omittedChars: typeof env.omitted_chars === 'number' ? env.omitted_chars : null,
    };
}

export function envelopeNote(env: TruncationEnvelope): string {
    const n = (v: number) => v.toLocaleString('en-US');
    const shown = env.tail
        ? `the first ${n(env.head.length)} and last ${n(env.tail.length)} characters`
        : `the first ${n(env.head.length)} characters`;
    const of = env.originalChars != null ? ` of ${n(env.originalChars)}` : '';
    const omitted = env.omittedChars != null ? ` (${n(env.omittedChars)} omitted)` : '';
    return `Showing ${shown}${of}${omitted}.`;
}

/**
 * A tool step's result. A truncation envelope shows as its plain head and tail;
 * a long output is capped to a preview and kept plain, since linking every path
 * in a command's output buries the output.
 */
export function StepResult({ text, renderShort }: { text: string; renderShort: (text: string) => React.ReactNode }) {
    const [expanded, setExpanded] = useState(false);
    const bodyId = useId();
    const envelope = parseTruncationEnvelope(text);
    const shown = envelope
        ? (envelope.tail ? `${envelope.head}\n…\n${envelope.tail}` : envelope.head)
        : text;
    const collapsible = countLines(shown) > STEP_COLLAPSE_LINES || shown.length > STEP_COLLAPSE_CHARS;
    return (
        <>
            {envelope && <div className="detail-result-note">{envelopeNote(envelope)}</div>}
            <div
                id={bodyId}
                className={`detail-result-content${collapsible && !expanded ? ' is-collapsed' : ''}`}
            >
                {envelope || collapsible ? shown : renderShort(shown)}
            </div>
            {collapsible && (
                <ExpandToggle
                    expanded={expanded}
                    onToggle={() => setExpanded((v) => !v)}
                    controls={bodyId}
                    collapsedLabel={`Expand (${sizeLabel(shown, STEP_COLLAPSE_LINES)})`}
                    expandedLabel="Collapse"
                    className="step-expand-toggle"
                />
            )}
        </>
    );
}
