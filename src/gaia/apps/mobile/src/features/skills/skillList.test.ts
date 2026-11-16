// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { SkillInfo } from '@/api/client.types';

import { filterSkills, isGrouped, skipReason, skippedName } from './skillList';

const skill = (name: string, description: string, source: SkillInfo['source'] = 'agent-bundled'): SkillInfo => ({
    name,
    description,
    version: null,
    tier: null,
    tools: [],
    path: `/s/${name}`,
    source,
});

describe('skill list', () => {
    it('searches names and descriptions, ignoring case', () => {
        const all = [skill('pdf', 'Read documents'), skill('Coding', 'Write code')];
        expect(filterSkills(all, ' CODE ').map((s) => s.name)).toEqual(['Coding']);
        expect(filterSkills(all, 'documents').map((s) => s.name)).toEqual(['pdf']);
        expect(filterSkills(all, '')).toHaveLength(2);
    });

    it('shows a list flat when a source has no group', () => {
        expect(isGrouped([skill('a', 'x'), skill('b', 'x', 'user')])).toBe(true);
        expect(isGrouped([skill('a', 'x', null)])).toBe(false);
    });

    it('explains why a skill was skipped', () => {
        expect(skipReason('description is 2000 characters, max 1024')).toBe('settings.skills.skipped.tooLong');
        expect(skipReason("'description' is missing")).toBe('settings.skills.skipped.noDescription');
        expect(skipReason('bad yaml')).toBe('settings.skills.skipped.other');
        expect(skippedName('C:\\skills\\broken\\SKILL.md')).toBe('SKILL.md');
    });
});
