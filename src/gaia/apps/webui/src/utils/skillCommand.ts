// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Where a message names a skill as `/<name>`, read the way the agent reads it
 * (`gaia.skills.command`; the TUI mirrors it in `research.go`).
 *
 * A command is one whitespace-separated word: `/name` or a Claude Code
 * plugin's `/plugin:name`, in any case, then any sentence punctuation. It may
 * start the message or come later in it. `/tmp/x.txt`, `/etc/hosts` and a URL
 * are never one. The first word that names an installed skill is the one
 * that runs.
 */

const COMMAND_WORD = /(?<!\S)\/((?:[a-z0-9]+(?:-[a-z0-9]+)*:)?[a-z0-9]+(?:-[a-z0-9]+)*)[.,!?;:،؟]*(?!\S)/gi;

/** `/name` in the text: *start* is the slash, *end* is just past the name. */
export interface SkillWord {
    start: number;
    end: number;
    /** Lowercased, as the agent reads it. */
    name: string;
}

/** Every word shaped like a skill command, installed or not, in order. */
function commandWords(text: string): SkillWord[] {
    return Array.from(text.matchAll(COMMAND_WORD), (m) => ({
        start: m.index,
        end: m.index + 1 + m[1].length,
        name: m[1].toLowerCase(),
    }));
}

/** The skill command that runs when *text* is sent: the first word naming an installed skill. */
export function findSkillCommand(text: string, installed: ReadonlySet<string> | ReadonlyMap<string, unknown>): SkillWord | null {
    return commandWords(text).find((word) => installed.has(word.name)) ?? null;
}

/** Whether *text* holds any word shaped like a skill command, so the installed list is worth reading. */
export function namesASkill(text: string): boolean {
    return commandWords(text).length > 0;
}

/** A `/` word being typed: `/`, `/na`, or `/plugin:na`. */
const TYPING_COMMAND = /^\/[a-z0-9-]*(?::[a-z0-9-]*)?$/i;

/** The `/` word the caret is in or just after: what the menu filters by, and what a pick replaces. */
export interface TypedCommand {
    start: number;
    end: number;
    /** From the slash to the caret. */
    typed: string;
}

/** The `/` word at *caret* that the skill menu offers names for, or null. */
export function commandAtCaret(text: string, caret: number): TypedCommand | null {
    let start = caret;
    while (start > 0 && !/\s/.test(text[start - 1])) start--;
    let end = caret;
    while (end < text.length && !/\s/.test(text[end])) end++;
    if (start === caret || !TYPING_COMMAND.test(text.slice(start, end))) return null;
    return { start, end, typed: text.slice(start, caret) };
}
