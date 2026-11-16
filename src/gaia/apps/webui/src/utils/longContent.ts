// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Collapse rules for long user messages, code blocks and step outputs. */

import { pluralKey, type MessageKey, type Params } from '../i18n/registry';

/** The part of a translator these helpers use; keeps this file free of the web UI's stores. */
type Translate = { bare: (key: MessageKey, params?: Params) => string };

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

export function lineCount(t: Translate, lines: number): string {
    return t.bare(pluralKey('size.lines', lines), { n: lines.toLocaleString('en-US') });
}

export function countLines(text: string): number {
    if (!text) return 0;
    return text.split('\n').length;
}

export function isLongUserMessage(text: string): boolean {
    return countLines(text) > USER_COLLAPSE_LINES || text.length > USER_COLLAPSE_CHARS;
}

/** Lines a collapsed user message keeps at each end. */
export const USER_EXCERPT_LINES = 3;
/** A kept line is cut past this, so one huge pasted line cannot fill the preview. */
export const USER_EXCERPT_LINE_CHARS = 300;

const clipLine = (line: string) => (line.length > USER_EXCERPT_LINE_CHARS ? `${line.slice(0, USER_EXCERPT_LINE_CHARS)}…` : line);

/**
 * The start and end of a long message, so a question typed after a pasted
 * block still shows; null when it is long in characters, not lines.
 */
export function messageExcerpt(text: string): { head: string; tail: string; hidden: number } | null {
    const lines = text.split('\n');
    if (lines.length <= USER_COLLAPSE_LINES) return null;
    return {
        head: lines.slice(0, USER_EXCERPT_LINES).map(clipLine).join('\n'),
        tail: lines.slice(-USER_EXCERPT_LINES).map(clipLine).join('\n'),
        hidden: lines.length - 2 * USER_EXCERPT_LINES,
    };
}

/** "700 lines", or "60,000 characters" when the length is in a few very long lines. */
export function sizeLabel(t: Translate, text: string, lineThreshold = USER_COLLAPSE_LINES): string {
    const lines = countLines(text);
    if (lines > lineThreshold) return lineCount(t, lines);
    return t.bare(pluralKey('size.characters', text.length), { n: text.length.toLocaleString('en-US') });
}
