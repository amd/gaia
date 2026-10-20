// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The session panel's container (#5279): docking, size, sections, overlay, and when it reads. */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SessionPanel, SessionPanelToggle } from '../SessionPanel';
import { useSessionPanelStore, PANEL_DEFAULT_WIDTH, PANEL_MIN_WIDTH } from '../../../stores/sessionPanelStore';
import { useChatStore } from '../../../stores/chatStore';
import { useModelStore } from '../../../stores/modelStore';
import type { Message, TurnSummary } from '../../../types';
import { resetPanelState, SID, stubPanelApi, stubWindowWidth } from './testUtils';

vi.mock('../../../services/api');

const SUMMARY: TurnSummary = {
    pages: [],
    documents: [],
    files: [],
    sites: [],
    live_data: { attempted: 2, failed: 2 },
    cost: { kind: 'local' },
    model: { remote: false, provider: 'lemonade', provider_name: 'Lemonade', name: 'Gemma' },
};

const ANSWERED: Message[] = [
    { id: 1, session_id: SID, role: 'user', content: 'q', created_at: '', rag_sources: null },
    { id: 2, session_id: SID, role: 'assistant', content: 'a', created_at: '', rag_sources: null, turnSummary: SUMMARY },
];

let mocked: ReturnType<typeof stubPanelApi>;

function Panel({ busy = false, messages = ANSWERED, onJump = vi.fn(() => true) }: { busy?: boolean; messages?: Message[]; onJump?: (id: number) => boolean }) {
    return (
        <>
            <SessionPanelToggle messages={messages} busy={busy} />
            <SessionPanel sessionId={SID} messages={messages} busy={busy} onJumpToMessage={onJump} />
        </>
    );
}

function panel() {
    return screen.getByRole('complementary', { name: 'Session panel' });
}

beforeEach(() => {
    vi.clearAllMocks();
    mocked = stubPanelApi();
    resetPanelState();
    stubWindowWidth(false);
    useChatStore.setState({
        sessions: [{ id: SID, title: 't', created_at: '', updated_at: '', model: 'm', system_prompt: null, message_count: 2, document_ids: [], inference_remote: false } as never],
        currentSessionId: SID,
    });
});

afterEach(() => {
    delete (window as { matchMedia?: unknown }).matchMedia;
    vi.useRealTimers();
});

describe('SessionPanel', () => {
    it("names this turn's model the way the model chip does, not by its raw id", () => {
        useModelStore.setState({ names: { 'Gemma-4-E4B-it-GGUF': 'Gemma 4 E4B' } });
        const local = { ...SUMMARY, model: { ...SUMMARY.model!, name: 'Gemma-4-E4B-it-GGUF' } };
        render(<Panel messages={[ANSWERED[0], { ...ANSWERED[1], turnSummary: local }]} />);
        expect(screen.getByText('Gemma 4 E4B')).toHaveAttribute('title', 'Gemma-4-E4B-it-GGUF');
        useModelStore.setState({ names: {} });
    });

    it('is docked open by default with every section', async () => {
        render(<Panel />);
        expect(panel()).toHaveClass('is-docked');
        for (const name of ['This answer', 'Environment', 'Context & performance', 'Outputs']) {
            expect(screen.getByRole('button', { name: new RegExp(`^${name}`) })).toBeInTheDocument();
        }
        await waitFor(() => expect(mocked.getOutputs).toHaveBeenCalledWith(SID));
    });

    it('closes, remembers it, and reopens from the header toggle', () => {
        const { unmount } = render(<Panel />);
        fireEvent.click(screen.getByRole('button', { name: 'Close session panel' }));
        expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
        unmount();
        useSessionPanelStore.getState().reload();
        render(<Panel />);
        expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Show session panel/ }));
        expect(panel()).toBeInTheDocument();
    });

    it("reads nothing while closed, and nothing on a timer while open", async () => {
        useSessionPanelStore.getState().setOpen(false);
        vi.useFakeTimers();
        const { rerender } = render(<Panel />);
        await act(async () => { vi.advanceTimersByTime(60_000); });
        expect(mocked.getOutputs).not.toHaveBeenCalled();
        expect(mocked.getEnvironment).not.toHaveBeenCalled();
        expect(mocked.getSessionChanges).not.toHaveBeenCalled();

        act(() => useSessionPanelStore.getState().setOpen(true));
        rerender(<Panel />);
        await act(async () => { await Promise.resolve(); });
        const calls = mocked.getOutputs.mock.calls.length;
        expect(calls).toBe(1);
        await act(async () => { vi.advanceTimersByTime(120_000); });
        expect(mocked.getOutputs.mock.calls.length).toBe(calls);
    });

    it('refreshes its sections when a reply ends', async () => {
        const { rerender } = render(<Panel busy={false} />);
        await waitFor(() => expect(mocked.getOutputs).toHaveBeenCalledTimes(1));
        rerender(<Panel busy />);
        rerender(<Panel busy={false} />);
        await waitFor(() => expect(mocked.getOutputs).toHaveBeenCalledTimes(2));
        expect(mocked.getSessionChanges).toHaveBeenCalledTimes(2);
    });

    it('collapses a section, stops reading it, and remembers it', async () => {
        render(<Panel />);
        await waitFor(() => expect(mocked.getOutputs).toHaveBeenCalledTimes(1));
        const head = screen.getByRole('button', { name: /^Outputs/ });
        fireEvent.click(head);
        expect(head).toHaveAttribute('aria-expanded', 'false');
        expect(useSessionPanelStore.getState().sections.outputs).toBe(false);
        expect(JSON.parse(window.localStorage.getItem('gaia.sessionPanel.sections') ?? '{}').outputs).toBe(false);
    });

    it('resizes from the keyboard within its limits and remembers the width', () => {
        render(<Panel />);
        const handle = screen.getByRole('separator', { name: 'Resize session panel' });
        expect(handle).toHaveAttribute('aria-valuenow', String(PANEL_DEFAULT_WIDTH));
        fireEvent.keyDown(handle, { key: 'ArrowLeft' });
        expect(useSessionPanelStore.getState().width).toBe(PANEL_DEFAULT_WIDTH + 16);
        fireEvent.keyDown(handle, { key: 'Home' });
        expect(useSessionPanelStore.getState().width).toBe(PANEL_MIN_WIDTH);
        expect(window.localStorage.getItem('gaia.sessionPanel.width')).toBe(String(PANEL_MIN_WIDTH));
        fireEvent.doubleClick(handle);
        expect(useSessionPanelStore.getState().width).toBe(PANEL_DEFAULT_WIDTH);
    });

    it('resizes by dragging its left edge', () => {
        render(<Panel />);
        const handle = screen.getByRole('separator', { name: 'Resize session panel' });
        fireEvent.pointerDown(handle, { button: 0, clientX: 1000, pointerId: 1 });
        fireEvent.pointerMove(handle, { clientX: 960, pointerId: 1 });
        expect(panel().style.width).toBe(`${PANEL_DEFAULT_WIDTH + 40}px`);
        fireEvent.pointerUp(handle, { clientX: 960, pointerId: 1 });
        expect(useSessionPanelStore.getState().width).toBe(PANEL_DEFAULT_WIDTH + 40);
    });

    it('opens as an overlay on a narrow window and closes on Escape', () => {
        stubWindowWidth(true);
        render(<Panel />);
        expect(screen.queryByRole('dialog', { name: 'Session panel' })).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Show session panel/ }));
        const overlay = screen.getByRole('dialog', { name: 'Session panel' });
        expect(overlay).toHaveClass('is-overlay');
        expect(screen.queryByRole('separator')).not.toBeInTheDocument();
        fireEvent.keyDown(document, { key: 'Escape' });
        expect(screen.queryByRole('dialog', { name: 'Session panel' })).not.toBeInTheDocument();
        // The docked preference is untouched by the overlay.
        expect(useSessionPanelStore.getState().open).toBe(true);
    });

    it('warns on the closed toggle when every live-data lookup failed', () => {
        useSessionPanelStore.getState().setOpen(false);
        render(<Panel />);
        expect(screen.getByRole('button', { name: "Show session panel: couldn't get live data" })).toBeInTheDocument();
    });

    it("opens the context section when asked, e.g. from the meter's Details", () => {
        useSessionPanelStore.getState().setOpen(false);
        render(<Panel />);
        expect(useSessionPanelStore.getState().sections.context).toBe(false);
        act(() => useSessionPanelStore.getState().reveal('context'));
        expect(panel()).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /^Context & performance/ })).toHaveAttribute('aria-expanded', 'true');
        expect(mocked.getContextUsage).toHaveBeenCalledWith(SID);
    });

    it('hides "This answer" until there is an answer to describe', () => {
        render(<Panel messages={[]} />);
        expect(screen.queryByRole('button', { name: /^This answer/ })).not.toBeInTheDocument();
    });

    it('survives a broken remembered setting', () => {
        window.localStorage.setItem('gaia.sessionPanel.sections', '{not json');
        useSessionPanelStore.getState().reload();
        render(<Panel />);
        expect(panel()).toBeInTheDocument();
    });
});
