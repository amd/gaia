// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';

// The key webui keeps its copy under (utils/sessionNav.ts).
const LAST_CHAT_KEY = 'gaia:last-session';

interface LastChatState {
    /** False until the saved id has been read; the launch restore waits for it. */
    loaded: boolean;
    lastChatId: string | null;
    /** The person went to New chat this launch, so the next launch restore must not pull them away. */
    choseNewChat: boolean;
    load(): Promise<void>;
    /** null, from New chat, forgets the chat. */
    remember(id: string | null): void;
    markNewChatChosen(): void;
}

export const useLastChatStore = create<LastChatState>((set, get) => ({
    loaded: false,
    lastChatId: null,
    choseNewChat: false,
    async load() {
        try {
            const saved = await AsyncStorage.getItem(LAST_CHAT_KEY);
            // A chat opened while this was reading is newer than the saved one.
            if (!get().loaded) set({ lastChatId: saved, loaded: true });
        } catch (err) {
            console.warn('Could not read the chat open at last launch', err);
            if (!get().loaded) set({ loaded: true });
        }
    },
    remember(id) {
        if (get().loaded && get().lastChatId === id) return;
        set({ lastChatId: id, loaded: true });
        const saving = id ? AsyncStorage.setItem(LAST_CHAT_KEY, id) : AsyncStorage.removeItem(LAST_CHAT_KEY);
        saving.catch((err: unknown) => console.warn('Could not save the open chat for the next launch', err));
    },
    markNewChatChosen: () => set({ choseNewChat: true }),
}));
