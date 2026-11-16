// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react-native';

import type { QuestionRequest } from '@/data';
import { ThemeProvider } from '@/ui';

import { ChatPrompts } from './ChatPrompts';

jest.mock('@/features/questions', () => {
    const actual = jest.requireActual('@/features/questions');
    const { Text } = jest.requireActual('@/ui');
    return {
        ...actual,
        QuestionCard: ({ data }: { data: { sessionId: string; requestId: string; question: string } }) => (
            <Text>{`${data.sessionId}/${data.requestId}: ${data.question}`}</Text>
        ),
    };
});

describe('ChatPrompts', () => {
    it('shows the question card for a pending question and nothing without one', async () => {
        const question = { type: 'needs_input', request_id: 'q1', question: 'Which folder?', options: [] } as QuestionRequest;
        const { rerender } = await render(<ChatPrompts sessionId="s1" question={question} />, { wrapper: ThemeProvider });
        expect(screen.getByText('s1/q1: Which folder?')).toBeTruthy();

        await rerender(<ChatPrompts sessionId="s1" question={null} />);
        expect(screen.queryByText(/Which folder/)).toBeNull();
    });
});
