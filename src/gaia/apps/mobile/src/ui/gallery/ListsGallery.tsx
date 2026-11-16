// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, type ReactNode } from 'react';
import { View } from 'react-native';
import { useT } from '@/i18n/useT';
import {
    ActionSheet,
    Badge,
    Button,
    ChoiceList,
    Chip,
    Disclosure,
    EmptyState,
    ErrorState,
    Icon,
    List,
    ListRow,
    ListSkeleton,
    LoadingState,
    Notice,
    SettingsGroup,
    ProgressLine,
    Screen,
    SectionHeader,
    SectionList,
    Sheet,
    Skeleton,
    Stack,
    StatusDot,
    Row,
    Text,
    TextField,
} from '@/ui';
import { Copy, RotateCcw, X } from '@/ui/icons';

function Section({ title, children }: { title: string; children: ReactNode }) {
    return (
        <Stack gap="sm" paddingY="md">
            <Text role="caption" weight="semibold" tone="muted">
                {title}
            </Text>
            {children}
        </Stack>
    );
}

export function ListsGallery() {
    const t = useT();
    const [sheet, setSheet] = useState(false);
    const [actions, setActions] = useState(false);
    const [open, setOpen] = useState(true);
    const [closed, setClosed] = useState(false);
    const [one, setOne] = useState('a');
    const [many, setMany] = useState<string[]>(['a']);
    const [chip, setChip] = useState(true);
    const [name, setName] = useState('');
    const [banner, setBanner] = useState(true);
    const [noticeOpen, setNoticeOpen] = useState(true);
    const long = t('chatError.context');
    const options = [
        { value: 'a', label: t('modelMenu.ready'), description: t('logs.hint.service') },
        { value: 'b', label: long },
        { value: 'c', label: t('env.offline') },
    ];
    const sections = [
        { title: t('sidebar.group.pinned'), data: [t('modelStatus.model'), long] },
        { title: t('sidebar.group.today'), data: [t('sidebar.group.yesterday')] },
    ];

    return (
        <Screen
            scroll
            banner={banner ? <Notice tone="warning" title={t('settings.custom.warning')} message={t('chatError.connection')} onDismiss={() => setBanner(false)} /> : null}
        >
            <Stack paddingX="lg" paddingY="lg" gap="md">
                <Section title="ListRow">
                    <ListRow title={t('modelStatus.model')} subtitle={t('row.status.running')} leading={<StatusDot tone="running" label={t('row.status.running')} />} trailing={<Badge label="3" tone="accent" />} onPress={() => {}} />
                    <ListRow title={long} subtitle={t('logs.hint.service')} onPress={() => {}} selected />
                    <ListRow title={t('modelStatus.model')} subtitle={t('chatError.connection')} subtitleTone="danger" onPress={() => {}} />
                    <ListRow title="quarterly-report.pdf" subtitle={t('row.status.running')} selected trailing={<Icon icon={Copy} />} onPress={() => {}} />
                    <ListRow title={long} titleLines={2} subtitle={t('env.offline')} trailing={<Badge label="12" shape="pill" />} />
                </Section>

                <Section title="StatusDot">
                    <Stack gap="xs">
                        <StatusDot tone="idle" label={t('env.offline')} showLabel />
                        <StatusDot tone="running" label={t('row.status.running')} showLabel />
                        <StatusDot tone="attention" label={t('settings.custom.warning')} showLabel />
                        <StatusDot tone="error" label={t('activity.filter.error')} showLabel />
                        <StatusDot tone="success" label={t('activity.filter.success')} showLabel />
                    </Stack>
                    <Row gap="sm" wrap>
                        <Badge label={t('modelMenu.ready')} tone="success" />
                        <Badge label={t('settings.custom.warning')} tone="warning" />
                        <Badge label={t('activity.filter.error')} tone="danger" />
                        <Badge label="9" shape="pill" />
                    </Row>
                </Section>

                <Section title="List, SectionList">
                    <View style={{ height: 220 }}>
                        <SectionList
                            sections={sections}
                            keyExtractor={(item, i) => `${item}${i}`}
                            renderSectionHeader={({ section }) => <SectionHeader title={section.title} trailing={<Badge shape="pill" label={String(section.data.length)} />} />}
                            renderItem={({ item }) => <ListRow title={item} onPress={() => {}} />}
                        />
                    </View>
                    <View style={{ height: 120 }}>
                        <List data={[t('modelStatus.model'), long]} keyExtractor={(i) => i} renderItem={({ item }) => <ListRow title={item} onPress={() => {}} />} />
                    </View>
                </Section>

                <Section title="Skeleton, ListSkeleton">
                    <Skeleton width="60%" height={16} />
                    <ListSkeleton rows={3} />
                </Section>

                <Section title="EmptyState, ErrorState, LoadingState">
                    <View style={{ height: 180 }}>
                        <EmptyState title={t('sidebar.empty')} action={{ label: t('sp.open'), onPress: () => {} }} />
                    </View>
                    <View style={{ height: 200 }}>
                        <ErrorState message={long} onRetry={() => {}} />
                    </View>
                    <View style={{ height: 120 }}>
                        <LoadingState />
                    </View>
                </Section>

                <Section title="Notice">
                    <Notice tone="info" title={t('notifications.filter.info')} message={t('logs.privacy')} />
                    <Notice tone="warning" title={t('settings.custom.warning')} message={long} action={{ label: t('docs.retry'), onPress: () => {} }} />
                    <Notice variant="card" tone="warning" title={t('settings.custom.warning')} message={t('chatError.connection')} />
                    <Notice tone="danger" title={t('activity.filter.error')} message={t('logs.reason.notRunning')} />
                    <Notice tone="success" title={t('activity.filter.success')} />
                    {noticeOpen ? <Notice tone="info" title={t('notifications.filter.info')} message={long} onDismiss={() => setNoticeOpen(false)} /> : null}
                </Section>

                <Section title='Text dir="auto"'>
                    <Text dir="auto">{`مرحبا ${t('modelStatus.model')}`}</Text>
                    <Text dir="auto">{`Hello ${t('modelStatus.model')}`}</Text>
                </Section>

                <Section title="ProgressLine">
                    <ProgressLine value={0.6} label={t('modelStatus.state.warming')} />
                    <ProgressLine value={0} />
                    <ProgressLine label={t('modelMenu.loading')} />
                </Section>

                <Section title="ChoiceList, Chip">
                    <ChoiceList options={options} value={one} onChange={setOne} />
                    <ChoiceList multiple options={options} value={many} onChange={setMany} />
                    <Row gap="sm" wrap>
                        <Chip label={t('modelMenu.ready')} selected={chip} onPress={() => setChip(!chip)} />
                        <Chip label={t('env.offline')} icon={X} selected={!chip} onPress={() => setChip(!chip)} />
                        <Chip label={long} />
                    </Row>
                </Section>

                <Section title="Disclosure">
                    <SettingsGroup title={t('modelStatus.model')} hint={t('logs.hint.service')} count={3} open={open} onToggle={() => setOpen(!open)}>
                        <Text tone="secondary">{t('logs.privacy')}</Text>
                    </SettingsGroup>
                    <Disclosure title={t('code.collapse')} open={open} onToggle={() => setOpen(!open)} trailing={<Badge label="2" shape="pill" />}>
                        <Text tone="secondary">{t('logs.hint.service')}</Text>
                    </Disclosure>
                    <Disclosure title={long} open={closed} onToggle={() => setClosed(!closed)}>
                        <Text tone="secondary">{t('logs.privacy')}</Text>
                    </Disclosure>
                </Section>

                <Section title="Sheet, ActionSheet">
                    <Row gap="sm" wrap>
                        <Button label={t('sp.open')} onPress={() => setSheet(true)} />
                        <Button label={t('ctx.show')} variant="secondary" onPress={() => setActions(true)} />
                    </Row>
                </Section>
            </Stack>

            <Sheet
                open={sheet}
                onClose={() => setSheet(false)}
                title={t('rail.model')}
                footer={<Button variant="primary" full label={t('cards.done')} onPress={() => setSheet(false)} />}
            >
                <Stack gap="md">
                    <Text tone="secondary">{t('question.expired')}</Text>
                    <TextField label={t('rail.model')} value={name} onChangeText={setName} />
                </Stack>
            </Sheet>
            <ActionSheet
                open={actions}
                onClose={() => setActions(false)}
                actions={[
                    { label: t('logs.copy'), icon: Copy, onPress: () => {} },
                    { label: t('msg.tryAgain'), icon: RotateCcw, onPress: () => {} },
                    { label: t('row.delete'), icon: X, destructive: true, onPress: () => {} },
                ]}
            />
        </Screen>
    );
}
