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

/** `/<name>` at the start, followed by space or the end: the shape the agent reads as a skill command, in any case. */
const SKILL_COMMAND = /^\/([a-z0-9]+(?:-[a-z0-9]+)*)(?=\s|$)/i;

/** null until the list has been read: an unread list is not "no skills". */
let installedSkills: ReadonlySet<string> | null = null;

/** Record the installed skills, so research mode leaves their `/<name>` commands alone. */
export function rememberInstalledSkills(names: Iterable<string>): void {
    installedSkills = new Set(Array.from(names, (name) => name.toLowerCase()));
}

/** Back to "not read yet", for tests. */
export function forgetInstalledSkills(): void {
    installedSkills = null;
}

/** *text* with its `/<name>` in the lowercase the agent reads, when *name* is an installed skill; else null. */
function asSkillCommand(text: string, skills: ReadonlySet<string>): string | null {
    const trimmed = text.trimStart();
    const name = SKILL_COMMAND.exec(trimmed)?.[1];
    if (name === undefined || !skills.has(name.toLowerCase())) return null;
    return `/${name.toLowerCase()}${trimmed.slice(name.length + 1)}`;
}

/** True when *text* starts with `/<name>` and *name* is an installed skill. */
export function isSkillCommand(text: string, skills: ReadonlySet<string>): boolean {
    return asSkillCommand(text, skills) !== null;
}

/**
 * True when sending *text* has to read the installed skills first: research
 * mode is on, the text is shaped like a skill command, and the list is unread.
 */
export function needsInstalledSkills(text: string, researchOn: boolean): boolean {
    return researchOn && installedSkills === null && !isResearchMessage(text) && SKILL_COMMAND.test(text.trimStart());
}

/**
 * The message to send: prefixed with `/research` while research mode is on,
 * unless it already is a research command or runs an installed skill.
 */
export function withResearchCommand(
    text: string,
    researchOn: boolean,
    skills: ReadonlySet<string> = installedSkills ?? new Set(),
): string {
    if (!researchOn || !text.trim() || isResearchMessage(text)) return text;
    return asSkillCommand(text, skills) ?? `${RESEARCH_COMMAND} ${text}`;
}
