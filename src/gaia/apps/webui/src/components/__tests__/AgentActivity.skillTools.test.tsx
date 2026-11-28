// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Cards from a skill ("guides/show_recipe") name what they did, never the raw tool id.

import { describe, expect, it } from 'vitest';
import { stepsSummary, toolRowText, type ActivityRow } from '../AgentActivity';
import { stripIsolates } from '../../i18n/registry';
import { translator } from '../../i18n/useT';

const ar = translator('ar');

function tool(id: number, name: string): ActivityRow {
    return { id, type: 'tool', label: `Ran ${name}`, tool: name, active: false, success: true, result: 'ok', timestamp: id };
}

describe('skill card tools', () => {
    it('summarizes a recipe and a loan calculator in words', () => {
        expect(stepsSummary([tool(1, 'guides/show_recipe'), tool(2, 'calculators/show_loan_calculator')]))
            .toBe('Showed a recipe, showed a loan calculator');
    });

    it('says it in Arabic without the tool id', () => {
        const line = stripIsolates(stepsSummary([tool(1, 'guides/show_recipe')], ar));
        expect(line).toContain('عرض وصفة');
        expect(line).not.toContain('guides');
    });

    it('labels the live row of a card tool', () => {
        expect(toolRowText({ ...tool(1, 'guides/show_workout'), active: true, success: undefined, result: undefined }))
            .toBe('Showing a workout');
    });

    it('drops the skill name from a tool that has no label', () => {
        expect(stepsSummary([tool(1, 'weather/compare_cities')])).toBe('Used compare cities');
    });
});
