// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Research mode: a turn sent as `/research <topic>`.
 *
 * The agent, not the app, turns it into a research turn (it loads the
 * research-report skill and asks for a cited report), so the Research switch
 * here and `/research` in the TUI behave the same.
 */
export const RESEARCH_COMMAND = '/research';

/** True when *text* already starts with the `/research` command. */
export function isResearchMessage(text: string): boolean {
    const trimmed = text.trimStart();
    if (!trimmed.toLowerCase().startsWith(RESEARCH_COMMAND)) return false;
    const next = trimmed.charAt(RESEARCH_COMMAND.length);
    return next === '' || /\s/.test(next);
}

/** The message to send: prefixed with `/research` while research mode is on. */
export function withResearchCommand(text: string, researchOn: boolean): string {
    if (!researchOn || !text.trim() || isResearchMessage(text)) return text;
    return `${RESEARCH_COMMAND} ${text}`;
}
