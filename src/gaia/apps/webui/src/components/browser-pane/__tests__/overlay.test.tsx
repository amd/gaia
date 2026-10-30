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
import { OPEN_CHATS_LIMIT, isPaneOpen, useBrowserPaneStore } from '../../../stores/browserPaneStore';
import { useSessionPanelStore } from '../../../stores/sessionPanelStore';
import { useChatStore } from '../../../stores/chatStore';
import type { GaiaBrowserPaneAPI, PaneCommand, PaneState, TabId } from '../../../../services/browser-pane/contract';

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
    chat: null,
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
    let command: (c: PaneCommand) => void = () => {};
    const api = {
        getState: vi.fn(() => Promise.resolve(state)),
        onState: vi.fn((cb: (s: PaneState) => void) => { push = cb; return () => {}; }),
        onCommand: vi.fn((cb: (c: PaneCommand) => void) => { command = cb; return () => {}; }),
        setBounds: vi.fn(),
        setView: vi.fn(),
        claimDraft: vi.fn(),
        forgetChat: vi.fn(),
        setOccluded: vi.fn((occluded: boolean) => Promise.resolve(occluded ? 'data:image/png;base64,still' : null)),
        openTab: vi.fn(), closeTab: vi.fn(), selectTab: vi.fn(),
        navigate: vi.fn(() => Promise.resolve()), history: vi.fn(), toggleDevTools: vi.fn(),
        clearBrowsingData: vi.fn(), openExternal: vi.fn(),
        togglePicking: vi.fn(() => Promise.resolve()), onPicked: vi.fn(() => () => {}),
    };
    return {
        api: api as unknown as GaiaBrowserPaneAPI,
        raw: api,
        push: (s: PaneState) => push(s),
        command: (c: PaneCommand = { kind: 'toggle' }) => command(c),
    };
}

const paneShown = () => screen.queryByRole('complementary', { name: 'Browser pane' });
const openNow = () => isPaneOpen(useBrowserPaneStore.getState(), useChatStore.getState().currentSessionId);

describe('BrowserPane', () => {
    afterEach(() => {
        act(() => {
            useBrowserPaneStore.setState({ openChats: [], draftOpen: false, pane: null });
            useChatStore.setState({ currentSessionId: null, sessions: [] });
        });
        window.localStorage.clear();
        delete window.gaiaBrowserPane;
    });

    it('swaps the live page for a still frame while an overlay is open', async () => {
        const { api, raw } = paneApi(PAGE);
        act(() => useBrowserPaneStore.setState({ draftOpen: true }));
        const { container } = render(<BrowserPane api={api} />);
        await screen.findByRole('complementary', { name: 'Browser pane' });
        expect(raw.setView).toHaveBeenLastCalledWith(null, true);
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
        await waitFor(() => expect(paneShown()).toBeNull());
        expect(raw.setView).toHaveBeenLastCalledWith(null, false);
    });

    it('a new tab page lists recent pages and opens one in the active tab', async () => {
        const blank: PaneState = {
            ...PAGE,
            tabs: [{ ...PAGE.tabs[0], url: null, title: 'New tab' }],
            recent: [{ url: 'http://localhost:5173/', title: 'My app' }],
        };
        const { api, raw } = paneApi(blank);
        act(() => useBrowserPaneStore.setState({ draftOpen: true }));
        render(<BrowserPane api={api} />);
        const recent = await screen.findByRole('button', { name: /My app/ });
        expect(screen.getByRole('textbox', { name: 'Address' })).toHaveFocus();
        await userEvent.click(recent);
        expect(raw.navigate).toHaveBeenCalledWith(TAB, 'http://localhost:5173/');
    });

    it('tells main which chat the window shows, and whether that chat has its pane open', () => {
        act(() => useChatStore.setState({ currentSessionId: 'chat-a' }));
        const { api, raw } = paneApi(PAGE);
        render(<BrowserPane api={api} />);
        expect(raw.setView).toHaveBeenLastCalledWith('chat-a', false);
        act(() => useBrowserPaneStore.getState().setOpen(true));
        expect(raw.setView).toHaveBeenLastCalledWith('chat-a', true);
        act(() => useChatStore.setState({ currentSessionId: 'chat-b' }));
        expect(raw.setView).toHaveBeenLastCalledWith('chat-b', false);
        act(() => useChatStore.setState({ currentSessionId: 'chat-a' }));
        expect(raw.setView).toHaveBeenLastCalledWith('chat-a', true);
        act(() => useChatStore.setState({ currentSessionId: null }));
        expect(raw.setView).toHaveBeenLastCalledWith(null, false);
    });

    it("draws only the shown chat's state, never the chat it just left", async () => {
        act(() => useChatStore.setState({ currentSessionId: 'chat-a' }));
        act(() => useBrowserPaneStore.getState().setOpen(true));
        const { api, push } = paneApi({ ...PAGE, chat: 'chat-a' });
        render(<BrowserPane api={api} />);
        await screen.findByRole('complementary', { name: 'Browser pane' });
        act(() => useChatStore.setState({ currentSessionId: 'chat-b' }));
        act(() => useBrowserPaneStore.getState().setOpen(true));
        expect(paneShown()).toBeNull();
        act(() => push({ ...PAGE, chat: 'chat-b', tabs: [{ ...PAGE.tabs[0], id: 'tab-2' as TabId, title: 'B page' }], activeTabId: 'tab-2' as TabId }));
        await screen.findByRole('complementary', { name: 'Browser pane' });
        expect(screen.getByRole('tab', { name: /B page/ })).toBeInTheDocument();
    });

    it('opens when its own agent opens a page, and says so while it reads', async () => {
        act(() => useChatStore.setState({ currentSessionId: 'chat-a' }));
        const { api, push, command } = paneApi({ ...PAGE, chat: 'chat-a' });
        render(<BrowserPane api={api} />);
        act(() => command({ kind: 'show', chat: 'chat-b' }));
        expect(openNow()).toBe(false);
        act(() => command({ kind: 'show', chat: 'chat-a' }));
        await screen.findByRole('complementary', { name: 'Browser pane' });
        expect(screen.queryByText('GAIA is reading this page')).toBeNull();
        act(() => push({ ...PAGE, chat: 'chat-a', tabs: [{ ...PAGE.tabs[0], agentBusy: true }] }));
        expect(await screen.findByText('GAIA is reading this page')).toBeInTheDocument();
        act(() => push({ ...PAGE, chat: 'chat-a' }));
        await waitFor(() => expect(screen.queryByText('GAIA is reading this page')).toBeNull());
    });

    it('shares the right side with the session panel: opening one closes the other', () => {
        act(() => useSessionPanelStore.getState().setOpen(true));
        act(() => useBrowserPaneStore.getState().setOpen(true));
        expect(useSessionPanelStore.getState().open).toBe(false);
        act(() => useSessionPanelStore.getState().setOpen(true));
        expect(openNow()).toBe(false);
    });
});

describe('open or closed per chat', () => {
    afterEach(() => {
        act(() => {
            useBrowserPaneStore.setState({ openChats: [], draftOpen: false, pane: null });
            useChatStore.setState({ currentSessionId: null, sessions: [] });
        });
        window.localStorage.clear();
        delete window.gaiaBrowserPane;
    });

    const stored = () => JSON.parse(window.localStorage.getItem('gaia.browserPane.openChats') ?? '[]');

    it('a chat opened elsewhere keeps its own state, remembered across reloads; a new chat starts closed', () => {
        act(() => useChatStore.setState({ currentSessionId: 'chat-a' }));
        act(() => useBrowserPaneStore.getState().setOpen(true));
        act(() => useChatStore.setState({ currentSessionId: 'chat-b' }));
        expect(openNow()).toBe(false);
        act(() => useChatStore.setState({ currentSessionId: null }));
        expect(openNow()).toBe(false);
        act(() => useChatStore.setState({ currentSessionId: 'chat-a' }));
        expect(openNow()).toBe(true);
        expect(stored()).toEqual(['chat-a']);
        act(() => useBrowserPaneStore.getState().toggle());
        expect(stored()).toEqual([]);
    });

    it("the new-chat screen's open pane goes to the chat its first message makes", () => {
        const api = paneApi(PAGE).raw;
        window.gaiaBrowserPane = api as unknown as GaiaBrowserPaneAPI;
        act(() => useBrowserPaneStore.getState().setOpen(true));
        expect(useBrowserPaneStore.getState().draftOpen).toBe(true);
        act(() => useBrowserPaneStore.getState().claimDraft('chat-new'));
        expect(api.claimDraft).toHaveBeenCalledWith('chat-new');
        act(() => useChatStore.setState({ currentSessionId: 'chat-new' }));
        expect(openNow()).toBe(true);
        expect(useBrowserPaneStore.getState().draftOpen).toBe(false);
        expect(stored()).toEqual(['chat-new']);
    });

    it("deleting a chat forgets its pane here and tells main to close its tabs", () => {
        const api = paneApi(PAGE).raw;
        window.gaiaBrowserPane = api as unknown as GaiaBrowserPaneAPI;
        act(() => useChatStore.setState({ currentSessionId: 'chat-a', sessions: [{ id: 'chat-a' }, { id: 'chat-b' }] as never }));
        act(() => useBrowserPaneStore.getState().setOpen(true));
        act(() => useChatStore.getState().removeSession('chat-a'));
        expect(api.forgetChat).toHaveBeenCalledWith('chat-a');
        expect(useBrowserPaneStore.getState().openChats).toEqual([]);
        expect(stored()).toEqual([]);
    });

    it('remembers a bounded number of open chats, most recent first', () => {
        for (let i = 0; i <= OPEN_CHATS_LIMIT; i++) {
            act(() => useChatStore.setState({ currentSessionId: `chat-${i}` }));
            act(() => useBrowserPaneStore.getState().setOpen(true));
        }
        expect(stored()).toHaveLength(OPEN_CHATS_LIMIT);
        expect(stored()[0]).toBe(`chat-${OPEN_CHATS_LIMIT}`);
        expect(stored()).not.toContain('chat-0');
    });
});
