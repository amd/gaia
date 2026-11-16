// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createContext, useContext, useEffect, useRef, useState } from 'react';

import { isolate } from '@webui/i18n/registry';

import { usePinned } from '@/api/routeAccess';
import { useT } from '@/i18n/useT';
import { Button, Card, ChoiceList, Disclosure, Icon, Ltr, Notice, Row, ScrollBox, Stack, Surface, Switch, Text } from '@/ui';
import { CircleCheck, Cpu, Sparkles, Square } from '@/ui/icons';

import { PcOnlyNotice } from '../../settings/PcOnlyNotice';
import type { MemoryPaneProps } from '../tabs';
import { useProfileRun, type LogLine, type ProfileKind } from './useProfileRun';

const COPY = {
    discovery: { title: 'mobileApp.mem.disc.title', desc: 'mem.disc.desc', run: 'mem.disc.run', saved: 'mem.disc.saved', empty: 'mem.disc.empty', icon: Cpu },
    inference: { title: 'mem.inf.title', desc: 'mobileApp.mem.inf.desc', run: 'mem.inf.run', saved: 'mem.inf.saved', empty: 'mem.inf.empty', icon: Sparkles },
} as const;

const LIVE_LINES = 8;

/** The dev gallery starts a run, and can save its result, without a tap. */
export const ProfileDevContext = createContext<{ kind: ProfileKind; save?: boolean } | null>(null);

function Log({ lines, running }: { lines: LogLine[]; running: boolean }) {
    const body = (running ? lines.slice(-LIVE_LINES) : lines).map((line, i) => (
        <Text key={i} role="caption" tone={line.kind === 'error' ? 'danger' : line.kind === 'finding' ? 'primary' : 'muted'} dir="auto">
            {line.server ? isolate(line.text, 'ltr') : line.text}
        </Text>
    ));
    return running ? (
        <Surface tone="recessed" radius="md" padding="md" border testID="profile-log">
            <Stack gap="xs">{body}</Stack>
        </Surface>
    ) : (
        <ScrollBox maxHeight={220} testID="profile-log">
            <Stack gap="xs">{body}</Stack>
        </ScrollBox>
    );
}

export function ProfileCard({ kind, notify }: { kind: ProfileKind; notify: MemoryPaneProps['notify'] }) {
    const t = useT();
    const copy = COPY[kind];
    const run = useProfileRun(kind, notify);
    // The scan reads the PC's files and browser, so the PC refuses it from a paired phone.
    const pinned = usePinned();
    const [includeBrowser, setIncludeBrowser] = useState(false);
    const [detailOpen, setDetailOpen] = useState(false);
    const { phase, logs, items, detail, savedCount } = run;
    const dev = useContext(ProfileDevContext);
    const auto = useRef(dev?.kind === kind);
    const autoSaved = useRef(false);
    const { start, save } = run;
    useEffect(() => {
        if (!auto.current) return;
        auto.current = false;
        void start(false);
    }, [start]);
    useEffect(() => {
        if (dev?.kind !== kind || !dev.save || phase !== 'review' || items.length === 0 || autoSaved.current) return;
        autoSaved.current = true;
        void save();
    }, [dev, kind, phase, items.length, save]);
    const busy = phase === 'running' || phase === 'saving';
    const kept = items.filter((item) => item.checked).length;
    const reviewing = phase === 'review' || phase === 'saving' || phase === 'done';
    const locked = phase === 'saving' || phase === 'done';

    return (
        <Card padding="lg" testID={`profile-${kind}`}>
            <Stack gap="md">
                <Row gap="sm" accessibilityRole="header">
                    <Icon icon={copy.icon} size="md" tone="secondary" />
                    <Stack flex={1}>
                        <Text role="body" weight="semibold">
                            {t(copy.title)}
                        </Text>
                    </Stack>
                </Row>
                <Text tone="secondary">{t(copy.desc)}</Text>
                {kind === 'inference' && !pinned ? (
                    <>
                        <Text role="caption" tone="muted">
                            {t('mobileApp.mem.inf.note')}
                        </Text>
                        <Switch label={t('mem.inf.browser')} value={includeBrowser} onValueChange={setIncludeBrowser} disabled={busy} testID="profile-browser" />
                    </>
                ) : null}
                {pinned ? <PcOnlyNotice testID={`profile-${kind}-pc-only`} /> : null}
                {pinned ? null : (
                    <Row gap="sm" wrap>
                        <Button
                            label={t.bare(phase === 'running' ? 'mem.running' : copy.run)}
                            accessibilityLabel={t(phase === 'running' ? 'mem.running' : copy.run)}
                            variant="secondary"
                            busy={phase === 'running'}
                            disabled={busy}
                            onPress={() => void run.start(includeBrowser)}
                            testID={`profile-${kind}-run`}
                        />
                        {phase === 'running' ? (
                            <Button label={t.bare('composer.stop')} accessibilityLabel={t('composer.stop')} variant="ghost" icon={Square} onPress={run.stop} testID={`profile-${kind}-stop`} />
                        ) : null}
                    </Row>
                )}

                {logs.length > 0 ? <Log lines={logs} running={phase === 'running'} /> : null}
                {detail ? (
                    <Disclosure title={t('prompt.technicalDetails')} open={detailOpen} onToggle={() => setDetailOpen((open) => !open)}>
                        <Ltr>{detail}</Ltr>
                    </Disclosure>
                ) : null}

                {reviewing && items.length > 0 ? (
                    <Stack gap="sm" testID={`profile-${kind}-review`}>
                        <Text role="label" weight="semibold" accessibilityRole="header">
                            {t('mem.review.title')}
                        </Text>
                        <ChoiceList
                            multiple
                            value={items.flatMap((item, i) => (item.checked ? [String(i)] : []))}
                            onChange={(next) => {
                                if (locked) return;
                                items.forEach((item, i) => {
                                    if (next.includes(String(i)) !== item.checked) run.setChecked(i);
                                });
                            }}
                            options={items.map((item, i) => ({
                                value: String(i),
                                label: item.content,
                                description: isolate(`${item.tag} · ${Math.round(item.confidence * 100)}%`, 'ltr'),
                            }))}
                            accessibilityLabel={t('mem.review.title')}
                            testID={`profile-${kind}-items`}
                        />
                        <Row gap="md" wrap>
                            <Button
                                label={t.bare('mem.review.selectAll')}
                                accessibilityLabel={t('mem.review.selectAll')}
                                variant="ghost"
                                size="sm"
                                disabled={locked}
                                onPress={() => run.setChecked('all')}
                            />
                            <Button label={t.bare('mem.review.none')} accessibilityLabel={t('mem.review.none')} variant="ghost" size="sm" disabled={locked} onPress={() => run.setChecked('none')} />
                        </Row>
                        <Button
                            label={t.bare(phase === 'saving' ? 'mem.saving' : 'mem.review.saveSelected', { n: kept })}
                            accessibilityLabel={t(phase === 'saving' ? 'mem.saving' : 'mem.review.saveSelected', { n: kept })}
                            variant="primary"
                            full
                            busy={phase === 'saving'}
                            disabled={locked || kept === 0}
                            onPress={() => void run.save()}
                            testID={`profile-${kind}-save`}
                        />
                    </Stack>
                ) : null}

                {phase === 'done' && savedCount !== null ? (
                    <Notice tone="success" message={t(copy.saved, { n: savedCount })} testID={`profile-${kind}-saved`} />
                ) : null}
                {phase === 'review' && items.length === 0 ? (
                    <Row gap="sm" testID={`profile-${kind}-empty`}>
                        <Icon icon={CircleCheck} size="sm" tone="muted" />
                        <Text tone="muted">{t(copy.empty)}</Text>
                    </Row>
                ) : null}
            </Stack>
        </Card>
    );
}
