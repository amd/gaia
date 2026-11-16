// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

import { useT } from '@/i18n/useT';
import { Button, Icon, Row, Stack, Text } from '@/ui';
import { CircleAlert } from '@/ui/icons';

interface Props {
    area: string;
    onReload: () => void;
    children: ReactNode;
}

/** Keeps a pane's render error to its section. "Try again" remounts the pane; "Reload" also re-fetches its data. */
export class SectionErrorBoundary extends Component<Props, { failed: boolean; attempt: number }> {
    state = { failed: false, attempt: 0 };

    static getDerivedStateFromError() {
        return { failed: true };
    }

    componentDidCatch(error: unknown, info: ErrorInfo): void {
        console.error(`[SectionErrorBoundary] ${this.props.area} failed to render:`, error, info.componentStack);
    }

    private retry = () => this.setState((s) => ({ failed: false, attempt: s.attempt + 1 }));

    private reload = () => {
        this.props.onReload();
        this.retry();
    };

    render(): ReactNode {
        if (this.state.failed) return <Fallback onRetry={this.retry} onReload={this.reload} />;
        return <Stack key={this.state.attempt} flex={1}>{this.props.children}</Stack>;
    }
}

function Fallback({ onRetry, onReload }: { onRetry: () => void; onReload: () => void }) {
    const t = useT();
    return (
        <Stack flex={1} padding="xl" gap="md" align="center" justify="center">
            <Icon icon={CircleAlert} size="lg" tone="danger" />
            <Text role="title" weight="semibold" align="center" accessibilityRole="alert">{t('errorBoundary.title')}</Text>
            <Text tone="secondary" align="center">{t('errorBoundary.body')}</Text>
            <Row gap="sm" justify="center" wrap>
                <Button label={t('errorBoundary.retry')} onPress={onRetry} />
                <Button label={t('errorBoundary.reload')} variant="secondary" onPress={onReload} />
            </Row>
        </Stack>
    );
}
