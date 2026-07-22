// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useCallback } from 'react';
import { X, ShieldAlert, AlertTriangle, Download } from 'lucide-react';
import type { AgentInfo } from '../types';

export interface InstallWarning {
    type: 'native_trust' | 'deprecated';
    title: string;
    detail: string;
}

/**
 * Build the list of warnings that gate an install. Empty list ⇒ install can
 * proceed without confirmation.
 *
 * - ``native_trust``: this check only raises the warning for a non-verified
 *   native (C++) agent, but the backend refuses (403) ANY non-verified
 *   install — not just native ones — unless the caller opts in via
 *   ``trust_native``.
 * - ``deprecated``: the publisher marked the agent deprecated; it may be
 *   unmaintained or superseded.
 */
export function installWarnings(agent: AgentInfo): InstallWarning[] {
    const warnings: InstallWarning[] = [];
    const nativeUntrusted =
        agent.requires_trust === true ||
        (agent.language === 'cpp' && !!agent.security_tier && agent.security_tier !== 'verified');
    if (nativeUntrusted) {
        warnings.push({
            type: 'native_trust',
            title: 'Native agent — runs unsandboxed',
            detail:
                `${agent.name} ships a native (C++) binary in the ` +
                `"${agent.security_tier ?? 'experimental'}" tier. It runs directly on your ` +
                `machine without sandboxing. Only install it if you trust the publisher.`,
        });
    }
    if (agent.deprecated) {
        warnings.push({
            type: 'deprecated',
            title: 'Deprecated by the publisher',
            detail:
                `${agent.name} has been deprecated and may be unmaintained or superseded. ` +
                `Install only if you specifically need this agent.`,
        });
    }
    return warnings;
}

interface InstallConfirmDialogProps {
    agent: AgentInfo;
    warnings: InstallWarning[];
    onConfirm: () => void;
    onCancel: () => void;
}

const WARNING_ICON: Record<InstallWarning['type'], typeof ShieldAlert> = {
    native_trust: ShieldAlert,
    deprecated: AlertTriangle,
};

/**
 * Confirmation shown before installing an agent that carries install warnings
 * (native-trust opt-in and/or deprecation). Confirming proceeds with
 * ``trust_native`` so the backend accepts a non-verified native install.
 */
export function InstallConfirmDialog({ agent, warnings, onConfirm, onCancel }: InstallConfirmDialogProps) {
    const handleKey = useCallback((e: KeyboardEvent) => {
        if (e.key === 'Escape') onCancel();
    }, [onCancel]);

    useEffect(() => {
        document.addEventListener('keydown', handleKey);
        return () => document.removeEventListener('keydown', handleKey);
    }, [handleKey]);

    return (
        <div className="agent-detail-overlay" onClick={onCancel}>
            <div
                className="agent-detail-modal install-confirm-modal"
                role="alertdialog"
                aria-modal="true"
                aria-labelledby="install-confirm-title"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="agent-detail-header">
                    <div className="agent-detail-icon install-confirm-icon">
                        <ShieldAlert size={24} />
                    </div>
                    <div className="agent-detail-title-area">
                        <h2 id="install-confirm-title" className="agent-detail-name">
                            Install {agent.name}?
                        </h2>
                        <p className="install-confirm-subtitle">Review before installing</p>
                    </div>
                    <button className="agent-detail-close" onClick={onCancel} aria-label="Cancel">
                        <X size={18} />
                    </button>
                </div>

                <div className="agent-detail-body">
                    <ul className="install-confirm-warnings">
                        {warnings.map((w) => {
                            const Icon = WARNING_ICON[w.type];
                            return (
                                <li key={w.type} className={`install-confirm-warning install-confirm-warning-${w.type}`}>
                                    <Icon size={16} className="install-confirm-warning-icon" />
                                    <div>
                                        <div className="install-confirm-warning-title">{w.title}</div>
                                        <div className="install-confirm-warning-detail">{w.detail}</div>
                                    </div>
                                </li>
                            );
                        })}
                    </ul>

                    <div className="install-confirm-actions">
                        <button className="btn-secondary" onClick={onCancel}>Cancel</button>
                        <button className="btn-install install-confirm-proceed" onClick={onConfirm}>
                            <Download size={14} /> Trust &amp; Install
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
