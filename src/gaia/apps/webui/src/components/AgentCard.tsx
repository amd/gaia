// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, memo } from 'react';
import {
  Play,
  Square,
  RotateCcw,
  Terminal,
  MessageSquare,
  Settings,
  Download,
  Clock,
  Wrench,
} from 'lucide-react';
import { useAgentStore } from '../stores/agentStore';
import type { AgentInfo, AgentStatus, AgentInstallProgress } from '../types/agent';
import { formatDuration, formatSize } from '../utils/format';
import { log } from '../utils/logger';

interface AgentCardProps {
  agent: AgentInfo;
  status: AgentStatus | undefined;
  installProgress?: AgentInstallProgress;
}

export const AgentCard = memo(function AgentCard({ agent, status, installProgress }: AgentCardProps) {
  const {
    selectedAgentId,
    setSelectedAgent,
    setShowConfigDialog,
    startAgent,
    stopAgent,
    restartAgent,
    installAgent,
  } = useAgentStore();

  const isRunning = status?.running ?? false;
  const isInstalled = status?.installed ?? false;
  const hasError = !!status?.error;
  const isSelected = selectedAgentId === agent.id;
  const isInstalling = installProgress &&
    (installProgress.state === 'downloading' || installProgress.state === 'verifying' || installProgress.state === 'installing');

  // Determine status class for dot (trivially cheap — no useMemo needed)
  const dotClass = hasError ? 'dot-error'
    : isRunning ? 'dot-running'
    : isInstalled ? 'dot-stopped'
    : 'dot-not-installed';

  // Status text (trivially cheap — no useMemo needed)
  const statusText = hasError ? (status?.error || 'Error')
    : isRunning ? ['Running', status?.pid && `PID ${status.pid}`, status?.memoryMB && `${status.memoryMB} MB`].filter(Boolean).join(' \u00b7 ')
    : isInstalled ? 'Stopped'
    : agent.sizeBytes ? `Not installed \u00b7 ${formatSize(agent.sizeBytes)}`
    : 'Not installed';

  // Card CSS classes
  const cardClasses = [
    'agent-card',
    isRunning && 'agent-card-running',
    hasError && 'agent-card-error',
    isSelected && 'agent-card-selected',
  ].filter(Boolean).join(' ');

  // ── Action handlers ───────────────────────────────────────────────────

  const handleStart = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    log.system.info(`[AgentCard] Starting agent: ${agent.name}`);
    startAgent(agent.id);
  }, [agent, startAgent]);

  const handleStop = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    log.system.info(`[AgentCard] Stopping agent: ${agent.name}`);
    stopAgent(agent.id);
  }, [agent, stopAgent]);

  const handleRestart = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    log.system.info(`[AgentCard] Restarting agent: ${agent.name}`);
    restartAgent(agent.id);
  }, [agent, restartAgent]);

  const handleInstall = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    log.system.info(`[AgentCard] Installing agent: ${agent.name}`);
    installAgent(agent.id);
  }, [agent, installAgent]);

  const handleOpenTerminal = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    log.system.info(`[AgentCard] Opening terminal for: ${agent.name}`);
    setSelectedAgent(agent.id);
    // TODO: Switch to terminal view when implemented
  }, [agent, setSelectedAgent]);

  const handleOpenChat = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    log.system.info(`[AgentCard] Opening chat for: ${agent.name}`);
    setSelectedAgent(agent.id);
    // TODO: Switch to chat view when implemented
  }, [agent, setSelectedAgent]);

  const handleOpenConfig = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    log.system.info(`[AgentCard] Opening config for: ${agent.name}`);
    setSelectedAgent(agent.id);
    setShowConfigDialog(true);
  }, [agent, setSelectedAgent, setShowConfigDialog]);

  const handleCardClick = useCallback(() => {
    setSelectedAgent(isSelected ? null : agent.id);
  }, [agent.id, isSelected, setSelectedAgent]);

  const handleCardKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      handleCardClick();
    }
  }, [handleCardClick]);

  return (
    <div
      className={cardClasses}
      role="listitem"
      aria-label={`${agent.name}, ${statusText}`}
      tabIndex={0}
      onClick={handleCardClick}
      onKeyDown={handleCardKeyDown}
    >
      {/* Status dot */}
      <div className={`agent-status-dot ${dotClass}`} aria-hidden="true" />

      {/* Content */}
      <div className="agent-card-content">
        <div className="agent-card-header">
          <span className="agent-card-name">{agent.name}</span>
          <span className="agent-card-version">v{agent.version}</span>
        </div>

        <div className="agent-card-description">{agent.description}</div>

        <div className="agent-card-meta">
          <span>{statusText}</span>
          {isRunning && agent.toolsCount > 0 && (
            <>
              <span className="meta-sep">·</span>
              <span title="Available tools">
                <Wrench size={10} className="meta-icon" />
                {agent.toolsCount} tools
              </span>
            </>
          )}
          {isRunning && status?.uptime != null && (
            <>
              <span className="meta-sep">·</span>
              <span title="Uptime">
                <Clock size={10} className="meta-icon" />
                {formatDuration(status.uptime)}
              </span>
            </>
          )}
          {agent.categories.length > 0 && (
            <>
              <span className="meta-sep">·</span>
              <span>{agent.categories[0]}</span>
            </>
          )}
        </div>

        {/* Install progress bar */}
        {isInstalling && installProgress && (
          <div className="agent-install-progress">
            <div className="install-progress-bar">
              <div
                className="install-progress-fill"
                style={{ width: `${installProgress.progress}%` }}
              />
            </div>
            <div className="install-progress-label">
              <div className="install-spinner" />
              <span>
                {installProgress.state === 'downloading' && `Downloading... ${installProgress.progress}%`}
                {installProgress.state === 'verifying' && 'Verifying...'}
                {installProgress.state === 'installing' && 'Installing...'}
              </span>
            </div>
          </div>
        )}

        {/* Install failed */}
        {installProgress?.state === 'failed' && (
          <div className="install-progress-error">
            Install failed: {installProgress.error || 'Unknown error'}
          </div>
        )}
      </div>

      {/* Action buttons */}
      <div className="agent-card-actions">
        {/* Running state: Stop, Restart, Terminal, Chat, Config */}
        {isRunning && (
          <>
            <button
              className="btn-card-action btn-card-stop"
              onClick={handleStop}
              title="Stop agent"
              aria-label={`Stop ${agent.name}`}
            >
              <Square size={14} />
            </button>
            <button
              className="btn-card-action"
              onClick={handleRestart}
              title="Restart agent"
              aria-label={`Restart ${agent.name}`}
            >
              <RotateCcw size={14} />
            </button>
            <button
              className="btn-card-action"
              onClick={handleOpenTerminal}
              title="Open terminal"
              aria-label={`Open terminal for ${agent.name}`}
            >
              <Terminal size={14} />
            </button>
            {agent.capabilities.interactiveChat && (
              <button
                className="btn-card-action"
                onClick={handleOpenChat}
                title="Open chat"
                aria-label={`Chat with ${agent.name}`}
              >
                <MessageSquare size={14} />
              </button>
            )}
            <button
              className="btn-card-action"
              onClick={handleOpenConfig}
              title="Settings"
              aria-label={`Configure ${agent.name}`}
            >
              <Settings size={14} />
            </button>
          </>
        )}

        {/* Stopped + Installed: Start, Terminal, Config */}
        {!isRunning && isInstalled && (
          <>
            <button
              className="btn-card-action btn-card-start"
              onClick={handleStart}
              title="Start agent"
              aria-label={`Start ${agent.name}`}
            >
              <Play size={14} />
            </button>
            <button
              className="btn-card-action"
              onClick={handleOpenTerminal}
              title="View logs"
              aria-label={`View logs for ${agent.name}`}
            >
              <Terminal size={14} />
            </button>
            <button
              className="btn-card-action"
              onClick={handleOpenConfig}
              title="Settings"
              aria-label={`Configure ${agent.name}`}
            >
              <Settings size={14} />
            </button>
          </>
        )}

        {/* Not installed: Install button */}
        {!isInstalled && !isInstalling && (
          <button
            className="btn-card-action btn-card-install"
            onClick={handleInstall}
            title={`Install ${agent.name}`}
            aria-label={`Install ${agent.name}`}
          >
            <Download size={14} />
            <span>Install</span>
          </button>
        )}
      </div>
    </div>
  );
});
