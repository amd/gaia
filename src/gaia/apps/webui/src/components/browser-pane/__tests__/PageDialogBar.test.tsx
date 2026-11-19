// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PageDialogBar } from '../PageDialogBar';
import type { GaiaBrowserPaneAPI, PageDialog, TabId, TabState } from '../../../../services/browser-pane/contract';

const TAB = 'tab-1' as TabId;

function tabWith(dialog: PageDialog | null): TabState {
    return {
        id: TAB, url: 'http://127.0.0.1:5000/dialogs.html', title: 'Boxes', loading: false,
        canGoBack: false, canGoForward: false, devToolsOpen: false, notice: null,
        openedBy: null, agentBusy: false, agentActing: false, control: 'shared', viewport: null, dialog,
    };
}

function box(over: Partial<PageDialog>): PageDialog {
    return { type: 'confirm', message: 'Save this draft?', defaultText: '', url: 'http://127.0.0.1:5000/dialogs.html', openedBy: 'page', ...over };
}

function show(dialog: PageDialog | null) {
    const api = { answerDialog: vi.fn(() => Promise.resolve()) } as unknown as GaiaBrowserPaneAPI;
    render(<PageDialogBar tab={tabWith(dialog)} api={api} />);
    return api;
}

describe('PageDialogBar', () => {
    it('shows nothing when the page has no box', () => {
        show(null);
        expect(screen.queryByRole('alertdialog')).toBeNull();
    });

    it('shows a confirm with the site it came from, and answers OK or Cancel', async () => {
        const api = show(box({}));
        expect(screen.getByRole('alertdialog', { name: '127.0.0.1:5000 says' })).toHaveTextContent('Save this draft?');
        await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
        expect(api.answerDialog).toHaveBeenLastCalledWith(TAB, false, null);
        await userEvent.click(screen.getByRole('button', { name: 'OK' }));
        expect(api.answerDialog).toHaveBeenLastCalledWith(TAB, true, null);
    });

    it('an alert has only OK', () => {
        show(box({ type: 'alert', message: 'Your trial ends in 3 days.' }));
        expect(screen.getByRole('button', { name: 'OK' })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull();
    });

    it('a prompt starts with its default text and sends what the user typed', async () => {
        const api = show(box({ type: 'prompt', message: 'What should we call you?', defaultText: 'friend' }));
        const field = screen.getByRole('textbox', { name: 'Your answer' });
        expect(field).toHaveValue('friend');
        await userEvent.clear(field);
        await userEvent.type(field, 'Ada{Enter}');
        expect(api.answerDialog).toHaveBeenLastCalledWith(TAB, true, 'Ada');
    });

    it('"leave this page?" offers Leave and Stay', async () => {
        const api = show(box({ type: 'beforeunload', message: 'Leave this page? Changes you made may not be saved.' }));
        await userEvent.click(screen.getByRole('button', { name: 'Stay' }));
        expect(api.answerDialog).toHaveBeenLastCalledWith(TAB, false, null);
        await userEvent.click(screen.getByRole('button', { name: 'Leave' }));
        expect(api.answerDialog).toHaveBeenLastCalledWith(TAB, true, null);
    });
});
