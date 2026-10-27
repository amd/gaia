// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The pane's page is a native view that paints over the DOM, so every app
 * overlay must hide it. useAppOverlayOpen reads the escape-layer registry;
 * these tests pin that overlays join it and that the pane reacts.
 */

import { useRef, useState } from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useAppOverlayOpen } from '../../../hooks/useAppOverlayOpen';
import { useEscapeToClose } from '../../../hooks/useEscapeToClose';
import { Popover } from '../../Popover';
import { BrowserPane } from '../BrowserPane';
import { useBrowserPaneStore } from '../../../stores/browserPaneStore';
import { useSessionPanelStore } from '../../../stores/sessionPanelStore';
import { useChatStore } from '../../../stores/chatStore';
import type { GaiaBrowserPaneAPI, PaneState, TabId } from '../../../../services/browser-pane/contract';

beforeAll(() => {
    globalThis.ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
    } as unknown as typeof ResizeObserver;
});

function Modal({ onClose }: { onClose: () => void }) {
    useEscapeToClose(true, onClose);
    return <div role="dialog" aria-modal="true" aria-label="Some modal" />;
}

function MenuButton() {
    const [open, setOpen] = useState(false);
    const anchor = useRef<HTMLButtonElement>(null);
    return (
        <>
            <button ref={anchor} onClick={() => setOpen(true)}>Menu</button>
            <Popover open={open} onClose={() => setOpen(false)} anchor={anchor} label="Menu">
                <button>Item</button>
            </Popover>
        </>
    );
}

describe('useAppOverlayOpen', () => {
    it('is true while a modal is open and false once it closes', () => {
        const { result } = renderHook(() => useAppOverlayOpen());
        expect(result.current).toBe(false);
        const modal = render(<Modal onClose={() => {}} />);
        expect(result.current).toBe(true);
        modal.unmount();
        expect(result.current).toBe(false);
    });

    it('follows a popover menu', async () => {
        const { result } = renderHook(() => useAppOverlayOpen());
        render(<MenuButton />);
        await userEvent.click(screen.getByRole('button', { name: 'Menu' }));
        expect(result.current).toBe(true);
        await userEvent.keyboard('{Escape}');
        expect(result.current).toBe(false);
    });
});

/**
 * Every component that draws a dialog, menu, listbox or fixed overlay must
 * join the escape-layer registry, directly or through Popover. A component
 * listed here is exempt only for the reason given.
 */
const NOT_OVER_THE_PANE: Record<string, string> = {
    'components/PermissionPrompt.tsx': 'drawn inline above the composer, which the pane never covers',
    'components/InfoBubble.tsx': 'only opens inside the model menu, a Popover',
    'components/browser-pane/PageDialogBar.tsx': "a bar in the pane's own toolbar area, above the page, never over it",
};

const OVERLAY_MARKERS = [
    /role="(dialog|alertdialog|menu|listbox)"/,
    /aria-modal/,
    /className="modal-overlay"/,
    /createPortal\(/,
    /position: 'fixed'/,
];

const JOINS_REGISTRY = /useEscapeToClose\(|useEscapeLayer\(|<Popover\b/;

describe('every app overlay hides the browser page', () => {
    const sources = import.meta.glob('/src/**/*.tsx', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
    const files = Object.entries(sources)
        .filter(([file]) => !file.includes('__tests__'))
        .map(([file, text]) => [file.replace(/^\/src\//, ''), text] as const);

    it('found the app source', () => {
        expect(files.length).toBeGreaterThan(50);
    });

    it('each overlay joins the registry useAppOverlayOpen reads', () => {
        const missing = files
            .filter(([, text]) => OVERLAY_MARKERS.some((m) => m.test(text)))
            .filter(([file, text]) => !JOINS_REGISTRY.test(text) && !(file in NOT_OVER_THE_PANE))
            .map(([file]) => file);
        expect(missing).toEqual([]);
    });

    it('the exemptions still exist', () => {
        const names = files.map(([file]) => file);
        for (const file of Object.keys(NOT_OVER_THE_PANE)) expect(names).toContain(file);
    });

    it('Popover itself joins the registry', () => {
        const popover = files.find(([file]) => file === 'components/Popover.tsx');
        expect(popover?.[1]).toMatch(/useEscapeLayer\(open\)/);
    });
});

const TAB = 'tab-1' as TabId;
const PAGE: PaneState = {
    visible: true,
    occluded: false,
    tabs: [{
        id: TAB, url: 'https://example.com/', title: 'Example', loading: false,
        canGoBack: false, canGoForward: false, devToolsOpen: false, notice: null,
        openedBy: null, agentBusy: false, agentActing: false, control: 'shared', viewport: null, dialog: null,
    }],
    activeTabId: TAB,
    recent: [],
    picking: false,
    devServers: [],
};

function paneApi(state: PaneState) {
    let push: (s: PaneState) => void = () => {};
    let command: (c: 'toggle' | 'show') => void = () => {};
    const api = {
        getState: vi.fn(() => Promise.resolve(state)),
        onState: vi.fn((cb: (s: PaneState) => void) => { push = cb; return () => {}; }),
        onCommand: vi.fn((cb: (c: 'toggle' | 'show') => void) => { command = cb; return () => {}; }),
        setBounds: vi.fn(),
        setVisible: vi.fn(() => Promise.resolve()),
        setOccluded: vi.fn((occluded: boolean) => Promise.resolve(occluded ? 'data:image/png;base64,still' : null)),
        openTab: vi.fn(), closeTab: vi.fn(), selectTab: vi.fn(),
        navigate: vi.fn(() => Promise.resolve()), history: vi.fn(), toggleDevTools: vi.fn(),
        clearBrowsingData: vi.fn(), openExternal: vi.fn(), setActiveSession: vi.fn(),
        togglePicking: vi.fn(() => Promise.resolve()), onPicked: vi.fn(() => () => {}),
    };
    return {
        api: api as unknown as GaiaBrowserPaneAPI,
        raw: api,
        push: (s: PaneState) => push(s),
        command: (c: 'toggle' | 'show' = 'toggle') => command(c),
    };
}

describe('BrowserPane', () => {
    afterEach(() => {
        act(() => useBrowserPaneStore.setState({ open: false, pane: null }));
    });

    it('swaps the live page for a still frame while an overlay is open', async () => {
        const { api, raw } = paneApi(PAGE);
        act(() => useBrowserPaneStore.setState({ open: true }));
        const { container } = render(<BrowserPane api={api} />);
        await screen.findByRole('complementary', { name: 'Browser pane' });
        expect(raw.setVisible).toHaveBeenCalledWith(true);
        expect(raw.setBounds).toHaveBeenCalled();

        const modal = render(<Modal onClose={() => {}} />);
        await waitFor(() => expect(container.querySelector('img.bp-frozen')).toHaveAttribute('src', 'data:image/png;base64,still'));
        expect(raw.setOccluded).toHaveBeenLastCalledWith(true);

        modal.unmount();
        await waitFor(() => expect(container.querySelector('img.bp-frozen')).toBeNull());
        expect(raw.setOccluded).toHaveBeenLastCalledWith(false);
    });

    it('the shortcut and main both toggle it, and closing hides the page', async () => {
        const { api, raw, command } = paneApi(PAGE);
        render(<BrowserPane api={api} />);
        await userEvent.keyboard('{Control>}{Shift>}B{/Shift}{/Control}');
        await screen.findByRole('complementary', { name: 'Browser pane' });
        act(() => command());
        await waitFor(() => expect(screen.queryByRole('complementary', { name: 'Browser pane' })).toBeNull());
        expect(raw.setVisible).toHaveBeenLastCalledWith(false);
    });

    it('a new tab page lists recent pages and opens one in the active tab', async () => {
        const blank: PaneState = {
            ...PAGE,
            tabs: [{ ...PAGE.tabs[0], url: null, title: 'New tab' }],
            recent: [{ url: 'http://localhost:5173/', title: 'My app' }],
        };
        const { api, raw } = paneApi(blank);
        act(() => useBrowserPaneStore.setState({ open: true }));
        render(<BrowserPane api={api} />);
        const recent = await screen.findByRole('button', { name: /My app/ });
        expect(screen.getByRole('textbox', { name: 'Address' })).toHaveFocus();
        await userEvent.click(recent);
        expect(raw.navigate).toHaveBeenCalledWith(TAB, 'http://localhost:5173/');
    });

    it('tells main which chat the window shows, and again when it changes', () => {
        act(() => useChatStore.setState({ currentSessionId: 'chat-a' }));
        const { api, raw } = paneApi(PAGE);
        render(<BrowserPane api={api} />);
        expect(raw.setActiveSession).toHaveBeenLastCalledWith('chat-a');
        act(() => useChatStore.setState({ currentSessionId: 'chat-b' }));
        expect(raw.setActiveSession).toHaveBeenLastCalledWith('chat-b');
        act(() => useChatStore.setState({ currentSessionId: null }));
        expect(raw.setActiveSession).toHaveBeenLastCalledWith(null);
    });

    it('opens when the agent opens a page, and says so while it reads', async () => {
        const { api, push, command } = paneApi(PAGE);
        render(<BrowserPane api={api} />);
        act(() => command('show'));
        await screen.findByRole('complementary', { name: 'Browser pane' });
        expect(screen.queryByText('GAIA is reading this page')).toBeNull();
        act(() => push({ ...PAGE, tabs: [{ ...PAGE.tabs[0], agentBusy: true }] }));
        expect(await screen.findByText('GAIA is reading this page')).toBeInTheDocument();
        act(() => push(PAGE));
        await waitFor(() => expect(screen.queryByText('GAIA is reading this page')).toBeNull());
    });

    it('shares the right side with the session panel: opening one closes the other', () => {
        act(() => useSessionPanelStore.getState().setOpen(true));
        act(() => useBrowserPaneStore.getState().setOpen(true));
        expect(useSessionPanelStore.getState().open).toBe(false);
        act(() => useSessionPanelStore.getState().setOpen(true));
        expect(useBrowserPaneStore.getState().open).toBe(false);
    });
});
