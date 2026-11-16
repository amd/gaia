// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isolate } from '@webui/i18n/registry';

import { ENDPOINTS } from '@/api/constants';
import { GaiaApiError } from '@/api/errors';
import { useDeviceRefused } from '@/api/routeAccess';
import { useLemonadeTarget, useSetLemonadeTarget } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { Button, Notice, Spinner, Stack, Text, TextField } from '@/ui';

import { ActionError, RawDetails } from './errors';

/**
 * Which model server GAIA uses. Switching is the PC's: the address and key are typed there, so a phone reads
 * them and can only hand the choice back to GAIA's own server.
 */
export function LemonadeTarget() {
    const t = useT();
    const query = useLemonadeTarget();
    const save = useSetLemonadeTarget();
    const saveRefused = useDeviceRefused('POST', ENDPOINTS.lemonadeTarget);
    const target = query.data;

    if (query.isPending) return <Spinner />;
    if (!target && query.error instanceof GaiaApiError && query.error.status === 403) {
        return <Notice tone="info" message={t('mobileApp.settings.pcOnly')} testID="advanced-lemonade-pc-only" />;
    }
    if (!target) {
        return (
            <Stack gap="sm">
                <Notice tone="warning" message={t('settings.server.readFailed')} />
                <RawDetails text={describeError(query.error, t)} />
            </Stack>
        );
    }

    const external = target.mode === 'external';
    const fromEnv = target.source === 'env';
    const url = isolate(target.base_url ?? '', 'ltr');
    // The refusal's own notice replaces the standing one, so the sentence shows once.
    const refused = save.error instanceof GaiaApiError && save.error.status === 403;

    return (
        <Stack gap="sm">
            <Text role="body">
                {fromEnv
                    ? t('settings.server.usesEnv', { url })
                    : external
                      ? t('settings.server.usesYours', { url })
                      : t('settings.server.usesOwn')}
            </Text>
            {!fromEnv && target.env_url_ignored ? (
                <Text role="caption" tone="muted">{t('settings.server.envIgnored', { url: isolate(target.env_url_ignored, 'ltr') })}</Text>
            ) : null}
            {fromEnv ? (
                <>
                    <Text role="caption" tone="muted">{t('settings.server.envUseOwn')}</Text>
                    {external && saveRefused ? <Notice tone="info" message={t('mobileApp.settings.pcOnly')} testID="advanced-lemonade-pc-only" /> : null}
                </>
            ) : (
                <>
                    <Text role="caption" tone="muted">{t('mobileApp.settings.serverUseYours')}</Text>
                    {external ? (
                        <TextField
                            value={target.saved_base_url ?? target.base_url ?? ''}
                            onChangeText={() => undefined}
                            disabled
                            accessibilityLabel={t('settings.server.address')}
                            testID="advanced-lemonade-address"
                        />
                    ) : null}
                    {refused ? null : <Notice tone="info" message={t('mobileApp.settings.pcOnly')} testID="advanced-lemonade-pc-only" />}
                </>
            )}
            {external && !saveRefused ? (
                <Button
                    label={t('settings.server.useOwn')}
                    variant="secondary"
                    full
                    busy={save.isPending}
                    disabled={save.isPending}
                    onPress={() => save.mutate({ mode: 'gaia' })}
                    testID="advanced-use-own"
                />
            ) : null}
            {save.isError ? (
                <ActionError error={save.error} title={t('settings.server.notSwitched')} />
            ) : null}
        </Stack>
    );
}
