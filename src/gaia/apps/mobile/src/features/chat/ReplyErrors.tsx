// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { failureRecordId } from '@webui/utils/streamErrors.core';
import { isErrorContent } from '@webui/utils/answerErrors.core';

import type { Message } from '@/api/client.types';
import { useSession } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { openLink } from '@/platform/openLink';
import { Button, Icon, Notice, Row, Stack, Text } from '@/ui';
import { Info } from '@/ui/icons';

import { chatErrorInfo, namesFailureRecord, reportTitle } from './chatErrors';
import { phoneBugReportUrl } from './bugReport';
import type { TurnTail } from './tailState';

/** "Something went wrong", the web UI's banner on a failed reply. */
export function ErrorBanner() {
    const t = useT();
    return <Notice tone="danger" title={t('msg.error')} testID="error-banner" />;
}

/** What only the PC running GAIA can do: one plain line, never a control that would fail. */
function PcOnlyLine({ text, testID }: { text: string; testID: string }) {
    return (
        <Row gap="sm" align="start" accessibilityRole="text" testID={testID}>
            <Icon icon={Info} size="sm" tone="secondary" />
            <Stack flex={1}>
                <Text role="label" tone="secondary">
                    {text}
                </Text>
            </Stack>
        </Row>
    );
}

/** The log and report lines of a failed reply on a local model: both are read on the PC only. */
function PcOnlyFailureLines({ showLog, showReport }: { showLog: boolean; showReport: boolean }) {
    const t = useT();
    return (
        <>
            {showLog ? <PcOnlyLine text={t('mobileApp.pcOnly.logs')} testID="pc-only-logs" /> : null}
            {showReport ? <PcOnlyLine text={t('mobileApp.pcOnly.report')} testID="pc-only-report" /> : null}
        </>
    );
}

/** What a saved reply that looks like an error needs beside it, decided as the web UI's message bubble does. */
export function SavedReplyErrors({ message, fixShownBelow = false }: { message: Message; fixShownBelow?: boolean }) {
    const isError = message.failed === true || isErrorContent(message.content);
    const local = useSession(message.session_id).data?.inference_remote === false;
    const showLog = local && isError;
    const showReport = failureRecordId(message.content) !== null;
    if (!isError && !showLog && !showReport) return null;
    return (
        <Stack gap="sm">
            {isError && !fixShownBelow ? <ErrorBanner /> : null}
            <PcOnlyFailureLines showLog={showLog} showReport={showReport} />
        </Stack>
    );
}

function PolicyNotice({ alert }: { alert: NonNullable<TurnTail['policyAlert']> }) {
    const t = useT();
    const [dismissed, setDismissed] = useState<number | null>(null);
    if (dismissed === alert.seq) return null;
    return (
        <Stack gap="sm" testID="policy-notice">
            <Notice
                tone="warning"
                message={t('chat.policyBlocked', { tool: alert.tool || t('activity.policy.unknownTool') })}
                action={{ label: t('banner.dismiss'), onPress: () => setDismissed(alert.seq) }}
            />
            <Text role="label" tone="secondary">
                {t('activity.policy.receiptUnavailable')}
            </Text>
        </Stack>
    );
}

/** "Unexpected error? Report it on GitHub", the link the web UI appends to an unclassified error. */
function ReportLink({ title }: { title: string }) {
    const t = useT();
    const [failure, setFailure] = useState<string | null>(null);
    const open = () => {
        setFailure(null);
        openLink(phoneBugReportUrl(title), { inApp: true }).catch((err: unknown) => setFailure(describeError(err, t)));
    };
    return (
        <Stack gap="xs">
            <Row gap="sm" wrap align="center">
                <Text role="label" tone="secondary">
                    {t('chatError.unexpected')}
                </Text>
                <Button variant="ghost" size="sm" label={t('chatError.report')} onPress={open} testID="report-github" />
            </Row>
            {failure ? <Text role="label" tone="danger">{failure}</Text> : null}
        </Stack>
    );
}

/** What a failed or blocked turn needs under its reply: the banner, the policy notice and the PC-only lines. */
export function TailIssues({ tail, local }: { tail: TurnTail; local: boolean }) {
    const t = useT();
    const { error, policyAlert } = tail;
    const ended = !tail.live;
    const serverDown = ended && tail.failure === 'model_server_down';
    // A server-down reply drops its own banner, so the tail shows it once.
    const banner = !!tail.errorBanner || serverDown;
    const report = !!error && chatErrorInfo(error, t).kind !== 'stream';
    const showLog = ended && local && !!tail.failedReply && !tail.shownOnReply;
    const showReportNote = ended && !!error && namesFailureRecord(error);
    if (!banner && !policyAlert && !report && !showLog && !showReportNote) return null;
    return (
        <Stack gap="sm" paddingX="lg" paddingY="xs">
            {banner ? <ErrorBanner /> : null}
            {policyAlert ? <PolicyNotice alert={policyAlert} /> : null}
            {error && report ? <ReportLink title={reportTitle(error)} /> : null}
            <PcOnlyFailureLines showLog={showLog} showReport={showReportNote} />
        </Stack>
    );
}
