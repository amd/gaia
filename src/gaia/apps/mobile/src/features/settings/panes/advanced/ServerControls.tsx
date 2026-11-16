// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, type ReactNode } from 'react';

import { isolate } from '@webui/i18n/registry';

import { useMcpStatus, useSettings, useUpdateSettings } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { Badge, Card, Disclosure, Ltr, Notice, Row, Spinner, Stack, Switch, Text } from '@/ui';

import { isPcRefusal, PcOnlyNotice } from '../../PcOnlyNotice';
import { SettingsAnchor } from '../../targets';
import { CustomModel } from './CustomModel';
import { ActionError, RawDetails } from './errors';
import { LemonadeTarget } from './LemonadeTarget';
import { ModelServerPanel } from './ModelServerPanel';

/** A collapsed card with an optional hint under its title; the body mounts when opened. */
function SettingCard({ title, hint, open, onToggle, trailing, testID, children }: {
    title: string;
    hint?: ReactNode;
    open: boolean;
    onToggle: () => void;
    trailing?: ReactNode;
    testID: string;
    children: ReactNode;
}) {
    return (
        <Card padding="lg" testID={testID}>
            <Stack gap="md">
                <Stack gap="xs">
                    <Disclosure title={title} open={open} onToggle={onToggle} trailing={trailing} />
                    {hint ? <Text role="caption" tone="muted">{hint}</Text> : null}
                </Stack>
                {open ? children : null}
            </Stack>
        </Card>
    );
}

/** The model server's address, backend and choice, the custom model ID, dynamic tools and MCP status. */
export function ServerControlsSlot() {
    return (
        <>
            <ModelServerCard />
            <CustomModelCard />
            <DynamicToolsCard />
            <McpCard />
        </>
    );
}

function ModelServerCard() {
    const t = useT();
    const [open, setOpen] = useState(false);
    return (
        <SettingsAnchor name="model-server" onTarget={() => setOpen(true)}>
            <SettingCard
                title={t('settings.advanced.serverTitle')}
                hint={t('settings.advanced.serverHint')}
                open={open}
                onToggle={() => setOpen((v) => !v)}
                testID="advanced-model-server"
            >
                <Stack gap="lg">
                    <Stack gap="md">
                        <Text role="title" accessibilityRole="header">{t('settings.advanced.modelServer')}</Text>
                        <ModelServerPanel />
                    </Stack>
                    <LemonadeTarget />
                </Stack>
            </SettingCard>
        </SettingsAnchor>
    );
}

function CustomModelCard() {
    const t = useT();
    const [open, setOpen] = useState(false);
    const settings = useSettings();
    const saved = settings.data?.custom_model;
    return (
        <SettingCard
            title={t('settings.advanced.customTitle')}
            hint={saved ? <Ltr>{saved}</Ltr> : t('settings.advanced.customHint')}
            open={open}
            onToggle={() => setOpen((v) => !v)}
            testID="advanced-custom"
        >
            {settings.data ? (
                <CustomModel settings={settings.data} />
            ) : settings.isPending ? (
                <Spinner />
            ) : isPcRefusal(settings.error) ? (
                <PcOnlyNotice testID="advanced-custom-pc-only" />
            ) : (
                <Stack gap="sm">
                    <Notice tone="warning" message={t('mobileApp.settings.modelActionFailed')} />
                    <RawDetails text={describeError(settings.error, t)} />
                </Stack>
            )}
        </SettingCard>
    );
}

function DynamicToolsCard() {
    const t = useT();
    const [open, setOpen] = useState(false);
    const settings = useSettings();
    const update = useUpdateSettings();
    const data = settings.data;
    const locked = data?.dynamic_tools_locked ?? false;
    // The flip shows at once; a failure puts the saved value back.
    const on = update.isPending && update.variables?.dynamic_tools !== undefined ? update.variables.dynamic_tools : (data?.dynamic_tools ?? false);
    return (
        <SettingCard title={t('settings.advanced.dynamicTitle')} open={open} onToggle={() => setOpen((v) => !v)} testID="advanced-dynamic">
            <Stack gap="sm">
                <Text role="caption" tone="muted">{t('settings.advanced.dynamicDesc')}</Text>
                <Switch
                    value={on}
                    label={t('settings.advanced.dynamicEnable')}
                    disabled={!data || update.isPending || locked}
                    onValueChange={(next) => update.mutate({ dynamic_tools: next })}
                    testID="advanced-dynamic-switch"
                />
                {locked ? <Text role="caption" tone="muted">{t('settings.advanced.dynamicLocked')}</Text> : null}
                {update.isError ? <ActionError error={update.error} title={t('settings.advanced.saveFailed')} /> : null}
            </Stack>
        </SettingCard>
    );
}

function McpCard() {
    const t = useT();
    const status = useMcpStatus();
    const [picked, setPicked] = useState<boolean | null>(null);
    const servers = status.data?.servers ?? [];
    if (servers.length === 0) return null;
    const open = picked ?? servers.some((s) => !s.connected);
    return (
        <SettingCard
            title={t('settings.advanced.mcp')}
            trailing={<Badge shape="pill" label={String(servers.length)} />}
            open={open}
            onToggle={() => setPicked(!open)}
            testID="advanced-mcp"
        >
            <Stack gap="md">
                {servers.map((s) => (
                    <Stack key={s.name} gap="xs">
                        <Row gap="md">
                            <Stack flex={1}>
                                <Text role="label" tone="secondary"><Ltr>{s.name}</Ltr></Text>
                            </Stack>
                            <Text role="label" weight="medium" tone={s.connected ? 'success' : 'warning'}>
                                {s.connected ? t.plural('settings.advanced.tools', s.tool_count, { n: isolate(String(s.tool_count), 'ltr') }) : t.bare('settings.custom.failed')}
                            </Text>
                        </Row>
                        {!s.connected && s.error ? <RawDetails text={s.error} /> : null}
                    </Stack>
                ))}
            </Stack>
        </SettingCard>
    );
}
