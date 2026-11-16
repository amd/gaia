// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * "N steps" is the agent's own step count, the figure the TUI and evals report.
 * Thinking, plan, and tool rows are not steps: a 10-step turn showed "23 steps",
 * and another turn's final answering step had no row at all.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { answerSummary } from '../AnswerStats';
import { MessageBubble } from '../MessageBubble';
import type { AgentStep, Message } from '../../types';

let nextId = 0;
const row = (type: AgentStep['type'], tool?: string): AgentStep => ({
    id: ++nextId, type, label: type, tool, active: false, success: true, timestamp: nextId,
    ...(type === 'plan' ? { planSteps: ['a', 'b'] } : {}),
});

/** A 10-step turn's shape: a thinking row and a plan per step, six tool calls, 24 rows. */
function tenStepTurnRows(): AgentStep[] {
    const rows: AgentStep[] = [];
    for (let i = 0; i < 10; i++) {
        rows.push(row('thinking'));
        if (i < 8) rows.push(row('plan'));
        if (i < 6) rows.push(row('tool', 'read_file'));
    }
    return rows;
}

describe('step count in the answer footer', () => {
    const base = { tokens_per_second: 0, time_to_first_token: 0, input_tokens: 0, output_tokens: 0 };

    it('reports the agent step count, not the number of rows', () => {
        expect(answerSummary(40_200, { ...base, steps: 10 })).toBe('Answered in 40 s · 10 steps');
    });

    it('says "1 step" for a one-step answer', () => {
        expect(answerSummary(undefined, { ...base, steps: 1 })).toBe('1 step');
    });

    it('ignores a zero count rather than printing "0 steps"', () => {
        expect(answerSummary(40_200, { ...base, steps: 0 })).toBe('Answered in 40 s');
    });

    it('reads a saved answer\'s count from its turn stats, whatever its rows', () => {
        const message: Message = {
            id: 7, session_id: 's', role: 'assistant', content: 'Done.', created_at: '', rag_sources: null,
            stats: { ...base, steps: 9 },
        };
        render(<MessageBubble message={message} agentSteps={tenStepTurnRows()} />);
        expect(screen.getByText(/9 steps/)).toBeInTheDocument();
    });
});
