// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useSyncExternalStore } from 'react';
import * as api from '../services/api';
import type { SkillInfo } from '../types';
import { log } from './logger';

/**
 * The installed skills, as the `/` menu last read them. Research mode, the
 * composer's highlight and the chat's highlight all read this one copy.
 */

type Installed = ReadonlyMap<string, SkillInfo>;

/** null until the list has been read: an unread list is not "no skills". */
let installed: Installed | null = null;
let reading: Promise<readonly SkillInfo[]> | null = null;
/** A passive read failed; only the menu, which shows the error, tries again. */
let passiveFailed = false;
const listeners = new Set<() => void>();

function changed(): void {
    for (const listener of listeners) listener();
}

/** Record the installed skills, keyed by their lowercased name, in the agent's order. */
export function rememberInstalledSkills(skills: readonly SkillInfo[]): void {
    installed = new Map(skills.map((skill) => [skill.name.toLowerCase(), skill]));
    passiveFailed = false;
    changed();
}

/** Back to "not read yet", for tests. */
export function forgetInstalledSkills(): void {
    installed = null;
    reading = null;
    passiveFailed = false;
    changed();
}

/** The installed skills by lowercased name, or null when unread. */
export function installedSkills(): Installed | null {
    return installed;
}

/** Read the list from the agent and remember it; calls made while one read runs share it. */
export function readInstalledSkills(): Promise<readonly SkillInfo[]> {
    reading ??= api.listSkills().then(
        (r) => {
            reading = null;
            rememberInstalledSkills(r.skills);
            return r.skills;
        },
        (e: unknown) => {
            reading = null;
            throw e;
        },
    );
    return reading;
}

function subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
}

/**
 * The installed skills, re-rendering when they change. With *wanted* the list
 * is read once if nobody has read it yet, for text that names a skill.
 */
export function useInstalledSkills(wanted = false): Installed | null {
    const current = useSyncExternalStore(subscribe, installedSkills, installedSkills);
    useEffect(() => {
        if (!wanted || installed !== null || passiveFailed) return;
        readInstalledSkills().catch((e: unknown) => {
            passiveFailed = true;
            log.chat.warn("Couldn't read the installed skills to mark /<name> commands; the / menu will try again", e);
        });
    }, [wanted]);
    return current;
}
