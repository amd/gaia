// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Cpu, Wrench, Shield } from 'lucide-react';
import { getAgentIcon } from './agentIcons';
import type { AgentInfo } from '../types';

function sourceBadge(source: string) {
    if (source === 'builtin') return <span className="agent-badge agent-badge-builtin">Built-in</span>;
    if (source === 'native') return <span className="agent-badge agent-badge-native">Native</span>;
    return <span className="agent-badge agent-badge-custom">Custom</span>;
}

interface AgentHubCardProps {
    agent: AgentInfo;
    isActive: boolean;
    isElectron: boolean;
    onSelect: (id: string) => void;
    onStartChat: (id: string) => void;
    onViewDetails: (agent: AgentInfo) => void;
}

export function AgentHubCard({ agent, isActive, isElectron, onSelect, onStartChat, onViewDetails }: AgentHubCardProps) {
    const isNative = agent.source === 'native';
    const canStart = !isNative || isElectron;
    const model = agent.models?.[0];
    const toolsCount = agent.tools_count ?? 0;
    const starter = agent.conversation_starters?.[0];
    const connections = agent.required_connections ?? [];
    const Icon = getAgentIcon(agent.icon);

    const cardClass = [
        'agent-hub-card',
        isActive && 'active',
        !canStart && 'disabled',
    ].filter(Boolean).join(' ');

    return (
        <div
            className={cardClass}
            role="button"
            tabIndex={canStart ? 0 : -1}
            onClick={() => canStart && onSelect(agent.id)}
            onKeyDown={(e) => { if (canStart && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onSelect(agent.id); } }}
        >
            {/* Header */}
            <div className="agent-hub-card-header">
                <div className="agent-hub-card-icon">
                    <Icon size={18} />
                </div>
                <div className="agent-hub-card-info">
                    <h3 className="agent-hub-card-name">{agent.name}</h3>
                    <div className="agent-hub-card-badges">
                        {sourceBadge(agent.source)}
                        {agent.language && agent.language !== 'python' && (
                            <span className="agent-badge agent-badge-native">{agent.language.toUpperCase()}</span>
                        )}
                        {agent.category && agent.category !== 'general' && (
                            <span className="agent-badge agent-badge-category">{agent.category}</span>
                        )}
                    </div>
                </div>
            </div>

            {/* Description */}
            <p className="agent-hub-card-desc">{agent.description || 'Custom agent'}</p>

            {/* Metadata */}
            <div className="agent-hub-card-meta">
                {model && (
                    <span className="agent-hub-card-meta-item">
                        <Cpu size={12} />
                        {model}
                    </span>
                )}
                {toolsCount > 0 && (
                    <span className="agent-hub-card-meta-item">
                        <Wrench size={12} />
                        {toolsCount} tools
                    </span>
                )}
                {connections.length > 0 ? (
                    <span className="agent-hub-card-meta-item">
                        <Shield size={12} />
                        {connections.map((c: any) => c.connector_id).join(', ')}
                    </span>
                ) : (
                    <span className="agent-hub-card-meta-item">
                        <Shield size={12} />
                        No special permissions
                    </span>
                )}
            </div>

            {/* Starter preview */}
            {starter && <div className="agent-hub-card-starter">{starter}</div>}

            {/* Actions */}
            <div className="agent-hub-card-actions">
                <button
                    className="btn-start-chat"
                    disabled={!canStart}
                    title={!canStart ? 'Available in GAIA Desktop' : `Start chat with ${agent.name}`}
                    onClick={(e) => { e.stopPropagation(); onStartChat(agent.id); }}
                >
                    Start Chat
                </button>
                <button
                    className="btn-details"
                    onClick={(e) => { e.stopPropagation(); onViewDetails(agent); }}
                >
                    Details
                </button>
            </div>
        </div>
    );
}
