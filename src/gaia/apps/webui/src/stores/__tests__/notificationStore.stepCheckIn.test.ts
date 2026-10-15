// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import {
    requiresFreshConsent,
    STEP_CHECK_IN_TOOL,
    stepCheckInQuestion,
    stepCheckInTitle,
} from '../notificationStore';
import { stepLabel } from '../../components/ChatView';

describe('stepLabel', () => {
    it('shows the limit when the turn has one', () => {
        expect(stepLabel(3, 50)).toBe('Step 3 of 50');
    });

    it('shows the step alone when the turn has no limit', () => {
        expect(stepLabel(3, null)).toBe('Step 3');
        expect(stepLabel(1000, undefined)).toBe('Step 1000');
        // 0 is the backend's "no limit"; never "of 0".
        expect(stepLabel(7, 0)).toBe('Step 7');
    });
});

describe('the hosted-model check-in', () => {
    it('asks in plain words, naming the steps and the model', () => {
        const args = { steps: 200, model: 'claude-sonnet-5' };
        expect(stepCheckInTitle(args)).toBe('Keep going after 200 steps?');
        expect(stepCheckInQuestion(args)).toContain('200 steps on claude-sonnet-5');
        expect(stepCheckInQuestion(args)).toContain('billed per use');
        expect(stepCheckInTitle(args)).not.toContain(STEP_CHECK_IN_TOOL);
    });

    it('survives missing or malformed args', () => {
        expect(stepCheckInTitle(undefined)).toBe('Keep going after many steps?');
        expect(stepCheckInQuestion({ steps: '200', model: 42 })).toContain('many steps on a hosted model');
    });

    it('is never covered by an "always" answer', () => {
        expect(requiresFreshConsent(STEP_CHECK_IN_TOOL)).toBe(true);
    });
});
