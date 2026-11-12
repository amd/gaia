// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A Settings > Setup row lands on the setting it names, with a way back (#7403). */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { useEffect, useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsDialog } from '../SettingsDialog';
import { useChatStore } from '../../../stores/chatStore';

vi.mock('../../../services/api');
vi.mock('../GeneralSettings', () => ({ GeneralSettings: () => <p>general pane</p> }));
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
                        <summary>GPU memory</summary>
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

beforeEach(() => {
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

    it('shows no way back when the section was picked from the list', () => {
        render(<SettingsDialog onUseSkill={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Model' }));
        expect(screen.queryByRole('button', { name: /Back to/ })).not.toBeInTheDocument();
    });
});
