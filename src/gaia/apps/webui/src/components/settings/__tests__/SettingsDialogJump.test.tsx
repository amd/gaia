// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A Settings > Setup row lands on the setting it names, with a way back (#7403). */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { useEffect, useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsDialog } from '../SettingsDialog';
import { useChatStore } from '../../../stores/chatStore';

vi.mock('../../../services/api');
vi.mock('../GeneralSettings', () => ({
    // Like the desktop app's switches: disabled until the tray has sent their values.
    GeneralSettings: () => {
        const [read, setRead] = useState(false);
        useEffect(() => { const id = setTimeout(() => setRead(true), 200); return () => clearTimeout(id); }, []);
        return (
            <div className="settings-pane">
                <p>general pane</p>
                <div className="settings-field">
                    <input id="desktop-startOnLogin" type="checkbox" role="switch" aria-label="Start GAIA when you sign in" disabled={!read} />
                </div>
            </div>
        );
    },
}));
vi.mock('../SetupSettings', () => ({ SetupSettings: () => <p>setup pane</p> }));
vi.mock('../ModelSettings', () => ({
    // Like the real pane: the setting appears only once its data has loaded.
    ModelSettings: () => {
        const [loaded, setLoaded] = useState(false);
        useEffect(() => { const id = setTimeout(() => setLoaded(true), 50); return () => clearTimeout(id); }, []);
        return (
            <div className="settings-pane">
                <h2 className="settings-pane-title">Model</h2>
                {loaded && (
                    <details className="settings-disclosure">
                        <summary><span id="gpu-title">GPU memory</span></summary>
                        <div className="settings-field">
                            <label htmlFor="ctx-size">Context window</label>
                            <select id="ctx-size"><option>64K</option></select>
                        </div>
                    </details>
                )}
            </div>
        );
    },
}));

/** The one observer the dialog makes per jump; `fire` plays a layout change. */
let resized: { fire: () => void; watching: Element[] } | null = null;

beforeEach(() => {
    resized = null;
    vi.stubGlobal('ResizeObserver', class {
        watching: Element[] = [];
        constructor(private readonly cb: () => void) { resized = { fire: () => { if (this.watching.length) this.cb(); }, watching: this.watching }; }
        observe(el: Element) { this.watching.push(el); }
        disconnect() { this.watching.length = 0; }
    });
    vi.useFakeTimers();
    vi.spyOn(HTMLElement.prototype, 'scrollIntoView').mockClear();
    useChatStore.setState({ settingsSection: 'setup', settingsJump: null });
});

describe('SettingsDialog jump from Setup', () => {
    it('opens the closed section, scrolls to the setting, focuses it, and offers a way back', async () => {
        render(<SettingsDialog onUseSkill={vi.fn()} />);
        act(() => useChatStore.getState().jumpToSetting({ section: 'model', target: '#ctx-size', from: 'setup' }));
        await act(async () => { await vi.advanceTimersByTimeAsync(100); });

        const field = screen.getByLabelText('Context window');
        expect(field).toHaveFocus();
        expect((field.closest('details') as HTMLDetailsElement).open).toBe(true);
        expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
        expect(field.closest('.settings-field')).toHaveClass('settings-target-hit');

        await act(async () => { await vi.advanceTimersByTimeAsync(1700); });
        expect(field.closest('.settings-field')).not.toHaveClass('settings-target-hit');

        fireEvent.click(screen.getByRole('button', { name: 'Back to Setup' }));
        expect(screen.getByText('setup pane')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Setup' })).toHaveFocus();
        expect(screen.queryByRole('button', { name: 'Back to Setup' })).not.toBeInTheDocument();
    });

    it('keeps the way back outside the scrolling pane, clear of the tab title (#7699)', async () => {
        render(<SettingsDialog onUseSkill={vi.fn()} />);
        act(() => useChatStore.getState().jumpToSetting({ section: 'model', target: '#ctx-size', from: 'setup' }));
        await act(async () => { await vi.advanceTimersByTimeAsync(100); });
        expect(screen.getByRole('button', { name: 'Back to Setup' }).closest('.settings-scroll')).toBeNull();
    });

    it('holds the setting in view while rows above it load, until the user scrolls (#7700)', async () => {
        render(<SettingsDialog onUseSkill={vi.fn()} />);
        act(() => useChatStore.getState().jumpToSetting({ section: 'model', target: '#ctx-size', from: 'setup' }));
        await act(async () => { await vi.advanceTimersByTimeAsync(100); });
        const scrolls = vi.mocked(HTMLElement.prototype.scrollIntoView);
        const landed = scrolls.mock.calls.length;

        act(() => resized?.fire());
        expect(scrolls.mock.calls.length).toBe(landed + 1);

        fireEvent.wheel(document.querySelector('.settings-scroll') as HTMLElement);
        act(() => resized?.fire());
        expect(scrolls.mock.calls.length).toBe(landed + 1);
    });

    it('keeps holding the setting when rows above it arrive after five seconds', async () => {
        render(<SettingsDialog onUseSkill={vi.fn()} />);
        act(() => useChatStore.getState().jumpToSetting({ section: 'model', target: '#gpu-title', from: 'setup' }));
        await act(async () => { await vi.advanceTimersByTimeAsync(100); });
        const scrolls = vi.mocked(HTMLElement.prototype.scrollIntoView);
        await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
        const before = scrolls.mock.calls.length;

        act(() => resized?.fire());
        expect(scrolls.mock.calls.length).toBe(before + 1);
    });

    it('waits for a switch to be enabled, then focuses it', async () => {
        render(<SettingsDialog onUseSkill={vi.fn()} />);
        act(() => useChatStore.getState().jumpToSetting({ section: 'general', target: '#desktop-startOnLogin', from: 'setup' }));
        await act(async () => { await vi.advanceTimersByTimeAsync(50); });
        expect(screen.getByRole('button', { name: 'General' })).toHaveFocus();

        await act(async () => { await vi.advanceTimersByTimeAsync(300); });
        expect(screen.getByRole('switch', { name: 'Start GAIA when you sign in' })).toHaveFocus();
    });

    it('moves focus to the tab it opened when the setting is not there', async () => {
        render(<SettingsDialog onUseSkill={vi.fn()} />);
        act(() => screen.getByRole('button', { name: 'Setup' }).focus());
        act(() => useChatStore.getState().jumpToSetting({ section: 'model', target: '#no-such-setting', from: 'setup' }));
        await act(async () => { await vi.advanceTimersByTimeAsync(100); });
        expect(screen.getByRole('button', { name: 'Model' })).toHaveFocus();
    });

    it('focuses the heading\'s own summary when a row points at a section title', async () => {
        render(<SettingsDialog onUseSkill={vi.fn()} />);
        act(() => useChatStore.getState().jumpToSetting({ section: 'model', target: '#gpu-title', from: 'setup' }));
        await act(async () => { await vi.advanceTimersByTimeAsync(100); });
        expect(screen.getByText('GPU memory').closest('summary')).toHaveFocus();
    });

    it('shows no way back when the section was picked from the list', () => {
        render(<SettingsDialog onUseSkill={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Model' }));
        expect(screen.queryByRole('button', { name: /Back to/ })).not.toBeInTheDocument();
    });
});
