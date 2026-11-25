// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Whether the backend refused this page for lacking its API token. A browser
 * signs in by opening the link `gaia chat --ui` prints; the desktop app sends
 * the token itself, so there it only shows if the token file was replaced.
 */

import { create } from 'zustand';

/** `code` of the backend's 401 body when the API token is missing or wrong. */
export const UI_TOKEN_REQUIRED = 'ui_token_required';

interface UiAuthState {
    signInRequired: boolean;
}

export const useUiAuthStore = create<UiAuthState>(() => ({ signInRequired: false }));

/** True when a 401 body is the backend's API-token refusal. */
export function isUiTokenRejection(status: number, body: string): boolean {
    if (status !== 401) return false;
    try {
        return JSON.parse(body)?.code === UI_TOKEN_REQUIRED;
    } catch {
        return false;
    }
}

/** Record what a backend answer says about sign-in: a refusal sets it, any success clears it. */
export function noteBackendAnswer(status: number, body = ''): void {
    const refused = isUiTokenRejection(status, body);
    if (refused || (status >= 200 && status < 300)) {
        if (useUiAuthStore.getState().signInRequired !== refused) useUiAuthStore.setState({ signInRequired: refused });
    }
}
