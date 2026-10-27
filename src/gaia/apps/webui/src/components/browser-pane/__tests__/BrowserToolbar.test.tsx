// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AgentStrip, BrowserToolbar, PickStrip } from '../BrowserToolbar';
import type { DevServerState, GaiaBrowserPaneAPI, PaneState, TabId, TabState } from '../../../../services/browser-pane/contract';

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
        openedBy: null,
        agentBusy: false,
        agentActing: false,
        control: 'shared',
        viewport: null,
        dialog: null,
        ...over,
    };
}

function pane(tabs: TabState[], active = tabs[0].id): PaneState {
    return { visible: true, occluded: false, tabs, activeTabId: active, recent: [], picking: false, devServers: [] };
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
        setActiveSession: vi.fn(),
        setControl: ok(),
        setViewport: ok(),
        togglePicking: ok(),
        onPicked: vi.fn(() => () => {}),
        stopDevServer: ok(),
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

describe('Responsive', () => {
    const open = () => userEvent.click(screen.getByRole('button', { name: 'Responsive' }));

    it('a device preset emulates its screen, keeping the color scheme', async () => {
        const { api } = renderToolbar(pane([tab({ viewport: { size: null, colorScheme: 'dark', source: 'user' } })]));
        await open();
        await userEvent.click(screen.getByRole('button', { name: 'iPad Air' }));
        expect(api.setViewport).toHaveBeenCalledWith('tab-1', { size: { width: 820, height: 1180, mobile: true }, colorScheme: 'dark' });
    });

    it('rotate swaps width and height, and an exact size can be typed', async () => {
        const size = { width: 390, height: 844, mobile: true };
        const { api } = renderToolbar(pane([tab({ viewport: { size, colorScheme: null, source: 'user' } })]));
        await open();
        await userEvent.click(screen.getByRole('button', { name: 'Rotate' }));
        expect(api.setViewport).toHaveBeenLastCalledWith('tab-1', { size: { width: 844, height: 390, mobile: true }, colorScheme: null });
        const width = screen.getByRole('textbox', { name: 'Width' });
        await userEvent.clear(width);
        await userEvent.type(width, '1024');
        expect(width).toHaveValue('1024');
        expect(screen.getByRole('button', { name: 'Set' })).toBeEnabled();
        await userEvent.click(screen.getByRole('button', { name: 'Set' }));
        expect(api.setViewport).toHaveBeenLastCalledWith('tab-1', { size: { width: 1024, height: 844, mobile: false }, colorScheme: null });
    });

    it('dark mode alone keeps the pane size; back to fill and system gives control back', async () => {
        const { api } = renderToolbar(pane([tab()]));
        await open();
        await userEvent.click(screen.getByRole('button', { name: 'Dark' }));
        expect(api.setViewport).toHaveBeenLastCalledWith('tab-1', { size: null, colorScheme: 'dark' });
        await userEvent.click(screen.getByRole('button', { name: 'Fill pane' }));
        expect(api.setViewport).toHaveBeenLastCalledWith('tab-1', null);
    });

    it('says when GAIA chose the size', async () => {
        renderToolbar(pane([tab({ viewport: { size: { width: 390, height: 844, mobile: true }, colorScheme: null, source: 'agent' } })]));
        await open();
        expect(screen.getByText(/GAIA chose this/)).toBeInTheDocument();
    });
});

describe('private tabs', () => {
    it('the menu makes a tab private and shares it again, and the tab shows a lock', async () => {
        const { api } = renderToolbar(pane([tab()]));
        await userEvent.click(screen.getByRole('button', { name: 'More browser actions' }));
        await userEvent.click(screen.getByRole('button', { name: /Make this tab private/ }));
        expect(api.setControl).toHaveBeenCalledWith('tab-1', 'private');
    });

    it('a private tab offers to share it again', async () => {
        const { api } = renderToolbar(pane([tab({ control: 'private' })]));
        expect(screen.getByLabelText('Private')).toBeInTheDocument();
        await userEvent.click(screen.getByRole('button', { name: 'More browser actions' }));
        await userEvent.click(screen.getByRole('button', { name: /Share this tab with GAIA/ }));
        expect(api.setControl).toHaveBeenCalledWith('tab-1', 'shared');
    });
});

describe('AgentStrip', () => {
    const strip = (over: Partial<TabState>) => {
        const api = fakeApi();
        render(<AgentStrip tab={tab(over)} api={api} />);
        return api;
    };

    it('says GAIA is using the tab while it acts, and Pause hands it to the user', async () => {
        const api = strip({ agentActing: true, agentBusy: true });
        expect(screen.getByRole('status')).toHaveTextContent('GAIA is using this tab');
        await userEvent.click(screen.getByRole('button', { name: 'Pause' }));
        expect(api.setControl).toHaveBeenCalledWith('tab-1', 'user');
    });

    it('keeps "reading" for reads', () => {
        strip({ agentBusy: true });
        expect(screen.getByRole('status')).toHaveTextContent('GAIA is reading this page');
    });

    it('a paused tab offers Resume', async () => {
        const api = strip({ control: 'user' });
        expect(screen.getByRole('status')).toHaveTextContent('GAIA is paused in this tab');
        await userEvent.click(screen.getByRole('button', { name: 'Resume' }));
        expect(api.setControl).toHaveBeenCalledWith('tab-1', 'shared');
    });

    it('shows nothing while GAIA is idle in a shared tab', () => {
        strip({});
        expect(screen.queryByRole('status')).toBeNull();
    });
});

describe('Select element', () => {
    it('has nothing to pick on a new tab page', () => {
        renderToolbar(pane([tab({ url: null, title: 'New tab' })]));
        expect(screen.getByRole('button', { name: 'Select element' })).toBeDisabled();
    });

    it('starts and ends picking, and shows when it is on', async () => {
        const { api } = renderToolbar({ ...pane([tab()]), picking: true });
        const button = screen.getByRole('button', { name: 'Select element' });
        expect(button).toHaveAttribute('aria-pressed', 'true');
        await userEvent.click(button);
        expect(api.togglePicking).toHaveBeenCalledTimes(1);
    });

    it('the strip says how to pick, and Done ends it', async () => {
        const api = fakeApi();
        render(<PickStrip api={api} />);
        expect(screen.getByRole('status')).toHaveTextContent('Shift-click adds');
        await userEvent.click(screen.getByRole('button', { name: 'Done' }));
        expect(api.togglePicking).toHaveBeenCalledTimes(1);
    });
});

describe('Dev servers', () => {
    const server = (over: Partial<DevServerState> = {}): DevServerState => ({
        key: '0123456789abcdef',
        name: 'web',
        argv: ['npm', 'run', 'dev'],
        cwd: '/Users/ada/site',
        port: 5173,
        env: {},
        url: 'http://localhost:5173/',
        status: 'ready',
        pid: 4242,
        startedAt: 1,
        exitCode: null,
        logFile: '/tmp/web.log',
        ...over,
    });

    it('lists each server with its status and port, opens it in the pane and stops it', async () => {
        const { api } = renderToolbar({ ...pane([tab()]), devServers: [server()] });
        const button = screen.getByRole('button', { name: 'Dev servers' });
        expect(button).toHaveAttribute('title', 'Dev servers: 1 running');
        await userEvent.click(button);
        const row = screen.getByRole('group', { name: 'Dev server web' });
        expect(row).toHaveTextContent('Running');
        expect(row).toHaveTextContent(':5173');
        expect(row).toHaveTextContent('npm run dev');
        await userEvent.click(screen.getByRole('button', { name: 'Stop' }));
        expect(api.stopDevServer).toHaveBeenCalledWith('0123456789abcdef');
        await userEvent.click(screen.getByRole('button', { name: 'Open in pane' }));
        expect(api.openTab).toHaveBeenCalledWith('http://localhost:5173/');
    });

    it('a stopped server can neither be opened nor stopped again', async () => {
        renderToolbar({ ...pane([tab()]), devServers: [server({ status: 'stopped', pid: null })] });
        await userEvent.click(screen.getByRole('button', { name: 'Dev servers' }));
        expect(screen.getByRole('group', { name: 'Dev server web' })).toHaveTextContent('Stopped');
        expect(screen.getByRole('button', { name: 'Open in pane' })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled();
    });

    it('with none it says how to get one', async () => {
        renderToolbar(pane([tab()]));
        await userEvent.click(screen.getByRole('button', { name: 'Dev servers' }));
        expect(screen.getByText(/reads .gaia\/launch.json/)).toBeInTheDocument();
    });
});
