// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { CutOff } from '@webui/utils/turnEnding.core';

import type { GaiaError } from '@/api/errors';
import { useT, type Translate } from '@/i18n/useT';
import { Icon, Row, Stack, Text } from '@/ui';
import { Square } from '@/ui/icons';

import { requestFailureText } from './chatErrors';

export interface Stop {
    state: 'stopped' | 'unconfirmed';
    error?: GaiaError;
}

function stopText(stop: Stop, t: Translate): string {
    if (stop.state === 'unconfirmed') return t('msg.stoppedUnconfirmed');
    return stop.error ? t('msg.stoppedError', { error: requestFailureText(stop.error, t) }) : t('msg.stopped');
}

export function StoppedNote({ stop }: { stop: Stop }) {
    const t = useT();
    return (
        <Row gap="sm" align="start" accessibilityRole="text" testID="stopped-note">
            <Icon icon={Square} size="sm" tone="secondary" />
            <Stack flex={1}>
                <Text role="label" tone="secondary">
                    {stopText(stop, t)}
                </Text>
            </Stack>
        </Row>
    );
}

/** The notice the server closed a cut-off reply with, in the app's language. */
export function CutOffNote({ cutOff }: { cutOff: CutOff }) {
    const t = useT();
    return (
        <Text role="label" tone="secondary">
            {t.compose((say) =>
                cutOff.tools ? `${say('msg.cutOff.toolsRan', { tools: cutOff.tools })} ${say(cutOff.notice)}` : say(cutOff.notice),
            )}
        </Text>
    );
}
