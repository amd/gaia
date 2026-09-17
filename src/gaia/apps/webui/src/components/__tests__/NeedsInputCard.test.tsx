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
import { NeedsInputCard } from '../render/NeedsInputCard';
import * as api from '../../services/api';

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

    it('renders a free-text input when allow_free_text is true', () => {
        render(<NeedsInputCard data={basicData} />);
        expect(screen.getByPlaceholderText(/type your answer/i)).toBeInTheDocument();
    });

    it('omits the free-text lane when allow_free_text is false', () => {
        render(<NeedsInputCard data={{ ...basicData, allow_free_text: false }} />);
        expect(screen.queryByPlaceholderText(/type your answer/i)).not.toBeInTheDocument();
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

    it('posts free-text input on submit', async () => {
        render(<NeedsInputCard data={basicData} />);

        fireEvent.change(screen.getByPlaceholderText(/type your answer/i), {
            target: { value: 'Use my work account' },
        });
        fireEvent.click(screen.getByRole('button', { name: /send/i }));

        await waitFor(() =>
            expect(mockedApi.respondToInput).toHaveBeenCalledWith(
                'session-1',
                'req-1',
                'Use my work account',
            ),
        );
    });

    it('shows an answered state and removes the options/input once submitted', async () => {
        render(<NeedsInputCard data={basicData} />);

        fireEvent.click(screen.getByRole('button', { name: 'Gmail' }));

        await waitFor(() => expect(screen.getByText(/answered/i)).toBeInTheDocument());
        expect(screen.queryByRole('button', { name: 'Gmail' })).not.toBeInTheDocument();
        expect(screen.queryByPlaceholderText(/type your answer/i)).not.toBeInTheDocument();
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
        await waitFor(() => expect(screen.getByText(/answered/i)).toBeInTheDocument());
        expect(container.textContent).not.toContain(secret);
    });
});
