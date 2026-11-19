// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The agent dies mid-answer. The server saves what it had written, then its
 * error ("Anything it had already written is kept above"), and the turn's
 * steps end with the same error. The error must sit below the words it points
 * at, once, not in the steps above them.
 */

import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MessageBubble, dropErrorsEchoedInAnswer } from '../MessageBubble';
import { answerSummary } from '../AnswerStats';
import type { AgentStep, Message } from '../../types';

const NOTICE = 'GAIA stopped unexpectedly while answering. Anything it had already written is kept above. '
    + 'Send your message again. The details are in this log folder: C:\\Users\\me\\.gaia\\agents\\gaia\\logs';
const ESSAY = 'Bridges are among the oldest inventions. The Romans built arches that still stand.';

const STEPS: AgentStep[] = [
    { id: 1, type: 'thinking', label: 'Thinking', detail: 'Let me write.', active: false, timestamp: 1 },
    { id: 2, type: 'error', label: 'Error', detail: NOTICE, active: false, success: false, timestamp: 2 },
];

const reply = (content: string): Message => ({
    id: 319, session_id: 's', role: 'assistant', content, created_at: '2026-11-12T00:26:43Z', rag_sources: null,
});

const count = (text: string, part: string) => text.split(part).length - 1;

describe('an answer the agent died writing', () => {
    it('shows the partial answer first and the error once, below it', () => {
        const { container } = render(<MessageBubble message={reply(`${ESSAY}\n\n${NOTICE}`)} agentSteps={STEPS} />);
        const text = container.textContent ?? '';
        expect(text).toContain('The Romans built arches that still stand.');
        expect(count(text, 'GAIA stopped unexpectedly')).toBe(1);
        expect(text.indexOf('The Romans built')).toBeLessThan(text.indexOf('GAIA stopped unexpectedly'));
    });

    it('keeps the error step when the answer only quotes part of it', () => {
        const { steps } = dropErrorsEchoedInAnswer(STEPS, `${ESSAY} Send your message again.`);
        expect(steps).toHaveLength(2);
    });

    it("keeps a tool's error step when a finished answer ends by quoting it", () => {
        const toolError = 'File not found: C:\\Users\\me\\notes.txt';
        const steps: AgentStep[] = [
            { id: 1, type: 'error', label: 'Error', detail: toolError, active: false, success: false, timestamp: 1 },
            { id: 2, type: 'thinking', label: 'Thinking', detail: 'Tell the user.', active: false, timestamp: 2 },
        ];
        expect(dropErrorsEchoedInAnswer(steps, `I could not open your notes. The tool said:\n\n${toolError}`).steps)
            .toHaveLength(2);
    });

    it('keeps the last error step when the answer ends with it mid-paragraph, not as its own', () => {
        expect(dropErrorsEchoedInAnswer(STEPS, `${ESSAY} The log said: ${NOTICE}`).steps).toHaveLength(2);
        expect(dropErrorsEchoedInAnswer(STEPS, `${ESSAY}\n${NOTICE}`).steps).toHaveLength(2);
    });

    it('drops the step when the saved notice carries trailing whitespace', () => {
        const padded: AgentStep[] = [STEPS[0], { ...STEPS[1], detail: `${NOTICE}\n` }];
        expect(dropErrorsEchoedInAnswer(padded, `${ESSAY}\n\n${NOTICE}\n\n`).steps).toHaveLength(1);
    });

    it('sets the error apart from the answer, as a note below it', () => {
        const { container } = render(<MessageBubble message={reply(`${ESSAY}\n\n${NOTICE}`)} agentSteps={STEPS} />);
        const note = container.querySelector('.msg-cut-off');
        expect(note?.textContent).toBe(NOTICE);
        expect(container.querySelector('.md-content')?.textContent ?? '').not.toContain('GAIA stopped unexpectedly');
    });

    it('says the turn failed in the footer, not that it answered', () => {
        const { container } = render(<MessageBubble message={reply(`${ESSAY}\n\n${NOTICE}`)} agentSteps={STEPS} latencyMs={10_000} />);
        const footer = container.querySelector('.msg-stats-summary')?.textContent ?? '';
        expect(footer).toBe(answerSummary(10_000, undefined, 'failed'));
        expect(footer).not.toBe(answerSummary(10_000, undefined));
    });

    it('names the error that ended a partial answer', () => {
        expect(dropErrorsEchoedInAnswer(STEPS, `${ESSAY}\n\n${NOTICE}`).endedBy).toBe(NOTICE);
        expect(dropErrorsEchoedInAnswer(STEPS, NOTICE).endedBy).toBeNull();
    });

    it('does not call a partial answer an error reply', () => {
        expect(dropErrorsEchoedInAnswer(STEPS, `${ESSAY}\n\n${NOTICE}`).echoed).toBe(false);
        expect(dropErrorsEchoedInAnswer(STEPS, NOTICE).echoed).toBe(true);
    });
});
