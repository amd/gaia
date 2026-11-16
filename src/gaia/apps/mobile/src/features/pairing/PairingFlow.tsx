// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { Platform } from 'react-native';

import { describeError, useT } from '@/i18n/useT';
import { useConnectionStore } from '@/state/connection';
import { Button, Card, Icon, Notice, Row, Screen, Spinner, Stack, Text, TextField } from '@/ui';
import { CircleAlert, Clock, Monitor, Smartphone } from '@/ui/icons';

import { parseInvite, type PairingInvite } from './invite';
import { PairingScanner } from './PairingScanner';
import { getPairingService } from './registry';
import type { PairingStatus, RedeemResult } from './service';

export type PairingStep = 'welcome' | 'how' | 'manual' | 'scan';

type View =
    | PairingStep
    | { at: 'confirm'; invite: PairingInvite; replaces: boolean; back: PairingStep }
    | { at: 'waiting'; pcName: string }
    | { at: 'refused'; rateLimited: boolean }
    | { at: 'unreachable' };

const POLL_MS = 2000;

export interface PairingFlowProps {
    initialStep?: PairingStep;
    /** Called once a PC has allowed this phone, after the connection is set. */
    onPaired?: () => void;
    /** Replaces the built-in camera step behind "Scan the code". */
    onScan?: () => void;
}

/** First launch: welcome, how to pair, paste a link or code, wait for Allow, and the two ways it can fail. */
export function PairingFlow({ initialStep = 'welcome', onPaired, onScan }: PairingFlowProps) {
    const t = useT();
    const setConnection = useConnectionStore((s) => s.setConnection);
    const removedFrom = useConnectionStore((s) => s.removedFrom);
    const [view, setView] = useState<View>(initialStep);
    const [text, setText] = useState('');
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const alive = useRef(true);

    useEffect(() => {
        alive.current = true;
        return () => {
            alive.current = false;
        };
    }, []);

    const settle = useCallback(
        (result: PairingStatus | RedeemResult) => {
            if (!alive.current) return;
            switch (result.state) {
                case 'pending':
                    setView({ at: 'waiting', pcName: result.pcName });
                    break;
                case 'paired':
                    setConnection(result.pc.connection);
                    onPaired?.();
                    break;
                case 'refused':
                    setView({ at: 'refused', rateLimited: result.rateLimited === true });
                    break;
                case 'unreachable':
                    setView({ at: 'unreachable' });
                    break;
                case 'none':
                    break;
            }
        },
        [onPaired, setConnection],
    );

    // A phone that was already waiting for Allow when the app reopened picks up where it was.
    useEffect(() => {
        getPairingService()
            .status()
            .then(settle)
            .catch((e) => alive.current && setError(describeError(e, t)));
    }, [settle, t]);

    // The camera exists on a phone build only; the web build pastes.
    const canScan = onScan !== undefined || Platform.OS !== 'web';
    const waiting = typeof view === 'object' && view.at === 'waiting';
    useEffect(() => {
        if (!waiting) return;
        const timer = setInterval(() => {
            getPairingService()
                .status()
                .then(settle)
                .catch((e) => alive.current && setError(describeError(e, t)));
        }, POLL_MS);
        return () => clearInterval(timer);
    }, [waiting, settle, t]);

    // A code only names a PC; the person confirms it before this phone sends the invitation.
    const submit = async (code: string = text) => {
        setError(null);
        let invite;
        try {
            invite = parseInvite(code);
        } catch (e) {
            setError(describeError(e, t));
            return;
        }
        setBusy(true);
        try {
            const paired = await getPairingService().list();
            if (!alive.current) return;
            setView({ at: 'confirm', invite, replaces: paired.some((pc) => pc.id === invite.pc_id), back: view === 'scan' ? 'scan' : 'manual' });
        } catch (e) {
            if (alive.current) setError(describeError(e, t));
        } finally {
            if (alive.current) setBusy(false);
        }
    };

    const redeem = async (invite: PairingInvite) => {
        setError(null);
        setBusy(true);
        try {
            settle(await getPairingService().redeem(invite));
        } catch (e) {
            if (alive.current) setError(describeError(e, t));
        } finally {
            if (alive.current) setBusy(false);
        }
    };

    const cancel = () => {
        void getPairingService().cancel();
        setView('how');
    };

    const retry = () => {
        setError(null);
        setView('how');
    };

    if (typeof view === 'object' && view.at === 'confirm') {
        const { invite, replaces, back } = view;
        const addresses = invite.origins.map((o) => o.slice('https://'.length)).join(', ');
        return (
            <Page
                icon={Monitor}
                title={t('mobileApp.pairing.confirm.title', { pc: invite.name })}
                body={t('mobileApp.pairing.confirm.body')}
                footer={
                    <>
                        <Button variant="primary" label={t('mobileApp.pairing.confirm.pair')} onPress={() => void redeem(invite)} busy={busy} full testID="pairing-confirm" />
                        <Button variant="secondary" label={t('mobileApp.pairing.confirm.cancel')} onPress={() => setView(back)} disabled={busy} full />
                    </>
                }
            >
                <Card>
                    <Stack gap="xs">
                        <Text role="caption" tone="secondary">
                            {t('mobileApp.pairing.confirm.address')}
                        </Text>
                        <Text role="body" selectable>
                            {addresses}
                        </Text>
                    </Stack>
                </Card>
                {replaces ? <Notice tone="warning" message={t('mobileApp.pairing.confirm.replaces', { pc: invite.name })} /> : null}
                {error ? <Notice tone="danger" message={error} /> : null}
            </Page>
        );
    }
    if (typeof view === 'object' && view.at === 'waiting') {
        return (
            <Page icon={Clock} title={t('mobileApp.pairing.waiting.title')} body={t('mobileApp.pairing.waiting.body', { pc: view.pcName })} busy>
                <Button variant="secondary" label={t('mobileApp.pairing.waiting.cancel')} onPress={cancel} full />
            </Page>
        );
    }
    if (typeof view === 'object' && view.at === 'refused') {
        return (
            <Page
                icon={CircleAlert}
                title={t('mobileApp.pairing.refused.title')}
                body={t(view.rateLimited ? 'mobileApp.pairing.refused.rateLimited' : 'mobileApp.pairing.refused.body')}
            >
                <Button variant="primary" label={t('mobileApp.pairing.refused.retry')} onPress={retry} full />
            </Page>
        );
    }
    if (typeof view === 'object' && view.at === 'unreachable') {
        return (
            <Page icon={CircleAlert} title={t('mobileApp.pairing.off.title')} body={t('mobileApp.pairing.off.body')}>
                <Button variant="primary" label={t('mobileApp.pairing.off.retry')} onPress={retry} full />
            </Page>
        );
    }
    if (view === 'welcome') {
        return (
            <Page icon={Smartphone} title={t('mobileApp.pairing.welcome.title')} body={t('mobileApp.pairing.welcome.body')}>
                {removedFrom ? <Notice tone="warning" message={t('mobileApp.pairing.removed', { pc: removedFrom })} testID="pairing-removed" /> : null}
                <Button variant="primary" label={t('mobileApp.pairing.welcome.start')} onPress={() => setView('how')} full />
            </Page>
        );
    }
    if (view === 'scan') {
        return (
            <Page title={t('mobileApp.pairing.how.scan')}>
                <PairingScanner onCode={(code) => void submit(code)} testID="pairing-scanner" />
                {busy ? <Spinner /> : null}
                {error ? <Notice tone="danger" message={error} /> : null}
                <Button variant="secondary" label={t('mobileApp.pairing.how.paste')} onPress={() => setView('manual')} full />
                <Button variant="ghost" label={t('mobileApp.pairing.manual.back')} onPress={() => setView('how')} full />
            </Page>
        );
    }
    if (view === 'how') {
        const steps = ['step1', 'step2', 'step3', 'step4'] as const;
        return (
            <Page title={t('mobileApp.pairing.how.title')}>
                <Card>
                    <Stack gap="md">
                        {steps.map((step, i) => (
                            <Row key={step} gap="md" align="start">
                                <Text role="body" weight="semibold" tone="accent">
                                    {i + 1}
                                </Text>
                                <Stack flex={1}>
                                    <Text role="body">{t(`mobileApp.pairing.how.${step}`)}</Text>
                                </Stack>
                            </Row>
                        ))}
                    </Stack>
                </Card>
                {canScan ? <Button variant="primary" label={t('mobileApp.pairing.how.scan')} onPress={onScan ?? (() => setView('scan'))} full /> : null}
                <Button variant={canScan ? 'secondary' : 'primary'} label={t('mobileApp.pairing.how.paste')} onPress={() => setView('manual')} full />
            </Page>
        );
    }
    return (
        <Page title={t('mobileApp.pairing.manual.title')} body={t('mobileApp.pairing.manual.body')}>
            <TextField
                label={t('mobileApp.pairing.manual.label')}
                value={text}
                onChangeText={(v) => {
                    setText(v);
                    setError(null);
                }}
                error={error ?? undefined}
                multiline
                maxLines={4}
                returnKeyType="done"
                disabled={busy}
                testID="pairing-input"
            />
            <Button variant="primary" label={t('mobileApp.pairing.manual.submit')} onPress={() => void submit()} busy={busy} full />
            <Button variant="ghost" label={t('mobileApp.pairing.manual.back')} onPress={() => setView('how')} full />
        </Page>
    );
}

function Page({
    icon,
    title,
    body,
    busy,
    footer,
    children,
}: {
    icon?: typeof Clock;
    title: string;
    body?: string;
    busy?: boolean;
    /** Actions pinned to the bottom of the screen, in thumb reach. */
    footer?: React.ReactNode;
    children?: React.ReactNode;
}) {
    return (
        <Screen
            banner={null}
            keyboard="avoid"
            scroll
            footer={
                footer ? (
                    <Stack gap="sm">
                        {footer}
                    </Stack>
                ) : undefined
            }
        >
            <Stack flex={1} gap="lg" padding="xl" justify="center" maxWidth={560} self="center">
                {icon ? (
                    <Stack align="center">
                        <Icon icon={icon} size="lg" tone="accent" />
                    </Stack>
                ) : null}
                <Text role="title" weight="semibold" align="center">
                    {title}
                </Text>
                {body ? (
                    <Text role="body" tone="secondary" align="center">
                        {body}
                    </Text>
                ) : null}
                {busy ? <Spinner /> : null}
                {children}
            </Stack>
        </Screen>
    );
}
