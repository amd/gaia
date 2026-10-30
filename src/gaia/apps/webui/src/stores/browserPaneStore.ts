// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The browser pane in the renderer: a read-only mirror of the PaneState that
 * Electron main pushes, plus the layout the renderer owns. Open or closed is
 * each chat's own and remembered per chat; the width is shared. The pane and
 * the docked session panel share the right side, so opening one closes the
 * other.
 */

import { create } from 'zustand';
import { log } from '../utils/logger';
import { useSessionPanelStore } from './sessionPanelStore';
import { onSessionRemoved, useChatStore } from './chatStore';
import type { ChatKey, GaiaBrowserPaneAPI, PaneState } from '../../services/browser-pane/contract';

export const PANE_MIN_WIDTH = 360;
export const PANE_MAX_WIDTH = 1200;
export const PANE_DEFAULT_WIDTH = 520;

const OPEN_CHATS_KEY = 'gaia.browserPane.openChats';
const WIDTH_KEY = 'gaia.browserPane.width';
/** Chats remembered with the pane open, most recently opened first; older ones read as closed. */
export const OPEN_CHATS_LIMIT = 200;

/** The pane exists only in the desktop app, where preload exposes this. */
export function browserPaneApi(): GaiaBrowserPaneAPI | undefined {
    return typeof window === 'undefined' ? undefined : window.gaiaBrowserPane;
}

function read(key: string): string | null {
    try {
        return window.localStorage.getItem(key);
    } catch (err) {
        log.ui.warn(`Browser pane setting ${key} unavailable; using the default`, err);
        return null;
    }
}

function write(key: string, value: string): void {
    try {
        window.localStorage.setItem(key, value);
    } catch (err) {
        log.ui.warn(`Could not remember browser pane setting ${key}`, err);
    }
}

/** Narrower than this and the composer's controls stop fitting. */
export const CHAT_MIN_WIDTH = 560;

/** Leaves the chat column at least CHAT_MIN_WIDTH beside the sidebar, whatever its width. */
export function clampPaneWidth(width: number): number {
    const room = typeof document !== 'undefined' ? window.innerWidth - chatLeftEdge() - CHAT_MIN_WIDTH : PANE_MAX_WIDTH;
    return Math.round(Math.max(PANE_MIN_WIDTH, Math.min(width, PANE_MAX_WIDTH, room)));
}

function chatLeftEdge(): number {
    return document.querySelector('.main-content')?.getBoundingClientRect().left ?? 0;
}

function readOpenChats(): string[] {
    const raw = read(OPEN_CHATS_KEY);
    if (raw === null) return [];
    try {
        const ids: unknown = JSON.parse(raw);
        if (!Array.isArray(ids) || !ids.every((id) => typeof id === 'string')) throw new Error('expected a list of chat ids');
        return ids.slice(0, OPEN_CHATS_LIMIT);
    } catch (err) {
        log.ui.warn(`Ignoring unreadable ${OPEN_CHATS_KEY}; every chat's browser pane starts closed`, err);
        return [];
    }
}

interface BrowserPaneStore {
    /** The last state main pushed. Only connectBrowserPane writes it. */
    pane: PaneState | null;
    /** Chats whose pane is open, most recently opened first. Every other chat's is closed. */
    openChats: string[];
    /** The new-chat screen's pane; its first message hands it to the chat it makes. */
    draftOpen: boolean;
    width: number;
    /** Open or close the pane of the chat on screen. */
    setOpen: (open: boolean) => void;
    toggle: () => void;
    /** The new-chat screen's first message made `sessionId`: the screen's tabs and open pane become that chat's. */
    claimDraft: (sessionId: string) => void;
    /** The chat was deleted: forget its pane here and in main. */
    forgetChat: (sessionId: string) => void;
    setWidth: (width: number) => void;
}

export function isPaneOpen(state: Pick<BrowserPaneStore, 'openChats' | 'draftOpen'>, chat: ChatKey): boolean {
    return chat === null ? state.draftOpen : state.openChats.includes(chat);
}

const currentChat = (): ChatKey => useChatStore.getState().currentSessionId;

export const useBrowserPaneStore = create<BrowserPaneStore>((set, get) => {
    const saveOpenChats = (openChats: string[]) => {
        const kept = openChats.slice(0, OPEN_CHATS_LIMIT);
        write(OPEN_CHATS_KEY, JSON.stringify(kept));
        set({ openChats: kept });
    };
    return {
        pane: null,
        openChats: browserPaneApi() !== undefined ? readOpenChats() : [],
        draftOpen: false,
        width: clampPaneWidth(Number(read(WIDTH_KEY)) || PANE_DEFAULT_WIDTH),
        setOpen: (open) => {
            const chat = currentChat();
            if (chat === null) set({ draftOpen: open });
            else saveOpenChats([...(open ? [chat] : []), ...get().openChats.filter((id) => id !== chat)]);
            if (open && useSessionPanelStore.getState().open) useSessionPanelStore.getState().setOpen(false);
        },
        toggle: () => get().setOpen(!isPaneOpen(get(), currentChat())),
        claimDraft: (sessionId) => {
            if (get().draftOpen) {
                set({ draftOpen: false });
                saveOpenChats([sessionId, ...get().openChats.filter((id) => id !== sessionId)]);
            }
            browserPaneApi()?.claimDraft(sessionId);
        },
        forgetChat: (sessionId) => {
            if (get().openChats.includes(sessionId)) saveOpenChats(get().openChats.filter((id) => id !== sessionId));
            browserPaneApi()?.forgetChat(sessionId);
        },
        setWidth: (width) => {
            const w = clampPaneWidth(width);
            write(WIDTH_KEY, String(w));
            set({ width: w });
        },
    };
});

export function useBrowserPaneOpen(): boolean {
    const chat = useChatStore((s) => s.currentSessionId);
    return useBrowserPaneStore((s) => isPaneOpen(s, chat));
}

onSessionRemoved((id) => useBrowserPaneStore.getState().forgetChat(id));

// Switching to a chat whose pane is open takes the right side back from the session panel.
useChatStore.subscribe((now, before) => {
    if (now.currentSessionId !== before.currentSessionId && isPaneOpen(useBrowserPaneStore.getState(), now.currentSessionId)) {
        if (useSessionPanelStore.getState().open) useSessionPanelStore.getState().setOpen(false);
    }
});

useSessionPanelStore.subscribe((now, before) => {
    if (now.open && !before.open && isPaneOpen(useBrowserPaneStore.getState(), currentChat())) {
        useBrowserPaneStore.getState().setOpen(false);
    }
});

/** Mirror main's PaneState into the store until the returned unsubscribe runs. */
export function connectBrowserPane(api: GaiaBrowserPaneAPI): () => void {
    let live = true;
    let pushed = false;
    const off = api.onState((pane) => {
        pushed = true;
        useBrowserPaneStore.setState({ pane });
    });
    api.getState()
        .then((pane) => {
            // A push that arrived first is newer than this reply.
            if (live && !pushed) useBrowserPaneStore.setState({ pane });
        })
        .catch((err) => log.ui.error('Could not read the browser pane state', err));
    return () => {
        live = false;
        off();
    };
}
