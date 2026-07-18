// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ArrowLeft, AlertTriangle, RotateCcw } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import { HubPage } from './HubPage';
import './SettingsPage.css';
import './AgentHub.css';

interface AgentHubViewProps {
    /** Launch a new task with the chosen agent (optionally pre-filled prompt). */
    onStartChat: (agentId: string, prompt?: string) => void;
    onCreateAgent?: () => void;
    /** Re-fetch the agent list after a loud discovery failure. */
    onRetryAgents: () => void;
}

/**
 * Full-screen Agent Hub — the reachable home for discovery, install, and
 * launching an agent (#2118). Unlike the WelcomeScreen grid, it renders even
 * when no agents are registered (a consumer install with no wheels): the
 * Available tab still lets the user install one, and a discovery failure shows
 * a loud, actionable error instead of an empty screen.
 */
export function AgentHubView({ onStartChat, onCreateAgent, onRetryAgents }: AgentHubViewProps) {
    const { agents, activeAgentId, setActiveAgentId, setShowHub, agentsError } = useChatStore();

    return (
        <div className="settings-page">
            <div className="settings-page-header">
                <button
                    className="btn-icon settings-back-btn"
                    onClick={() => setShowHub(false)}
                    aria-label="Back"
                >
                    <ArrowLeft size={18} />
                </button>
                <h3>Agent Hub</h3>
            </div>

            <div className="settings-page-body">
                {agentsError && (
                    <div className="agent-hub-banner agent-hub-banner-error" role="alert">
                        <AlertTriangle size={14} />
                        <span>Couldn’t load installed agents: {agentsError}</span>
                        <button className="agent-hub-retry" onClick={onRetryAgents}>
                            <RotateCcw size={13} /> Retry
                        </button>
                    </div>
                )}
                <HubPage
                    agents={agents}
                    activeAgentId={activeAgentId}
                    onSelect={setActiveAgentId}
                    onStartChat={onStartChat}
                    onCreateAgent={onCreateAgent}
                />
            </div>
        </div>
    );
}
