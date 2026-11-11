// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Settings is modal: Tab must not reach the page behind it, such as the sidebar resize handle. */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsDialog } from '../SettingsDialog';
import { useChatStore } from '../../../stores/chatStore';

vi.mock('../../../services/api');
vi.mock('../GeneralSettings', () => ({
    GeneralSettings: () => <button type="button">Last in pane</button>,
}));

beforeEach(() => {
    useChatStore.setState({ settingsSection: 'general' });
});

describe('SettingsDialog focus', () => {
    it('wraps Tab from the last control back to the first, never to the page behind', async () => {
        const user = userEvent.setup();
        render(
            <>
                <SettingsDialog onUseSkill={vi.fn()} />
                <div role="separator" tabIndex={0} aria-label="Resize sidebar" />
            </>,
        );
        const first = screen.getByRole('button', { name: 'General' });
        const last = screen.getByRole('button', { name: 'Last in pane' });
        last.focus();

        await user.tab();
        expect(first).toHaveFocus();

        await user.tab({ shift: true });
        expect(last).toHaveFocus();
    });
});
