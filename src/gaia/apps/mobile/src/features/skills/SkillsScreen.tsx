// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { View } from 'react-native';

import type { SkillInfo } from '@/api/client.types';
import { useSkills } from '@/data';
import { ScreenHeader } from '@/features/nav/ScreenHeader';
import { describeError, useT } from '@/i18n/useT';
import {
    Button,
    Disclosure,
    Divider,
    EmptyState,
    ErrorState,
    ListSkeleton,
    Ltr,
    Notice,
    Row,
    Screen,
    SearchField,
    Stack,
    Surface,
    Text,
} from '@/ui';

import { ChipButton } from '../schedules/ChipButton';
import { AddSkillsSheet } from './AddSkillsSheet';
import { SettingsGroup } from './SettingsGroup';

import { filterSkills, GROUPS, isGrouped, SEARCH_ABOVE, SKILLS_HOW_PARAMS, skippedName, skipReason } from './skillList';

// A description longer than this is cut at two lines, with Show more.
const CUT_ABOVE_CHARS = 110;

export function SkillsScreen({ onTry }: { onTry: (name: string, prompt: string) => void }) {
    const t = useT();
    return (
        <Screen scroll header={<ScreenHeader title={t('settings.section.skills')} />}>
            <SkillsBody onTry={onTry} />
        </Screen>
    );
}

/** The skills list and its add sheet, for the screen and for the Settings pane. */
export function SkillsBody({ onTry }: { onTry: (name: string, prompt: string) => void }) {
    const t = useT();
    const skills = useSkills();
    const [query, setQuery] = useState('');
    const [howOpen, setHowOpen] = useState(false);
    const [adding, setAdding] = useState(false);
    const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});

    const all = skills.data?.skills ?? [];
    const invalid = Object.entries(skills.data?.invalid ?? {});
    const q = query.trim();
    const shown = filterSkills(all, query);
    const grouped = isGrouped(all);

    const groupOpen = (id: string, byDefault: boolean) => !!q || (openGroups[id] ?? byDefault);
    const toggleGroup = (id: string, byDefault: boolean) => () =>
        setOpenGroups((prev) => ({ ...prev, [id]: !(prev[id] ?? byDefault) }));
    const row = (s: SkillInfo) => (
        <SkillRow key={s.name} skill={s} onTry={() => onTry(s.name, t('settings.skills.tryPrompt', { name: s.name }))} />
    );

    return (
        <>
            <Stack gap="md" paddingX="lg" paddingY="md">
                <Text role="ui" tone="secondary">{t('settings.skills.lede')}</Text>
                <Disclosure title={t('settings.model.howItWorks')} open={howOpen} onToggle={() => setHowOpen(!howOpen)}>
                    <Text role="caption" tone="muted">{t('settings.skills.how', SKILLS_HOW_PARAMS)}</Text>
                </Disclosure>
                <Button label={t('settings.skills.link.title')} variant="secondary" full onPress={() => setAdding(true)} />
                {skills.error ? (
                    <ErrorState title={describeError(skills.error, t)} onRetry={() => void skills.refetch()} />
                ) : skills.isPending ? (
                    <ListSkeleton rows={6} />
                ) : (
                    <>
                        {all.length > SEARCH_ABOVE ? (
                            <SearchField
                                value={query}
                                onChangeText={setQuery}
                                placeholder={t.bare('settings.skills.search')}
                                accessibilityLabel={t('settings.skills.search')}
                            />
                        ) : null}
                        {all.length === 0 ? <EmptyState title={t('settings.skills.none')} /> : null}
                        {q && all.length > 0 && shown.length === 0 ? (
                            <Stack gap="xs">
                                <Text tone="muted">{t('settings.skills.noMatch', { query: q })}</Text>
                                <Button label={t('settings.skills.clearSearch')} variant="ghost" size="sm" onPress={() => setQuery('')} />
                            </Stack>
                        ) : null}
                        {grouped ? (
                            GROUPS.map((g) => {
                                const rows = shown.filter((s) => s.source === g.source);
                                if (rows.length === 0 && (q || !g.empty)) return null;
                                const byDefault = g.source === 'agent-bundled' || (g.source === 'user' && rows.length > 0);
                                const open = groupOpen(g.source, byDefault);
                                return (
                                    <SettingsGroup
                                        key={g.source}
                                        title={t(g.title)}
                                        hint={t(g.note, g.params)}
                                        count={rows.length}
                                        open={open}
                                        onToggle={toggleGroup(g.source, byDefault)}
                                    >
                                        {rows.length === 0 && g.empty ? (
                                            <Text role="label" tone="muted">{t(g.empty, g.params)}</Text>
                                        ) : (
                                            rows.map((s, i) => <View key={s.name}>{i > 0 ? <Divider /> : null}{row(s)}</View>)
                                        )}
                                    </SettingsGroup>
                                );
                            })
                        ) : shown.length > 0 ? (
                            <Surface tone="primary" radius="md" border padding="md">{shown.map((s, i) => <View key={s.name}>{i > 0 ? <Divider /> : null}{row(s)}</View>)}</Surface>
                        ) : null}
                        {invalid.length > 0 ? (
                            <Stack gap="xs">
                                <Text role="label" weight="medium" tone="secondary">{t('settings.skills.skipped')}</Text>
                                {invalid.map(([path, why]) => (
                                    <Notice key={path} tone="warning" message={t(skipReason(why), { name: skippedName(path) })} />
                                ))}
                            </Stack>
                        ) : null}
                    </>
                )}
            </Stack>
            <AddSkillsSheet open={adding} onClose={() => setAdding(false)} />
        </>
    );
}

function SkillRow({ skill, onTry }: { skill: SkillInfo; onTry: () => void }) {
    const t = useT();
    const [open, setOpen] = useState(false);
    const long = skill.description.length > CUT_ABOVE_CHARS;
    return (
        <View style={{ paddingVertical: 9 }}>
            <Row gap="md" align="center">
                <Stack flex={1} gap="xs">
                    <Text role="ui">
                        <Ltr>{skill.name}</Ltr>
                        {skill.always_on ? <Text role="ui" tone="muted">{` · ${t('settings.skills.alwaysOn')}`}</Text> : null}
                    </Text>
                    <Text role="label" tone="secondary" numberOfLines={open ? undefined : 2}>{skill.description}</Text>
                    {long ? (
                        <Button variant="secondary" size="sm" label={t(open ? 'settings.skills.less' : 'settings.skills.more')} onPress={() => setOpen(!open)} />
                    ) : null}
                    {!skill.always_on ? (
                        <ChipButton variant="plain" label={t.bare('settings.skills.try')} accessibilityLabel={t('settings.skills.try')} onPress={onTry} />
                    ) : null}
                </Stack>
            </Row>
        </View>
    );
}
