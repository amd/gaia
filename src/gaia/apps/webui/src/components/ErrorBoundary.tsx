// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { useT } from '../i18n/useT';

interface ErrorBoundaryProps {
    /** Names the screen in the console log, e.g. "sidebar" or "settings.connectors". */
    area: string;
    children: ReactNode;
}

interface ErrorBoundaryState {
    hasError: boolean;
}

/** Keeps a render error to the screen it happened in instead of blanking the window. */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
    constructor(props: ErrorBoundaryProps) {
        super(props);
        this.state = { hasError: false };
    }

    static getDerivedStateFromError(): ErrorBoundaryState {
        return { hasError: true };
    }

    componentDidCatch(error: unknown, info: ErrorInfo): void {
        console.error(`[ErrorBoundary] ${this.props.area} failed to render:`, error, info.componentStack);
    }

    render(): ReactNode {
        if (this.state.hasError) {
            return <ErrorFallback onRetry={() => this.setState({ hasError: false })} />;
        }
        return this.props.children;
    }
}

function ErrorFallback({ onRetry }: { onRetry: () => void }) {
    const t = useT();
    return (
        <div role="alert" className="error-boundary">
            <p className="error-boundary-title">{t('errorBoundary.title')}</p>
            <p className="error-boundary-body">{t('errorBoundary.body')}</p>
            <div className="error-boundary-actions">
                <button type="button" className="btn-primary" onClick={onRetry}>{t('errorBoundary.retry')}</button>
                <button type="button" className="btn-secondary" onClick={() => window.location.reload()}>{t('errorBoundary.reload')}</button>
            </div>
        </div>
    );
}
