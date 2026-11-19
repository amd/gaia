// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The optional "get to know me" pass offered once a connector is connected:
 * GAIA reads recent activity through that connector, saves what it learns to
 * memory, and suggests automations. The prompt asks before scheduling
 * anything, so running it never changes the account.
 */

export const PERSONALIZE_EVENT = 'gaia:personalize-connector';

export interface PersonalizeRequest {
    connectorId: string;
    displayName: string;
}

/**
 * What to read for each connector family, keyed by a prefix of its id, said
 * as "my ..." because it goes into the user's prompt.
 * tui/internal/gaiaconnectors mirrors this table; a Go test reads this file.
 */
const SOURCES: [prefix: string, what: string][] = [
    ['google', 'my last two weeks of Gmail (and Google Calendar, if it is connected)'],
    ['microsoft', 'my last two weeks of Outlook mail (and calendar, if it is connected)'],
    ['slack', 'my recent Slack channels and direct messages'],
    ['github', 'my recent GitHub activity: open PRs, review requests, issues and notifications'],
];

export function personalizeSource(connectorId: string, displayName: string): string {
    const id = connectorId.toLowerCase().replace(/^mcp-/, '');
    const hit = SOURCES.find(([prefix]) => id.startsWith(prefix));
    return hit ? hit[1] : `my recent activity in ${displayName}`;
}

export type PersonalizeSourceKind = 'google' | 'microsoft' | 'slack' | 'github';

/** Which known source a connector reads, for on-screen copy; null for any other connector. */
export function personalizeSourceKind(connectorId: string): PersonalizeSourceKind | null {
    const id = connectorId.toLowerCase().replace(/^mcp-/, '');
    const hit = SOURCES.find(([prefix]) => id.startsWith(prefix));
    return hit ? (hit[0] as PersonalizeSourceKind) : null;
}

/** The chat prompt that runs the personalization pass for one connector. */
export function buildPersonalizePrompt({ connectorId, displayName }: PersonalizeRequest): string {
    return [
        `I just connected ${displayName}. Get to know me from ${personalizeSource(connectorId, displayName)}.`,
        'Read only — do not send, delete, label or change anything.',
        'If you have no tools that read it, or they fail, tell me you couldn\'t reach it and stop. Never guess or count what it holds.',
        '1. Skim the recent items and work out who I deal with most, what I work on, recurring meetings or threads, and what keeps landing on my plate.',
        '2. Save the durable facts to memory (people, projects, preferences, routines). Skip one-off details and anything sensitive like passwords, codes or financial numbers.',
        '3. Recommend up to five automations you could run for me on a schedule, each with what it does, how often, and why it fits what you saw.',
        'Don\'t create any schedules yet — tell me what you saved and list the automations so I can pick which to set up.',
    ].join('\n');
}

export function requestPersonalize(req: PersonalizeRequest): void {
    window.dispatchEvent(new CustomEvent<PersonalizeRequest>(PERSONALIZE_EVENT, { detail: req }));
}
