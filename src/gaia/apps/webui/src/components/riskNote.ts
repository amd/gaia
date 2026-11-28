// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ref, type MessageKey, type Param } from '../i18n/registry';
import type { Translate } from '../i18n/useT';

/** Fast and light's note on a shell command it can't check (cpp `shellApprovalNote`). */
const UNCHECKED = /^GAIA can't check where this command reads or writes: (.+)\. It can reach files outside the folders this chat may use(, and GAIA can't tell whether it goes online)?\.$/s;

/** Its most common reasons; a program name is the only part that varies. */
const REASONS: Array<[RegExp, MessageKey]> = [
    [/^(\S+) runs code, which can read and write anywhere you can$/, 'prompt.unchecked.why.runsCode'],
    [/^(\S+) runs a script file, which GAIA does not read$/, 'prompt.unchecked.why.runsScript'],
    [/^GAIA has no rule for what (\S+) reads or writes$/, 'prompt.unchecked.why.noRule'],
    [/^(\S+) starts other programs, which GAIA can't follow$/, 'prompt.unchecked.why.startsPrograms'],
    [/^it runs the program file (\S+), which GAIA can't inspect$/, 'prompt.unchecked.why.programFile'],
    [/^it runs the commands in a file, which GAIA does not read$/, 'prompt.unchecked.why.commandsFile'],
    [/^a file name in it is worked out while the command runs$/, 'prompt.unchecked.why.workedOut'],
    [/^cmd\.exe runs it, and GAIA reads only bash commands$/, 'prompt.unchecked.why.cmdExe'],
];

function reason(text: string): Param | null {
    for (const [pattern, key] of REASONS) {
        const match = pattern.exec(text);
        if (match) return ref(key, match[1] ? { name: match[1] } : undefined);
    }
    return null;
}

/**
 * An approval card's risk note in the UI's language. Fast and light writes its
 * note on an unchecked command in English; the ones it words the same way every
 * time are translated, with a reason the UI doesn't know left out outside English.
 * Any other note is shown as the agent wrote it.
 */
export function riskNoteText(note: string, t: Translate): string {
    const match = UNCHECKED.exec(note.trim());
    if (!match) return note;
    const online = !!match[2];
    const why = reason(match[1]);
    if (why) return t(online ? 'prompt.unchecked.offline' : 'prompt.unchecked', { why });
    if (t.locale === 'en') return note;
    return t(online ? 'prompt.unchecked.plainOffline' : 'prompt.unchecked.plain');
}
