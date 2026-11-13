// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Settings > Messaging: the Discord bridge (`/api/messaging/discord`). */

import { apiFetch, UI_HEADER } from './api';

export type DiscordState =
    | 'stopped'
    | 'starting'
    | 'pairing'
    | 'connected'
    | 'taken_over'
    | 'token_invalid'
    | 'credentials_unavailable'
    | 'conflict'
    | 'error';

export interface DiscordStatus {
    connected: boolean;
    paired: boolean;
    running: boolean;
    state: DiscordState;
    /** A sentence from the bridge about the current state. */
    detail: string;
    bot_name: string;
    owner_name: string;
    autostart: boolean;
    /** What each remembered "Always allow" permits, as its prompt described it. */
    always_allowed: string[];
    model: string | null;
    portal_url: string;
    bot_page_url: string;
    invite_url: string;
    bot_profile_url: string;
    pairing_code: string;
    pairing_expires_at: number;
    discord_installed: boolean;
    should_offer_setup: boolean;
}

export interface DiscordConnectResult {
    connect: {
        connected: boolean;
        bot_name: string;
        application_id: string;
        invite_url: string;
        bot_page_url: string;
        /** Anyone with the invite link can add a public bot to their server. */
        bot_public: boolean;
    };
    status: DiscordStatus;
}

export function getDiscordStatus(): Promise<DiscordStatus> {
    return apiFetch('GET', '/messaging/discord');
}

/** Checks the token with Discord and starts the bridge; can take ~45 s. */
export function connectDiscord(token: string): Promise<DiscordConnectResult> {
    return apiFetch('POST', '/messaging/discord/connect', { token }, UI_HEADER);
}

/** `takeOver`: answer from this PC even though another PC holds the bot. */
export function startDiscord(takeOver = false): Promise<DiscordStatus> {
    return apiFetch('POST', '/messaging/discord/start', { take_over: takeOver }, UI_HEADER);
}

export function stopDiscord(): Promise<DiscordStatus> {
    return apiFetch('POST', '/messaging/discord/stop', undefined, UI_HEADER);
}

export function restartDiscord(): Promise<DiscordStatus> {
    return apiFetch('POST', '/messaging/discord/restart', undefined, UI_HEADER);
}

/** Forget every "Always allow" answered in Discord. */
export function forgetDiscordAlways(): Promise<DiscordStatus> {
    return apiFetch('POST', '/messaging/discord/forget-always', undefined, UI_HEADER);
}

/** Forget the paired account and show a new code. */
export function pairDiscordAgain(): Promise<DiscordStatus> {
    return apiFetch('POST', '/messaging/discord/pair', undefined, UI_HEADER);
}

export function disconnectDiscord(): Promise<DiscordStatus> {
    return apiFetch('POST', '/messaging/discord/disconnect', undefined, UI_HEADER);
}
