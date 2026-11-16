// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ActiveSkill, SkillOrigin } from '@/api/client.types';
import { useCapabilities } from '@/data';
import { isPcRefusal, PcOnlyNotice } from '@/features/settings/PcOnlyNotice';
import { useT } from '@/i18n/useT';
import { Icon, Notice, Row, Stack, Text } from '@/ui';
import { Sparkles } from '@/ui/icons';
import { isolate } from '@webui/i18n/registry';
import type { MessageKey } from '@webui/i18n/registry';

import { capabilityFailure } from './capabilityState';
import { PanelSection } from './PanelSection';
import { useSessionPanelStore } from './panelStore';
import type { SectionProps } from './sections';

const ORIGIN_LABEL: Record<SkillOrigin, MessageKey> = {
    model: 'skills.how.model',
    auto: 'skills.how.auto',
    always: 'skills.how.always',
};

export function SkillsSection({ sessionId, live }: SectionProps) {
    const t = useT();
    const open = useSessionPanelStore((s) => s.sections.skills);
    const query = useCapabilities(sessionId, { enabled: open, live });
    // An agent that runs elsewhere doesn't report its skills, so there is nothing to show.
    if (query.data && !query.data.skills) return null;
    return (
        <PanelSection id="skills" title={t('skills.title')}>
            <SkillList skills={query.data?.skills ?? undefined} error={query.error} />
        </PanelSection>
    );
}

function SkillList({ skills, error }: { skills: ActiveSkill[] | undefined; error: unknown }) {
    const t = useT();
    if (isPcRefusal(error)) return <PcOnlyNotice testID="skills-pc-only" />;
    if (error) {
        const failure = capabilityFailure(error, t, 'skills.listError');
        if (failure.notRunning) return <Text tone="secondary">{t('skills.notRunning')}</Text>;
        return <Notice tone="danger" message={failure.text} />;
    }
    if (!skills) return <Text tone="muted">{t('sp.checking')}</Text>;
    if (skills.length === 0) return <Text tone="secondary">{t('skills.empty')}</Text>;
    return (
        <Stack gap="sm" accessibilityRole="list">
            {skills.map((skill) => (
                <Stack key={skill.name} gap="xs">
                    <Row gap="sm">
                        <Icon icon={Sparkles} size="sm" tone="muted" />
                        <Text mono weight="medium">
                            {isolate(skill.name, 'ltr')}
                        </Text>
                    </Row>
                    <Text role="caption" tone="muted">
                        {t(ORIGIN_LABEL[skill.how] ?? 'skills.how.always')}
                    </Text>
                </Stack>
            ))}
        </Stack>
    );
}
