// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { MessageKey, Params } from '@webui/i18n/registry';

import type { SkillInfo, SkillSource } from '@/api/client.types';

/** The search box appears once there are more skills than this, as on the web. */
export const SEARCH_ABOVE = 6;

// Paths go in as params, so the translator keeps them left to right inside an Arabic sentence.
const SKILLS_DIR = { path: '~/.gaia/skills' };
const PROJECT_SKILLS_DIR = { path: '.claude/skills' };

export interface SkillGroupSpec {
    source: SkillSource;
    title: MessageKey;
    note: MessageKey;
    empty?: MessageKey;
    params?: Params;
}

export const SKILLS_HOW_PARAMS = SKILLS_DIR;

export const GROUPS: SkillGroupSpec[] = [
    { source: 'agent-bundled', title: 'settings.skills.bundled', note: 'settings.skills.bundledNote' },
    { source: 'user', title: 'settings.skills.yours', note: 'settings.skills.yoursNote', empty: 'mobileApp.skills.yoursEmpty', params: SKILLS_DIR },
    { source: 'claude-project', title: 'settings.skills.project', note: 'settings.skills.projectNote', params: PROJECT_SKILLS_DIR },
    { source: 'claude-import', title: 'settings.skills.import', note: 'settings.skills.importNote' },
];

export function skipReason(why: string): MessageKey {
    if (/description is \d+ characters/.test(why)) return 'settings.skills.skipped.tooLong';
    if (/'description' is missing/.test(why)) return 'settings.skills.skipped.noDescription';
    return 'settings.skills.skipped.other';
}

export function filterSkills(skills: SkillInfo[], query: string): SkillInfo[] {
    const q = query.trim().toLowerCase();
    return skills.filter((s) => !q || s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q));
}

/** A source this build has no group for (a newer agent's) shows the list flat, never hides it. */
export function isGrouped(skills: SkillInfo[]): boolean {
    return skills.every((s) => GROUPS.some((g) => g.source === s.source));
}

export function skippedName(path: string): string {
    return path.split(/[\\/]/).filter(Boolean).pop() ?? path;
}
