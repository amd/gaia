// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Contract tests for `NeedsInputCard` (#2595): the question, its options and
 * a free-text lane render, submitting posts the answer, and a `sensitive`
 * answer is masked while typing and never echoed back into the card once
 * answered.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { NeedsInputCard, resolveTypedAnswer, waitingQuestionId } from '../render/NeedsInputCard';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';

vi.mock('../../services/api');
const mockedApi = vi.mocked(api);

const basicData = {
    session_id: 'session-1',
    request_id: 'req-1',
    question: 'Which mailbox should I use?',
    options: [
        { value: 'gmail', label: 'Gmail', description: 'Use the connected Gmail account.' },
        { value: 'outlook', label: 'Outlook', description: 'Use the connected Outlook account.' },
    ],
    allow_free_text: true,
    sensitive: false,
};

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.respondToInput.mockResolvedValue({ status: 'ok', request_id: 'req-1' });
    // The agent is still waiting on this question (expiry is in NeedsInputCard.expiry.test).
    useChatStore.setState({ liveQuestions: { 'req-1': { sessionId: 'session-1', expiresAt: null } }, questionAnswers: {} });
});

describe('NeedsInputCard — rendering', () => {
    it('renders the question text', () => {
        render(<NeedsInputCard data={basicData} />);
        expect(screen.getByText(basicData.question)).toBeInTheDocument();
    });

    it('renders each option as a clickable button', () => {
        render(<NeedsInputCard data={basicData} />);
        expect(screen.getByRole('button', { name: 'Gmail' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Outlook' })).toBeInTheDocument();
    });

    it('points a free-text answer at the message box instead of a second input', () => {
        render(<NeedsInputCard data={basicData} />);
        expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /send/i })).not.toBeInTheDocument();
        expect(screen.getByText('Type your answer in the message box below, or pick one.')).toBeInTheDocument();
    });

    it('omits the typing hint when allow_free_text is false', () => {
        render(<NeedsInputCard data={{ ...basicData, allow_free_text: false }} />);
        expect(screen.queryByText(/message box below/i)).not.toBeInTheDocument();
    });

    it('says how long GAIA waits when the question has a deadline', () => {
        useChatStore.setState({ liveQuestions: { 'req-1': { sessionId: 'session-1', expiresAt: Date.now() + 290_000 } } });
        render(<NeedsInputCard data={basicData} />);
        expect(screen.getByText('GAIA waits 5 min more for an answer.')).toBeInTheDocument();
    });

    it('renders the invalid-payload fallback for a malformed payload', () => {
        render(<NeedsInputCard data={{ question: 42 }} />);
        expect(screen.getByText('Invalid needs_input payload')).toBeInTheDocument();
    });
});

describe('NeedsInputCard — answering', () => {
    it('posts the option value to respondToInput when an option button is clicked', async () => {
        render(<NeedsInputCard data={basicData} />);

        fireEvent.click(screen.getByRole('button', { name: 'Gmail' }));

        await waitFor(() =>
            expect(mockedApi.respondToInput).toHaveBeenCalledWith('session-1', 'req-1', 'gmail'),
        );
    });

    it('keeps the question and the answer as a record once submitted', async () => {
        render(<NeedsInputCard data={basicData} />);

        fireEvent.click(screen.getByRole('button', { name: 'Gmail' }));

        await waitFor(() => expect(screen.getByText('You answered: Gmail')).toBeInTheDocument());
        expect(screen.getByText(basicData.question)).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Gmail' })).not.toBeInTheDocument();
    });

    it('still shows the answer, not "no longer waiting", when the card is drawn again after its turn', async () => {
        const first = render(<NeedsInputCard data={basicData} />);
        fireEvent.click(screen.getByRole('button', { name: 'Gmail' }));
        await waitFor(() => expect(screen.getByText('You answered: Gmail')).toBeInTheDocument());
        first.unmount();

        render(<NeedsInputCard data={basicData} />);
        expect(screen.getByText('You answered: Gmail')).toBeInTheDocument();
        expect(screen.queryByText(/no longer waiting/i)).not.toBeInTheDocument();
    });

    it('shows an error message and stays answerable when the respond call fails', async () => {
        mockedApi.respondToInput.mockRejectedValue(new Error('Could not deliver the answer: 409'));
        render(<NeedsInputCard data={basicData} />);

        fireEvent.click(screen.getByRole('button', { name: 'Gmail' }));

        await waitFor(() =>
            expect(screen.getByText(/could not deliver the answer/i)).toBeInTheDocument(),
        );
        // Still answerable — the question was not consumed by a failed attempt.
        expect(screen.getByRole('button', { name: 'Gmail' })).toBeInTheDocument();
    });
});

describe('NeedsInputCard — sensitive answers are masked and never echoed', () => {
    const sensitiveData = {
        ...basicData,
        options: [],
        sensitive: true,
    };

    it('renders the free-text input as a password field when sensitive', () => {
        render(<NeedsInputCard data={sensitiveData} />);
        const input = screen.getByPlaceholderText(/hidden/i) as HTMLInputElement;
        expect(input.type).toBe('password');
    });

    it('never renders the typed sensitive value as plain text anywhere in the card', async () => {
        const { container } = render(<NeedsInputCard data={sensitiveData} />);

        const secret = 'super-secret-token-123';
        fireEvent.change(screen.getByPlaceholderText(/hidden/i), { target: { value: secret } });
        fireEvent.click(screen.getByRole('button', { name: /send/i }));

        await waitFor(() => expect(mockedApi.respondToInput).toHaveBeenCalled());
        expect(mockedApi.respondToInput).toHaveBeenCalledWith('session-1', 'req-1', secret);

        // The answered state must not echo the secret back into the DOM.
        await waitFor(() => expect(screen.getByText('You answered (hidden).')).toBeInTheDocument());
        expect(container.textContent).not.toContain(secret);
    });
});

describe('answering from the message box', () => {
    const question = { options: [{ value: 'r', label: 'Red' }, { value: 'g', label: 'Green' }], allowFreeText: true };

    it('reads a typed option label, value or number as that option', () => {
        expect(resolveTypedAnswer('green', question)).toEqual({ value: 'g', shown: 'Green' });
        expect(resolveTypedAnswer('R', question)).toEqual({ value: 'r', shown: 'Red' });
        expect(resolveTypedAnswer('2', question)).toEqual({ value: 'g', shown: 'Green' });
    });

    it('takes any text when free text is allowed, and none that misses a choose-only question', () => {
        expect(resolveTypedAnswer('teal, please', question)).toEqual({ value: 'teal, please', shown: 'teal, please' });
        expect(resolveTypedAnswer('teal', { ...question, allowFreeText: false })).toBeNull();
    });

    it('answers the newest question of this chat, never a sensitive one', () => {
        const live = {
            a: { sessionId: 's1', expiresAt: null },
            b: { sessionId: 's2', expiresAt: null },
            c: { sessionId: 's1', expiresAt: null },
            d: { sessionId: 's1', expiresAt: null, sensitive: true },
        };
        expect(waitingQuestionId(live, 's1')).toBe('c');
        expect(waitingQuestionId(live, 's3')).toBeNull();
    });
});
