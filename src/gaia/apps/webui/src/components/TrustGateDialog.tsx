// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, useCallback } from 'react';
import { X, ShieldCheck, ShieldAlert, Download, Key, MonitorCog } from 'lucide-react';
import type { AgentInfo } from '../types';
import type { TrustGate } from '../utils/hubLanes';

interface TrustGateDialogProps {
    agent: AgentInfo;
    gate: TrustGate;
    onConfirm: (trustNative: boolean) => void;
    onCancel: () => void;
}

const TIER_LABEL: Record<TrustGate['tier'], string> = {
    verified: 'AMD Verified',
    community: 'Community',
    experimental: 'Experimental',
};

/**
 * Install trust gate (issue #1722). Before an install proceeds it surfaces the
 * agent's ``security_tier``, declared ``permissions``, and platform
 * ``requirements``. When the gate ``requiresOverride`` (anything not
 * AMD-verified, native/unsandboxed, or deprecated) the primary button stays
 * DISABLED until the user ticks the explicit acknowledgement — the issue's
 * hard "refuse non-verified without override" requirement. A verified agent
 * shows an informational gate with the button enabled immediately.
 */
export function TrustGateDialog({ agent, gate, onConfirm, onCancel }: TrustGateDialogProps) {
    const [acknowledged, setAcknowledged] = useState(false);
    const canProceed = !gate.requiresOverride || acknowledged;

    const handleKey = useCallback(
        (e: KeyboardEvent) => {
            if (e.key === 'Escape') onCancel();
        },
        [onCancel],
    );

    useEffect(() => {
        document.addEventListener('keydown', handleKey);
        return () => document.removeEventListener('keydown', handleKey);
    }, [handleKey]);

    const TierIcon = gate.tier === 'verified' ? ShieldCheck : ShieldAlert;

    return (
        <div className="agent-detail-overlay" onClick={onCancel}>
            <div
                className="agent-detail-modal trust-gate-modal"
                role="alertdialog"
                aria-modal="true"
                aria-labelledby="trust-gate-title"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="agent-detail-header">
                    <div className={`agent-detail-icon trust-gate-icon trust-gate-icon-${gate.tier}`}>
                        <TierIcon size={24} />
                    </div>
                    <div className="agent-detail-title-area">
                        <h2 id="trust-gate-title" className="agent-detail-name">
                            Install {agent.name}?
                        </h2>
                        <p className="trust-gate-subtitle">
                            <span className={`trust-gate-tier-badge trust-gate-tier-${gate.tier}`}>
                                {TIER_LABEL[gate.tier]}
                            </span>
                            {agent.latest_version ? ` · v${agent.latest_version}` : ''}
                        </p>
                    </div>
                    <button className="agent-detail-close" onClick={onCancel} aria-label="Cancel">
                        <X size={18} />
                    </button>
                </div>

                <div className="agent-detail-body">
                    {gate.reasons.length > 0 && (
                        <ul className="trust-gate-reasons" aria-label="Trust warnings">
                            {gate.reasons.map((r) => (
                                <li key={r} className="trust-gate-reason">
                                    <ShieldAlert size={15} className="trust-gate-reason-icon" />
                                    <span>{r}</span>
                                </li>
                            ))}
                        </ul>
                    )}

                    <div className="trust-gate-section">
                        <div className="trust-gate-section-title">
                            <Key size={14} /> Permissions
                        </div>
                        {gate.permissions.length > 0 ? (
                            <ul className="trust-gate-perm-list">
                                {gate.permissions.map((p) => (
                                    <li key={p} className="trust-gate-perm">
                                        <code>{p}</code>
                                    </li>
                                ))}
                            </ul>
                        ) : (
                            <p className="trust-gate-empty">No special permissions declared.</p>
                        )}
                    </div>

                    <div className="trust-gate-section">
                        <div className="trust-gate-section-title">
                            <MonitorCog size={14} /> Requirements
                        </div>
                        {gate.platforms.length > 0 ? (
                            <p className="trust-gate-requirements">
                                Platforms: {gate.platforms.join(', ')}
                            </p>
                        ) : (
                            <p className="trust-gate-empty">No platform restrictions.</p>
                        )}
                    </div>

                    {gate.requiresOverride && (
                        <label className="trust-gate-ack">
                            <input
                                type="checkbox"
                                checked={acknowledged}
                                onChange={(e) => setAcknowledged(e.target.checked)}
                                aria-label="I understand and want to install this agent"
                            />
                            <span>
                                I understand this agent isn&apos;t AMD-verified and I want to
                                install it anyway.
                            </span>
                        </label>
                    )}

                    <div className="trust-gate-actions">
                        <button className="btn-secondary" onClick={onCancel}>
                            Cancel
                        </button>
                        <button
                            className="btn-install trust-gate-proceed"
                            onClick={() => onConfirm(gate.trustNative)}
                            disabled={!canProceed}
                            title={
                                canProceed
                                    ? undefined
                                    : 'Acknowledge the warning above to enable install'
                            }
                        >
                            <Download size={14} />{' '}
                            {gate.requiresOverride ? 'Trust & Install' : 'Install'}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
