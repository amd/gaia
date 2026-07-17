// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Component } from 'react';
import type { ReactNode } from 'react';

interface CardErrorBoundaryProps {
    children: ReactNode;
}

interface CardErrorBoundaryState {
    hasError: boolean;
}

/**
 * Error boundary wrapping every registered render card (see RenderCard.tsx).
 * A bug in one card must never blank the whole assistant message — no
 * boundary exists anywhere else in the app, so this is the first one.
 */
export class CardErrorBoundary extends Component<CardErrorBoundaryProps, CardErrorBoundaryState> {
    constructor(props: CardErrorBoundaryProps) {
        super(props);
        this.state = { hasError: false };
    }

    static getDerivedStateFromError(): CardErrorBoundaryState {
        return { hasError: true };
    }

    componentDidCatch(error: unknown): void {
        console.error('[CardErrorBoundary] card render failed:', error);
    }

    render(): ReactNode {
        if (this.state.hasError) {
            return (
                <div role="alert" className="render-card-error">
                    Card failed to render
                </div>
            );
        }
        return this.props.children;
    }
}
