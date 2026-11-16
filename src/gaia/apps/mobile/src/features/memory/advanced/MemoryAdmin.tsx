// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import type { MessageKey } from '@webui/i18n/registry';

import type { MemorySettings } from '@/api/client.types';
import { ENDPOINTS } from '@/api/constants';
import { useDeviceRefused } from '@/api/routeAccess';
import { useClearAllMemory, useReinitializeMemory, useUpdateMemorySettings } from '@/data';
import { describeFailureWith, useT } from '@/i18n/useT';
import { Badge, Button, Card, Row, Sheet, Stack, Switch, Text } from '@/ui';
import { Shield } from '@/ui/icons';

import type { MemoryNotice } from '../useMemoryNotice';
import { SectionTitle } from './SectionTitle';

type Beta = 'memory' | 'mcp' | 'discovery';
type Danger = 'reinit' | 'delete';

const BETA: Record<Beta, { title: MessageKey; description: MessageKey; bullets: MessageKey[]; confirm: MessageKey; setting: keyof MemorySettings }> = {
    memory: {
        title: 'mem.beta.memory.title',
        description: 'mem.beta.memory.desc',
        bullets: ['mobileApp.mem.beta.memory.local', 'mem.beta.memory.lost', 'mem.beta.memory.disable'],
        confirm: 'mem.beta.memory.confirm',
        setting: 'memory_enabled',
    },
    mcp: {
        title: 'mem.beta.mcp.title',
        description: 'mem.beta.mcp.desc',
        bullets: ['mem.beta.mcp.read', 'mem.beta.mcp.restart', 'mem.beta.mcp.debug'],
        confirm: 'mem.beta.mcp.confirm',
        setting: 'mcp_memory_enabled',
    },
    discovery: {
        title: 'mem.beta.discovery.title',
        description: 'mem.beta.discovery.desc',
        bullets: ['mem.beta.discovery.collects', 'mem.beta.discovery.scans', 'mobileApp.mem.beta.discovery.local'],
        confirm: 'mem.beta.discovery.confirm',
        setting: 'system_discovery_consent',
    },
};

const DANGER: Record<Danger, { title: MessageKey; description: MessageKey; button: MessageKey; confirm: MessageKey }> = {
    reinit: { title: 'mem.settings.reinit', description: 'mem.settings.reinitDesc', button: 'mem.settings.reinitButton', confirm: 'mem.settings.reinitConfirm' },
    delete: { title: 'mem.settings.deleteAll', description: 'mem.settings.deleteAllDesc', button: 'mem.settings.deleteButton', confirm: 'mem.settings.deleteConfirm' },
};

/** The Advanced tab's Settings: the three switches (each ON asks first), then the two wipes, each behind a confirm sheet. */
export function MemoryAdmin({ settings, notify }: { settings: MemorySettings; notify: (notice: MemoryNotice) => void }) {
    const t = useT();
    const update = useUpdateMemorySettings();
    const clear = useClearAllMemory();
    const reinit = useReinitializeMemory();
    const [beta, setBeta] = useState<Beta | null>(null);
    const [danger, setDanger] = useState<Danger | null>(null);
    // Turning discovery consent on runs the same PC scan as a refresh; turning it off works anywhere.
    const scanRefused = useDeviceRefused('POST', ENDPOINTS.memoryRefreshSystemContext);
    const reinitRefused = useDeviceRefused('POST', ENDPOINTS.memoryReinitialize);
    const busy = update.isPending || clear.isPending || reinit.isPending;

    const fail = (key: MessageKey, error: unknown) => notify({ tone: 'danger', text: t.compose((say) => say(key, { error: describeFailureWith(error, say) })) });

    const turnOff = (kind: Beta) => update.mutate({ [BETA[kind].setting]: false }, { onError: (e) => fail('mem.toast.settingFailed', e) });

    const turnOn = async (kind: Beta) => {
        try {
            const updated = await update.mutateAsync({ [BETA[kind].setting]: true });
            if (kind === 'memory') notify({ tone: 'info', text: t('mem.toast.memoryEnabled') });
            else if (kind === 'mcp') notify({ tone: 'info', text: t('mem.toast.mcpEnabled') });
            else if (updated.system_context_error) notify({ tone: 'danger', text: t('mem.toast.discoveryScanFailed', { error: updated.system_context_error }) });
            else notify({ tone: 'info', text: t('mem.toast.discoveryEnabled', { n: updated.system_context_refresh?.stored ?? 0 }) });
        } catch (error) {
            fail('mem.toast.settingFailed', error);
        } finally {
            setBeta(null);
        }
    };

    const wipe = async (kind: Danger) => {
        try {
            if (kind === 'reinit') {
                const result = await reinit.mutateAsync();
                notify(
                    result.system_context.error
                        ? { tone: 'danger', text: t('mem.toast.server', { message: result.system_context.error }) }
                        : { tone: 'info', text: t('mem.toast.reinitialized', { cleared: result.cleared.knowledge, stored: result.system_context.stored }) },
                );
            } else {
                const result = await clear.mutateAsync();
                notify({ tone: 'info', text: t('mem.toast.cleared', { knowledge: result.knowledge, conversations: result.conversations }) });
            }
        } catch (error) {
            fail(kind === 'reinit' ? 'mem.toast.reinitFailed' : 'mem.toast.clearFailed', error);
        } finally {
            setDanger(null);
        }
    };

    const toggle = (kind: Beta, on: boolean) => (on ? turnOff(kind) : setBeta(kind));
    const betaConfig = beta ? BETA[beta] : null;
    const dangerConfig = danger ? DANGER[danger] : null;

    return (
        <Stack gap="md" testID="memory-admin">
            <Row gap="sm" wrap>
                <SectionTitle icon={Shield} title={t('mem.settings.title')} />
                <Badge label={t.bare('mem.beta')} accessibilityLabel={t('mem.beta')} tone="warning" />
            </Row>
            <Card>
                <Stack gap="md">
                    <Switch
                        label={t('mem.settings.memory')}
                        description={t('mem.settings.memoryDesc')}
                        value={settings.memory_enabled}
                        onValueChange={() => toggle('memory', settings.memory_enabled)}
                        disabled={busy}
                        testID="memory-switch-memory"
                    />
                    <Switch
                        label={t('mem.settings.mcp')}
                        description={t('mem.settings.mcpDesc')}
                        value={settings.mcp_memory_enabled}
                        onValueChange={() => toggle('mcp', settings.mcp_memory_enabled)}
                        disabled={busy}
                        testID="memory-switch-mcp"
                    />
                    <Switch
                        label={t('mem.settings.discovery')}
                        description={t('mem.settings.discoveryDesc')}
                        value={settings.system_discovery_consent}
                        onValueChange={() => toggle('discovery', settings.system_discovery_consent)}
                        disabled={busy || (scanRefused && !settings.system_discovery_consent)}
                        testID="memory-switch-discovery"
                    />
                    {scanRefused && !settings.system_discovery_consent ? (
                        <Text role="caption" tone="muted" testID="memory-discovery-pc-only">{t('mobileApp.mem.admin.pcOnly')}</Text>
                    ) : null}
                </Stack>
            </Card>
            {(['reinit', 'delete'] as const).map((kind) => (
                <Card key={kind}>
                    <Stack gap="md">
                        <Text role="ui" weight="semibold" accessibilityRole="header">{t(DANGER[kind].title)}</Text>
                        <Text role="label" tone="muted">{t(DANGER[kind].description)}</Text>
                        {kind === 'reinit' && reinitRefused ? (
                            <Text role="caption" tone="muted" testID="memory-reinit-pc-only">{t('mobileApp.mem.admin.pcOnly')}</Text>
                        ) : null}
                        <Button
                            variant="danger"
                            full
                            label={t(DANGER[kind].button)}
                            disabled={busy || (kind === 'reinit' && reinitRefused)}
                            onPress={() => setDanger(kind)}
                            testID={`memory-${kind}`}
                        />
                    </Stack>
                </Card>
            ))}

            <Sheet
                open={beta !== null}
                onClose={() => setBeta(null)}
                title={t(betaConfig?.title ?? 'mem.beta.memory.title')}
                footer={
                    <Stack gap="sm">
                        <Button
                            variant="primary"
                            full
                            label={t(betaConfig?.confirm ?? 'mem.beta.memory.confirm')}
                            busy={update.isPending}
                            onPress={() => beta && void turnOn(beta)}
                            testID="memory-beta-confirm"
                        />
                        <Button variant="secondary" full label={t('mem.cancel')} onPress={() => setBeta(null)} />
                    </Stack>
                }
            >
                <Stack gap="sm">
                    <Row><Badge label={t.bare('mem.beta')} accessibilityLabel={t('mem.beta')} tone="warning" /></Row>
                    <Text role="body">{t(betaConfig?.description ?? 'mem.beta.memory.desc')}</Text>
                    {betaConfig?.bullets.map((key) => (
                        <Text key={key} role="body" tone="secondary">{`• ${t(key)}`}</Text>
                    ))}
                </Stack>
            </Sheet>

            <Sheet
                open={danger !== null}
                onClose={() => setDanger(null)}
                title={t('mem.settings.sure')}
                footer={
                    <Stack gap="sm">
                        <Button
                            variant="danger"
                            full
                            label={t(dangerConfig?.confirm ?? 'mem.settings.deleteConfirm')}
                            busy={clear.isPending || reinit.isPending}
                            onPress={() => danger && void wipe(danger)}
                            testID="memory-wipe-confirm"
                        />
                        <Button variant="secondary" full label={t('mem.cancel')} onPress={() => setDanger(null)} />
                    </Stack>
                }
            >
                <Stack gap="sm">
                    <Text role="body" weight="medium">{t(dangerConfig?.title ?? 'mem.settings.deleteAll')}</Text>
                    <Text role="body">{t(dangerConfig?.description ?? 'mem.settings.deleteAllDesc')}</Text>
                </Stack>
            </Sheet>
        </Stack>
    );
}
