// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, type ReactNode } from 'react';

import { GaiaNetworkError, GaiaPinMismatchError } from '@/api/errors';
import { useGaiaClient, useSetupGate } from '@/data';
import { PinMismatchScreen, usePairAgain } from '@/features/pairing';
import { describeErrorWith, technicalDetail, useT } from '@/i18n/useT';
import { Button, Disclosure, Ltr, Notice, Screen, Stack } from '@/ui';

import { SetupScreen, type SetupScreenProps } from './SetupScreen';
import { StartupScreen } from './StartupScreen';

let reopen: (() => void) | null = null;

/** Reopens setup in re-run mode from outside the gate (Settings > Setup > Run setup again). */
export function reopenSetup(): void {
    if (!reopen) throw new Error('Setup cannot reopen: the setup gate is not mounted.');
    reopen();
}

/**
 * The first-run gate around the app: "Starting GAIA…" until the check answers, the setup screen in
 * place of the app when setup is needed, and the app, with a notice above it, when the PC's model
 * server is down or the check couldn't be answered. With no PC connected the app shows its own state.
 */
export function SetupGate({ children, renderStep }: { children: ReactNode; renderStep?: SetupScreenProps['renderStep'] }) {
    const client = useGaiaClient();
    const t = useT();
    const { gate, recheck, finish, open } = useSetupGate();
    const pairAgain = usePairAgain();

    useEffect(() => {
        reopen = open;
        return () => {
            if (reopen === open) reopen = null;
        };
    }, [open]);

    if (!client) return <>{children}</>;
    if (gate.state === 'checking') return <StartupScreen />;
    if (gate.state === 'upgrading') return <StartupScreen upgrading />;
    if (gate.state === 'needed') return <SetupScreen onReady={finish} renderStep={renderStep} />;
    if (gate.state === 'addingLocal') return <SetupScreen onReady={finish} onClose={finish} renderStep={renderStep} />;
    if (gate.state === 'ready') return <>{children}</>;

    // A PC whose certificate changed is a hard stop: the only way on is to pair again.
    if (gate.state === 'unknown' && gate.error instanceof GaiaPinMismatchError) {
        return <PinMismatchScreen onPairAgain={() => pairAgain.mutate()} busy={pairAgain.isPending} />;
    }
    const unreachable = gate.state === 'unknown' && !gate.upgradeFailed && gate.error instanceof GaiaNetworkError;

    const notice = unreachable ? (
        <Notice
            tone="warning"
            title={t('mobileApp.pairing.off.title')}
            message={t('mobileApp.pairing.unreachable.body')}
            action={{ label: t('mobileApp.pairing.off.retry'), onPress: () => void recheck() }}
        />
    ) :
        gate.state === 'serverDown' ? (
            <Notice
                tone="warning"
                message={t(gate.modelListUnread ? 'setup.modelListUnread.notice' : 'setup.serverDown.notice')}
                action={{ label: t('setup.unknown.retry'), onPress: () => void recheck({ quiet: true }) }}
            />
        ) : (
            <Notice
                tone="danger"
                message={
                    gate.upgradeFailed
                        ? t.compose((say) => say('setup.upgrading.failed', { error: describeErrorWith(gate.error, say) }))
                        : t.compose((say) => say('setup.unknown.failed', { error: describeErrorWith(gate.error, say) }))
                }
                action={{
                    label: t(gate.upgradeFailed ? 'setup.upgrading.retry' : 'setup.unknown.retry'),
                    onPress: () => void recheck(),
                }}
            />
        );
    return (
        <Screen edges={['top', 'start', 'end']}>
            <Stack gap="md" padding="md">
                {notice}
                {gate.state === 'unknown' && !unreachable ? <ErrorDetails error={gate.error} /> : null}
                {gate.state === 'serverDown' && !gate.modelListUnread ? (
                    <Button variant="secondary" label={t('setup.serverDown.setup')} onPress={open} />
                ) : null}
            </Stack>
            <Stack flex={1}>{children}</Stack>
        </Screen>
    );
}

function ErrorDetails({ error }: { error: unknown }) {
    const t = useT();
    const [open, setOpen] = useState(false);
    return (
        <Disclosure title={t('setup.progress.details')} open={open} onToggle={() => setOpen((o) => !o)}>
            <Ltr>{technicalDetail(error)}</Ltr>
        </Disclosure>
    );
}
