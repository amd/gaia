// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ShortcutsDialog, SHORTCUTS } from '../ShortcutsDialog';

describe('ShortcutsDialog', () => {
    it('lists every shortcut with its keys', () => {
        render(<ShortcutsDialog onClose={vi.fn()} />);
        const dialog = screen.getByRole('dialog', { name: 'Keyboard shortcuts' });
        expect(dialog.querySelectorAll('.shortcuts-row')).toHaveLength(SHORTCUTS.length);
        expect(dialog).toHaveTextContent('New chat');
        expect(dialog).toHaveTextContent('Search chats');
        expect(dialog).toHaveTextContent('Stop the answer');
        expect(dialog).toHaveTextContent('Shift+Enter');
    });

    it('closes on Escape and on its close button', () => {
        const onClose = vi.fn();
        render(<ShortcutsDialog onClose={onClose} />);
        fireEvent.keyDown(document, { key: 'Escape' });
        fireEvent.click(screen.getByRole('button', { name: 'Close keyboard shortcuts' }));
        expect(onClose).toHaveBeenCalledTimes(2);
    });
});
