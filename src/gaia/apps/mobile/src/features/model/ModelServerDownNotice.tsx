// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { GaiaApiError } from '@/api/errors';
import { useLocalModelStatus, useRestartLocalModel } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { ChipButton } from '@/features/schedules/ChipButton';
import { Icon, Notice, Row, Text, useTheme } from '@/ui';
import { TriangleAlert } from '@/ui/icons';
import { View } from 'react-native';

/**
 * Under a turn that failed because the model server took no connection: start GAIA's own
 * server, or look again. Switching to GAIA's own server is PC-only, so it is not offered here.
 */
export function ModelServerDownNotice() {
    const t = useT();
    const status = useLocalModelStatus(true);
    const restart = useRestartLocalModel();
    const { colors, radius, space } = useTheme();
    const [asked, setAsked] = useState(false);

    const s = status.data;
    const starting = restart.isPending || !!s?.restarting;
    const ours = s?.owner === 'gaia';
    const down = !s || s.state === 'stopped';

    if (asked && s && !down && !starting) {
        return <Notice tone="success" message={t('serverDown.back')} testID="server-down-back" />;
    }
    const raw = restart.error
        ? restart.error instanceof GaiaApiError
            ? restart.error.detail
            : describeError(restart.error, t)
        : s?.restart_error;
    const stillDown = asked && !starting && !status.isFetching && down && !raw;
    return (
        <View
            testID="server-down-notice"
            accessibilityRole="alert"
            style={{
                gap: space.sm,
                padding: space.md,
                backgroundColor: colors.bgCard,
                borderColor: colors.accentYellow,
                borderWidth: 1,
                borderRadius: radius.md,
            }}
        >
            <Row gap="sm">
                <Icon icon={TriangleAlert} size="sm" tone="warning" />
                <Text role="ui" weight="semibold">{t('mobileApp.serverDown.title')}</Text>
            </Row>
            <Text role="ui">{t('serverDown.body')}</Text>
            {s && !ours ? <Text role="ui">{t('serverDown.notOurs')}</Text> : null}
            {stillDown ? <Text role="ui">{t('serverDown.stillDown')}</Text> : null}
            {raw ? <Text role="label" tone="secondary" mono dir="ltr">{raw}</Text> : null}
            <Row gap="xs" wrap>
                {ours ? (
                    <ChipButton
                        variant="primary"
                        label={starting ? t('serverDown.starting') : t('serverDown.start')}
                        disabled={starting}
                        testID="server-down-start"
                        onPress={() => {
                            setAsked(true);
                            restart.mutate({ confirm: false });
                        }}
                    />
                ) : null}
                <ChipButton
                    variant="plain"
                    label={t('settings.key.checkAgain')}
                    disabled={starting}
                    testID="server-down-check"
                    onPress={() => {
                        setAsked(true);
                        void status.refetch();
                    }}
                />
            </Row>
        </View>
    );
}
