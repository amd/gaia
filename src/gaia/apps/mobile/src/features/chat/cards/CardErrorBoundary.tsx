// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Component, type ReactNode } from 'react';

import { useT } from '@/i18n/useT';
import { Notice } from '@/ui';

function CardFailed() {
    const t = useT();
    return <Notice tone="danger" title={t('render.cardFailed')} testID="card-failed" />;
}

/** One card that throws shows a notice in its place; the rest of the answer stays. */
export class CardErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
    state = { failed: false };

    static getDerivedStateFromError() {
        return { failed: true };
    }

    componentDidCatch(error: unknown): void {
        console.error('[CardErrorBoundary] card render failed:', error);
    }

    render(): ReactNode {
        return this.state.failed ? <CardFailed /> : this.props.children;
    }
}
