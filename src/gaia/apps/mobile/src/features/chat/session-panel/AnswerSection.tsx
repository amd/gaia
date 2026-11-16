// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import * as WebBrowser from 'expo-web-browser';
import type { ReactNode } from 'react';

import type { TurnSummary } from '@webui/types';
import { isolate, pluralKey, type Say } from '@webui/i18n/registry';

import { useFileStatus, useMessages } from '@/data';
import { openPcFile } from '@/features/files/openPcFile';
import { describeFailureWith, useT } from '@/i18n/useT';
import { Icon, Notice, Pressable, Row, Stack, Text } from '@/ui';
import { Cloud, ExternalLink, FileText, Globe, Monitor } from '@/ui/icons';

import { PanelSection } from './PanelSection';
import type { SectionProps } from './sections';
import { answerState, hostOf, liveDataFailed, turnFacts } from './turnSummary';

/** The latest answer's sources, files and where its data went. Shown only once the chat has an answer. */
export function AnswerSection({ sessionId, live }: SectionProps) {
    const t = useT();
    const messages = useMessages(sessionId).data?.messages ?? [];
    const state = answerState(messages, live);
    if (!state) return null;
    const summary = state.kind === 'turn' ? state.summary : null;
    const noLive = summary ? liveDataFailed(summary) : false;
    return (
        <PanelSection
            id="answer"
            title={t('sp.section.answer')}
            summary={noLive ? t('rail.noLive') : undefined}
            summaryTone="warning"
        >
            <Stack gap="lg">
                {!summary ? (
                    <Text tone="secondary" accessibilityRole="summary">
                        {t('rail.pending')}
                    </Text>
                ) : null}
                {noLive ? <Notice tone="warning" title={t('rail.noLive')} message={t('rail.noLiveWhy')} testID="answer-no-live" /> : null}
                {summary ? (
                    summary.error ? (
                        <Notice tone="danger" message={t('rail.error', { error: summary.error })} testID="answer-error" />
                    ) : (
                        <>
                            <Sources summary={summary} />
                            <Files sessionId={sessionId} summary={summary} />
                            <ThisTurn summary={summary} stopped={state.kind === 'turn' && state.stopped} />
                        </>
                    )
                ) : null}
            </Stack>
        </PanelSection>
    );
}

function Group({ label, children }: { label: string; children: ReactNode }) {
    return (
        <Stack gap="sm">
            <Text weight="semibold" accessibilityRole="header">
                {label}
            </Text>
            {children}
        </Stack>
    );
}

function documentMeta(say: Say, pages: number[] | undefined, passages: number): string {
    const parts: string[] = [];
    if (pages && pages.length > 0) parts.push(say(pluralKey('rail.pages', pages.length), { list: pages.join(', ') }));
    parts.push(say(pluralKey('rail.passages', passages), { n: passages }));
    return parts.join(' · ');
}

function Sources({ summary }: { summary: TurnSummary }) {
    const t = useT();
    const pages = (summary.pages ?? []).filter((p) => /^https?:\/\//i.test(p.url));
    const documents = summary.documents ?? [];
    if (pages.length === 0 && documents.length === 0) return null;
    return (
        <Group label={t('rail.sources')}>
            <Stack gap="md" accessibilityRole="list">
                {pages.map((p) => (
                    <Pressable
                        key={p.url}
                        accessibilityRole="link"
                        accessibilityLabel={p.title || hostOf(p.url)}
                        onPress={() => void WebBrowser.openBrowserAsync(p.url)}
                    >
                        <Row gap="sm" align="start">
                            <Icon icon={Globe} size="sm" tone="muted" />
                            <Stack flex={1} gap="xs">
                                <Row gap="xs">
                                    <Text tone="accent" numberOfLines={2}>
                                        {p.title || hostOf(p.url)}
                                    </Text>
                                    <Icon icon={ExternalLink} size="sm" tone="accent" />
                                </Row>
                                <Text role="caption" tone="muted">
                                    {hostOf(p.url)}
                                    {p.kind === 'search_result' ? ` · ${t('rail.searchResult')}` : ''}
                                </Text>
                            </Stack>
                        </Row>
                    </Pressable>
                ))}
                {documents.map((d) => (
                    <Row key={d.path} gap="sm" align="start">
                        <Icon icon={FileText} size="sm" tone="muted" />
                        <Stack flex={1} gap="xs">
                            <Text numberOfLines={2}>{isolate(d.name, 'ltr')}</Text>
                            <Text role="caption" tone="muted">
                                {t.compose((say) => documentMeta(say, d.pages, d.passages ?? 0))}
                            </Text>
                        </Stack>
                    </Row>
                ))}
            </Stack>
        </Group>
    );
}

function Files({ sessionId, summary }: { sessionId: string; summary: TurnSummary }) {
    const t = useT();
    const files = summary.files ?? [];
    const status = useFileStatus(
        files.map((f) => f.path),
        { enabled: files.length > 0 },
    );
    if (files.length === 0) return null;
    const now = new Map((status.data ?? []).map((f) => [f.path, f]));
    return (
        <Group label={t('rail.files')}>
            <Stack gap="md" accessibilityRole="list">
                {files.map((f) => {
                    const current = now.get(f.path);
                    const gone = current?.exists === false;
                    const changed =
                        current?.exists === true && typeof current.mtime === 'number' && typeof f.mtime === 'number' && current.mtime !== f.mtime;
                    return (
                        <Row key={f.path} gap="sm" align="start">
                            <Icon icon={FileText} size="sm" tone="muted" />
                            <Stack flex={1} gap="xs">
                                <Text tone={gone ? 'muted' : 'primary'} numberOfLines={2}>
                                    {isolate(f.name, 'ltr')}
                                </Text>
                                {gone ? (
                                    <Text role="caption" tone="muted">
                                        {t('sp.gone')}
                                    </Text>
                                ) : (
                                    <>
                                        {changed ? (
                                            <Text role="caption" tone="muted">
                                                {t('rail.changedSince')}
                                            </Text>
                                        ) : null}
                                        <Pressable
                                            accessibilityLabel={`${t('mobileApp.rail.preview')}: ${f.name}`}
                                            onPress={() => openPcFile(f.path, sessionId)}
                                        >
                                            <Text role="caption" tone="accent" weight="medium">
                                                {t('mobileApp.rail.preview')}
                                            </Text>
                                        </Pressable>
                                    </>
                                )}
                            </Stack>
                        </Row>
                    );
                })}
            </Stack>
            {status.error ? (
                <Notice tone="danger" message={t.compose((say) => say('rail.checkFailed', { error: describeFailureWith(status.error, say) }))} />
            ) : null}
        </Group>
    );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
    return (
        <Stack gap="xs">
            <Text role="caption" tone="muted">
                {label}
            </Text>
            {children}
        </Stack>
    );
}

function ThisTurn({ summary, stopped }: { summary: TurnSummary; stopped: boolean }) {
    const t = useT();
    const facts = turnFacts(summary, stopped);
    const remote = !!summary.model?.remote;
    return (
        <Group label={t('rail.thisTurn')}>
            <Stack gap="md">
                <Fact label={t('rail.model')}>
                    <Row gap="xs">
                        {summary.model ? <Icon icon={remote ? Cloud : Monitor} size="sm" tone={remote ? 'warning' : 'muted'} /> : null}
                        <Text>{t(facts.model.key, facts.model.params)}</Text>
                    </Row>
                    {facts.modelName ? (
                        <Text role="caption" tone="muted">
                            {isolate(facts.modelName, 'ltr')}
                        </Text>
                    ) : null}
                </Fact>
                {facts.documents ? (
                    <Fact label={t('rail.documents')}>
                        <Text>{t(facts.documents.key, facts.documents.params)}</Text>
                    </Fact>
                ) : null}
                <Fact label={t('rail.websites')}>
                    <Text>{facts.websites.length > 0 ? isolate(facts.websites.join(', '), 'ltr') : t('rail.noSites')}</Text>
                </Fact>
                <Fact label={t('rail.cost')}>
                    <Text>{t(facts.cost.key, facts.cost.params)}</Text>
                </Fact>
            </Stack>
        </Group>
    );
}
