// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Telescope } from 'lucide-react';
import { useT } from '../i18n/useT';
import { researchOn, useChatStore } from '../stores/chatStore';

/**
 * Composer toggle: while on, each message is sent as a `/research` turn.
 * `compact` drops the label, for a chat's crowded message box; the tooltip and
 * accessible name still say what it is.
 */
export function ResearchChip({ sessionId, disabled, compact = false }: {
    /** The chat it switches; null on the new-chat screen. */
    sessionId: string | null;
    disabled?: boolean;
    compact?: boolean;
}) {
    const t = useT();
    const on = useChatStore((s) => researchOn(s, sessionId));
    const setResearch = useChatStore((s) => s.setResearch);
    const setOn = (value: boolean) => setResearch(sessionId, value);
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
