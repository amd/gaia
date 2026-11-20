// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { reloadAppAsync } from 'expo';
import { View } from 'react-native';
import { Component, type ReactNode } from 'react';

import { useT } from '@/i18n/useT';
import { Button, Stack, Text } from '@/ui';

export type BoundaryVariant = 'window' | 'fill' | 'compact';

function Fallback({ variant, onRetry }: { variant: BoundaryVariant; onRetry: () => void }) {
    const t = useT();
    return (
        <Stack gap="sm" padding="xl" align="center" justify="center" flex={variant === 'compact' ? undefined : 1} accessibilityRole="alert" testID="error-boundary">
            <Text role="prose" weight="semibold" align="center">{t('errorBoundary.title')}</Text>
            <Text role="ui" tone="secondary" align="center">
                {t(variant === 'window' ? 'errorBoundary.bodyWindow' : 'errorBoundary.body')}
            </Text>
            <View style={{ alignSelf: 'stretch', gap: 8 }}>
                <Button full variant="primary" label={t('errorBoundary.retry')} onPress={onRetry} testID="error-boundary-retry" />
                <Button full label={t('errorBoundary.reload')} onPress={() => void reloadAppAsync()} testID="error-boundary-reload" />
            </View>
        </Stack>
    );
}

interface Props {
    variant?: BoundaryVariant;
    /** A change clears a caught error, e.g. the open chat's id: leaving the chat leaves its crash. */
    resetKey?: string | number | null;
    children: ReactNode;
}

/** Catches a render error under it and shows "Try again" and "Reload" in its place (the web ErrorBoundary). */
export class AppErrorBoundary extends Component<Props, { error: Error | null }> {
    state: { error: Error | null } = { error: null };

    static getDerivedStateFromError(error: Error) {
        return { error };
    }

    componentDidCatch(error: Error) {
        console.error('GAIA render error', error);
    }

    componentDidUpdate(prev: Props) {
        if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
    }

    render() {
        if (!this.state.error) return this.props.children;
        return <Fallback variant={this.props.variant ?? 'fill'} onRetry={() => this.setState({ error: null })} />;
    }
}
