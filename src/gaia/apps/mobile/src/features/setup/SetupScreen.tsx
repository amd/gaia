// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, type ReactNode } from 'react';

import type { SetupPlan, SetupRunStatus } from '@/api/client.types';
import { useSetupPlan, useSetupStatus } from '@/data';
import { DirectionNotice } from '@/features/language/DirectionNotice';
import { useT } from '@/i18n/useT';
import { Button, IconButton, Notice, Row, Screen, Stack, Text } from '@/ui';
import { ChevronLeft, ChevronRight } from '@/ui/icons';

import {
    fullRun, isRunning, liveStageOf, navTargets, offersResume, railSteps, runWasLost, SETUP_STAGES, STAGE_LABELS,
    type SetupStage,
} from './flow';
import { LanguagePicker } from './LanguagePicker';
import { Reveal } from './Reveal';
import { SetupFooterBar, SetupFooterProvider } from './SetupFooter';
import { SetupRail } from './SetupRail';
import { SETUP_STEPS } from './steps';

/** What a step's body is given: where setup is, and the way to move it. */
export interface SetupStepContext {
    stage: SetupStage;
    run: SetupRunStatus | null;
    plan: SetupPlan | undefined;
    /** A run is going and this step is one before it: its choices wait for the run. */
    locked: boolean;
    /** Setup was reopened from Settings, not the first run. */
    again: boolean;
    goTo(stage: SetupStage): void;
    /** Setup moves to its download: the run has started. */
    startedRun(): void;
    /** Setup is done. */
    finish(): void;
}

export interface SetupScreenProps {
    onReady(): void;
    /** Opened from Settings (run setup again, or add local models): offers a way back. */
    onClose?: () => void;
    /** Each step's body; the steps are other screens' work. Without one the step shows only its title. */
    renderStep?: (ctx: SetupStepContext) => ReactNode;
}

type Phase = 'check' | 'plan' | 'download';

/**
 * First run on a phone: the step rail, top navigation, language, the resume and running notes,
 * Back to GAIA and the footer bar every step docks its actions in. Nothing downloads until a step says so.
 */
export function SetupScreen({ onReady, onClose, renderStep }: SetupScreenProps) {
    const t = useT();
    const status = useSetupStatus();
    const run = status.data ?? null;
    const plan = useSetupPlan();
    const again = onClose != null;

    const [phase, setPhase] = useState<Phase>('check');
    const [viewing, setViewing] = useState<SetupStage | null>(null);
    const [visited, setVisited] = useState<ReadonlySet<SetupStage>>(() => new Set<SetupStage>(['check']));
    const [interrupted, setInterrupted] = useState(false);

    // Pick up a setup already started (another device, a restart of this app); run again starts over.
    const [pickedUp, setPickedUp] = useState(false);
    if (!pickedUp && status.isSuccess) {
        setPickedUp(true);
        const s = status.data;
        const started = !(s.state === 'idle' && !s.models?.length);
        if (started && !(again && !isRunning(s)) && !s.server_only) setPhase('download');
    }

    // GAIA restarted and lost the run: back to the plan, which offers to resume.
    const [seen, setSeen] = useState<SetupRunStatus | null>(null);
    if (status.data && status.data !== seen) {
        setSeen(status.data);
        if (runWasLost(seen, status.data)) {
            setPhase('plan');
            setInterrupted(true);
        }
    }
    const { refetch: refetchPlan } = plan;
    useEffect(() => {
        if (interrupted) void refetchPlan();
    }, [interrupted, refetchPlan]);

    const running = isRunning(run);
    const live = liveStageOf(phase, run);
    const shown = viewing ?? live;
    if (!visited.has(shown)) setVisited(new Set(visited).add(shown));

    const full = fullRun(run);
    const failed = full?.state === 'failed' || full?.state === 'cancelled' || !!full?.model_failed;
    const steps = railSteps({ live, shown, running, failed, visited });
    const { prev, next } = navTargets(steps);

    const goTo = (id: SetupStage) => {
        if (phase === 'download') {
            setViewing(id === live ? null : id);
            return;
        }
        setViewing(null);
        setPhase(id === 'test' || id === 'ready' ? 'download' : id);
    };
    const startedRun = () => {
        setViewing(null);
        setPhase('download');
    };

    const locked = viewing != null && running && (shown === 'check' || shown === 'plan');
    const at = SETUP_STAGES.indexOf(shown);
    const ctx: SetupStepContext = { stage: shown, run, plan: plan.data, locked, again, goTo, startedRun, finish: onReady };
    const Step = SETUP_STEPS[shown];

    return (
        <SetupFooterProvider>
            <Screen scroll footer={<SetupFooterBar />} footerDivider>
                <Stack gap="lg" padding="lg">
                    <DirectionNotice />
                    <SetupRail steps={steps} onOpen={goTo} />

                    <Row gap="sm" wrap>
                        <IconButton icon={ChevronLeft} label={t('setup.nav.prev')} disabled={!prev} onPress={() => prev && goTo(prev)} />
                        <IconButton icon={ChevronRight} label={t('setup.nav.next')} disabled={!next} onPress={() => next && goTo(next)} />
                        <Text role="label" tone="secondary">
                            {t('setup.nav.stepOf', { n: String(at + 1), total: String(SETUP_STAGES.length) })}
                        </Text>
                        {viewing != null && viewing !== live ? (
                            <Button
                                variant="ghost"
                                size="sm"
                                label={t.compose((say) => say('setup.nav.toLive', { step: say(STAGE_LABELS[live]) }))}
                                onPress={() => setViewing(null)}
                            />
                        ) : null}
                    </Row>

                    <LanguagePicker />

                    <Reveal open={offersResume(plan.data, run) && phase !== 'download'}>
                        <Notice tone="info" message={t('setup.resume.banner')} role="note" />
                    </Reveal>
                    <Reveal open={interrupted && phase !== 'download'}>
                        <Text role="caption" tone="secondary">{t('setup.interrupted')}</Text>
                    </Reveal>

                    {renderStep ? (
                        renderStep(ctx)
                    ) : Step ? (
                        <Step key={shown} ctx={ctx} />
                    ) : (
                        <Stack gap="sm">
                            <Text role="title" weight="semibold" accessibilityRole="header">{t(STAGE_LABELS[shown])}</Text>
                            <Text tone="secondary">{t('setup.lede.start')}</Text>
                        </Stack>
                    )}

                    <Reveal open={locked}>
                        <Text role="caption" tone="secondary">{t('setup.nav.runGoing')}</Text>
                    </Reveal>

                    {onClose && !running && shown !== 'ready' ? (
                        <Button variant="ghost" label={t('setup.backToGaia')} onPress={onClose} />
                    ) : null}
                </Stack>
            </Screen>
        </SetupFooterProvider>
    );
}
