// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useMemo, useState } from 'react';

import { isGuideWire, parseGuideDoc, type GuideWire } from '@webui/components/render/guide/guideDoc';
import { formatGuideText, itemDisplay, stepText, stepTitle, type GuideViewState } from '@webui/components/render/guide/guideText';
import type { UnitSystem } from '@webui/components/render/guide/units';
import { pluralKey } from '@webui/i18n/registry';

import { useDirection, useT } from '@/i18n/useT';
import { contentDir, Button, Icon, IconButton, Ltr, Pressable, Row, SegmentedControl, Spacer, Stack, Surface, Text } from '@/ui';
import { Circle, CircleCheck, Copy, Check, Lightbulb, Minus, Play, Plus, Share2, X } from '@/ui/icons';

import { copyText, errorText, shareText } from './guideActions';
import { dismissGuideTip, loadTicks, saveTicks, useGuideTipDismissed } from './guideStore';
import { GuidedView } from './GuidedView';
import { StepTimers, useGuideTimers } from './GuideTimers';
import { UnsupportedCard } from './UnsupportedCard';

const MAX_SERVINGS = 99;
const NOTICE_MS = 2500;

export function GuideCard({ data, startGuided = false }: { data: unknown; startGuided?: boolean }) {
    if (!isGuideWire(data)) return <UnsupportedCard variant="invalid" render="guide" data={data} />;
    return <Guide wire={data} startGuided={startGuided} />;
}

function Guide({ wire, startGuided }: { wire: GuideWire; startGuided: boolean }) {
    const t = useT();
    const uiDir = useDirection();
    const doc = useMemo(() => parseGuideDoc(wire), [wire]);
    const [servings, setServings] = useState(doc.baseServings);
    const [system, setSystem] = useState<UnitSystem>(doc.nativeSystem);
    const [checked, setChecked] = useState<ReadonlySet<number>>(new Set());
    const [guided, setGuided] = useState(startGuided);
    const [session, setSession] = useState(0);
    const [notice, setNotice] = useState<string | null>(null);
    const timers = useGuideTimers();
    const tipDismissed = useGuideTipDismissed();
    const state: GuideViewState = { servings, system, checked };
    const { features } = doc;
    const copiedNotice = t('cards.copied');
    const dir = contentDir(`${doc.title} ${doc.items[0] ?? ''}`, uiDir);

    useEffect(() => {
        let live = true;
        if (features.checkItems) loadTicks(doc.key).then((saved) => live && setChecked(saved));
        return () => {
            live = false;
        };
    }, [doc.key, features.checkItems]);

    useEffect(() => {
        if (!notice) return;
        const id = setTimeout(() => setNotice(null), NOTICE_MS);
        return () => clearTimeout(id);
    }, [notice]);

    const toggleItem = (i: number) => {
        const next = new Set(checked);
        if (next.has(i)) next.delete(i);
        else next.add(i);
        setChecked(next);
        saveTicks(doc.key, next);
    };

    const fail = (key: 'cards.copyFailed' | 'cards.shareFailed') => (err: unknown) => setNotice(t(key, { error: errorText(err) }));
    const onCopy = () => copyText(formatGuideText(doc, state, t)).then(() => setNotice(copiedNotice), fail('cards.copyFailed'));
    const onShare = () => shareText(doc.title, formatGuideText(doc, state, t)).catch(fail('cards.shareFailed'));

    const other: UnitSystem = system === 'us' ? 'metric' : 'us';
    const systemBare = (s: UnitSystem) => t.bare(s === 'us' ? 'cards.system.us' : 'cards.system.metric');
    const showUnits = features.units && doc.convertible;
    const used = (fn: () => void) => () => {
        dismissGuideTip();
        fn();
    };

    return (
        <Surface tone="card" radius="md" border padding="md" testID="card-guide">
            <Stack gap="md" dir={dir}>
                <Stack gap="xs">
                    <Text role="title" weight="semibold" dir="auto" accessibilityRole="header">
                        {doc.title}
                    </Text>
                    {doc.description ? (
                        <Text role="label" tone="secondary" dir="auto">
                            {doc.description}
                        </Text>
                    ) : null}
                </Stack>

                <Row gap="xs" wrap>
                    {features.scaling && servings !== null ? (
                        <Row gap="xs" testID="guide-servings">
                            <IconButton
                                icon={Minus}
                                label={t('cards.fewerServings')}
                                size="sm"
                                disabled={servings <= 1}
                                onPress={used(() => setServings((n) => Math.max(1, (n ?? 1) - 1)))}
                                testID="guide-fewer"
                            />
                            <Text role="label" weight="medium" accessibilityLabel={`${t('cards.servings')}: ${t.plural('cards.servingCount', servings)}`}>
                                {t.bare(pluralKey('cards.servingCount', servings), { n: servings })}
                            </Text>
                            <IconButton
                                icon={Plus}
                                label={t('cards.moreServings')}
                                size="sm"
                                disabled={servings >= MAX_SERVINGS}
                                onPress={used(() => setServings((n) => Math.min(MAX_SERVINGS, (n ?? 1) + 1)))}
                                testID="guide-more"
                            />
                        </Row>
                    ) : null}
                    <Spacer />
                    <IconButton icon={Share2} label={t('cards.share')} size="sm" onPress={onShare} testID="guide-share" />
                    <IconButton icon={notice === copiedNotice ? Check : Copy} label={t('cards.copy')} size="sm" onPress={onCopy} testID="guide-copy" />
                </Row>

                {showUnits ? (
                    <SegmentedControl
                        options={[
                            { value: 'us', label: systemBare('us') },
                            { value: 'metric', label: systemBare('metric') },
                        ]}
                        value={system}
                        onChange={used(() => setSystem(other))}
                        accessibilityLabel={t.compose((say) => say('cards.unitsSwitch', { current: say(`cards.system.${system}`), other: say(`cards.system.${other}`) }))}
                        testID="guide-units"
                    />
                ) : null}

                {notice ? (
                    <Text role="caption" tone="secondary" dir="auto" accessibilityRole="alert">
                        {notice}
                    </Text>
                ) : null}

                {features.startLabel && doc.steps.length > 0 ? (
                    <Button
                        variant="primary"
                        icon={Play}
                        full
                        label={t.bare(features.startLabel)}
                        accessibilityLabel={t(features.startLabel)}
                        onPress={used(() => {
                            setSession((n) => n + 1);
                            setGuided(true);
                        })}
                        testID="guide-start"
                    />
                ) : null}

                {doc.items.length > 0 ? (
                    <Stack gap="xs">
                        <Text role="label" weight="semibold" tone="secondary" accessibilityRole="header">
                            {t(features.itemsLabel)}
                        </Text>
                        {doc.items.map((_, i) => {
                            const { qty, text } = itemDisplay(doc, i, state);
                            const content = (
                                <Text role="body" dir="auto" strike={features.checkItems && checked.has(i)} tone={features.checkItems && checked.has(i) ? 'muted' : 'primary'}>
                                    {qty ? (
                                        <Text role="body" weight="semibold" dir="ltr">
                                            {`${qty} `}
                                        </Text>
                                    ) : null}
                                    {stepText(doc, text, system)}
                                </Text>
                            );
                            return features.checkItems ? (
                                <Pressable
                                    key={i}
                                    onPress={() => toggleItem(i)}
                                    accessibilityRole="checkbox"
                                    accessibilityState={{ checked: checked.has(i) }}
                                    accessibilityLabel={qty ? `${qty} ${text}` : text}
                                    testID={`guide-item-${i}`}
                                >
                                    <Row gap="sm" dir={dir}>
                                        <Icon icon={checked.has(i) ? CircleCheck : Circle} tone={checked.has(i) ? 'accent' : 'muted'} />
                                        <Stack flex={1}>{content}</Stack>
                                    </Row>
                                </Pressable>
                            ) : (
                                <Row key={i} gap="sm" align="start" dir={dir}>
                                    <Text role="body" tone="secondary">
                                        •
                                    </Text>
                                    <Stack flex={1}>{content}</Stack>
                                </Row>
                            );
                        })}
                    </Stack>
                ) : null}

                {doc.steps.length > 0 && features.stepsLabel ? (
                    <Stack gap="sm">
                        <Text role="label" weight="semibold" tone="secondary" accessibilityRole="header">
                            {t(features.stepsLabel)}
                        </Text>
                        {doc.steps.map((step, i) => {
                            const title = stepTitle(doc, i, (n) => t('cards.dayN', { n }));
                            const body = stepText(doc, step.body, system);
                            return (
                                <Row key={i} gap="sm" align="start" dir={dir}>
                                    <Text role="label" weight="semibold" tone="accent">
                                        {features.timeline ? '•' : <Ltr>{`${i + 1}.`}</Ltr>}
                                    </Text>
                                    <Stack flex={1} gap="xs">
                                        {title ? (
                                            <Text role="label" weight="semibold" dir="auto">
                                                {title}
                                            </Text>
                                        ) : null}
                                        <Text role="body" dir="auto">
                                            {body}
                                        </Text>
                                        {features.timers ? <StepTimers text={body} stepIndex={i} timers={timers} /> : null}
                                    </Stack>
                                </Row>
                            );
                        })}
                    </Stack>
                ) : null}

                {doc.notes ? (
                    <Stack gap="xs">
                        <Text role="label" weight="semibold" tone="secondary" accessibilityRole="header">
                            {t('cards.notes')}
                        </Text>
                        <Text role="label" dir="auto">
                            {stepText(doc, doc.notes, system)}
                        </Text>
                    </Stack>
                ) : null}

                {features.tip && !tipDismissed ? (
                    <Surface tone="recessed" radius="md" border padding="sm" testID="guide-tip">
                        <Row gap="sm" align="start">
                            <Icon icon={Lightbulb} tone="accent" size="sm" />
                            <Stack flex={1}>
                                <Text role="label" tone="secondary">
                                    {t(features.tip)}
                                </Text>
                            </Stack>
                            <IconButton icon={X} label={t('cards.dismissTip')} size="sm" onPress={dismissGuideTip} testID="guide-tip-dismiss" />
                        </Row>
                    </Surface>
                ) : null}
            </Stack>
            {features.startLabel && doc.steps.length > 0 ? <GuidedView key={session} open={guided} doc={doc} state={state} timers={timers} onClose={() => setGuided(false)} /> : null}
        </Surface>
    );
}
