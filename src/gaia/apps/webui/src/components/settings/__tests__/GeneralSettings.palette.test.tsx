// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { GeneralSettings } from '../GeneralSettings';
import { useChatStore } from '../../../stores/chatStore';

afterEach(() => {
    useChatStore.getState().setPalette('default');
    localStorage.clear();
});

describe('Settings > General colors', () => {
    it('keeps the warm gray default and switches to a palette in one click, remembered', () => {
        render(<GeneralSettings />);
        expect(screen.getByRole('radio', { name: /Warm gray/ })).toHaveAttribute('aria-checked', 'true');
        expect(document.documentElement.dataset.palette).toBeUndefined();

        fireEvent.click(screen.getByRole('radio', { name: /Blue marble/ }));
        expect(document.documentElement.dataset.palette).toBe('marble');
        expect(localStorage.getItem('gaia-chat-palette')).toBe('marble');
    });

    it('offers custom colors that apply as they are picked', () => {
        render(<GeneralSettings />);
        fireEvent.click(screen.getByRole('radio', { name: /Custom/ }));
        const accent = screen.getByLabelText(/Accent/) as HTMLInputElement;
        fireEvent.change(accent, { target: { value: '#e91e63' } });
        expect(document.documentElement.dataset.palette).toBe('custom');
        expect(document.documentElement.style.getPropertyValue('--accent-solid')).not.toBe('');
        expect(JSON.parse(localStorage.getItem('gaia-chat-custom-colors') ?? '{}').accent).toBe('#E91E63');

        fireEvent.click(screen.getByRole('radio', { name: /Warm gray/ }));
        expect(document.documentElement.style.getPropertyValue('--accent-solid')).toBe('');
    });
});
