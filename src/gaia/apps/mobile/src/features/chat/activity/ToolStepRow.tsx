// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { AgentStep } from '@webui/types';
import {
    argRows, failureText, formatElapsed, formatToolLatency, thoughtLabel, toolRowText, type ActivityRow,
} from '@webui/utils/agentActivity.core';
import { isolate } from '@webui/i18n/registry';
import { useState } from 'react';

import { useT } from '@/i18n/useT';
import { Disclosure, Icon, Notice, Row, ScrollBox, Spinner, Stack, Text } from '@/ui';
import { Ban, CircleAlert } from '@/ui/icons';

import { CodeBlock } from '../markdown/CodeBlock';
import { ChunksView, FileListView } from './SourceLists';
import { TerminalOutput } from './TerminalOutput';

function Section({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <Stack gap="xs">
            <Text role="caption" tone="muted" weight="medium">
                {label}
            </Text>
            {children}
        </Stack>
    );
}

const OUTPUT_MAX_HEIGHT = 240;

function Output({ step, t }: { step: AgentStep; t: ReturnType<typeof useT> }) {
    const cmd = step.commandOutput;
    if (cmd) {
        return <TerminalOutput output={cmd} />;
    }
    if (step.mcpOutput) {
        return (
            <Section label={t('activity.detail.output')}>
                <ScrollBox maxHeight={OUTPUT_MAX_HEIGHT}>
                    <CodeBlock lang="" code={step.mcpOutput.text} streaming={false} />
                </ScrollBox>
                {step.mcpOutput.truncated ? <Text role="caption" tone="muted">{t('activity.truncated')}</Text> : null}
            </Section>
        );
    }
    if (!step.result || step.needsModel === 'vision') return null;
    return (
        <Section label={step.success === false ? t('activity.detail.error') : t('activity.detail.result')}>
            <ScrollBox maxHeight={OUTPUT_MAX_HEIGHT}>
                <Text role="label" dir="auto" tone={step.success === false ? 'danger' : 'secondary'} selectable testID="step-result">
                    {step.result}
                </Text>
            </ScrollBox>
        </Section>
    );
}

export interface ToolStepRowProps {
    step: ActivityRow;
    /** The turn is waiting on the person: a running call hasn't started. */
    awaitingUser?: boolean;
    /** Wall-clock ms, for a running call's timer. */
    now: number;
    testID?: string;
}

/** One tool call: a line that opens to its tool, arguments and output. */
export function ToolStepRow({ step, awaitingUser = false, now, testID }: ToolStepRowProps) {
    const t = useT();
    const [open, setOpen] = useState(false);
    const waiting = !!step.active && awaitingUser;
    const line = waiting ? t('activity.waiting') : toolRowText(step, t);
    const count = step.count ?? 1;
    const notRun = (!!step.declined || !!step.timedOut) && !step.active;
    const failure = step.success === false && step.result && !notRun && step.needsModel !== 'vision' ? failureText(step, t) : '';
    const timing = waiting
        ? ''
        : step.active
          ? formatElapsed((now - step.timestamp) / 1000)
          : step.latencyMs != null
            ? formatToolLatency(step.latencyMs)
            : '';

    const trailing = (
        <Row gap="sm">
            {count > 1 ? (
                <Text role="caption" tone="muted" accessibilityLabel={t('activity.identicalCalls', { n: count })}>
                    {`×${count}`}
                </Text>
            ) : null}
            {timing ? (
                <Text role="caption" tone="muted">
                    {isolate(timing, 'ltr')}
                </Text>
            ) : null}
        </Row>
    );
    const leading =
        step.active && !waiting ? (
            <Spinner size="sm" />
        ) : notRun ? (
            <Icon icon={Ban} size="sm" tone="muted" />
        ) : step.success === false ? (
            <Icon icon={CircleAlert} size="sm" tone="danger" />
        ) : null;

    return (
        <Stack>
            <Disclosure title={line} open={open} onToggle={() => setOpen((v) => !v)} leading={leading} trailing={trailing} headerTestID={testID}>
                <Stack gap="sm">
                    {step.thought?.detail ? (
                        <Section label={thoughtLabel(false, undefined, t)}>
                            <Text role="label" dir="auto" tone="secondary">
                                {step.thought.detail.trim()}
                            </Text>
                        </Section>
                    ) : null}
                    <Section label={t('activity.detail.tool')}>
                        <Row gap="sm" wrap>
                            <Text role="label" mono code>
                                {step.tool || t('activity.policy.unknownTool')}
                            </Text>
                            {step.mcpServer ? (
                                <Text role="caption" dir="auto" tone="muted">
                                    {t('activity.detail.via', { server: step.mcpServer })}
                                </Text>
                            ) : null}
                        </Row>
                    </Section>
                    {step.detail && !step.commandOutput ? (
                        <Section label={t('activity.detail.arguments')}>
                            <Stack gap="xs">
                                {argRows(step.detail).map((row, i) => (
                                    <Text key={i} role="label" dir="auto" tone="secondary" selectable>
                                        {row.key !== undefined ? `${row.key}: ${row.value}` : row.value}
                                    </Text>
                                ))}
                            </Stack>
                        </Section>
                    ) : null}
                    {failure ? <Text role="caption" dir="auto" tone="danger">{failure}</Text> : null}
                    <Output step={step} t={t} />
                    {step.retrievalChunks && step.retrievalChunks.length > 0 ? <ChunksView chunks={step.retrievalChunks} /> : null}
                    {step.fileList && step.fileList.files.length > 0 ? <FileListView fileList={step.fileList} /> : null}
                </Stack>
            </Disclosure>
            {step.needsModel === 'vision' && !step.active ? (
                <Notice tone="info" message={t('activity.visionNeeded')} testID="vision-offer" />
            ) : null}
        </Stack>
    );
}
