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

    it("offers the agent's log, not the model server's, when the agent itself stopped", () => {
        place.mockReturnValue({ remote: false, label: 'Local', description: null });
        // Word for word what the relay writes when the agent dies mid-answer (stream_ended_text).
        const content = 'GAIA stopped unexpectedly while answering. Anything it had already written is kept above. Send your message again. The details are in this log folder: C:\\Users\\me\\.gaia\\agents\\gaia\\logs';
        render(<MessageBubble message={{ ...failed, content }} />);
        expect(screen.queryByRole('button', { name: 'Model server log' })).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Agent log' }));
        expect(useLogsStore.getState()).toMatchObject({ open: true, source: 'agent' });
    });

    it("offers the agent's log when the agent stopped partway through an answer", () => {
        place.mockReturnValue({ remote: false, label: 'Local', description: null });
        const stopped = 'GAIA stopped unexpectedly while answering. Anything it had already written is kept above. Send your message again. The details are in this log folder: C:\\Users\\me\\.gaia\\agents\\gaia\\logs';
        const content = `Alistair had known the rhythm of the sea for thirty years.\n\n${stopped}`;
        render(
            <MessageBubble
                message={{ ...failed, failed: false, content }}
                agentSteps={[{ id: 1, type: 'error', label: 'Error', detail: stopped, active: false, timestamp: 0 }]}
            />,
        );
        fireEvent.click(screen.getByRole('button', { name: 'Agent log' }));
        expect(useLogsStore.getState()).toMatchObject({ open: true, source: 'agent' });
    });

    it("offers the agent's log for a cloud chat too, since the agent runs on this PC", () => {
        place.mockReturnValue({ remote: true, label: 'Cloud', description: null });
        const content = "GAIA couldn't start its agent. Send your message again. The details are in this log folder: /home/me/.gaia/agents/gaia/logs";
        render(<MessageBubble message={{ ...failed, content }} />);
        expect(screen.getByRole('button', { name: 'Agent log' })).toBeInTheDocument();
    });

    it('is not offered on an answer', () => {
        place.mockReturnValue({ remote: false, label: 'Local', description: null });
        render(<MessageBubble message={{ ...failed, failed: false, content: 'Ulaanbaatar.' }} />);
        expect(screen.queryByRole('button', { name: 'Model server log' })).toBeNull();
    });
});
