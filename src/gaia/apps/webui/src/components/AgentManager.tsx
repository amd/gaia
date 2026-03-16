// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useCallback, useMemo } from 'react';
import { Bot, PlayCircle, StopCircle, RotateCcw, AlertTriangle } from 'lucide-react';
import { useAgentStore, selectSortedAgents, selectRunningCount } from '../stores/agentStore';
import { AgentCard } from './AgentCard';
import { AgentConfigDialog } from './AgentConfigDialog';
import { log } from '../utils/logger';
import './AgentManager.css';

export function AgentManager() {
  const {
    agents,
    statuses,
    installProgress,
    selectedAgentId,
    showConfigDialog,
    isLoadingManifest,
    lastError,
    fetchManifest,
    refreshStatuses,
    startAgent,
    stopAgent,
    setLastError,
    setShowConfigDialog,
  } = useAgentStore();

  const sortedAgents = useAgentStore(selectSortedAgents);
  const runningCount = useAgentStore(selectRunningCount);

  // ── Initialize on mount ───────────────────────────────────────────────
  useEffect(() => {
    log.system.info('[AgentManager] Initializing agent manager...');
    fetchManifest();
    refreshStatuses();

    // Poll statuses every 10s (skip when tab is hidden to save resources)
    const interval = setInterval(() => {
      if (document.visibilityState !== 'hidden') {
        refreshStatuses();
      }
    }, 10000);

    return () => clearInterval(interval);
  }, [fetchManifest, refreshStatuses]);

  // ── IPC listeners for real-time status changes ────────────────────────
  useEffect(() => {
    const api = window.gaiaAPI;
    if (!api) return;

    // Use getState() inside callback to avoid stale closure over statuses.
    // onCrashed returns void per GaiaElectronAPI type (no cleanup function).
    api.agent.onCrashed(({ agentId, exitCode, signal }) => {
      log.system.warn(`[AgentManager] Agent ${agentId} crashed (exit=${exitCode}, signal=${signal})`);
      const currentStatuses = useAgentStore.getState().statuses;
      useAgentStore.getState().setStatus(agentId, {
        ...currentStatuses[agentId],
        installed: currentStatuses[agentId]?.installed ?? true,
        running: false,
        healthy: false,
        error: `Crashed with exit code ${exitCode}${signal ? ` (${signal})` : ''}`,
      });
    });
  }, []); // No deps: uses getState() inside callback

  // ── Bulk actions (concurrent with Promise.allSettled) ────────────────
  const handleStartAll = useCallback(async () => {
    log.system.info('[AgentManager] Starting all installed agents...');
    setLastError(null);
    const installed = sortedAgents.filter((a) => statuses[a.id]?.installed && !statuses[a.id]?.running);
    await Promise.allSettled(installed.map((agent) => startAgent(agent.id)));
  }, [sortedAgents, statuses, startAgent, setLastError]);

  const handleStopAll = useCallback(async () => {
    log.system.info('[AgentManager] Stopping all running agents...');
    setLastError(null);
    const running = sortedAgents.filter((a) => statuses[a.id]?.running);
    await Promise.allSettled(running.map((agent) => stopAgent(agent.id)));
  }, [sortedAgents, statuses, stopAgent, setLastError]);

  const handleRefresh = useCallback(() => {
    log.system.info('[AgentManager] Refreshing agent statuses...');
    setLastError(null);
    refreshStatuses();
  }, [refreshStatuses, setLastError]);

  // ── Count helpers ─────────────────────────────────────────────────────
  const agentCount = sortedAgents.length;
  const installedCount = useMemo(
    () => Object.values(statuses).filter((s) => s.installed).length,
    [statuses],
  );

  return (
    <div className="agent-manager">
      {/* Header */}
      <div className="agent-manager-header">
        <div className="agent-manager-title">
          <h2>Agents</h2>
          {agentCount > 0 && (
            <div className="agent-manager-stats">
              <span className="stat-badge">
                {installedCount} installed
              </span>
              {runningCount > 0 && (
                <span className="stat-badge stat-badge-running">
                  <span className="running-dot" aria-hidden="true" />
                  {runningCount} running
                </span>
              )}
            </div>
          )}
        </div>
        <div className="agent-manager-actions">
          <button
            className="btn-agent-action"
            onClick={handleStartAll}
            disabled={isLoadingManifest || installedCount === 0}
            title="Start all installed agents"
            aria-label="Start all agents"
          >
            <PlayCircle size={15} />
            <span>Start All</span>
          </button>
          <button
            className="btn-agent-action"
            onClick={handleStopAll}
            disabled={isLoadingManifest || runningCount === 0}
            title="Stop all running agents"
            aria-label="Stop all agents"
          >
            <StopCircle size={15} />
            <span>Stop All</span>
          </button>
          <button
            className="btn-agent-action btn-agent-icon-only"
            onClick={handleRefresh}
            disabled={isLoadingManifest}
            title="Refresh statuses"
            aria-label="Refresh agent statuses"
          >
            <RotateCcw size={15} className={isLoadingManifest ? 'spin' : ''} />
          </button>
        </div>
      </div>

      {/* Error banner */}
      {lastError && (
        <div className="agent-error-banner" role="alert">
          <AlertTriangle size={14} />
          <span>{lastError}</span>
          <button
            className="btn-icon-sm"
            onClick={() => setLastError(null)}
            aria-label="Dismiss error"
          >
            ×
          </button>
        </div>
      )}

      {/* Loading state */}
      {isLoadingManifest && agentCount === 0 && (
        <div className="agent-loading">
          <div className="loading-spinner" />
          <span>Loading agents...</span>
        </div>
      )}

      {/* Agent list */}
      {agentCount > 0 && (
        <div className="agent-list" role="list" aria-label="Agent list">
          {sortedAgents.map((agent) => (
            <AgentCard
              key={agent.id}
              agent={agent}
              status={statuses[agent.id]}
              installProgress={installProgress[agent.id]}
            />
          ))}
        </div>
      )}

      {/* Empty state */}
      {agentCount === 0 && !isLoadingManifest && (
        <div className="agent-empty-state">
          <Bot size={48} strokeWidth={1} />
          <h3>No agents installed yet</h3>
          <p>
            GAIA agents extend your AI assistant with specialized capabilities
            like code generation, 3D modeling, Jira integration, and more.
          </p>
          <button className="btn-primary agent-browse-btn" onClick={handleRefresh}>
            Browse Available Agents
          </button>
        </div>
      )}

      {/* Config dialog */}
      {showConfigDialog && selectedAgentId && (
        <AgentConfigDialog
          agentId={selectedAgentId}
          onClose={() => setShowConfigDialog(false)}
        />
      )}
    </div>
  );
}
