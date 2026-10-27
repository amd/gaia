// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserToolbar } from '../BrowserToolbar';
import type { GaiaBrowserPaneAPI, PaneState, TabId, TabState } from '../../../../services/browser-pane/contract';

const id = (s: string) => s as TabId;

function tab(over: Partial<TabState> = {}): TabState {
    return {
        id: id('tab-1'),
        url: 'https://example.com/',
        title: 'Example',
        loading: false,
        canGoBack: false,
        canGoForward: false,
        devToolsOpen: false,
        notice: null,
        ...over,
    };
}

function pane(tabs: TabState[], active = tabs[0].id): PaneState {
    return { visible: true, occluded: false, tabs, activeTabId: active, recent: [] };
}

function fakeApi(): GaiaBrowserPaneAPI {
    const ok = () => vi.fn(() => Promise.resolve());
    return {
        getState: vi.fn(),
        onState: vi.fn(() => () => {}),
        onCommand: vi.fn(() => () => {}),
        setBounds: vi.fn(),
        setVisible: ok(),
        setOccluded: vi.fn(() => Promise.resolve(null)),
        openTab: vi.fn(() => Promise.resolve(id('tab-9'))),
        closeTab: ok(),
        selectTab: ok(),
        navigate: ok(),
        history: ok(),
        toggleDevTools: ok(),
        clearBrowsingData: ok(),
        openExternal: ok(),
    } as unknown as GaiaBrowserPaneAPI;
}

function renderToolbar(state: PaneState, api = fakeApi()) {
    const onClosePane = vi.fn();
    render(<BrowserToolbar pane={state} api={api} onClosePane={onClosePane} addressRef={createRef()} />);
    return { api, onClosePane };
}

describe('BrowserToolbar', () => {
    it('a new tab page has nothing to go back to, reload or inspect', () => {
        renderToolbar(pane([tab({ url: null, title: 'New tab' })]));
        expect(screen.getByRole('button', { name: 'Back' })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Forward' })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Reload' })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Developer tools' })).toBeDisabled();
        expect(screen.getByRole('textbox', { name: 'Address' })).toHaveValue('');
    });

    it('history buttons follow the page and drive the active tab', async () => {
        const { api } = renderToolbar(pane([tab({ canGoBack: true })]));
        expect(screen.getByRole('button', { name: 'Forward' })).toBeDisabled();
        await userEvent.click(screen.getByRole('button', { name: 'Back' }));
        await userEvent.click(screen.getByRole('button', { name: 'Reload' }));
        expect(api.history).toHaveBeenCalledWith('tab-1', 'back');
        expect(api.history).toHaveBeenCalledWith('tab-1', 'reload');
    });

    it('reload turns into stop while the page loads', async () => {
        const { api } = renderToolbar(pane([tab({ loading: true })]));
        expect(screen.queryByRole('button', { name: 'Reload' })).toBeNull();
        await userEvent.click(screen.getByRole('button', { name: 'Stop loading' }));
        expect(api.history).toHaveBeenCalledWith('tab-1', 'stop');
    });

    it('shows every tab, marks the active one, and selects, closes and adds tabs', async () => {
        const tabs = [tab(), tab({ id: id('tab-2'), title: 'Second', url: 'https://two.test/' })];
        const { api } = renderToolbar(pane(tabs, id('tab-2')));
        const [first, second] = screen.getAllByRole('tab');
        expect(first).toHaveAttribute('aria-selected', 'false');
        expect(second).toHaveAttribute('aria-selected', 'true');
        await userEvent.click(first);
        expect(api.selectTab).toHaveBeenCalledWith('tab-1');
        await userEvent.click(screen.getByRole('button', { name: 'Close tab Second' }));
        expect(api.closeTab).toHaveBeenCalledWith('tab-2');
        await userEvent.click(screen.getByRole('button', { name: 'New tab' }));
        expect(api.openTab).toHaveBeenCalledWith(null);
        expect(screen.getByRole('textbox', { name: 'Address' })).toHaveValue('https://two.test/');
    });

    it('a typed host loads over https and a local one over http', async () => {
        const { api } = renderToolbar(pane([tab()]));
        const address = screen.getByRole('textbox', { name: 'Address' });
        await userEvent.clear(address);
        await userEvent.type(address, 'docs.example.com{Enter}');
        expect(api.navigate).toHaveBeenLastCalledWith('tab-1', 'https://docs.example.com/');
        await userEvent.clear(address);
        await userEvent.type(address, 'localhost:5173{Enter}');
        expect(api.navigate).toHaveBeenLastCalledWith('tab-1', 'http://localhost:5173/');
    });

    it('text that is not an address is flagged and never loaded', async () => {
        const { api } = renderToolbar(pane([tab()]));
        const address = screen.getByRole('textbox', { name: 'Address' });
        await userEvent.clear(address);
        await userEvent.type(address, 'what is react{Enter}');
        expect(address).toHaveAttribute('aria-invalid', 'true');
        expect(api.navigate).not.toHaveBeenCalled();
    });

    it('DevTools shows as pressed while open', async () => {
        const { api } = renderToolbar(pane([tab({ devToolsOpen: true })]));
        const devtools = screen.getByRole('button', { name: 'Developer tools' });
        expect(devtools).toHaveAttribute('aria-pressed', 'true');
        await userEvent.click(devtools);
        expect(api.toggleDevTools).toHaveBeenCalledWith('tab-1');
    });

    it('the menu clears browsing data and opens the page in the system browser', async () => {
        const { api } = renderToolbar(pane([tab()]));
        await userEvent.click(screen.getByRole('button', { name: 'More browser actions' }));
        await userEvent.click(screen.getByRole('button', { name: /Open in system browser/ }));
        expect(api.openExternal).toHaveBeenCalledWith('tab-1');
        await userEvent.click(screen.getByRole('button', { name: 'More browser actions' }));
        await userEvent.click(screen.getByRole('button', { name: /Clear browsing data/ }));
        expect(api.clearBrowsingData).toHaveBeenCalled();
    });

    it('the close button closes the pane', async () => {
        const { onClosePane } = renderToolbar(pane([tab()]));
        await userEvent.click(screen.getByRole('button', { name: 'Close browser pane' }));
        expect(onClosePane).toHaveBeenCalled();
    });
});
