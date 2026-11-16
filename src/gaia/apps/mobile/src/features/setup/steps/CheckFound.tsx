// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { facts, installed, JOB_KEYS, specText, deviceText } from '@webui/components/setup/detected';
import { formatSize } from '@webui/components/setup/format';
import * as Clipboard from 'expo-clipboard';
import { useEffect, useRef, useState, type ReactNode } from 'react';

import type { PreflightReport, SetupPlan } from '@/api/client.types';
import { useT } from '@/i18n/useT';
import { Badge, Button, Card, Disclosure, Icon, IconButton, Ltr, Row, Stack, Text } from '@/ui';
import { Check, Copy, TriangleAlert } from '@/ui/icons';

import { findingWarnings } from './checkRows';

function Fact({ label, children }: { label: string; children: ReactNode }) {
    return (
        <Stack gap="xs">
            <Text role="caption" tone="secondary">{label}</Text>
            <Row gap="sm" wrap align="center">{children}</Row>
        </Stack>
    );
}

/** What setup found on this PC: the facts that decide setup, and the rest behind "All details". */
export function CheckFound({ plan, hardware, onChangeMemory }: {
    plan: SetupPlan;
    hardware: PreflightReport | null;
    /** Set when this PC's GPU memory split can be changed: opens the GPU memory screen. */
    onChangeMemory?: () => void;
}) {
    const t = useT();
    const f = facts(plan, hardware);
    const [open, setOpen] = useState(false);
    const [noticed, setNoticed] = useState(false);
    const [copied, setCopied] = useState<'done' | 'failed' | null>(null);
    const fade = useRef<ReturnType<typeof setTimeout> | null>(null);
    useEffect(() => () => { if (fade.current) clearTimeout(fade.current); }, []);

    const settle = (outcome: 'done' | 'failed') => {
        setCopied(outcome);
        if (fade.current) clearTimeout(fade.current);
        fade.current = setTimeout(() => setCopied(null), 2000);
    };
    const copy = () => {
        Clipboard.setStringAsync(specText(plan, hardware)).then(
            () => settle('done'),
            () => settle('failed'),
        );
    };

    const unknown = <Text tone="muted">{t('setup.found.unknown')}</Text>;
    const none = <Text tone="muted">{t('setup.found.none')}</Text>;
    const warnings = findingWarnings(hardware);
    const build = f.build && f.build !== 'release' ? <Badge label={t(`setup.found.build.${f.build}`)} /> : null;

    return (
        <Card>
            <Stack gap="md">
                <Row gap="sm" align="center" justify="between">
                    <Text role="title" weight="semibold" accessibilityRole="header">{t('setup.found.title')}</Text>
                    <Row gap="sm" align="center">
                        {copied ? <Text role="caption" tone="secondary">{t(copied === 'done' ? 'setup.found.copied' : 'setup.found.copyFailed')}</Text> : null}
                        <IconButton icon={copied === 'done' ? Check : Copy} label={t('setup.found.copy')} onPress={copy} />
                    </Row>
                </Row>

                <Fact label={t('setup.found.runsOn')}>
                    {f.runsOn ? <Ltr>{f.runsOn}</Ltr> : unknown}
                    {f.npu ? <Badge label="NPU" /> : null}
                </Fact>
                <Fact label={t('setup.found.memory')}>
                    {plan.capacity_gb != null && f.totalMemory
                        ? <Text>{t('setup.found.memoryOf', { size: formatSize(plan.capacity_gb), total: f.totalMemory })}</Text>
                        : plan.capacity_gb != null ? <Ltr>{formatSize(plan.capacity_gb)}</Ltr>
                            : f.totalMemory ? <Ltr>{f.totalMemory}</Ltr> : unknown}
                    {onChangeMemory ? <Button variant="ghost" size="sm" label={t('setup.found.changeMemory')} onPress={onChangeMemory} /> : null}
                </Fact>
                <Fact label={t('setup.found.space')}>{f.freeGb != null ? <Ltr>{formatSize(f.freeGb)}</Ltr> : unknown}</Fact>
                <Fact label={t('setup.found.software')}>
                    {f.software.length ? <Ltr>{f.software.join(' · ')}</Ltr> : unknown}
                    {build}
                </Fact>

                {warnings.length > 0 ? (
                    <Disclosure
                        title={t('setup.found.noticed')}
                        open={noticed}
                        onToggle={() => setNoticed((o) => !o)}
                        leading={<Icon icon={TriangleAlert} size="sm" tone="warning" />}
                    >
                        <Stack gap="xs">
                            {warnings.map((w) => <Ltr key={w}>{w}</Ltr>)}
                        </Stack>
                    </Disclosure>
                ) : null}

                <Disclosure title={t('setup.found.details')} open={open} onToggle={() => setOpen((o) => !o)}>
                    <Stack gap="md">
                        <Text role="label" weight="semibold">{t('setup.found.software')}</Text>
                        <Fact label="GAIA">{f.gaia ? <Ltr>{f.gaia}</Ltr> : unknown}{build}</Fact>
                        <Fact label={t('setup.found.agent')}>
                            {f.sys?.agent_version ? <Ltr>{f.sys.agent_version}</Ltr> : <Text tone="muted">{t('setup.found.agentLater')}</Text>}
                        </Fact>
                        <Fact label={t('setup.found.server')}>
                            <Text>{plan.lemonade.mode === 'external' ? t('setup.found.serverYours') : t('setup.found.serverOwn')}</Text>
                            {f.sys?.lemonade_version ? <Ltr>{f.sys.lemonade_version}</Ltr> : null}
                            {plan.lemonade.url ? <Ltr>{plan.lemonade.url}</Ltr> : null}
                        </Fact>

                        <Text role="label" weight="semibold">{t('setup.found.runtime')}</Text>
                        {f.runtimes.length ? f.runtimes.map(({ name, b }) => (
                            <Stack key={b.recipe} gap="xs">
                                <Row gap="sm" wrap align="center">
                                    <Ltr>{name}</Ltr>
                                    <Ltr>{[b.backend, b.version].filter(Boolean).join(' ')}</Ltr>
                                </Row>
                                <Text role="caption" tone={installed(b) ? 'success' : 'secondary'}>
                                    {t(installed(b) ? 'setup.pc.backend.installed' : 'setup.pc.backend.available')}
                                </Text>
                            </Stack>
                        )) : unknown}

                        <Text role="label" weight="semibold">{t('setup.found.models')}</Text>
                        {(plan.catalog ?? []).length ? (plan.catalog ?? []).map((e) => {
                            const size = e.full_size_gb ?? e.size_gb;
                            return (
                                <Stack key={e.id} gap="xs">
                                    <Text weight="medium">{e.name}</Text>
                                    <Row gap="sm" wrap align="center">
                                        <Text role="caption" tone="secondary">{e.roles.map((r) => t(JOB_KEYS[r])).join(' · ')}</Text>
                                        {e.support === 'verified' ? <Badge tone="success" label={t('setup.label.verified')} /> : null}
                                        {e.support === 'untested' ? <Badge tone="warning" label={t('setup.label.untested')} /> : null}
                                        {size ? <Ltr>{formatSize(size)}</Ltr> : null}
                                    </Row>
                                    <Text role="caption" tone={e.downloaded ? 'success' : 'secondary'}>
                                        {t(e.downloaded ? 'setup.models.status.onPc' : 'setup.found.notDownloaded')}
                                    </Text>
                                </Stack>
                            );
                        }) : unknown}

                        <Text role="label" weight="semibold">{t('setup.found.hardware')}</Text>
                        <Fact label={t('setup.found.os')}>{f.sys?.os ?? hardware?.os ? <Ltr>{(f.sys?.os ?? hardware?.os) as string}</Ltr> : unknown}</Fact>
                        <Fact label={t('setup.found.cpu')}>
                            {f.cpu || f.sys?.processor ? <Ltr>{(f.cpu?.name ?? f.sys?.processor) as string}</Ltr> : unknown}
                            {f.cpu?.cores
                                ? <Text role="caption" tone="secondary">{t('setup.found.cores', { cores: String(f.cpu.cores), threads: String(f.cpu.threads ?? f.cpu.cores) })}</Text>
                                : null}
                        </Fact>
                        <Fact label={t('setup.found.gpu')}>{f.graphics ? <Ltr>{f.graphics}</Ltr> : none}</Fact>
                        <Fact label={t('setup.found.npu')}>{f.npus.length ? <Ltr>{f.npus.map(deviceText).join('; ')}</Ltr> : none}</Fact>
                    </Stack>
                </Disclosure>
            </Stack>
        </Card>
    );
}
