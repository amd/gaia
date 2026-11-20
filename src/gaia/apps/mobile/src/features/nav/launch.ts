// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { resolveUrlNavTarget } from '@webui/utils/sessionNav.core';

import { GaiaApiError } from '@/api/errors';
import type { Navigator } from './navigation';
import { useRouteNoticeStore } from '@/state/routeNotice';

export interface LaunchState {
    lastChatId: string | null;
    sessions: { id: string }[];
    choseNewChat: boolean;
    /** The path on screen; only Home is replaced by the saved chat. */
    pathname: string;
}

/**
 * The chat to reopen at launch, as webui's first-load rule in App.tsx: nothing when the person
 * is already somewhere, went to New chat, or the saved chat no longer exists.
 */
export function chatToRestore(state: LaunchState): string | null {
    if (state.pathname !== '/' || state.choseNewChat) return null;
    return resolveUrlNavTarget(state.lastChatId, null, state.sessions);
}

/** A link that names a chat: its id, or the short hash the web UI puts in its URLs. */
export function chatFromLink(target: string | null | undefined, sessions: { id: string }[]): string | null {
    return resolveUrlNavTarget(target, null, sessions);
}

export function isMissingChat(error: unknown): boolean {
    return error instanceof GaiaApiError && error.status === 404;
}

/** A chat that is gone sends the person to Home with a notice; returns whether it did. */
export function leaveMissingChat(error: unknown, router: Pick<Navigator, 'replace'>): boolean {
    if (!isMissingChat(error)) return false;
    useRouteNoticeStore.getState().show('mobileApp.chat.notFound');
    router.replace('/');
    return true;
}
