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

/** `/<name>` at the start, followed by space or the end: the shape the agent reads as a skill command. */
const SKILL_COMMAND = /^\/([a-z0-9]+(?:-[a-z0-9]+)*)(?:\s|$)/;

let installedSkills: ReadonlySet<string> = new Set();

/** Record the installed skills, so research mode leaves their `/<name>` commands alone. */
export function rememberInstalledSkills(names: Iterable<string>): void {
    installedSkills = new Set(names);
}

/** True when *text* starts with `/<name>` and *name* is an installed skill. */
export function isSkillCommand(text: string, skills: ReadonlySet<string>): boolean {
    const match = SKILL_COMMAND.exec(text.trimStart());
    return match !== null && skills.has(match[1]);
}

/**
 * The message to send: prefixed with `/research` while research mode is on,
 * unless it already is a research command or runs an installed skill.
 */
export function withResearchCommand(
    text: string,
    researchOn: boolean,
    skills: ReadonlySet<string> = installedSkills,
): string {
    if (!researchOn || !text.trim() || isResearchMessage(text) || isSkillCommand(text, skills)) return text;
    return `${RESEARCH_COMMAND} ${text}`;
}
