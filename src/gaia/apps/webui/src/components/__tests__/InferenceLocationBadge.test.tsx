// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The chat footer said "100% local & private" while turns were sent to
 * Fireworks. It may claim local only when the backend says chat stays here.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { InferenceLocationBadge, selectInferenceLocation } from '../InferenceLocationBadge';
import type { Session } from '../../types';

const FIREWORKS_SENTENCE =
    'Chat inference runs on Fireworks AI via Lemonade — a cloud provider — using model '
    + '`fireworks.deepseek-v4p1-flash`. Everything in this conversation is sent there to be answered.';

describe('InferenceLocationBadge', () => {
    it('claims local and private only for a local model', () => {
        render(
            <InferenceLocationBadge
                session={{ inference_remote: false, inference_provider_name: 'Lemonade on this machine' }}
            />,
        );
        const badge = screen.getByTestId('inference-location');
        expect(badge.textContent).toBe('100% local & private');
    });

    it('names the cloud provider and carries the full sentence as a tooltip', () => {
        render(
            <InferenceLocationBadge
                session={{
                    inference_remote: true,
                    inference_provider_name: 'Fireworks AI',
                    inference_description: FIREWORKS_SENTENCE,
                }}
            />,
        );
        const badge = screen.getByTestId('inference-location');
        expect(badge.textContent).toBe('Sent to Fireworks AI');
        expect(badge.getAttribute('title')).toBe(FIREWORKS_SENTENCE);
        expect(badge.textContent).not.toMatch(/local|private/i);
    });

    it.each([
        ['an unresolvable location', { inference_remote: null }],
        ['a backend that predates the field', {}],
        ['no session loaded yet', undefined],
    ])('does not claim local for %s', (_label, session) => {
        render(<InferenceLocationBadge session={session} />);
        const badge = screen.getByTestId('inference-location');
        expect(badge.textContent).toBe('Model location unknown');
        expect(badge.textContent).not.toMatch(/local|private/i);
    });
});

describe('selectInferenceLocation', () => {
    const s = (id: string, remote: boolean) =>
        ({ id, inference_remote: remote } as unknown as Session);

    it('prefers the open session, then the most recent one', () => {
        const sessions = [s('a', true), s('b', false)];
        expect(selectInferenceLocation(sessions, 'b')?.inference_remote).toBe(false);
        expect(selectInferenceLocation(sessions, null)?.inference_remote).toBe(true);
        expect(selectInferenceLocation([], null)).toBeUndefined();
    });
});
