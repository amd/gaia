// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import * as Clipboard from 'expo-clipboard';
import { useEffect, useRef, useState } from 'react';

import type { Translate } from '@webui/i18n/translator';

import type { Settings } from '@/api/client.types';
import { GaiaApiError } from '@/api/errors';
import { usePinned } from '@/api/routeAccess';
import { useActiveModel, useSessions, useSetNetworkPolicy, useSettings, useSystemStatus } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { Card, Disclosure, IconButton, Ltr, Notice, Row, Stack, Switch, Text } from '@/ui';
import { Check, Copy } from '@/ui/icons';

import { PcOnlyNotice } from '../PcOnlyNotice';
import { SettingsAnchor } from '../targets';
import { DeleteChats } from './DeleteChats';
import { PrivacyUndo } from './PrivacyUndo';

type PolicyNotices = NonNullable<Settings['policy_notices']>;

/** The sentence the PC's policy notices add up to, and the raw urls and reasons they carry, kept for Details. */
function policyText(t: Translate, notices: PolicyNotices, on: boolean): { text: string; details: string[] } {
    if (!notices.length) return { text: t(on ? 'settings.privacy.localOnlyOn' : 'settings.privacy.localOnlyOff'), details: [] };
    const details = notices.flatMap((n) => [n.url, n.reason].filter((x): x is string => !!x));
    const text = notices
        .map((notice) => {
            if (notice.code === 'users_server_keeps_keys') return t('mobileApp.settings.privacy.usersServerKeepsKeys');
            if (notice.code === 'runtime_keys_not_dropped') return t('mobileApp.settings.privacy.runtimeKeysNotDropped');
            if (notice.code === 'saved_models_not_checked') return t('mobileApp.settings.privacy.savedModelsNotChecked');
            return t('settings.privacy.policyNoticeUnknown');
        })
        .join(' ');
    return { text, details };
}

function LocalOnly() {
    const t = useT();
    const settings = useSettings();
    const set = useSetNetworkPolicy();
    const [result, setResult] = useState<{ tone: 'success' | 'warning'; text: string; details: string[] } | null>(null);
    const [detailsOpen, setDetailsOpen] = useState(false);
    const pinned = usePinned();
    const error = set.error ?? settings.error;
    const refused = error instanceof GaiaApiError && error.status === 403;
    const on = set.isPending ? set.variables === 'local_ai_only' : settings.data?.network_policy === 'local_ai_only';

    const toggle = (next: boolean) => {
        if (pinned) return;
        setResult(null);
        set.mutate(next ? 'local_ai_only' : 'cloud_allowed', {
            onSuccess: (saved) => {
                const notices = saved.policy_notices ?? [];
                setDetailsOpen(false);
                setResult({
                    tone: notices.some((n) => n.severity === 'warning') ? 'warning' : 'success',
                    ...policyText(t, notices, saved.network_policy === 'local_ai_only'),
                });
            },
        });
    };

    return (
        <SettingsAnchor name="local-only">
            <Card>
                <Stack gap="sm">
                    {pinned ? (
                        <>
                            <Row gap="sm" justify="between">
                                <Stack gap="xs" flex={1}>
                                    <Text role="label" weight="medium" accessibilityRole="header">
                                        {t('settings.privacy.localOnlyTitle')}
                                    </Text>
                                    <Text role="caption" tone="secondary">
                                        {t('mobileApp.settings.privacy.localOnlyHint')}
                                    </Text>
                                </Stack>
                                <Text role="label" weight="medium" testID="privacy-local-only-state">
                                    {t(on ? 'mobileApp.settings.on' : 'mobileApp.settings.off')}
                                </Text>
                            </Row>
                            <PcOnlyNotice testID="privacy-pc-only" />
                        </>
                    ) : (
                        <Switch
                            label={t('settings.privacy.localOnlyTitle')}
                            description={t('mobileApp.settings.privacy.localOnlyHint')}
                            value={!!on}
                            disabled={!settings.data || set.isPending}
                            onValueChange={toggle}
                            testID="privacy-local-only"
                        />
                    )}
                    {refused ? (
                        <Notice tone="info" message={t('mobileApp.settings.pcOnly')} testID="privacy-pc-only" />
                    ) : error ? (
                        <Notice tone="danger" message={describeError(error, t)} testID="privacy-policy-error" />
                    ) : result ? (
                        <Stack gap="sm">
                            <Notice tone={result.tone} message={result.text} testID="privacy-policy-result" />
                            {result.details.length ? (
                                <Disclosure title={t('mobileApp.settings.privacy.details')} open={detailsOpen} onToggle={() => setDetailsOpen((o) => !o)}>
                                    <Stack gap="xs">
                                        {result.details.map((d) => (
                                            <Ltr key={d}>{d}</Ltr>
                                        ))}
                                    </Stack>
                                </Disclosure>
                            ) : null}
                        </Stack>
                    ) : null}
                </Stack>
            </Card>
        </SettingsAnchor>
    );
}

/** Where answers are generated: the cloud pick counts at once, else the newest chat's place, else the pick's own. */
function useAnswersFrom(): { state: 'remote'; name: string } | { state: 'local' | 'unknown' } {
    const t = useT();
    const active = useActiveModel().data;
    const chat = useSessions().data?.sessions[0];
    if (active?.remote) return { state: 'remote', name: active.provider_name || t.bare('mobileApp.settings.privacy.cloudProvider') };
    const remote = chat ? chat.inference_remote : active?.inference_remote;
    if (remote === true) return { state: 'remote', name: chat?.inference_provider_name || t.bare('mobileApp.settings.privacy.cloudProvider') };
    return { state: remote === false ? 'local' : 'unknown' };
}

function DataFolder({ path }: { path: string }) {
    const t = useT();
    const [copied, setCopied] = useState<'done' | 'failed' | null>(null);
    const fade = useRef<ReturnType<typeof setTimeout> | null>(null);
    useEffect(() => () => { if (fade.current) clearTimeout(fade.current); }, []);

    const copy = () => {
        const settle = (outcome: 'done' | 'failed') => {
            setCopied(outcome);
            if (fade.current) clearTimeout(fade.current);
            fade.current = setTimeout(() => setCopied(null), 2000);
        };
        Clipboard.setStringAsync(path).then(() => settle('done'), () => settle('failed'));
    };

    return (
        <Stack gap="xs">
            <Row gap="sm">
                <Text role="label" tone="secondary">
                    {t('settings.privacy.dataFolder')}
                </Text>
                <Row gap="xs" justify="end" flex={1}>
                    <Ltr chip>{path}</Ltr>
                    <IconButton icon={copied === 'done' ? Check : Copy} label={t('mobileApp.settings.privacy.copyPath')} size="sm" onPress={copy} />
                </Row>
            </Row>
            {copied ? (
                <Text role="caption" tone="secondary">
                    {t(copied === 'done' ? 'setup.found.copied' : 'setup.found.copyFailed')}
                </Text>
            ) : null}
        </Stack>
    );
}

function WhereYourDataIs() {
    const t = useT();
    const answers = useAnswersFrom();
    const dataDir = useSystemStatus().data?.data_dir;
    return (
        <Card>
            <Stack gap="sm">
                <Text role="label">{t('mobileApp.settings.privacy.stays')}</Text>
                <Text role="label" tone={answers.state === 'remote' ? 'primary' : 'muted'}>
                    {answers.state === 'remote'
                        ? t('settings.privacy.remote', { name: answers.name })
                        : t(answers.state === 'local' ? 'mobileApp.settings.privacy.local' : 'mobileApp.settings.privacy.unknown')}
                </Text>
                {dataDir ? <DataFolder path={dataDir} /> : null}
            </Stack>
        </Card>
    );
}

// The report bundles every GAIA log, which only the PC may read: the phone points there.
function TroubleshootingReport() {
    const t = useT();
    return (
        <Card>
            <Stack gap="sm">
                <Notice tone="info" message={t('setup.logs.reportOnPc')} testID="privacy-report-pc-only" />
            </Stack>
        </Card>
    );
}

/** Where data lives and goes, and how to delete it. */
export default function Privacy() {
    return (
        <Stack gap="lg" padding="lg">
            <LocalOnly />
            <WhereYourDataIs />
            <TroubleshootingReport />
            <PrivacyUndo />
            <DeleteChats />
        </Stack>
    );
}
