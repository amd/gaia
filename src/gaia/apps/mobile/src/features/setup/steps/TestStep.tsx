// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { formatSize } from '@webui/components/setup/format';
import { isolate } from '@webui/i18n/registry';
import type { SetupTest } from '@webui/types';

import { useCancelSetup, useRedownloadSetupModel, useStartSetup } from '@/data';
import { describeError, useT, type Translate } from '@/i18n/useT';
import { Button, Card, Disclosure, Icon, Ltr, Notice, ProgressLine, Row, Spinner, Stack, Text } from '@/ui';
import { Check, Clock, Minus, X } from '@/ui/icons';

import { SetupFooter } from '../SetupFooter';
import type { SetupStepContext } from '../SetupScreen';
import { useConfirmSheet } from '../sheets/ConfirmSheet';
import { SetupLogs } from '../sheets/SetupLogs';
import { rerunOptions, testView, whyKey } from './testView';

type Shown = SetupTest['state'] | 'waiting';

const TONE = { passed: 'success', failed: 'danger', skipped: 'muted', waiting: 'muted', running: 'secondary' } as const;

function Mark({ state }: { state: Shown }) {
    if (state === 'running') return <Spinner />;
    const icon = state === 'passed' ? Check : state === 'failed' ? X : state === 'skipped' ? Minus : Clock;
    return <Icon icon={icon} size="sm" tone={TONE[state]} />;
}

function resultText(row: SetupTest, t: Translate): string {
    if (row.state === 'running') return t('setup.test.running');
    if (row.state === 'failed') return t('setup.test.failed');
    if (row.state === 'skipped') return t('setup.test.skipped');
    return row.seconds != null ? t('setup.test.passedIn', { seconds: isolate(String(Math.round(row.seconds)), 'ltr') }) : t('setup.test.passed');
}

function Details({ text, open, onToggle }: { text: string; open: boolean; onToggle: () => void }) {
    const t = useT();
    return (
        <Disclosure title={t('setup.progress.details')} open={open} onToggle={onToggle}>
            <Ltr>{text}</Ltr>
        </Disclosure>
    );
}

function ResultRow({ state, label, subject, value, why, error }: {
    state: Shown;
    label: string;
    subject?: string;
    value: string;
    why?: string;
    error?: string | null;
}) {
    const [open, setOpen] = useState(false);
    return (
        <Row gap="sm" align="start">
            <Mark state={state} />
            <Stack flex={1} gap="xs">
                <Text weight="medium">{label}</Text>
                {subject ? <Ltr>{subject}</Ltr> : null}
                <Text role="caption" tone={state === 'failed' ? 'danger' : 'secondary'}>{value}</Text>
                {why ? <Text role="caption">{why}</Text> : null}
                {error ? <Details text={error} open={open} onToggle={() => setOpen((o) => !o)} /> : null}
            </Stack>
        </Row>
    );
}

/** The fourth step: each job runs for real, and setup is ready only when they pass. */
export function TestStep({ ctx }: { ctx: SetupStepContext }) {
    const t = useT();
    const run = ctx.run && !ctx.run.server_only ? ctx.run : null;
    const catalog = ctx.plan?.catalog ?? [];
    const cancel = useCancelSetup();
    const start = useStartSetup();
    const redownload = useRedownloadSetupModel();
    const { ask, sheet } = useConfirmSheet();
    const [errorOpen, setErrorOpen] = useState(false);

    if (!run) return <Text role="title" weight="semibold" accessibilityRole="header">{t('setup.test.title')}</Text>;

    const canRedownload = ctx.plan?.lemonade.mode !== 'external';
    const cloud = !!run.skip_chat_model;
    const view = testView(run, catalog, canRedownload);
    const nameOf = (id: string) => catalog.find((e) => e.id === id)?.name ?? id;
    const actionError = [start, redownload, cancel].find((m) => m.isError)?.error;
    const busy = start.isPending || redownload.isPending;
    const rowErrors = view.rows.some((r) => r.error === run.error);

    const downloadAgain = async (id: string) => {
        const size = catalog.find((e) => e.id === id)?.full_size_gb;
        const name = nameOf(id);
        const yes = await ask({
            title: t('setup.redownload.title', { name }),
            body: [t('setup.redownload.body', { name, size: size != null ? isolate(formatSize(size), 'ltr') : '' })],
            confirmLabel: t('setup.redownload.confirm'),
            cancelLabel: t('setup.redownload.keep'),
            danger: true,
        });
        if (yes) redownload.mutate(id);
    };

    let footer = null;
    if (run.state === 'verifying') {
        footer = <Button label={t('setup.progress.cancel')} busy={cancel.isPending} onPress={() => cancel.mutate()} />;
    } else if (view.stopped) {
        footer = (
            <>
                {view.modelAtFault && !cloud ? (
                    <Button label={t('setup.test.chooseAnother')} disabled={busy} wrap onPress={() => ctx.goTo('plan')} />
                ) : null}
                <Button
                    label={t('setup.test.tryAgain')}
                    variant={view.broken.length ? 'secondary' : 'primary'}
                    busy={start.isPending}
                    disabled={redownload.isPending}
                    wrap
                    onPress={() => start.mutate({ options: rerunOptions(run, catalog) })}
                />
                {view.broken.map((id) => (
                    <Button
                        key={id}
                        variant="primary"
                        wrap
                        busy={redownload.isPending}
                        disabled={start.isPending}
                        label={view.broken.length === 1 ? t('setup.test.downloadItAgain') : t('setup.test.downloadAgain', { name: nameOf(id) })}
                        onPress={() => void downloadAgain(id)}
                    />
                ))}
            </>
        );
    }

    return (
        <Stack gap="md">
            <Text role="title" weight="semibold" accessibilityRole="header">{t('setup.test.title')}</Text>
            <Text tone="secondary">{t('setup.test.lede')}</Text>
            <Card>
                <Stack gap="md" accessibilityRole="summary">
                    <ProgressLine
                        value={view.finished / view.total}
                        label={t('setup.test.count', { done: String(view.finished), total: String(view.total) })}
                    />
                    <Ltr>{`${view.percent}%`}</Ltr>
                    <Stack gap="sm">
                        {view.rows.map((r) => (
                            <ResultRow
                                key={`${r.job}:${r.subject ?? ''}`}
                                state={r.state}
                                label={t(`setup.test.job.${r.job}`)}
                                subject={r.subject ? nameOf(r.subject) : undefined}
                                value={resultText(r, t)}
                                why={r.error ? t(whyKey(r, canRedownload)) : undefined}
                                error={r.error}
                            />
                        ))}
                        {view.waiting.map((job) =>
                            job === 'agent' && view.agentNotInstalled ? (
                                <ResultRow key={job} state="failed" label={t(`setup.test.job.${job}`)} value={t('setup.test.failed')} why={t('setup.test.why.hub')} />
                            ) : (
                                <ResultRow
                                    key={job}
                                    state={job === view.startingJob ? 'running' : 'waiting'}
                                    label={t(`setup.test.job.${job}`)}
                                    value={t(job === view.startingJob ? 'setup.test.starting' : 'setup.test.waiting')}
                                />
                            ),
                        )}
                    </Stack>
                </Stack>
            </Card>
            {view.lead ? <Notice tone={run.state === 'cancelled' ? 'info' : 'danger'} message={t(view.lead)} /> : null}
            {view.stopped && run.error && !rowErrors ? (
                <Details text={run.error} open={errorOpen} onToggle={() => setErrorOpen((o) => !o)} />
            ) : null}
            {actionError ? <Notice tone="danger" message={describeError(actionError, t)} /> : null}
            {view.stopped ? <SetupLogs run={run} failed={run.state === 'failed'} /> : null}
            {footer ? <SetupFooter>{footer}</SetupFooter> : null}
            {sheet}
        </Stack>
    );
}
