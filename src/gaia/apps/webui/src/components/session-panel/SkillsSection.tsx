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
import { useTurnEnd } from '../../hooks/useTurnEnd';
import { useChatStore } from '../../stores/chatStore';
import { PanelSection } from './PanelSection';
import type { ActiveSkill, SkillOrigin } from '../../types/sessionPanel';
import type { MessageKey } from '../../i18n/registry';
import { useT } from '../../i18n/useT';

const ORIGIN_LABEL: Record<SkillOrigin, MessageKey> = {
    model: 'skills.how.model',
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

    const load = useCallback(() => {
        let live = true;
        Promise.resolve()
            .then(() => api.getCapabilities(sessionId))
            .then((res) => {
                if (!live) return;
                const shaped = expectShape(res, (r) => r.skills === null || Array.isArray(r.skills), 'list of skills');
                setSkills(shaped.skills ?? null);
                setFailure(null);
            })
            .catch((err) => {
                if (!live) return;
                const status = (err as { httpStatus?: number }).httpStatus;
                const detail = (err as { detail?: string }).detail;
                log.ui.warn(`Could not list the skills of session=${sessionId}`, err);
                setSkills(undefined);
                setFailure({
                    notRunning: status === 409,
                    text: detail || (err instanceof Error ? err.message : String(err)),
                });
            });
        return () => { live = false; };
    }, [sessionId]);
    // skillUses: a skill picked up mid-reply shows without waiting for the reply to end.
    useEffect(load, [load, skillUses]);
    useTurnEnd(busy, load);

    // An agent that runs elsewhere doesn't report its skills, so there is nothing to show.
    if (skills === null) return null;
    return (
        <PanelSection id="skills" title={t('skills.title')}>
            <SkillList skills={skills} failure={failure} />
        </PanelSection>
    );
}

function SkillList({ skills, failure }: { skills: ActiveSkill[] | undefined; failure: Failure | null }) {
    const t = useT();
    if (failure?.notRunning) return <p className="sp-meta" role="status">{t('skills.notRunning')}</p>;
    // Open by default, so a failed read is shown, not announced; alerts are for what the user just did.
    if (failure) return <p className="sp-error">{t('skills.listError', { error: failure.text })}</p>;
    if (skills === undefined) return <p className="sp-meta">{t('sp.checking')}</p>;
    if (skills.length === 0) return <p className="sp-meta">{t('skills.empty')}</p>;

    return (
        <ul className="sp-list" aria-label={t('skills.listLabel')}>
            {skills.map((skill) => (
                <li key={skill.name} className="sp-tool sp-skill">
                    <span className="sp-skill-name">
                        <Sparkles size={12} aria-hidden="true" />
                        <code className="sp-item-name" dir="ltr">{skill.name}</code>
                    </span>
                    <span className="sp-meta">{t(ORIGIN_LABEL[skill.how] ?? 'skills.how.always')}</span>
                </li>
            ))}
        </ul>
    );
}
