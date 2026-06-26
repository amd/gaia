// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useCallback } from 'react';
import { Wrench, Cpu, Shield, X, HardDrive, CheckCircle2, FlaskConical, AlertTriangle, BarChart2 } from 'lucide-react';
import { getAgentIcon } from './agentIcons';
import type { AgentInfo } from '../types';

const isElectron = typeof window !== 'undefined' && !!(window as any).electronAPI;

const TIER_META: Record<string, { Icon: typeof Shield; label: string }> = {
    verified: { Icon: CheckCircle2, label: 'Verified' },
    community: { Icon: Shield, label: 'Community' },
    experimental: { Icon: FlaskConical, label: 'Experimental' },
};

function tierBadge(tier?: string) {
    const meta = tier ? TIER_META[tier] : undefined;
    if (!meta) return null;
    const { Icon, label } = meta;
    return (
        <span className={`agent-badge agent-badge-tier-${tier}`} title={`${label} security tier`}>
            <Icon size={10} /> {label}
        </span>
    );
}

interface AgentDetailModalProps {
    agent: AgentInfo;
    onClose: () => void;
    onStartChat: (id: string, prompt?: string) => void;
}

export function AgentDetailModal({ agent, onClose, onStartChat }: AgentDetailModalProps) {
    const connections = agent.required_connections ?? [];
    const tags = agent.tags ?? [];
    const starters = agent.conversation_starters ?? [];
    const models = agent.models ?? [];
    const toolsCount = agent.tools_count ?? 0;
    const isNative = agent.source === 'native';
    const canStart = !isNative || isElectron;
    const DetailIcon = getAgentIcon(agent.icon);

    // Close on Escape
    const handleKey = useCallback((e: KeyboardEvent) => {
        if (e.key === 'Escape') onClose();
    }, [onClose]);

    useEffect(() => {
        document.addEventListener('keydown', handleKey);
        return () => document.removeEventListener('keydown', handleKey);
    }, [handleKey]);

    return (
        <div className="agent-detail-overlay" onClick={onClose}>
            <div
                className="agent-detail-modal"
                role="dialog"
                aria-modal="true"
                aria-labelledby="agent-detail-title"
                onClick={(e) => e.stopPropagation()}
            >
                {/* Header */}
                <div className="agent-detail-header">
                    <div className="agent-detail-icon">
                        <DetailIcon size={24} />
                    </div>
                    <div className="agent-detail-title-area">
                        <h2 id="agent-detail-title" className="agent-detail-name">{agent.name}</h2>
                        <div className="agent-hub-card-badges">
                            {agent.source === 'builtin' && <span className="agent-badge agent-badge-builtin">Built-in</span>}
                            {agent.source === 'native' && <span className="agent-badge agent-badge-native">Native</span>}
                            {agent.source === 'custom_python' && <span className="agent-badge agent-badge-custom">Custom</span>}
                            {agent.source === 'installed' && <span className="agent-badge agent-badge-installed">Installed</span>}
                            {tierBadge(agent.security_tier)}
                            {agent.deprecated && (
                                <span className="agent-badge agent-badge-deprecated" title="Deprecated by the publisher — may be unmaintained">
                                    <AlertTriangle size={10} /> Deprecated
                                </span>
                            )}
                            {agent.language && agent.language !== 'python' && (
                                <span className="agent-badge agent-badge-native">{agent.language.toUpperCase()}</span>
                            )}
                            {agent.category && agent.category !== 'general' && (
                                <span className="agent-badge agent-badge-category">{agent.category}</span>
                            )}
                        </div>
                    </div>
                    <button className="agent-detail-close" onClick={onClose} aria-label="Close">
                        <X size={18} />
                    </button>
                </div>

                {/* Body */}
                <div className="agent-detail-body">
                    {/* Description */}
                    <div className="agent-detail-section">
                        <div className="agent-detail-section-title">About</div>
                        <p className="agent-detail-desc">{agent.description || 'No description available.'}</p>
                    </div>

                    {/* Technical metadata */}
                    <div className="agent-detail-section">
                        <div className="agent-detail-section-title">Details</div>
                        <div className="agent-detail-meta-grid">
                            {models.length > 0 && (
                                <div className="agent-detail-meta-item">
                                    <Cpu size={14} />
                                    <div>
                                        <div className="agent-detail-meta-label">Model</div>
                                        <div className="agent-detail-meta-value">{models[0]}</div>
                                    </div>
                                </div>
                            )}
                            {toolsCount > 0 && (
                                <div className="agent-detail-meta-item">
                                    <Wrench size={14} />
                                    <div>
                                        <div className="agent-detail-meta-label">Tools</div>
                                        <div className="agent-detail-meta-value">{toolsCount}</div>
                                    </div>
                                </div>
                            )}
                            {agent.min_memory_gb != null && (
                                <div className="agent-detail-meta-item">
                                    <HardDrive size={14} />
                                    <div>
                                        <div className="agent-detail-meta-label">Min Memory</div>
                                        <div className="agent-detail-meta-value">{agent.min_memory_gb} GB</div>
                                    </div>
                                </div>
                            )}
                            {models.length > 1 && (
                                <div className="agent-detail-meta-item">
                                    <Cpu size={14} />
                                    <div>
                                        <div className="agent-detail-meta-label">Fallback</div>
                                        <div className="agent-detail-meta-value">{models[1]}</div>
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Access rights */}
                    <div className="agent-detail-section">
                        <div className="agent-detail-section-title">Access Rights</div>
                        {connections.length > 0 ? (
                            <div className="agent-detail-permissions">
                                {connections.map((c, i) => (
                                    <div key={i} className="agent-detail-permission">
                                        <Shield size={14} />
                                        <span>{c.connector_id}</span>
                                        {c.reason && <span style={{ color: 'var(--text-muted)', fontSize: 12 }}> — {c.reason}</span>}
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <p className="agent-detail-no-permissions">No special permissions required.</p>
                        )}
                    </div>

                    {/* Tags */}
                    {tags.length > 0 && (
                        <div className="agent-detail-section">
                            <div className="agent-detail-section-title">Tags</div>
                            <div className="agent-detail-tags">
                                {tags.map((tag) => (
                                    <span key={tag} className="agent-detail-tag">{tag}</span>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Eval scorecard */}
                    {agent.eval_score != null && (
                        <div className="agent-detail-section">
                            <div className="agent-detail-section-title">Eval scorecard</div>
                            <div className="agent-detail-meta-item">
                                <BarChart2 size={14} />
                                <div>
                                    <div className="agent-detail-meta-label">Eval score</div>
                                    <div className="agent-detail-meta-value">
                                        {agent.eval_score} / 100
                                        {agent.eval_scorecard_url && (
                                            <> &mdash; <a
                                                href={agent.eval_scorecard_url}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                                style={{ color: 'var(--accent)', textDecoration: 'underline', fontSize: 12 }}
                                            >View scorecard</a></>
                                        )}
                                    </div>
                                </div>
                            </div>
                        </div>
                    )}

                    {/* Conversation starters */}
                    {starters.length > 0 && (
                        <div className="agent-detail-section">
                            <div className="agent-detail-section-title">Try asking</div>
                            <div className="agent-detail-starters">
                                {starters.map((s) => (
                                    <button
                                        key={s}
                                        className="agent-detail-starter-chip"
                                        disabled={!canStart}
                                        title={!canStart ? 'Available in GAIA Desktop' : undefined}
                                        onClick={() => { onStartChat(agent.id, s); onClose(); }}
                                    >
                                        {s}
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
