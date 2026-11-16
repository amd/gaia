// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { initialSelection } from '@webui/components/setup/selection';
import type { MessageKey } from '@webui/i18n/registry';
import type { SetupProblem } from '@webui/types';
import { useEffect, useRef, useState } from 'react';

import type { SetupPlan, SetupRunStatus } from '@/api/client.types';
import {
    useCancelSetup,
    useGpuMemory,
    useLemonadeDetect,
    useLemonadeTarget,
    useSetupPlan,
    useSetupPreflight,
    useSetupStatus,
    useStartSetupServer,
} from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { Button, Card, Disclosure, Icon, Ltr, Notice, ProgressLine, Row, Spinner, Stack, Text } from '@/ui';
import { Check, Clock, ShieldQuestion, X } from '@/ui/icons';

import { SetupFooter } from '../SetupFooter';
import type { SetupStepContext } from '../SetupScreen';
import { CheckFound } from './CheckFound';
import { CheckOffer } from './CheckOffer';
import { usePlanScreenRequest } from './planScreenRequest';
import { offerSettled, offerState } from './offerState';
import { checkLede, checkRows, type CheckRow, type CheckRowState } from './checkRows';

const ROW_ICON = { done: Check, failed: X, question: ShieldQuestion, waiting: Clock } as const;
const ROW_TONE = { done: 'success', failed: 'danger', question: 'accent', waiting: 'muted', active: 'secondary' } as const;

function RowMark({ state }: { state: CheckRowState }) {
    return state === 'active' ? <Spinner /> : <Icon icon={ROW_ICON[state]} size="sm" tone={ROW_TONE[state]} />;
}

function RowList({ rows }: { rows: CheckRow[] }) {
    const t = useT();
    return (
        <Stack gap="sm">
            {rows.map((r) => (
                <Row key={r.id} gap="sm" align="start">
                    <RowMark state={r.state} />
                    <Stack flex={1} gap="xs">
                        <Text weight="medium">{t(r.label)}</Text>
                        {r.value ? <Ltr>{r.value}</Ltr> : null}
                    </Stack>
                </Row>
            ))}
        </Stack>
    );
}

/** The five checks with a bar counting the finished ones; once all pass, one line that opens to each. */
function Checks({ rows, awaitingChoice }: { rows: CheckRow[]; awaitingChoice: boolean }) {
    const t = useT();
    const [open, setOpen] = useState(false);
    const finished = rows.filter((r) => r.state === 'done').length;
    if (finished === rows.length) {
        return (
            <Card>
                <Disclosure
                    title={t('setup.checks.allPassed', { n: String(rows.length) })}
                    open={open}
                    onToggle={() => setOpen((o) => !o)}
                    leading={<Icon icon={Check} size="sm" tone="success" />}
                >
                    <RowList rows={rows} />
                </Disclosure>
            </Card>
        );
    }
    return (
        <Card>
            <Stack gap="md" accessibilityRole="summary">
                <ProgressLine
                    value={finished / rows.length}
                    label={awaitingChoice ? t('setup.checks.waitingChoice') : t('setup.checks.count', { done: String(finished), total: String(rows.length) })}
                />
                <RowList rows={rows} />
            </Stack>
        </Card>
    );
}

/** The model server's install: the part being set up, how far it is, and how many parts are ready. */
function RunActivity({ run, plan }: { run: SetupRunStatus; plan: SetupPlan | null }) {
    const t = useT();
    const parts = plan?.components ?? [];
    const active = parts.find((c) => run.components?.[c.id] === 'active');
    const ready = parts.filter((c) => run.components?.[c.id] === 'done' || (run.components?.[c.id] == null && c.installed)).length;
    const percent = run.percent != null ? Math.max(0, Math.min(100, Math.round(run.percent))) : null;
    const label = active ? t('setup.download.now', { name: t(`setup.component.${active.id}` as MessageKey) }) : t('setup.check.serverWorking');
    return (
        <Row gap="sm" wrap align="center">
            <Spinner />
            <Text role="caption" tone="secondary" accessibilityRole="summary">{label}</Text>
            {percent != null ? <Ltr>{`${percent}%`}</Ltr> : null}
            {parts.length > 0 ? <Text role="caption" tone="secondary">{t('setup.download.partsReady', { done: String(ready), total: String(parts.length) })}</Text> : null}
        </Row>
    );
}

/** A stopped server install: the words for a problem GAIA understands, the raw output behind Details. */
function RunError({ run }: { run: SetupRunStatus }) {
    const t = useT();
    const [open, setOpen] = useState(false);
    const lead: MessageKey = run.problem
        ? run.problem.kind === 'server_too_old' ? 'setup.server.tooOld' : 'setup.server.unreachable'
        : run.components?.server === 'failed' ? 'setup.server.ownFailed' : 'setup.stopped';
    const detail = run.problem?.message ?? run.error;
    return (
        <Stack gap="xs">
            <Notice tone="danger" message={t(lead)} />
            {detail ? (
                <Disclosure title={t('setup.progress.details')} open={open} onToggle={() => setOpen((o) => !o)}>
                    <Ltr>{detail}</Ltr>
                </Disclosure>
            ) : null}
        </Stack>
    );
}

function ProblemHelp({ problem }: { problem: SetupProblem }) {
    const t = useT();
    const [open, setOpen] = useState(false);
    return (
        <Stack gap="xs">
            {problem.start ? (
                <Disclosure title={t('setup.server.howToStart')} open={open} onToggle={() => setOpen((o) => !o)}>
                    <Ltr>{problem.start}</Ltr>
                </Disclosure>
            ) : null}
            {problem.use_gaia ? <Text role="caption" tone="secondary">{t('setup.server.useGaiaNote')}</Text> : null}
            {problem.use_gaia && problem.env_url ? (
                <Text role="caption" tone="secondary">{t('setup.server.envIgnoredNote', { url: problem.env_url })}</Text>
            ) : null}
        </Stack>
    );
}

/** The first step: GAIA reads this PC and its model server, and stops on what it found. */
export function CheckStep({ ctx }: { ctx: SetupStepContext }) {
    const t = useT();
    const status = useSetupStatus();
    const plan = useSetupPlan();
    const preflight = useSetupPreflight();
    const target = useLemonadeTarget();
    const detect = useLemonadeDetect(target.data?.offer === true);
    const start = useStartSetupServer();
    const cancel = useCancelSetup();
    const gpu = useGpuMemory().data;
    const changeMemory = gpu?.applies && !ctx.locked
        ? () => {
              usePlanScreenRequest.getState().request({ screen: 'gpu', from: 'check' });
              ctx.goTo('plan');
          }
        : undefined;

    const run = status.data ?? null;
    const serverRun = run?.server_only ? run : null;
    const serverRunning = serverRun?.state === 'running' || serverRun?.state === 'verifying';
    const serverFailed = serverRun?.state === 'failed' || serverRun?.state === 'cancelled';
    const planData = plan.data ?? null;
    const hardware = preflight.data ?? null;
    const hardwareError = preflight.isError ? describeError(preflight.error, t) : null;

    const [wasOffered, setWasOffered] = useState(false);
    const [carryOn, setCarryOn] = useState(false);
    const offer = offerState({
        target: target.data,
        targetError: target.error,
        detect: detect.data,
        detectError: detect.error,
        wasOffered,
    });
    if (offer.kind === 'offer' && !wasOffered) setWasOffered(true);
    const settled = carryOn || offerSettled(offer);
    const awaitingChoice = offer.kind === 'offer' && !carryOn;

    // The checks start the model server themselves: it downloads no model.
    const asked = useRef(false);
    const { mutate: startServer } = start;
    useEffect(() => {
        if (asked.current || !status.isSuccess || !settled || !planData || planData.lemonade.reachable) return;
        if (run && run.state !== 'idle') return;
        asked.current = true;
        startServer();
    }, [status.isSuccess, settled, planData, run, startServer]);

    // The server came up, or the PC chose one: read the device and the models against it.
    const { refetch: refetchPlan } = plan;
    const { refetch: refetchHardware } = preflight;
    const serverReady = serverRun?.state === 'ready';
    const chosen = offer.kind === 'chosen';
    useEffect(() => {
        if (!serverReady && !chosen) return;
        void refetchHardware();
        void refetchPlan();
    }, [serverReady, chosen, refetchHardware, refetchPlan]);

    const checksDone = !!(planData?.catalog && planData.lemonade.reachable && (hardware || hardwareError));
    const rows = checkRows({ hardware, hardwareError, plan: planData, serverRun, awaitingChoice }, t);
    const ticked = planData ? initialSelection(planData).ticked : new Set<string>();
    const canInstallOwn = !!planData && !planData.lemonade.reachable && !serverRun;
    const startError = start.isError ? describeError(start.error, t) : null;
    const [errorOpen, setErrorOpen] = useState(false);

    const retry = (gaiaLemonade: boolean) => start.mutate({ gaiaLemonade });
    const problem = serverFailed ? serverRun?.problem ?? null : null;
    const switchFirst = problem?.kind === 'server_too_old' && !!problem.use_gaia;

    let footer = null;
    if (serverFailed && serverRun) {
        footer = problem ? (
            <>
                <Button label={t('setup.tryAgain')} variant={switchFirst ? 'secondary' : 'primary'} busy={start.isPending} onPress={() => retry(false)} />
                {problem.use_gaia ? (
                    <Button label={t('setup.server.useGaia')} variant={switchFirst ? 'primary' : 'secondary'} busy={start.isPending} onPress={() => retry(true)} />
                ) : null}
            </>
        ) : (
            <Button label={t('setup.tryAgain')} variant="primary" busy={start.isPending} onPress={() => retry(!!serverRun.gaia_lemonade)} />
        );
    } else if (startError && !serverRun && planData && !planData.lemonade.reachable) {
        footer = <Button label={t('setup.tryAgain')} variant="primary" busy={start.isPending} onPress={() => retry(false)} />;
    } else if (plan.isError && !planData) {
        footer = <Button label={t('setup.tryAgain')} variant="primary" busy={plan.isFetching} onPress={() => void refetchPlan()} />;
    } else if (serverRunning) {
        footer = <Button label={t('setup.progress.cancel')} busy={cancel.isPending} onPress={() => cancel.mutate()} />;
    } else if ((offer.kind === 'error' || offer.kind === 'noAnswer') && !carryOn) {
        const checking = target.isFetching || detect.isFetching;
        footer = (
            <>
                <Button
                    label={t('setup.offer.checkAgain')}
                    busy={checking}
                    onPress={() => { void target.refetch(); void detect.refetch(); }}
                />
                {canInstallOwn ? <Button label={t('setup.offer.useOwn')} variant="primary" disabled={checking} onPress={() => setCarryOn(true)} /> : null}
            </>
        );
    } else if (checksDone && !ctx.locked && !awaitingChoice) {
        footer = <Button label={t('setup.continue')} variant="primary" onPress={() => ctx.goTo('plan')} />;
    }

    const showOffer = status.isSuccess && !!planData && ((!planData.lemonade.reachable && !serverRun) || (checksDone && !ctx.locked && planData.lemonade.mode !== 'external'));

    return (
        <Stack gap="md">
            <Text role="title" weight="semibold" accessibilityRole="header">{t(checksDone ? 'setup.check.headingDone' : 'setup.check.heading')}</Text>
            <Text tone="secondary">{t(checkLede(checksDone, planData, ticked))}</Text>
            <Checks rows={rows} awaitingChoice={awaitingChoice} />
            {serverRunning && serverRun ? <RunActivity run={serverRun} plan={planData} /> : null}
            {checksDone && planData ? <CheckFound plan={planData} hardware={hardware} onChangeMemory={changeMemory} /> : null}
            {serverFailed && serverRun ? <RunError run={serverRun} /> : null}
            {problem ? <ProblemHelp problem={problem} /> : null}
            {plan.isError && !planData ? (
                <Notice tone="danger" message={t('setup.failedLead')} />
            ) : null}
            {startError ? (
                <Stack gap="xs">
                    <Notice tone="danger" message={t('setup.failedLead')} />
                    <Disclosure title={t('setup.progress.details')} open={errorOpen} onToggle={() => setErrorOpen((o) => !o)}>
                        <Ltr>{startError}</Ltr>
                    </Disclosure>
                </Stack>
            ) : null}
            {showOffer ? <CheckOffer state={offer} /> : null}
            {footer ? <SetupFooter>{footer}</SetupFooter> : null}
        </Stack>
    );
}
