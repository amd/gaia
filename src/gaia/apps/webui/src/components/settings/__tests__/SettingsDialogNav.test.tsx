// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The Settings nav marks one section: focus follows the tab you pick (#5260). */

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsDialog } from '../SettingsDialog';
import { useChatStore } from '../../../stores/chatStore';

vi.mock('../../../services/api');
vi.mock('../GeneralSettings', () => ({ GeneralSettings: () => <p>general pane</p> }));
vi.mock('../ModelSettings', () => ({ ModelSettings: () => <p>model pane</p> }));
vi.mock('../PrivacySettings', () => ({ PrivacySettings: () => <p>privacy pane</p> }));

beforeEach(() => {
    useChatStore.setState({ settingsSection: 'general' });
});

describe('SettingsDialog nav', () => {
    it('moves focus to the tab you pick, so only one looks selected', () => {
        render(<SettingsDialog onUseSkill={vi.fn()} />);
        const general = screen.getByRole('button', { name: 'General' });
        expect(general).toHaveFocus();

        const model = screen.getByRole('button', { name: 'Model' });
        fireEvent.click(model);

        expect(model).toHaveFocus();
        expect(model).toHaveAttribute('aria-current', 'page');
        expect(general).not.toHaveAttribute('aria-current');
        expect(screen.getByText('model pane')).toBeInTheDocument();
    });
});
