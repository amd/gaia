// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Skills: the skills this chat's agent has loaded, and how each one came in,
 * read from the running agent like Agent info. Re-reads when a reply ends and
 * each time a skill is picked up mid-reply.
 */

import { useCallback, useEffect, useState } from 'react';
import { Sparkles } from 'lucide-react';
import * as api from '../../services/api';
import { expectShape } from '../../utils/responseShape';
import { log } from '../../utils/logger';
import { useRereadWhileStarting, useTurnEnd } from '../../hooks/useTurnEnd';
import { useChatStore } from '../../stores/chatStore';
import { PanelSection } from './PanelSection';
import { useLatestRead } from '../../hooks/useLatestRead';
import { useSessionPanelStore } from '../../stores/sessionPanelStore';
import type { ActiveSkill, SkillOrigin } from '../../types/sessionPanel';
import type { MessageKey } from '../../i18n/registry';
import { useT } from '../../i18n/useT';

const ORIGIN_LABEL: Record<SkillOrigin, MessageKey> = {
    model: 'skills.how.model',
    user: 'skills.how.user',
    auto: 'skills.how.auto',
    always: 'skills.how.always',
};

interface SkillsSectionProps {
    sessionId: string;
    busy: boolean;
}

type Failure = { notRunning: boolean; text: string };

export function SkillsSection({ sessionId, busy }: SkillsSectionProps) {
    const t = useT();
    const skillUses = useChatStore((s) => s.skillUses);
    // undefined: not read yet; null: the agent runs elsewhere and doesn't report them.
    const [skills, setSkills] = useState<ActiveSkill[] | null | undefined>(undefined);
    const [failure, setFailure] = useState<Failure | null>(null);

    const begin = useLatestRead();
    const load = useCallback(() => {
        const lands = begin();
        Promise.resolve()
            .then(() => api.getCapabilities(sessionId))
            .then((res) => {
                if (!lands()) return;
                const shaped = expectShape(res, (r) => r.skills === null || Array.isArray(r.skills), 'list of skills');
                useSessionPanelStore.getState().setSignals(sessionId, { skills: (shaped.skills ?? []).map((s) => s.name) });
                setSkills(shaped.skills ?? null);
                setFailure(null);
            })
            .catch((err) => {
                if (!lands()) return;
                const status = (err as { httpStatus?: number }).httpStatus;
                const detail = (err as { detail?: string }).detail;
                log.ui.warn(`Could not list the skills of session=${sessionId}`, err);
                setSkills(undefined);
                // A skill the chat can no longer be shown to have must not keep naming the chat.
                useSessionPanelStore.getState().setSignals(sessionId, { skills: [] });
                setFailure({
                    notRunning: status === 409,
                    text: detail || (err instanceof Error ? err.message : String(err)),
                });
            });
    }, [begin, sessionId]);
    // skillUses: a skill picked up mid-reply shows without waiting for the reply to end.
    useEffect(() => { load(); }, [load, skillUses]);
    useTurnEnd(busy, load);
    useRereadWhileStarting(busy, failure?.notRunning === true, load);

    // Nothing to list is no section: not reported (an agent elsewhere), not started, or none loaded.
    const failed = failure && !failure.notRunning ? failure : null;
    if (!failed && (!skills || skills.length === 0)) return null;
    return (
        <PanelSection id="skills" title={t('skills.title')}>
            {failed
                // Open by default, so a failed read is shown, not announced; alerts are for what the user just did.
                ? <p className="sp-error">{t('skills.listError', { error: failed.text })}</p>
                : (
                    <ul className="sp-list" aria-label={t('skills.listLabel')}>
                        {(skills ?? []).map((skill) => (
                            <li
                                key={skill.name}
                                className="sp-row-line"
                                title={t(ORIGIN_LABEL[skill.how] ?? 'skills.how.always')}
                            >
                                <span className="sp-row-icon sp-skill-icon" aria-hidden="true"><Sparkles size={14} /></span>
                                <code className="sp-item-name" dir="ltr">{skill.name}</code>
                            </li>
                        ))}
                    </ul>
                )}
        </PanelSection>
    );
}
