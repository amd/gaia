// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { SettingsSection } from '../stores/chatStore';

/** What a failed turn's card is headed with. */
export type FailedTurnKind = 'modelUnavailable' | 'modelLoad' | 'context' | 'unknown';

export interface FailedTurn {
    kind: FailedTurnKind;
    /** The saved text is a server error, not a sentence for the user: it goes under Details. */
    raw: boolean;
    /** Settings tabs the fix lives on, each offered as a button. */
    sections: SettingsSection[];
}

const START_PREFIX = 'Failed to start the query run:';
const RAW_RE = /^(?:[A-Za-z_.]*(?:Error|Exception):|Failed to (?:preload|load|auto-download)\b)|Traceback \(most recent/;
const RAW_MAX_CHARS = 1500;
// A remedy written for a terminal never reads as the answer in the app.
const COMMAND_RE = /\brun:\s|re-run your|`gaia [a-z]/i;

const MODEL_UNAVAILABLE_RE = /no model called|doesn't list (?:it|this model)|must include the .?user.? namespace|isn't installed on the local LLM server|\bmodel\b[^\n]{0,80}\bwas not found\b/i;
const MODEL_LOAD_RE = /couldn't load|failed to (?:pre)?load/i;
const CONTEXT_RE = /context (?:window|size|length|limit)|exceeds the available context|maximum context|too many tokens/i;

const SECTION_RE = /Settings\s*(?:>|›|→)\s*(General|Setup|Model|Permissions|Skills|Connectors|Memory|Privacy|Notifications|Advanced)\b/g;

function kindOf(text: string): FailedTurnKind {
    if (MODEL_UNAVAILABLE_RE.test(text)) return 'modelUnavailable';
    if (MODEL_LOAD_RE.test(text)) return 'modelLoad';
    if (CONTEXT_RE.test(text)) return 'context';
    return 'unknown';
}

/** How a failed turn's saved text is shown: its heading, whether it is raw, and where its fix is. */
export function describeFailedTurn(content: string): FailedTurn {
    const text = failedTurnDetails(content);
    const kind = kindOf(text);
    // Length-capped: a long answer that only mentions a command is not an error dump.
    const raw = RAW_RE.test(text) || (text.length < RAW_MAX_CHARS && COMMAND_RE.test(text));
    const sections: SettingsSection[] = kind === 'modelUnavailable' || kind === 'modelLoad' ? ['model'] : [];
    for (const match of text.matchAll(SECTION_RE)) {
        const section = match[1].toLowerCase() as SettingsSection;
        if (!sections.includes(section)) sections.push(section);
    }
    return { kind, raw, sections };
}

/** The server's own words for Details, without the wrapper the app adds. */
export function failedTurnDetails(content: string): string {
    const text = content.trim();
    return text.startsWith(START_PREFIX) ? text.slice(START_PREFIX.length).trim() : text;
}
