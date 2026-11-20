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

/** The research-report skill's own `/research-report` is the same turn. */
const RESEARCH_COMMANDS = ['/research-report', RESEARCH_COMMAND];

/** True when *text* already starts with `/research` or `/research-report`. */
export function isResearchMessage(text: string): boolean {
    const trimmed = text.trimStart().toLowerCase();
    return RESEARCH_COMMANDS.some((command) => {
        if (!trimmed.startsWith(command)) return false;
        const next = trimmed.charAt(command.length);
        return next === '' || /\s/.test(next);
    });
}

/** The message to send: prefixed with `/research` while research mode is on. */
export function withResearchCommand(text: string, researchOn: boolean): string {
    if (!researchOn || !text.trim() || isResearchMessage(text)) return text;
    return `${RESEARCH_COMMAND} ${text}`;
}
