// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Skills: what this chat is using, how each came in, and every way that can't be listed. */

import { act, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SkillsSection } from '../SkillsSection';
import { useChatStore } from '../../../stores/chatStore';
import { tEn } from '../../../i18n/useT';
import { resetPanelState, SID, stubPanelApi } from './testUtils';

vi.mock('../../../services/api');

let mocked: ReturnType<typeof stubPanelApi>;

const SKILLS = [
    { name: 'gaia-voice', how: 'always' as const },
    { name: 'analyze-recording', how: 'auto' as const },
    { name: 'summarize', how: 'model' as const },
];

function httpError(httpStatus: number, detail: string) {
    return Object.assign(new Error(detail), { detail, httpStatus });
}

beforeEach(() => {
    vi.clearAllMocks();
    mocked = stubPanelApi();
    resetPanelState();
    useChatStore.setState({ skillUses: 0 });
});

describe('SkillsSection', () => {
    it('starts open and lists each skill with how it was loaded', async () => {
        mocked.getCapabilities.mockResolvedValue({ session_id: SID, agent_type: 'gaia', tools: [], skills: SKILLS });
        render(<SkillsSection sessionId={SID} busy={false} />);

        const list = await screen.findByRole('list', { name: 'Skills this chat is using' });
        const items = within(list).getAllByRole('listitem');
        expect(items.map((li) => li.querySelector('code')?.textContent))
            .toEqual(['gaia-voice', 'analyze-recording', 'summarize']);
        expect(items[0]).toHaveTextContent(tEn('skills.how.always'));
        expect(items[1]).toHaveTextContent(tEn('skills.how.auto'));
        expect(items[2]).toHaveTextContent(tEn('skills.how.model'));
    });

    it('says so when no skill is in use', async () => {
        render(<SkillsSection sessionId={SID} busy={false} />);
        expect(await screen.findByText(tEn('skills.empty'))).toBeInTheDocument();
    });

    it('hides the section for an agent running elsewhere, which does not report skills', async () => {
        mocked.getCapabilities.mockResolvedValue({ session_id: SID, agent_type: 'gaia', tools: [], skills: null });
        render(<SkillsSection sessionId={SID} busy={false} />);

        await waitFor(() => expect(screen.queryByText(tEn('sp.checking'))).not.toBeInTheDocument());
        expect(screen.queryByText(tEn('skills.title'))).not.toBeInTheDocument();
        expect(screen.queryByText(tEn('skills.empty'))).not.toBeInTheDocument();
    });

    it('says the agent has not started yet on a 409', async () => {
        mocked.getCapabilities.mockRejectedValue(httpError(409, 'not running'));
        render(<SkillsSection sessionId={SID} busy={false} />);

        expect(await screen.findByRole('status')).toHaveTextContent(tEn('skills.notRunning'));
    });

    it('reads again while a reply runs, so a turn paused on a permission prompt is not called "not running"', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        try {
            mocked.getCapabilities
                .mockRejectedValueOnce(httpError(409, 'not running'))
                .mockResolvedValue({ session_id: SID, agent_type: 'gaia', tools: [], skills: SKILLS });
            render(<SkillsSection sessionId={SID} busy />);
            expect(await screen.findByRole('status')).toHaveTextContent(tEn('skills.notRunning'));

            await act(async () => { await vi.advanceTimersByTimeAsync(3000); });

            expect(await screen.findByRole('list', { name: 'Skills this chat is using' })).toBeInTheDocument();
        } finally {
            vi.useRealTimers();
        }
    });

    it('treats a response with no skills field as an error', async () => {
        mocked.getCapabilities.mockResolvedValue({ session_id: SID, agent_type: 'gaia', tools: [] });
        render(<SkillsSection sessionId={SID} busy={false} />);

        expect(await screen.findByText(/list of skills/)).toHaveClass('sp-error');
    });

    it('reads again when a skill is picked up mid-reply', async () => {
        render(<SkillsSection sessionId={SID} busy />);
        await screen.findByText(tEn('skills.empty'));
        mocked.getCapabilities.mockResolvedValue({ session_id: SID, agent_type: 'gaia', tools: [], skills: SKILLS });

        act(() => useChatStore.getState().addSkillStep(null));

        expect(await screen.findByText('analyze-recording')).toBeInTheDocument();
    });
});
