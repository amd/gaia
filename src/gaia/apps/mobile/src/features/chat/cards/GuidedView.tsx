// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { activateKeepAwakeAsync, deactivateKeepAwake } from 'expo-keep-awake';
import { useEffect, useState } from 'react';
import { useWindowDimensions } from 'react-native';

import { bigTimerIndex, findDurations, formatClock, parseSets } from '@webui/components/render/guide/durations';
import type { GuideDoc } from '@webui/components/render/guide/guideDoc';
import { itemDisplay, stepText, type GuideViewState } from '@webui/components/render/guide/guideText';
import type { Timers } from '@webui/components/render/guide/timerState';

import { useT } from '@/i18n/useT';
import { Button, Disclosure, IconButton, Ltr, Row, Sheet, Stack, Text } from '@/ui';
import { ChevronLeft, ChevronRight, Pause, Play, Square, X } from '@/ui/icons';

import { StepTimers, timerId } from './GuideTimers';

const AWAKE_TAG = 'guided-view';

/** True while the screen is held awake; false when the phone refuses, so the view can say it may sleep. */
function useScreenAwake(active: boolean): boolean {
    const [awake, setAwake] = useState(true);
    useEffect(() => {
        if (!active) return;
        let live = true;
        activateKeepAwakeAsync(AWAKE_TAG).then(
            () => live && setAwake(true),
            (err: unknown) => {
                console.warn('[guide] could not keep the screen awake', err);
                if (live) setAwake(false);
            },
        );
        return () => {
            live = false;
            deactivateKeepAwake(AWAKE_TAG).catch((err: unknown) => console.warn('[guide] could not release the screen lock', err));
        };
    }, [active]);
    return awake;
}

/** One step at a time in a sheet: big text, the step's timer, sets for a workout, large previous and next. */
export function GuidedView({ open, doc, state, timers, onClose }: { open: boolean; doc: GuideDoc; state: GuideViewState; timers: Timers; onClose: () => void }) {
    const t = useT();
    const { height } = useWindowDimensions();
    const [index, setIndex] = useState(0);
    const [setsDone, setSetsDone] = useState<Record<number, number>>({});
    const [itemsOpen, setItemsOpen] = useState(false);
    const awake = useScreenAwake(open);
    const total = doc.steps.length;
    const last = index === total - 1;

    const step = doc.steps[index];
    const body = stepText(doc, step.body, state.system);
    const spans = doc.features.timers ? findDurations(body) : [];
    const sets = doc.type === 'workout' ? parseSets(body) : null;
    const done = setsDone[index] ?? 0;

    const restIndex = sets?.restIndex ?? null;
    const resting = restIndex !== null && timers.read(timerId(index, restIndex), spans[restIndex].seconds).status !== 'idle';
    const bigIndex = bigTimerIndex(spans.length, restIndex, resting);
    const big = bigIndex === null ? null : { id: timerId(index, bigIndex), seconds: spans[bigIndex].seconds };
    const bigView = big ? timers.read(big.id, big.seconds) : null;

    const finishSet = () => {
        if (!sets?.sets) return;
        setSetsDone((prev) => ({ ...prev, [index]: Math.min(sets.sets!, done + 1) }));
        if (restIndex !== null && done + 1 < sets.sets) {
            const id = timerId(index, restIndex);
            timers.reset(id);
            timers.toggle(id, spans[restIndex].seconds);
        }
    };

    const counter = t('cards.stepOf', { n: index + 1, total });
    const footer = (
        <Stack gap="sm">
            <Row gap="sm">
                <Stack flex={1} align="center">
                    <Text role="label" tone="secondary" accessibilityLabel={counter} accessibilityRole="header">
                        <Ltr>{`${index + 1} / ${total}`}</Ltr>
                    </Text>
                    {awake ? null : (
                        <Text role="caption" tone="muted">
                            {t('cards.screenMaySleep')}
                        </Text>
                    )}
                </Stack>
                <IconButton icon={X} label={t('cards.closeGuided')} size="sm" onPress={onClose} testID="guided-close" />
            </Row>
            <Row gap="sm" stack>
                <Stack flex={1}>
                    <Button
                        icon={ChevronLeft}
                        label={t.bare('cards.prevStep')}
                        accessibilityLabel={t('cards.prevStep')}
                        disabled={index === 0}
                        onPress={() => setIndex((i) => Math.max(0, i - 1))}
                        full
                        testID="guided-prev"
                    />
                </Stack>
                <Stack flex={1}>
                    {last ? (
                        <Button variant="primary" label={t.bare('cards.finish')} accessibilityLabel={t('cards.finish')} onPress={onClose} full testID="guided-finish" />
                    ) : (
                        <Button
                            variant="primary"
                            iconEnd={ChevronRight}
                            label={t.bare('cards.nextStep')}
                            accessibilityLabel={t('cards.nextStep')}
                            onPress={() => setIndex((i) => Math.min(total - 1, i + 1))}
                            full
                            testID="guided-next"
                        />
                    )}
                </Stack>
            </Row>
        </Stack>
    );

    return (
        <Sheet open={open} onClose={onClose} title={doc.title} footer={footer}>
            <Stack gap="lg" minHeight={Math.round(height * 0.4)}>
                {doc.items.length > 0 ? (
                    <Disclosure title={t(doc.features.itemsLabel)} open={itemsOpen} onToggle={() => setItemsOpen((o) => !o)}>
                        <Stack gap="xs">
                            {doc.items.map((_, i) => {
                                const { qty, text } = itemDisplay(doc, i, state);
                                return (
                                    <Text key={i} role="label" dir="auto">
                                        {qty ? (
                                            <Text role="label" weight="semibold" dir="ltr">
                                                {`${qty} `}
                                            </Text>
                                        ) : null}
                                        {text}
                                    </Text>
                                );
                            })}
                        </Stack>
                    </Disclosure>
                ) : null}

                <Text role="title" weight="semibold" dir="auto" accessibilityRole="header">
                    {step.title ?? t('cards.stepTitle', { n: index + 1 })}
                </Text>
                <Text role="prose" dir="auto" testID="guided-step-text">
                    {body}
                </Text>
                {spans.length > 1 ? <StepTimers text={body} stepIndex={index} timers={timers} /> : null}

                {sets?.sets ? (
                    <Row gap="md">
                        <Stack flex={1}>
                            <Text role="body" weight="medium">
                                {t(done >= sets.sets ? 'cards.setAllDone' : 'cards.setProgress', { n: Math.min(done + 1, sets.sets), total: sets.sets })}
                            </Text>
                        </Stack>
                        <Button label={t.bare('cards.setDone')} accessibilityLabel={t('cards.setDone')} disabled={done >= sets.sets} onPress={finishSet} testID="guided-set-done" />
                    </Row>
                ) : null}

                {big && bigView ? (
                    <Stack gap="md" align="center">
                        <Text role="display" mono tone={bigView.status === 'done' ? 'success' : 'primary'} accessibilityRole="timer">
                            {bigView.status === 'done' ? t.bare('cards.done') : <Ltr>{formatClock(bigView.remainingSeconds)}</Ltr>}
                        </Text>
                        <Row gap="sm">
                            <Button
                                variant="primary"
                                icon={bigView.status === 'running' ? Pause : Play}
                                label={t.bare(bigView.status === 'running' ? 'cards.pause' : bigView.status === 'paused' ? 'cards.resume' : 'cards.start')}
                                accessibilityLabel={t(bigView.status === 'running' ? 'cards.pause' : bigView.status === 'paused' ? 'cards.resume' : 'cards.start')}
                                onPress={() => timers.toggle(big.id, big.seconds)}
                                testID="guided-timer-toggle"
                            />
                            {bigView.status !== 'idle' ? (
                                <Button icon={Square} label={t.bare('cards.stop')} accessibilityLabel={t('cards.stop')} onPress={() => timers.reset(big.id)} testID="guided-timer-stop" />
                            ) : null}
                        </Row>
                    </Stack>
                ) : null}
            </Stack>
        </Sheet>
    );
}
