// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { MessageKey } from '@webui/i18n/registry';
import type { AlertEvent, AlertMode } from '@webui/utils/alertSettings.core';

import { useT } from '@/i18n/useT';
import { useAlertSettings } from '@/state/alertSettings';
import { Card, SegmentedControl, Stack, Switch, Text } from '@/ui';

import { SettingsAnchor } from '../targets';

const EVENTS: { id: AlertEvent; label: MessageKey }[] = [
    { id: 'needs_approval', label: 'settings.alerts.approval' },
    { id: 'question', label: 'settings.alerts.question' },
    { id: 'finished', label: 'settings.alerts.finished' },
];

const MODES: { value: AlertMode; label: MessageKey }[] = [
    { value: 'banner', label: 'settings.alerts.banner' },
    { value: 'badge', label: 'settings.alerts.badge' },
    { value: 'off', label: 'settings.alerts.off' },
];

/** When a chat alerts you, and how; keep-awake belongs to the PC. */
export default function Notifications() {
    const t = useT();
    const { modes, sound, setMode, update } = useAlertSettings();
    return (
        <Stack gap="lg" padding="lg">
            <Text role="label" tone="secondary">
                {t('settings.alerts.lede')}
            </Text>
            <SettingsAnchor name="alerts">
                <Card>
                    <Stack gap="lg">
                        {EVENTS.map((event) => (
                            <Stack key={event.id} gap="xs">
                                <Text role="ui" weight="medium">
                                    {t(event.label)}
                                </Text>
                                <SegmentedControl
                                    options={MODES.map((m) => ({ value: m.value, label: t.bare(m.label), accessibilityLabel: t(m.label) }))}
                                    value={modes[event.id]}
                                    onChange={(mode) => setMode(event.id, mode as AlertMode)}
                                    accessibilityLabel={t(event.label)}
                                    testID={`alerts-${event.id}`}
                                />
                            </Stack>
                        ))}
                        <Text role="label" tone="muted">
                            {t('mobileApp.settings.alerts.bannersHint')}
                        </Text>
                        <Switch label={t('settings.alerts.sound')} value={sound} onValueChange={(on) => update({ sound: on })} testID="alerts-sound" />
                    </Stack>
                </Card>
            </SettingsAnchor>

            <SettingsAnchor name="keep-awake">
                <Card>
                    <Stack gap="sm">
                        <Text role="ui" weight="semibold" accessibilityRole="header">
                            {t('settings.alerts.keepAwakeTitle')}
                        </Text>
                        <Text role="label" tone="secondary">
                            {t('mobileApp.settings.alerts.keepAwakePc')}
                        </Text>
                    </Stack>
                </Card>
            </SettingsAnchor>
        </Stack>
    );
}
