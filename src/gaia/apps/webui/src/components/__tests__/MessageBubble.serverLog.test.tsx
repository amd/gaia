// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A turn a local model failed offers the model server's log; a cloud one doesn't. */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import { useLogsStore } from '../../stores/logsStore';
import * as modelStore from '../../stores/modelStore';
import type { Message } from '../../types';

vi.mock('../../stores/modelStore', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../stores/modelStore')>();
    return { ...actual, useInferencePlace: vi.fn() };
});
const place = vi.mocked(modelStore.useInferencePlace);

const failed: Message = {
    id: 1,
    session_id: 'sess-1',
    role: 'assistant',
    content: "Couldn't reach the local LLM. It may be loading or briefly busy — try sending the message again.",
    created_at: '2026-11-04T00:00:00.000Z',
    rag_sources: null,
    failed: true,
};

describe('the model server log on a failed turn', () => {
    afterEach(() => act(() => useLogsStore.setState({ open: false })));

    it('opens the log from a turn a local model failed', () => {
        place.mockReturnValue({ remote: false, label: 'Local', description: null });
        render(<MessageBubble message={failed} />);
        fireEvent.click(screen.getByRole('button', { name: 'Model server log' }));
        expect(useLogsStore.getState()).toMatchObject({ open: true, source: 'model_server' });
    });

    it('is not offered for a cloud model', () => {
        place.mockReturnValue({ remote: true, label: 'Cloud', description: null });
        render(<MessageBubble message={failed} />);
        expect(screen.queryByRole('button', { name: 'Model server log' })).toBeNull();
    });

    it('is offered under a failure answer the server flagged, whatever its opening says', () => {
        place.mockReturnValue({ remote: false, label: 'Local', description: null });
        // A headline the agent chose, so nothing in the text says it failed.
        const content = "GAIA's AI engine couldn't start, so this request is unfinished. Lemond exited.\n\nNothing had run yet.";
        render(<MessageBubble message={{ ...failed, content }} latencyMs={2_000} />);
        expect(screen.getAllByText('Something went wrong')).toHaveLength(1);
        expect(screen.getByRole('button', { name: 'Failed after 2 s' })).toBeInTheDocument();
        expect(document.body).not.toHaveTextContent('Answered in');
        fireEvent.click(screen.getByRole('button', { name: 'Model server log' }));
        expect(useLogsStore.getState()).toMatchObject({ open: true, source: 'model_server' });
    });

    it('is not offered on an answer', () => {
        place.mockReturnValue({ remote: false, label: 'Local', description: null });
        render(<MessageBubble message={{ ...failed, failed: false, content: 'Ulaanbaatar.' }} />);
        expect(screen.queryByRole('button', { name: 'Model server log' })).toBeNull();
    });
});
