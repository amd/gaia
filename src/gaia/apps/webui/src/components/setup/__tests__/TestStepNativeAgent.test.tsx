// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../../services/api';
import { AGENT_PROGRAMS, SETTINGS } from '../../../test/setupApiMocks';
import type { SetupTest } from '../../../types';
import { TestStep } from '../TestStep';
import { GEMMA, QWEN, runStatus } from './fixtures';

vi.mock('../../../services/api');

const SAID = 'the model server at http://127.0.0.1:13305 did not answer.';

const row = (job: SetupTest['job'], state: SetupTest['state'], over: Partial<SetupTest> = {}): SetupTest => ({
    job, subject: null, state, error: null, kind: null, seconds: null, ...over,
});

const passedModel = row('chat', 'passed', { subject: QWEN.id, seconds: 12 });
const failedOnNative = row('agent', 'failed', {
    subject: QWEN.id, kind: 'agent', agent: 'gaiacpp', agent_error: SAID,
    error: `The test chat runs on gaiacpp, the beta agent, and it didn't answer: ${SAID}`,
});

function show(tests: SetupTest[], state: 'verifying' | 'failed' | 'cancelled' = 'failed') {
    const handlers = { onRetry: vi.fn(), onChooseAnother: vi.fn(), onDownloadAgain: vi.fn() };
    render(
        <TestStep
            run={runStatus([], { state, tests })}
            expected={[]}
            names={{ [QWEN.id]: QWEN.name, [GEMMA.id]: GEMMA.name }}
            catalog={[QWEN, GEMMA]}
            busy={false}
            {...handlers}
        />,
    );
    return handlers;
}

const finish = () => screen.getByRole('button', { name: 'Finish setup on Full-featured' });

describe('Test step, when the chat failed on gaiacpp', () => {
    beforeEach(() => {
        vi.resetAllMocks();
        vi.mocked(api.getAgentPrograms).mockResolvedValue({ ...AGENT_PROGRAMS, current: 'gaiacpp' });
        vi.mocked(api.updateSettings).mockResolvedValue({ ...SETTINGS, flagship_agent: 'gaiapy' });
    });

    it('names the agent, shows what it said, and does not send the user to another model', () => {
        show([passedModel, failedOnNative]);
        const chat = screen.getByText(/A real chat with GAIA/).closest('li') as HTMLElement;
        expect(chat).toHaveTextContent("The test chat runs on Fast and light, the beta agent, and it didn't answer. The model passed its own test.");
        expect(chat).toHaveTextContent(`Fast and light said: ${SAID}`);
        expect(chat).not.toHaveTextContent("GAIA's assistant didn't answer a test message.");
        expect(screen.queryByRole('button', { name: 'Choose another model' })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Test again' })).toBeInTheDocument();
        expect(finish()).toBeInTheDocument();
        expect(screen.getByRole('alert')).toHaveTextContent("Fast and light, the beta agent, didn't answer the test chat");
    });

    it('switches to the Python agent only on the button, then runs the test again', async () => {
        const { onRetry } = show([passedModel, failedOnNative]);
        expect(api.updateSettings).not.toHaveBeenCalled();
        await userEvent.click(finish());
        await waitFor(() => expect(onRetry).toHaveBeenCalledTimes(1));
        expect(api.updateSettings).toHaveBeenCalledWith({ flagship_agent: 'gaiapy' });
    });

    it('keeps gaiacpp and runs nothing when the Python agent is not on this PC', async () => {
        vi.mocked(api.getAgentPrograms).mockResolvedValue({
            ...AGENT_PROGRAMS, current: 'gaiacpp',
            programs: [{ ...AGENT_PROGRAMS.programs[0], installed: false }, AGENT_PROGRAMS.programs[1]],
        });
        const { onRetry } = show([passedModel, failedOnNative]);
        await userEvent.click(finish());
        expect(await screen.findByText(/Couldn't switch to Full-featured: it isn’t installed on this PC, so gaiacpp stays your agent\./)).toBeInTheDocument();
        expect(api.updateSettings).not.toHaveBeenCalled();
        expect(onRetry).not.toHaveBeenCalled();
    });

    it('says why a refused switch failed and runs nothing', async () => {
        vi.mocked(api.updateSettings).mockRejectedValue(new Error('GAIA is answering in a chat'));
        const { onRetry } = show([passedModel, failedOnNative]);
        await userEvent.click(finish());
        expect(await screen.findByText(/Couldn't switch to Full-featured: GAIA is answering in a chat/)).toBeInTheDocument();
        expect(onRetry).not.toHaveBeenCalled();
    });

    it('keeps the model message when the chat failed on the Python agent', () => {
        show([passedModel, row('agent', 'failed', { subject: QWEN.id, kind: 'agent', error: 'got no answer' })]);
        expect(screen.getByText("GAIA's assistant didn't answer a test message.")).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Choose another model' })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Finish setup on Full-featured' })).not.toBeInTheDocument();
    });

    it('offers no switch while the tests are still running', () => {
        show([passedModel, failedOnNative], 'verifying');
        expect(screen.queryByRole('button', { name: 'Finish setup on Full-featured' })).not.toBeInTheDocument();
    });
});
