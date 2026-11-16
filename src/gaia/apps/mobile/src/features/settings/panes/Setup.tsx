// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { formatSize, ltrIn } from '@webui/components/setup/format';
import { isolate } from '@webui/i18n/registry';
import { tEn } from '@webui/i18n/translator';
import { isServerDown } from '@webui/utils/serverDown';
import { useRouter } from 'expo-router';
import { useState } from 'react';

import { useActiveModel, useGpuMemory, useSetupPlan, useSetupPreflight } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { reopenSetup } from '@/features/setup/SetupGate';
import { Button, Icon, ListRow, Notice, Sheet, Stack, Surface, Text } from '@/ui';
import { ChevronRight } from '@/ui/icons';

import { openSettings } from '../openSettings';
import type { SectionId } from '../sections';
import { SettingsAnchor } from '../targets';
import { chatModelName, detectedLine } from './setup/summary';

interface Line {
    label: string;
    value: string | null;
    /** Where the row's setting lives; a row without one only reports. */
    to?: { section: SectionId; target?: string; provider?: 'local' | 'fireworks' };
    testID: string;
}

/** Everything setup chose, each row jumping to the setting that owns it, and setup again. */
export default function Setup() {
    const t = useT();
    const router = useRouter();
    const plan = useSetupPlan();
    const hardware = useSetupPreflight();
    const active = useActiveModel();
    const [asking, setAsking] = useState(false);

    const chatName = chatModelName(active.data, plan.data);
    const catalog = plan.data?.catalog;
    const installed = (catalog ?? []).filter((e) => e.downloaded);
    const installedGb = installed.reduce((sum, e) => sum + (e.full_size_gb ?? 0), 0);
    const error = plan.error ?? hardware.error;
    const local = !!active.data?.model && !active.data.remote && active.data.downloaded !== false;

    // As on the web: where the split can change, or where reading it failed for a reason Model explains.
    const gpuRead = useGpuMemory();
    const gpuShown = !!gpuRead.data?.applies || (gpuRead.error != null && !isServerDown(describeError(gpuRead.error, tEn)));
    const groups: { title: string; lines: Line[] }[] = [
        {
            title: t('settings.setup.where'),
            lines: [
                { label: t('settings.setup.chatRuns'), value: chatName, to: { section: 'model', target: 'model-in-use' }, testID: 'setup-chat-model' },
                { label: t('settings.setup.cloudKeys'), value: null, to: { section: 'model', target: 'cloud-key', provider: 'fireworks' }, testID: 'setup-cloud-keys' },
            ],
        },
        {
            title: t('mobileApp.setup.pc.step'),
            lines: [
                { label: t('settings.setup.detected'), value: detectedLine(t, plan.data, hardware.data), testID: 'setup-detected' },
                {
                    label: t('settings.setup.server'),
                    value: plan.data ? t(plan.data.lemonade.mode === 'external' ? 'settings.setup.serverYours' : 'settings.setup.serverOwn') : null,
                    to: { section: 'advanced', target: 'model-server' },
                    testID: 'setup-server',
                },
                ...(gpuShown
                    ? [{ label: t('setup.gpu.title'), value: null, to: { section: 'model' as const, target: 'gpu-memory', provider: 'local' as const }, testID: 'setup-gpu' }]
                    : []),
            ],
        },
        {
            title: t('setup.models.step'),
            lines: [
                {
                    label: t('mobileApp.settings.setup.models'),
                    value: catalog ? t('settings.setup.modelsValue', { n: String(installed.length), size: ltrIn(t.locale, formatSize(installedGb)) }) : null,
                    to: { section: 'model', target: 'models-on-pc', provider: 'local' },
                    testID: 'setup-models',
                },
                ...(local ? [{ label: t('settings.setup.context'), value: null, to: { section: 'model' as const, target: 'ctx-size' }, testID: 'setup-context' }] : []),
            ],
        },
        {
            title: t('settings.setup.after'),
            lines: [
                { label: t('settings.section.connectors'), value: null, to: { section: 'connectors' }, testID: 'setup-connectors' },
                { label: t('settings.section.memory'), value: null, to: { section: 'memory' }, testID: 'setup-memory' },
                { label: t('settings.language'), value: null, to: { section: 'general', target: 'ui-language' }, testID: 'setup-language' },
                { label: t('settings.setup.signIn'), value: t('setup.plan.pcOnly'), testID: 'setup-sign-in' },
            ],
        },
    ];

    const runAgain = () => {
        setAsking(false);
        router.replace('/');
        reopenSetup();
    };

    return (
        <SettingsAnchor name="pane">
            <Stack gap="lg" paddingX="lg" paddingY="md">
                <Text role="body" tone="secondary">{t('settings.setup.lede')}</Text>
                <Surface radius="lg" border padding="md">
                    <Stack gap="sm">
                        <Text role="ui" accessibilityRole="header">
                            <Text role="ui" weight="semibold">{t('settings.setup.ready')}</Text>
                            {chatName ? <Text role="label" tone="muted">{` ${t('settings.setup.running', { name: isolate(chatName, 'ltr') })}`}</Text> : null}
                        </Text>
                        <Button variant="secondary" size="sm" label={t('settings.setup.runAgain')} onPress={() => setAsking(true)} testID="setup-run-again" />
                    </Stack>
                </Surface>
                {error ? <Notice tone="danger" message={describeError(error, t)} testID="setup-error" /> : null}
                {groups.map((g) => (
                    <Stack key={g.title} gap="xs">
                        <Text role="label" weight="medium" tone="secondary" accessibilityRole="header">{g.title}</Text>
                        {g.lines.map((l) => {
                            const to = l.to;
                            return (
                                <ListRow
                                    key={l.label}
                                    title={l.label}
                                    subtitle={l.value ?? undefined}
                                    subtitleLines={3}
                                    trailing={to ? <Icon icon={ChevronRight} size="sm" tone="muted" /> : undefined}
                                    onPress={to ? () => openSettings(to.section, to.target, 'setup', router, to.provider) : undefined}
                                    testID={l.testID}
                                />
                            );
                        })}
                    </Stack>
                ))}
            </Stack>
            <Sheet
                open={asking}
                onClose={() => setAsking(false)}
                title={t('settings.setup.confirmTitle')}
                footer={
                    <Stack gap="sm">
                        <Button label={t('settings.setup.confirmYes')} full onPress={runAgain} testID="setup-confirm-run" />
                        <Button label={t('setup.again.notNow')} variant="secondary" full onPress={() => setAsking(false)} />
                    </Stack>
                }
            >
                <Text role="body">{t('settings.setup.confirmBody')}</Text>
            </Sheet>
        </SettingsAnchor>
    );
}
