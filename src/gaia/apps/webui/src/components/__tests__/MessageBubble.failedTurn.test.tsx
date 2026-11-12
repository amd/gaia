// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MessageBubble } from '../MessageBubble';
import { useChatStore } from '../../stores/chatStore';
import { describeFailedTurn } from '../../utils/failedTurn';
import type { AgentStep, Message } from '../../types';

vi.mock('../../services/api');

const baseMessage: Message = {
    id: 1, session_id: 's', role: 'assistant', content: '',
    created_at: '2026-07-16T00:00:00.000Z', rag_sources: null,
};
const errorStep = (detail: string): AgentStep => (
    { id: 1, type: 'error', label: 'Error', detail, active: false, success: false, timestamp: 1 }
);
const renderFailed = (content: string, props: Partial<Parameters<typeof MessageBubble>[0]> = {}) =>
    render(<MessageBubble message={{ ...baseMessage, content }} agentSteps={[errorStep(content)]} {...props} />);

const RAW = "Failed to start the query run: Failed to preload Lemonade model 'no-such-model-xyz-123' with ctx_size=65536 "
    + "on idle server at http://localhost:64679/api/v1: Failed to auto-download 'no-such-model-xyz-123': Download failed: "
    + 'When registering a new model, the model name must include the `user` namespace, for example `user.Phi-4-Mini-GGUF`. '
    + 'Received: no-such-model-xyz-123\nTo recover manually: stop the running server, then restart it (GAIA starts its '
    + 'Lemonade Server when it needs it. To start it now, run: gaia lemonade embedded start) and re-run your GAIA command.';

const WORDED = 'There is no model called `no-such-model-xyz-123` to download: the model server doesn\'t list it. Check the '
    + 'name for a typo. Pick another model from the model menu next to the Send button. If you typed this model ID yourself, '
    + 'clear it under Settings > Advanced > Custom model ID.';

afterEach(() => {
    useChatStore.setState({ settingsSection: null, settingsProvider: null });
});

describe('a turn that failed on a model the server does not have', () => {
    it('is headed with the cause, in plain words, with the server error under Details', () => {
        const { container } = renderFailed(RAW);
        expect(screen.getByText("This model isn't available")).toBeInTheDocument();
        expect(screen.queryByText('Something went wrong')).toBeNull();
        expect(container.querySelector('.msg-failed-lead')?.textContent).toContain('Choose another model');
        const details = container.querySelector('details.msg-failed-details') as HTMLDetailsElement;
        expect(details.open).toBe(false);
        expect(details.textContent).toContain('must include the');
        // Outside Details nothing tells the user to run a command.
        const outside = container.cloneNode(true) as HTMLElement;
        outside.querySelector('details')?.remove();
        expect(outside.textContent).not.toMatch(/gaia lemonade|re-run your/);
    });

    it('offers Settings > Model as a button', () => {
        renderFailed(RAW);
        fireEvent.click(screen.getByRole('button', { name: 'Open Settings › Model' }));
        expect(useChatStore.getState().settingsSection).toBe('model');
    });

    it('shows the server\'s plain sentence as written, with a button for each tab it names', () => {
        const { container } = renderFailed(WORDED);
        expect(screen.getByText("This model isn't available")).toBeInTheDocument();
        expect(container.querySelector('details.msg-failed-details')).toBeNull();
        expect(container.textContent).toContain('Check the name for a typo');
        expect(screen.getByRole('button', { name: 'Open Settings › Model' })).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Open Settings › Advanced' }));
        expect(useChatStore.getState().settingsSection).toBe('advanced');
    });

});

describe('describeFailedTurn', () => {
    it('keeps a plain failure as the answer', () => {
        const memory = 'Not enough free memory to load Gemma-4-E4B-it-GGUF with a 32,768-token window: needs about 7.8 GB '
            + 'and only 6.0 GB is free. Close apps that use a lot of memory, then try again.';
        expect(describeFailedTurn(memory)).toEqual({ kind: 'unknown', raw: false, sections: [] });
        expect(describeFailedTurn(`Failed to start the query run: ${memory}`).raw).toBe(false);
    });

    it('puts an exception dump under Details', () => {
        expect(describeFailedTurn('RuntimeError: boom').raw).toBe(true);
        expect(describeFailedTurn('RuntimeError: boom').kind).toBe('unknown');
    });

    it('does not hide a long answer that only mentions a command', () => {
        const answer = `${'Here is how the tool works. '.repeat(80)}Then run: gaia chat`;
        expect(describeFailedTurn(answer).raw).toBe(false);
    });
});
