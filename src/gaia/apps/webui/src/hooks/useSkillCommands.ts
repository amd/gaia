// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import type { SkillInfo } from '../types';
import { readInstalledSkills, useInstalledSkills } from '../utils/installedSkills';
import { commandAtCaret, type TypedCommand } from '../utils/skillCommand';

type Status = 'loading' | 'ready' | 'error';

export interface SkillCommands {
    /** Whether the list shows under the composer's text. */
    open: boolean;
    status: Status;
    error: string | null;
    /** Installed skills whose /name starts with what is typed. */
    matches: SkillInfo[];
    selected: number;
    select: (index: number) => void;
    /** Hide the list until the text changes. */
    dismiss: () => void;
    /** The `/` word at the caret, which a picked name replaces. */
    word: TypedCommand | null;
}

/**
 * The skills a `/` command can run, for the `/` word at the caret: at the
 * start of the text or after a space, anywhere in it. *caret* is null while
 * a range is selected. Read fresh each time a `/` starts, so a skill added a
 * moment ago is offered.
 */
export function useSkillCommands(value: string, caret: number | null): SkillCommands {
    const word = caret === null ? null : commandAtCaret(value, caret);
    const typing = word !== null;
    const skills = useInstalledSkills();
    const [status, setStatus] = useState<Status>('loading');
    const [error, setError] = useState<string | null>(null);
    const [selected, setSelected] = useState(0);
    const [dismissedAt, setDismissedAt] = useState<string | null>(null);

    useEffect(() => {
        if (!typing) return;
        let live = true;
        setStatus((s) => (s === 'ready' ? s : 'loading'));
        readInstalledSkills().then(
            () => {
                if (!live) return;
                setStatus('ready');
                setError(null);
            },
            (e: unknown) => {
                if (!live) return;
                setStatus('error');
                setError(e instanceof Error ? e.message : String(e));
            },
        );
        return () => { live = false; };
    }, [typing]);

    const typed = word?.typed.toLowerCase() ?? null;
    useEffect(() => { setSelected(0); }, [typed]);

    const matches = typed === null || !skills ? [] : Array.from(skills.values()).filter((s) => `/${s.name}`.toLowerCase().startsWith(typed));
    const open = typing && dismissedAt !== value && (status !== 'ready' || matches.length > 0);
    const select = useCallback((index: number) => setSelected(index), []);
    const dismiss = useCallback(() => setDismissedAt(value), [value]);

    return { open, status, error, matches, selected: Math.min(selected, Math.max(matches.length - 1, 0)), select, dismiss, word };
}
