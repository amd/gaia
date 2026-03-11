// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback } from 'react';
import {
  X,
  Download,
  ShieldCheck,
  Package,
  Check,
  AlertTriangle,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import { useAgentStore } from '../stores/agentStore';
import type { AgentInstallState } from '../types/agent';
import './AgentInstallDialog.css';

// ── Props ────────────────────────────────────────────────────────────────

interface AgentInstallDialogProps {
  agentId: string;
  onClose: () => void;
}

// ── Helpers ──────────────────────────────────────────────────────────────

function formatSize(bytes?: number): string {
  if (!bytes || bytes === 0) return 'Unknown';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

interface StepInfo {
  label: string;
  icon: React.ReactNode;
  description: string;
}

function getStepInfo(state: AgentInstallState): StepInfo {
  switch (state) {
    case 'downloading':
      return {
        label: 'Downloading...',
        icon: <Download size={16} className="install-step-icon downloading" />,
        description: 'Fetching agent binary from repository',
      };
    case 'verifying':
      return {
        label: 'Verifying SHA-256...',
        icon: <ShieldCheck size={16} className="install-step-icon verifying" />,
        description: 'Validating integrity checksum',
      };
    case 'installing':
      return {
        label: 'Installing...',
        icon: <Package size={16} className="install-step-icon installing" />,
        description: 'Configuring agent and registering tools',
      };
    case 'installed':
      return {
        label: 'Installed',
        icon: <Check size={16} className="install-step-icon installed" />,
        description: 'Agent is ready to use',
      };
    case 'failed':
      return {
        label: 'Failed',
        icon: <AlertTriangle size={16} className="install-step-icon failed" />,
        description: 'Installation encountered an error',
      };
    default:
      return {
        label: 'Preparing...',
        icon: <Loader2 size={16} className="install-step-icon preparing" />,
        description: 'Initializing installation',
      };
  }
}

/** Installation step order for the progress stepper. */
const INSTALL_STEPS: AgentInstallState[] = ['downloading', 'verifying', 'installing', 'installed'];

function getStepIndex(state: AgentInstallState): number {
  const idx = INSTALL_STEPS.indexOf(state);
  return idx >= 0 ? idx : 0;
}

// ── Component ────────────────────────────────────────────────────────────

export function AgentInstallDialog({ agentId, onClose }: AgentInstallDialogProps) {
  const agent = useAgentStore((s) => s.agents[agentId]);
  const progress = useAgentStore((s) => s.installProgress[agentId]);
  const installAgent = useAgentStore((s) => s.installAgent);
  const clearInstallProgress = useAgentStore((s) => s.clearInstallProgress);

  const state = progress?.state ?? 'not_installed';
  const percent = progress?.progress ?? 0;
  const error = progress?.error;
  const stepInfo = getStepInfo(state);
  const currentStepIdx = getStepIndex(state);
  const isInstalling = state === 'downloading' || state === 'verifying' || state === 'installing';

  const handleRetry = useCallback(() => {
    clearInstallProgress(agentId);
    installAgent(agentId);
  }, [agentId, clearInstallProgress, installAgent]);

  const handleClose = useCallback(() => {
    if (state === 'installed' || state === 'failed' || state === 'not_installed') {
      clearInstallProgress(agentId);
    }
    onClose();
  }, [agentId, state, clearInstallProgress, onClose]);

  if (!agent) {
    return (
      <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label="Install agent">
        <div className="modal-panel install-dialog" onClick={(e) => e.stopPropagation()}>
          <div className="modal-header">
            <h3>Agent Not Found</h3>
            <button className="btn-icon" onClick={onClose} aria-label="Close">
              <X size={18} />
            </button>
          </div>
          <div className="modal-body">
            <p className="install-error-text">Unknown agent: {agentId}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="modal-overlay" onClick={handleClose} role="dialog" aria-modal="true" aria-label={`Install ${agent.name}`}>
      <div className="modal-panel install-dialog" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="modal-header">
          <h3>
            {state === 'installed' ? 'Installation Complete' : `Installing ${agent.name}`}
          </h3>
          <button className="btn-icon" onClick={handleClose} aria-label="Close" disabled={isInstalling}>
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="modal-body install-body">
          {/* Progress stepper */}
          <div className="install-stepper">
            {INSTALL_STEPS.map((step, idx) => {
              const isCurrent = state === step;
              const isComplete = state !== 'failed' && currentStepIdx > idx;
              const isFailed = state === 'failed' && currentStepIdx === idx;

              return (
                <div
                  key={step}
                  className={`install-step ${isCurrent ? 'current' : ''} ${isComplete ? 'complete' : ''} ${isFailed ? 'failed' : ''}`}
                >
                  <div className="install-step-dot">
                    {isComplete ? (
                      <Check size={10} />
                    ) : isFailed ? (
                      <X size={10} />
                    ) : isCurrent ? (
                      <Loader2 size={10} className="install-step-spinner" />
                    ) : (
                      <span className="install-step-num">{idx + 1}</span>
                    )}
                  </div>
                  {idx < INSTALL_STEPS.length - 1 && (
                    <div className={`install-step-line ${isComplete ? 'complete' : ''}`} />
                  )}
                </div>
              );
            })}
          </div>

          {/* Progress bar */}
          <div className="install-progress">
            <div
              className="install-progress-bar"
              role="progressbar"
              aria-valuenow={percent}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`Installation progress: ${percent}%`}
            >
              <div
                className={`install-progress-fill ${state === 'failed' ? 'failed' : ''} ${state === 'installed' ? 'success' : ''}`}
                style={{ width: `${percent}%` }}
              />
            </div>
            <div className="install-progress-info">
              <span className="install-progress-label">
                {stepInfo.icon}
                {stepInfo.label}
              </span>
              <span className="install-progress-percent">{percent}%</span>
            </div>
            <p className="install-progress-desc">{stepInfo.description}</p>
          </div>

          {/* Agent details */}
          <div className="install-details">
            <div className="install-detail-row">
              <span className="install-detail-key">Agent</span>
              <span className="install-detail-value">{agent.name}</span>
            </div>
            <div className="install-detail-row">
              <span className="install-detail-key">Version</span>
              <span className="install-detail-value">{agent.version}</span>
            </div>
            <div className="install-detail-row">
              <span className="install-detail-key">Size</span>
              <span className="install-detail-value">{formatSize(agent.sizeBytes)}</span>
            </div>
            <div className="install-detail-row">
              <span className="install-detail-key">Tools</span>
              <span className="install-detail-value">{agent.toolsCount} registered</span>
            </div>
            {agent.categories.length > 0 && (
              <div className="install-detail-row">
                <span className="install-detail-key">Categories</span>
                <span className="install-detail-value">
                  {agent.categories.map((cat) => (
                    <span key={cat} className="install-category-badge">{cat}</span>
                  ))}
                </span>
              </div>
            )}
          </div>

          {/* Error display */}
          {error && (
            <div className="install-error">
              <AlertTriangle size={14} />
              <div className="install-error-content">
                <span className="install-error-title">Installation Failed</span>
                <span className="install-error-text">{error}</span>
              </div>
            </div>
          )}

          {/* Success message */}
          {state === 'installed' && (
            <div className="install-success">
              <Check size={14} />
              <span>{agent.name} has been installed successfully and is ready to use.</span>
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="install-actions">
          {state === 'installed' ? (
            <button className="btn-primary" onClick={handleClose}>
              <Check size={14} />
              Done
            </button>
          ) : state === 'failed' ? (
            <>
              <button className="btn-secondary" onClick={handleClose}>
                Cancel
              </button>
              <button className="btn-primary" onClick={handleRetry}>
                <RefreshCw size={14} />
                Retry
              </button>
            </>
          ) : (
            <button className="btn-secondary" onClick={handleClose} disabled={isInstalling}>
              {isInstalling ? 'Installing...' : 'Cancel'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
