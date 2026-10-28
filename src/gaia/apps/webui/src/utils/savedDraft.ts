// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { log } from './logger';

/** Text typed but not sent, kept so a restart (an update, a crash) doesn't lose it. */

const PREFIX = 'gaia:draft:';

/** The draft key for a chat's input. */
export const chatDraftKey = (sessionId: string) => `chat:${sessionId}`;
export const NEW_CHAT_DRAFT_KEY = 'new-chat';

export function readSavedDraft(key: string): string {
    try {
        return localStorage.getItem(PREFIX + key) ?? '';
    } catch (err) {
        log.ui.warn(`Couldn't read the saved draft for ${key}`, err);
        return '';
    }
}

export function writeSavedDraft(key: string, value: string): void {
    try {
        if (value) localStorage.setItem(PREFIX + key, value);
        else localStorage.removeItem(PREFIX + key);
    } catch (err) {
        log.ui.warn(`Couldn't save the draft for ${key}`, err);
    }
}

export function clearSavedDraft(key: string): void {
    writeSavedDraft(key, '');
}
