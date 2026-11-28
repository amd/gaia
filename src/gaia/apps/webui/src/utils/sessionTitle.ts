// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { markStrayDirection } from '../components/CalendarApproval';
import { tEn, type Translate } from '../i18n/useT';
import type { Session } from '../types';

// Mirrors PLACEHOLDER_TITLES in src/gaia/ui/database.py: titles the server
// still treats as unnamed, so it names the chat after its first question.
const PLACEHOLDER_TITLES = new Set(['', 'new chat', 'new task', 'untitled', 'untitled session', 'chat']);

export function isPlaceholderTitle(title: string | null | undefined): boolean {
    return PLACEHOLDER_TITLES.has((title ?? '').trim().toLowerCase());
}

/**
 * A chat title as shown: a direction override in it reads as its code. Chats GAIA
 * makes itself keep the English title scheduler.py and agent_loop.py store, so they
 * read in the chosen language whenever they were made.
 */
export function shownTitle(t: Translate, session: Pick<Session, 'title' | 'kind'> | null | undefined): string {
    const title = markStrayDirection(session?.title ?? '');
    if (session?.kind !== 'scheduled') return title;
    if (title === tEn('chat.title.goals')) return t('chat.title.goals');
    const schedule = tEn('chat.title.schedule', { name: '' });
    if (title.startsWith(schedule) && title.length > schedule.length) {
        return t('chat.title.schedule', { name: title.slice(schedule.length) });
    }
    return title;
}
