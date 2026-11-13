// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** An image step that found no vision model offers it, in the app, instead of naming a settings path. */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AgentActivity } from '../AgentActivity';
import { toolResultUpdates } from '../../utils/toolEvents';
import { ensureModel } from '../../stores/modelNeededStore';
import type { AgentStep, StreamEvent } from '../../types';

vi.mock('../../services/api');
vi.mock('../../stores/modelNeededStore', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../stores/modelNeededStore')>()),
    ensureModel: vi.fn(async () => true),
}));

const imageStep = (over: Partial<AgentStep> = {}): AgentStep => ({
    id: 1, type: 'tool', label: 'Using tool', tool: 'answer_question_about_image', active: false,
    success: false, result: "Vision model 'Gemma-4-E4B-it-GGUF' is not downloaded", timestamp: 1, ...over,
});

describe('an image step with no vision model', () => {
    it('offers the vision model and opens the download prompt', () => {
        render(<AgentActivity steps={[imageStep({ needsModel: 'vision' })]} isActive={false} />);

        expect(screen.getByText(/Reading images needs the vision model/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Get the vision model' }));
        expect(ensureModel).toHaveBeenCalledWith('vision');
    });

    it('offers nothing for an image step that failed for another reason', () => {
        render(<AgentActivity steps={[imageStep()]} isActive={false} />);

        expect(screen.queryByRole('button', { name: 'Get the vision model' })).toBeNull();
    });

    it.each([
        ['on the event', { needs_model: 'vision' }],
        ['in relayed data', { data: { needs_model: 'vision' } }],
    ])('is marked from the tool result (%s)', (_where, extra) => {
        const event = { type: 'tool_result', success: false, summary: 'x', ...extra } as StreamEvent;
        expect(toolResultUpdates(event).needsModel).toBe('vision');
    });
});
