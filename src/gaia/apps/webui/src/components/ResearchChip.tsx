// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect } from 'react';
import { Telescope } from 'lucide-react';
import { useT } from '../i18n/useT';
import * as api from '../services/api';
import { researchOn, useChatStore } from '../stores/chatStore';
import { log } from '../utils/logger';
import { rememberInstalledSkills, withResearchCommand } from '../utils/research';

/** Whether research mode is on for *sessionId* (null: the new-chat screen). */
export function useResearchMode(sessionId: string | null): boolean {
    return useChatStore((s) => researchOn(s, sessionId));
}

/** *text* as it is sent from *sessionId*: a `/research` turn while research mode is on. */
export function withResearch(text: string, sessionId: string | null): string {
    return withResearchCommand(text, researchOn(useChatStore.getState(), sessionId));
}

/**
 * Composer toggle: while on, each message is sent as a `/research` turn.
 * `compact` drops the label, for a crowded message box; the tooltip and
 * accessible name still say what it is.
 */
export function ResearchChip({ sessionId, disabled, compact = false }: {
    /** The chat it switches; null on the new-chat screen. */
    sessionId: string | null;
    disabled?: boolean;
    compact?: boolean;
}) {
    const t = useT();
    const on = useResearchMode(sessionId);
    const setResearch = useChatStore((s) => s.setResearch);
    const setOn = (value: boolean) => setResearch(sessionId, value);
    // A `/<skill>` command runs its skill rather than being researched, so research mode needs the skill names.
    useEffect(() => {
        if (!on) return;
        let live = true;
        api.listSkills().then(
            (r) => { if (live) rememberInstalledSkills(r.skills.map((s) => s.name)); },
            (e: unknown) => log.chat.warn('Research mode could not read the installed skills; /<skill> commands will be researched', e),
        );
        return () => { live = false; };
    }, [on]);
    return (
        <button
            type="button"
            className={`composer-chip composer-chip-research${on ? ' is-on' : ''}`}
            onClick={() => setOn(!on)}
            disabled={disabled}
            aria-pressed={on}
            aria-label={t('composer.research')}
            title={on ? t('composer.researchOnTitle') : t('composer.researchOffTitle')}
        >
            <Telescope size={13} aria-hidden="true" />
            {!compact && <span className="composer-chip-label" dir="auto">{t('composer.research')}</span>}
        </button>
    );
}
