// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';

import { providerName } from '@webui/utils/providerText';

import type { ProviderConnected } from '@/api/client.types';
import { useReconnectProvider, type ProviderRow } from '@/data';
import { useT, type Translate } from '@/i18n/useT';
import { PcOnlyAction } from '@/features/logs/PcOnlyAction';
import { Badge, Button, Notice, Row, Stack, Text } from '@/ui';
import { KeyRound, Trash2 } from '@/ui/icons';

import { errorText, refusedOnPhone } from './modelText';

function connectedText(r: ProviderConnected, name: string, t: Translate): string {
    if (r.remember_error) return t('settings.key.sessionOnly', { reason: r.remember_error });
    if (r.known_models) return t('settings.key.knownModels', { name });
    return r.remembered ? t('settings.key.kept') : t('settings.key.connectedDone');
}

type Outcome = { tone: 'success' | 'info' | 'danger'; text: string } | { tone: 'pcOnly' };

/**
 * A cloud provider's API key. The PC refuses a typed key from a phone, so the phone never offers a key
 * field: it shows whether the provider is connected (the list route says no more), Reconnect with the
 * key the PC holds, and where to change it.
 */
export function ProviderKey({ provider, connected }: { provider: ProviderRow; connected: boolean }) {
    const t = useT();
    const name = providerName(t, provider);
    const reconnect = useReconnectProvider();
    const [outcome, setOutcome] = useState<Outcome | null>(null);

    // Model mounts one per provider, so leaving the screen or picking another provider both unmount.
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);

    const onReconnect = () => {
        setOutcome(null);
        const movedOn = () => !mounted.current;
        reconnect.mutate(
            { provider: provider.id, onWaiting: (detail) => !movedOn() && setOutcome({ tone: 'info', text: detail }), stopped: movedOn },
            {
                onSuccess: (r) => !movedOn() && setOutcome({ tone: 'success', text: connectedText(r, name, t) }),
                onError: (err) =>
                    !movedOn() && setOutcome(refusedOnPhone(err) ? { tone: 'pcOnly' } : { tone: 'danger', text: errorText(err, t) }),
            },
        );
    };

    const busy = reconnect.isPending;

    return (
        <Stack gap="sm" testID="provider-key">
            <Row gap="sm" justify="between">
                <Text role="label" weight="medium" tone="secondary" accessibilityRole="header">{t('settings.key.title')}</Text>
                <Badge
                    tone={connected ? 'success' : 'danger'}
                    label={t.bare(connected ? 'settings.key.connected' : 'settings.key.notConnected')}
                    accessibilityLabel={t(connected ? 'settings.key.connected' : 'settings.key.notConnected')}
                />
            </Row>

            {connected ? null : (
                <Button
                    variant="primary"
                    full
                    label={t('settings.key.reconnect')}
                    busy={reconnect.isPending}
                    disabled={busy}
                    onPress={onReconnect}
                />
            )}

            {connected ? (
                <>
                    <PcOnlyAction label={t('settings.key.replace')} icon={KeyRound} detail={t('mobileApp.settings.pcOnly')} testID="provider-key-replace" />
                    <PcOnlyAction label={t('settings.key.remove')} icon={Trash2} detail={t('mobileApp.settings.pcOnly')} testID="provider-key-remove" />
                </>
            ) : (
                <PcOnlyAction label={t('settings.key.paste')} icon={KeyRound} detail={t('mobileApp.settings.pcOnly')} testID="provider-key-paste" />
            )}
            <Notice tone="info" role="note" message={t('mobileApp.settings.pcOnly')} testID="provider-key-pc-only" />
            {outcome && outcome.tone !== 'pcOnly' ? <Notice tone={outcome.tone} message={outcome.text} /> : null}
        </Stack>
    );
}
