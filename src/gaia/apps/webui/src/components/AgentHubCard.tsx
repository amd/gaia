// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Cpu, Wrench, Shield, CheckCircle2, AlertTriangle, Download, Trash2, ArrowUpCircle, X, RefreshCw, FlaskConical } from 'lucide-react';
import { getAgentIcon } from './agentIcons';
import type { AgentInfo, InstallStatus } from '../types';
import { useChatStore } from '../stores/chatStore';
import {
    compatLevel,
    compatLabel,
    formatBytes,
    isInstalling,
} from '../utils/agentHub';

function sourceBadge(source: string) {
    if (source === 'builtin') return <span className="agent-badge agent-badge-builtin">Built-in</span>;
    if (source === 'native') return <span className="agent-badge agent-badge-native">Native</span>;
    if (source === 'installed') return <span className="agent-badge agent-badge-installed">Installed</span>;
    return <span className="agent-badge agent-badge-custom">Custom</span>;
}

const TIER_META: Record<string, { Icon: typeof Shield; label: string }> = {
    verified: { Icon: CheckCircle2, label: 'Verified' },
    community: { Icon: Shield, label: 'Community' },
    experimental: { Icon: FlaskConical, label: 'Experimental' },
};

/** Security-tier badge: checkmark (verified), shield (community), flask (experimental). */
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

const DEVICE_LABELS: Record<string, string> = { cpu: 'CPU', gpu: 'GPU', npu: 'NPU' };

const INSTALL_STAGE_LABEL: Record<string, string> = {
    downloading: 'Downloading…',
    verifying: 'Verifying…',
    installing: 'Installing…',
    installed: 'Installed',
    failed: 'Failed',
};

interface AgentHubCardProps {
    agent: AgentInfo;
    /** Which tab the card lives on — drives the action set. */
    variant: 'installed' | 'available';
    isActive: boolean;
    isElectron: boolean;
    onSelect: (id: string) => void;
    onStartChat: (id: string) => void;
    onViewDetails: (agent: AgentInfo) => void;
    /** Live install/update progress for this agent. */
    installStatus?: InstallStatus | null;
    /** Start an install / update. */
    onInstall?: (id: string) => void;
    /** Abort an in-flight install. */
    onCancelInstall?: (id: string) => void;
    /** Uninstall an installed agent. */
    onUninstall?: (id: string) => void;
}

export function AgentHubCard({
    agent,
    variant,
    isActive,
    isElectron,
    onSelect,
    onStartChat,
    onViewDetails,
    installStatus,
    onInstall,
    onCancelInstall,
    onUninstall,
}: AgentHubCardProps) {
    const isAvailable = variant === 'available';
    const isNative = agent.source === 'native';
    const canStart = !isNative || isElectron;
    const model = agent.models?.[0];
    const toolsCount = agent.tools_count ?? 0;
    const starter = agent.conversation_starters?.[0];
    const connections = agent.required_connections ?? [];
    const Icon = getAgentIcon(agent.icon);

    const installing = isInstalling(installStatus);
    const installFailed = installStatus?.state === 'failed';
    const hasUpdate = agent.status === 'update_available';
    const level = compatLevel(agent);
    const incompatible = level === 'incompatible';

    // Device selection (installed agents only)
    const activeDevice = useChatStore((s) => s.activeDevice);
    const setActiveDevice = useChatStore((s) => s.setActiveDevice);
    const detectedDevices = useChatStore((s) => s.detectedDevices);
    const deviceConfigs = agent.device_configs ?? [];
    const availableConfigs = deviceConfigs.filter((c) => detectedDevices.includes(c.device));
    const selectedConfig = availableConfigs.find((c) => c.device === activeDevice);
    const showDeviceSelector = availableConfigs.length > 1;

    // Model-size tier selection (#1162) — replaces duplicate "… Lite" cards.
    const activeModelTier = useChatStore((s) => s.activeModelTier);
    const setActiveModelTier = useChatStore((s) => s.setActiveModelTier);
    const modelTiers = agent.model_tiers ?? [];
    const showTierSelector = !isAvailable && modelTiers.length > 1;

    // Installed-tab cards select-to-chat; available-tab cards do not.
    const clickable = !isAvailable && canStart;

    const cardClass = [
        'agent-hub-card',
        isActive && !isAvailable && 'active',
        !canStart && !isAvailable && 'disabled',
        isAvailable && 'available-card',
    ].filter(Boolean).join(' ');

    // Primary install/update button (used on both tabs while updating/installing).
    const installButton = installing ? (
        <button
            className="btn-install-cancel"
            onClick={(e) => { e.stopPropagation(); onCancelInstall?.(agent.id); }}
        >
            <X size={14} /> Cancel
        </button>
    ) : (
        <button
            className="btn-install"
            disabled={incompatible}
            title={incompatible
                ? [compatLabel(level), ...(agent.compatibility?.reasons ?? [])].join(' ')
                : `${hasUpdate ? 'Update' : 'Install'} ${agent.name}`}
            onClick={(e) => { e.stopPropagation(); onInstall?.(agent.id); }}
        >
            {installFailed
                ? <><RefreshCw size={14} /> Retry</>
                : hasUpdate
                    ? <><ArrowUpCircle size={14} /> Update</>
                    : <><Download size={14} /> Install</>}
        </button>
    );

    return (
        <div
            className={cardClass}
            role={clickable ? 'button' : undefined}
            tabIndex={clickable ? 0 : -1}
            onClick={() => clickable && onSelect(agent.id)}
            onKeyDown={(e) => { if (clickable && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onSelect(agent.id); } }}
        >
            {/* Header */}
            <div className="agent-hub-card-header">
                <div className="agent-hub-card-icon">
                    <Icon size={18} />
                </div>
                <div className="agent-hub-card-info">
                    <h3 className="agent-hub-card-name">{agent.name}</h3>
                    <div className="agent-hub-card-badges">
                        {!isAvailable && sourceBadge(agent.source)}
                        {hasUpdate && (
                            <span className="agent-badge agent-badge-update">
                                <ArrowUpCircle size={10} /> Update
                            </span>
                        )}
                        {agent.version && !isAvailable && (
                            <span className="agent-badge agent-badge-version">v{agent.version}</span>
                        )}
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
                {/* Compatibility indicator (catalog cards) */}
                {agent.compatibility && (
                    <span
                        className={`agent-compat-dot agent-compat-${level}`}
                        title={[compatLabel(level), ...(agent.compatibility.reasons ?? [])].join('\n')}
                        aria-label={compatLabel(level)}
                    />
                )}
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
                {isAvailable && agent.download_size_bytes != null ? (
                    <span className="agent-hub-card-meta-item">
                        <Download size={12} />
                        {formatBytes(agent.download_size_bytes)}
                    </span>
                ) : connections.length > 0 ? (
                    <span className="agent-hub-card-meta-item">
                        <Shield size={12} />
                        {connections.map((c) => c.connector_id).join(', ')}
                    </span>
                ) : (
                    <span className="agent-hub-card-meta-item">
                        <Shield size={12} />
                        No special permissions
                    </span>
                )}
            </div>

            {/* Device selector (installed agents only) */}
            {!isAvailable && availableConfigs.length > 0 && (
                <div className="agent-hub-card-device">
                    {showDeviceSelector ? (
                        <select
                            className="agent-hub-device-select"
                            aria-label={`Device for ${agent.name}`}
                            value={activeDevice}
                            onChange={(e) => { e.stopPropagation(); setActiveDevice(e.target.value); }}
                            onClick={(e) => e.stopPropagation()}
                        >
                            {availableConfigs.map((c) => (
                                <option key={c.device} value={c.device}>
                                    {DEVICE_LABELS[c.device] ?? c.device.toUpperCase()}{c.verified ? '' : ' ⚠'}
                                </option>
                            ))}
                        </select>
                    ) : (
                        <span className="agent-hub-device-label">
                            {DEVICE_LABELS[availableConfigs[0].device] ?? availableConfigs[0].device.toUpperCase()}
                        </span>
                    )}
                    {selectedConfig && (
                        <span className={`agent-hub-device-verified ${selectedConfig.verified ? 'verified' : 'unverified'}`}>
                            {selectedConfig.verified
                                ? <><CheckCircle2 size={11} /> Verified</>
                                : <><AlertTriangle size={11} /> Unverified</>
                            }
                        </span>
                    )}
                </div>
            )}

            {/* Model-size selector (installed agents with multiple tiers, #1162) */}
            {showTierSelector && (
                <div className="agent-hub-card-tier">
                    <Cpu size={12} />
                    <select
                        className="agent-hub-tier-select"
                        aria-label={`Model size for ${agent.name}`}
                        value={activeModelTier}
                        onChange={(e) => { e.stopPropagation(); setActiveModelTier(e.target.value); }}
                        onClick={(e) => e.stopPropagation()}
                    >
                        {modelTiers.map((t) => (
                            <option key={t.name} value={t.name}>{t.label}</option>
                        ))}
                    </select>
                </div>
            )}

            {/* Starter preview (installed only) */}
            {!isAvailable && starter && <div className="agent-hub-card-starter">{starter}</div>}

            {/* Install / update progress */}
            {installing && (
                <div className="agent-install-progress">
                    <div className="agent-install-progress-head">
                        <span>{INSTALL_STAGE_LABEL[installStatus!.state] ?? 'Installing…'}</span>
                        <span>{Math.round(installStatus!.progress)}%</span>
                    </div>
                    <div className="agent-install-bar" role="progressbar"
                        aria-valuenow={Math.round(installStatus!.progress)} aria-valuemin={0} aria-valuemax={100}>
                        <div className="agent-install-bar-fill" style={{ width: `${Math.max(2, installStatus!.progress)}%` }} />
                    </div>
                </div>
            )}

            {/* Install failure */}
            {installFailed && (
                <div className="agent-install-error" role="alert">
                    <AlertTriangle size={12} />
                    <span>{installStatus?.error || 'Install failed.'}</span>
                </div>
            )}

            {/* Actions */}
            <div className="agent-hub-card-actions">
                {isAvailable ? (
                    installButton
                ) : (
                    <>
                        <button
                            className="btn-start-chat"
                            disabled={!canStart}
                            title={!canStart ? 'Available in GAIA Desktop' : `Start chat with ${agent.name}`}
                            onClick={(e) => { e.stopPropagation(); onStartChat(agent.id); }}
                        >
                            Start Chat
                        </button>
                        {/* In-place update for installed agents with a pending update. */}
                        {(hasUpdate || installing || installFailed) && (onInstall || onCancelInstall) && installButton}
                    </>
                )}

                <button
                    className="btn-details"
                    onClick={(e) => { e.stopPropagation(); onViewDetails(agent); }}
                >
                    Details
                </button>

                {/* Uninstall — only for installed agents that can be removed */}
                {!isAvailable && onUninstall && agent.source === 'installed' && (
                    <button
                        className="btn-uninstall"
                        title={`Uninstall ${agent.name}`}
                        aria-label={`Uninstall ${agent.name}`}
                        onClick={(e) => { e.stopPropagation(); onUninstall(agent.id); }}
                    >
                        <Trash2 size={14} />
                    </button>
                )}
            </div>
        </div>
    );
}
