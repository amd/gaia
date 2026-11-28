// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { installedSkills } from './installedSkills';
import { findSkillCommand, namesASkill } from './skillCommand';

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

type InstalledNames = ReadonlySet<string> | ReadonlyMap<string, unknown>;

/** *text* with the skill command it runs in the lowercase the agent reads; null when it names no installed skill. */
function asSkillCommand(text: string, skills: InstalledNames): string | null {
    const word = findSkillCommand(text, skills);
    if (word === null) return null;
    return `${text.slice(0, word.start)}/${word.name}${text.slice(word.end)}`;
}

/** True when *text* names an installed skill as `/<name>`, at the start or later on. */
export function isSkillCommand(text: string, skills: InstalledNames): boolean {
    return asSkillCommand(text, skills) !== null;
}

/**
 * True when sending *text* has to read the installed skills first: research
 * mode is on, the text names something shaped like a skill, and the list is unread.
 */
export function needsInstalledSkills(text: string, researchOn: boolean): boolean {
    return researchOn && installedSkills() === null && !isResearchMessage(text) && namesASkill(text);
}

/**
 * The message to send: prefixed with `/research` while research mode is on,
 * unless it already is a research command or runs an installed skill.
 */
export function withResearchCommand(
    text: string,
    researchOn: boolean,
    skills: InstalledNames = installedSkills() ?? new Set(),
): string {
    if (!researchOn || !text.trim() || isResearchMessage(text)) return text;
    return asSkillCommand(text, skills) ?? `${RESEARCH_COMMAND} ${text}`;
}
