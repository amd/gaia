// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react-native';
import { View } from 'react-native';
import { useT } from '@/i18n/useT';
import { Button } from '../controls/Button';
import { Icon } from '../controls/Icon';
import { Spinner } from '../controls/Spinner';
import { CircleAlert } from '../icons';
import { Row, Stack } from '../layout/layout';
import { useTheme } from '../theme/ThemeProvider';
import { Text } from '../text/Text';

export interface StateAction {
    label: string;
    onPress: () => void;
}

interface StateProps {
    title?: string;
    message?: string;
    action?: StateAction;
}

function Centered({ children }: { children: ReactNode }) {
    const { space } = useTheme();
    return <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', padding: space.xl }}>{children}</View>;
}

export function EmptyState({ title, message, action, icon }: StateProps & { title: string; icon?: LucideIcon }) {
    return (
        <Centered>
            <Stack gap="md" align="center">
                {icon ? <Icon icon={icon} size="lg" tone="muted" /> : null}
                <Text role="title" weight="semibold" align="center">
                    {title}
                </Text>
                {message ? (
                    <Text tone="secondary" align="center">
                        {message}
                    </Text>
                ) : null}
                {action ? (
                    <Row justify="center">
                        <Button label={action.label} onPress={action.onPress} />
                    </Row>
                ) : null}
            </Stack>
        </Centered>
    );
}

export function ErrorState({ title, message, action, onRetry }: StateProps & { onRetry?: () => void }) {
    const t = useT();
    return (
        <Centered>
            <Stack gap="md" align="center">
                <Icon icon={CircleAlert} size="lg" tone="danger" />
                <Text role="title" weight="semibold" align="center" accessibilityRole="alert">
                    {title ?? t('msg.error')}
                </Text>
                {message ? (
                    <Text tone="secondary" align="center">
                        {message}
                    </Text>
                ) : null}
                {onRetry ? (
                    <Row justify="center">
                        <Button label={t('msg.tryAgain')} onPress={onRetry} />
                    </Row>
                ) : null}
                {action ? (
                    <Row justify="center">
                        <Button variant="ghost" label={action.label} onPress={action.onPress} />
                    </Row>
                ) : null}
            </Stack>
        </Centered>
    );
}

export function LoadingState({ title, message }: Pick<StateProps, 'title' | 'message'>) {
    const t = useT();
    return (
        <Centered>
            <View accessible accessibilityRole="progressbar" accessibilityLabel={title ?? t('pick.loading')}>
                <Stack gap="md" align="center">
                    <Spinner />
                    <Text tone="secondary" align="center">
                        {title ?? t('pick.loading')}
                    </Text>
                    {message ? (
                        <Text role="caption" tone="muted" align="center">
                            {message}
                        </Text>
                    ) : null}
                </Stack>
            </View>
        </Centered>
    );
}
