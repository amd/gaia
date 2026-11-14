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
    /** The setting on a tab the text names, so its button lands there rather than at the top. */
    targets: Partial<Record<SettingsSection, string>>;
}

/** Settings the server's remedies name by their label, and where each one lives in the app. */
const NAMED_SETTINGS: Array<{ re: RegExp; section: SettingsSection; target: string }> = [
    { re: /Settings\s*(?:>|›|→)\s*Advanced\s*(?:>|›|→)\s*Custom model ID/i, section: 'advanced', target: '[data-settings-target="custom-model"]' },
];

const START_PREFIX = 'Failed to start the query run:';
const RAW_RE = /^(?:[A-Za-z_.]*(?:Error|Exception):|Failed to (?:preload|load|auto-download)\b)|Traceback \(most recent/;
const RAW_MAX_CHARS = 1500;
// A remedy written for a terminal never reads as the answer in the app.
const COMMAND_RE = /\brun:\s|re-run your|`gaia [a-z]/i;

const MODEL_UNAVAILABLE_RE = /no model called|doesn't list (?:it|this model)|must include the .?user.? namespace|isn't installed on the local LLM server|\bmodel\b[^\n]{0,80}\bwas not found\b/i;
// Anchored on the model: "GAIA's assistant failed to load" is a setup fault, not a model one.
const MODEL_LOAD_RE = /\bmodel\b[^\n]{0,40}\b(?:couldn't|didn't|failed to) (?:pre)?load|failed to (?:pre)?load (?:lemonade )?model/i;
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
    const targets: FailedTurn['targets'] = {};
    for (const named of NAMED_SETTINGS) {
        if (sections.includes(named.section) && named.re.test(text)) targets[named.section] = named.target;
    }
    return { kind, raw, sections, targets };
}

/** The server's own words for Details, without the wrapper the app adds. */
export function failedTurnDetails(content: string): string {
    const text = content.trim();
    return text.startsWith(START_PREFIX) ? text.slice(START_PREFIX.length).trim() : text;
}
