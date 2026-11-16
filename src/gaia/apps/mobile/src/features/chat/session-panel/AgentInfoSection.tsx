// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { AgentTool } from '@/api/client.types';
import { useCapabilities } from '@/data';
import { isPcRefusal, PcOnlyNotice } from '@/features/settings/PcOnlyNotice';
import { useT, type Translate } from '@/i18n/useT';
import { Notice, Row, Stack, Text } from '@/ui';
import { isolate } from '@webui/i18n/registry';
import type { MessageKey } from '@webui/i18n/registry';

import { capabilityFailure } from './capabilityState';
import { PanelSection } from './PanelSection';
import { useSessionPanelStore } from './panelStore';
import type { SectionProps } from './sections';

const NAMED_CATEGORIES: Record<string, MessageKey> = {
    mcp: 'tools.cat.mcp',
    skill: 'tools.cat.skill',
    agent: 'tools.cat.agent',
};

/** A category's heading: a mixin name (`file_io`) reads as `file io`. */
function categoryLabel(category: string, t: Translate): string {
    const key = NAMED_CATEGORIES[category];
    return key ? t(key) : category.replace(/_/g, ' ');
}

/** Tools grouped by category, groups in the order their first tool appears. */
function groupTools(tools: AgentTool[]): [string, AgentTool[]][] {
    const groups = new Map<string, AgentTool[]>();
    for (const tool of tools) groups.set(tool.category, [...(groups.get(tool.category) ?? []), tool]);
    return [...groups.entries()];
}

export function AgentInfoSection({ sessionId, live }: SectionProps) {
    const t = useT();
    const open = useSessionPanelStore((s) => s.sections.tools);
    const query = useCapabilities(sessionId, { enabled: open, live });
    return (
        <PanelSection id="tools" title={t('tools.title')}>
            <ToolList tools={query.data?.tools} error={query.error} />
        </PanelSection>
    );
}

function ToolList({ tools, error }: { tools: AgentTool[] | undefined; error: unknown }) {
    const t = useT();
    if (isPcRefusal(error)) return <PcOnlyNotice testID="tools-pc-only" />;
    if (error) {
        const failure = capabilityFailure(error, t, 'tools.listError');
        if (failure.notRunning) return <Text tone="secondary">{t('tools.notRunning')}</Text>;
        return <Notice tone="danger" message={failure.text} />;
    }
    if (!tools) return <Text tone="muted">{t('sp.checking')}</Text>;
    if (tools.length === 0) return <Text tone="secondary">{t('tools.empty')}</Text>;
    return (
        <Stack gap="md" accessibilityRole="list">
            {groupTools(tools).map(([category, group]) => (
                <Stack key={category} gap="sm">
                    <Row gap="sm">
                        <Text role="label" weight="semibold" accessibilityRole="header">
                            {categoryLabel(category, t)}
                        </Text>
                        <Text role="caption" tone="muted">
                            {String(group.length)}
                        </Text>
                    </Row>
                    {group.map((tool) => (
                        <Stack key={tool.name} gap="xs">
                            <Text mono weight="medium">
                                {isolate(tool.name, 'ltr')}
                            </Text>
                            {tool.description ? (
                                <Text role="caption" tone="muted" numberOfLines={2}>
                                    {tool.description.split('\n', 1)[0]!.trim()}
                                </Text>
                            ) : null}
                        </Stack>
                    ))}
                </Stack>
            ))}
        </Stack>
    );
}
