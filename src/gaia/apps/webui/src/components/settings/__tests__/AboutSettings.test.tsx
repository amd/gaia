// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AboutSettings } from '../AboutSettings';
import { useChatStore } from '../../../stores/chatStore';

beforeEach(() => {
    useChatStore.setState({ settingsSection: 'about' });
});

describe('AboutSettings', () => {
    it('shows the version up front and copies it', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        Object.assign(navigator, { clipboard: { writeText } });
        render(<AboutSettings />);
        expect(screen.getByText(`GAIA v${__APP_VERSION__}`)).toBeInTheDocument();
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Copy version' })); });
        expect(writeText).toHaveBeenCalledWith(`GAIA v${__APP_VERSION__}`);
        expect(screen.getByRole('button', { name: 'Copied' })).toBeInTheDocument();
    });

    it('says why the version could not be copied', async () => {
        Object.assign(navigator, { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } });
        render(<AboutSettings />);
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Copy version' })); });
        expect(screen.getByRole('alert')).toHaveTextContent('denied');
    });

    it('links release notes, docs, problem reports and the license', () => {
        render(<AboutSettings />);
        expect(screen.getByRole('link', { name: /What’s new/ })).toHaveAttribute('href', 'https://github.com/amd/gaia/releases');
        expect(screen.getByRole('link', { name: /Documentation/ })).toHaveAttribute('href', 'https://amd-gaia.ai/docs');
        expect(screen.getByRole('link', { name: /Report a problem/ })).toHaveAttribute('rel', 'noopener noreferrer');
        expect(screen.getByRole('link', { name: /License/ })).toBeInTheDocument();
    });

    it('sends the user to Privacy to save a troubleshooting report', () => {
        render(<AboutSettings />);
        fireEvent.click(screen.getByRole('button', { name: 'Save a report in Privacy' }));
        expect(useChatStore.getState().settingsSection).toBe('privacy');
    });
});
