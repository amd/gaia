// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, useCallback, useEffect } from 'react';
import { X, Cpu } from 'lucide-react';
import { useAgentStore, DEFAULT_AGENT_CONFIG } from '../stores/agentStore';
import type { AgentConfig } from '../types/agent';
import { log } from '../utils/logger';

interface AgentConfigDialogProps {
  agentId: string;
  onClose: () => void;
}

export function AgentConfigDialog({ agentId, onClose }: AgentConfigDialogProps) {
  const { agents, configs, statuses, setConfig } = useAgentStore();
  const agent = agents[agentId];
  const status = statuses[agentId];

  // Local config state for form editing
  const existingConfig = configs[agentId];
  const [localConfig, setLocalConfig] = useState<AgentConfig>(
    existingConfig ?? DEFAULT_AGENT_CONFIG,
  );
  const [isDirty, setIsDirty] = useState(false);

  // Sync if external config changes
  useEffect(() => {
    if (existingConfig) {
      setLocalConfig(existingConfig);
      setIsDirty(false);
    }
  }, [existingConfig]);

  const updateField = useCallback(<K extends keyof AgentConfig>(key: K, value: AgentConfig[K]) => {
    setLocalConfig((prev) => ({ ...prev, [key]: value }));
    setIsDirty(true);
  }, []);

  const handleSave = useCallback(() => {
    log.system.info(`[AgentConfigDialog] Saving config for ${agentId}:`, localConfig);
    setConfig(agentId, localConfig);
    setIsDirty(false);

    // Persist via Electron IPC if available
    const api = window.gaiaAPI;
    if (api) {
      api.tray.getConfig().then((trayConfig) => {
        const updated = {
          ...trayConfig,
          agents: { ...trayConfig.agents, [agentId]: localConfig },
        };
        api.tray.setConfig(updated).catch((err: unknown) => {
          log.system.error(`[AgentConfigDialog] Failed to persist config for ${agentId}:`, err);
        });
      }).catch((err: unknown) => {
        log.system.error(`[AgentConfigDialog] Failed to get tray config:`, err);
      });
    }

    onClose();
  }, [agentId, localConfig, setConfig, onClose]);

  const handleCancel = useCallback(() => {
    if (isDirty) {
      // Reset to stored config
      setLocalConfig(existingConfig ?? DEFAULT_AGENT_CONFIG);
    }
    onClose();
  }, [isDirty, existingConfig, onClose]);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleCancel();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleCancel]);

  if (!agent) return null;

  return (
    <div
      className="modal-overlay"
      onClick={handleCancel}
      role="dialog"
      aria-modal="true"
      aria-label={`${agent.name} configuration`}
    >
      <div className="modal-panel config-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>
            <Cpu size={16} className="config-header-icon" />
            {agent.name} Configuration
          </h3>
          <button className="btn-icon" onClick={handleCancel} aria-label="Close configuration">
            <X size={18} />
          </button>
        </div>

        <div className="modal-body">
          {/* Agent info */}
          <section className="config-section">
            <h4>Agent Info</h4>
            <div className="config-row">
              <div className="config-label">
                <span className="config-label-text">Version</span>
              </div>
              <span className="config-value">v{agent.version}</span>
            </div>
            <div className="config-row">
              <div className="config-label">
                <span className="config-label-text">Status</span>
              </div>
              <span className={`config-value-status ${status?.running ? 'status-running' : ''}`}>
                {status?.running ? 'Running' : status?.installed ? 'Stopped' : 'Not Installed'}
              </span>
            </div>
            {agent.toolsCount > 0 && (
              <div className="config-row">
                <div className="config-label">
                  <span className="config-label-text">Tools</span>
                </div>
                <span className="config-value">{agent.toolsCount}</span>
              </div>
            )}
            {agent.categories.length > 0 && (
              <div className="config-row">
                <div className="config-label">
                  <span className="config-label-text">Categories</span>
                </div>
                <span className="config-value">
                  {agent.categories.join(', ')}
                </span>
              </div>
            )}
          </section>

          {/* Lifecycle settings */}
          <section className="config-section">
            <h4>Lifecycle</h4>
            <div className="config-row">
              <div className="config-label">
                <span className="config-label-text">Auto-start on launch</span>
                <span className="config-label-hint">Start this agent when GAIA launches</span>
              </div>
              <button
                className={`config-toggle ${localConfig.autoStart ? 'active' : ''}`}
                onClick={() => updateField('autoStart', !localConfig.autoStart)}
                role="switch"
                aria-checked={localConfig.autoStart}
                aria-label="Toggle auto-start"
              />
            </div>
            <div className="config-row">
              <div className="config-label">
                <span className="config-label-text">Restart on crash</span>
                <span className="config-label-hint">Automatically restart if the agent crashes</span>
              </div>
              <button
                className={`config-toggle ${localConfig.restartOnCrash ? 'active' : ''}`}
                onClick={() => updateField('restartOnCrash', !localConfig.restartOnCrash)}
                role="switch"
                aria-checked={localConfig.restartOnCrash}
                aria-label="Toggle restart on crash"
              />
            </div>
          </section>

          {/* Logging settings */}
          <section className="config-section">
            <h4>Logging</h4>
            <div className="config-row">
              <div className="config-label">
                <span className="config-label-text">Log level</span>
                <span className="config-label-hint">Controls verbosity of agent output</span>
              </div>
              <select
                className="config-select"
                value={localConfig.logLevel}
                onChange={(e) => updateField('logLevel', e.target.value as AgentConfig['logLevel'])}
                aria-label="Log level"
              >
                <option value="debug">Debug</option>
                <option value="info">Info</option>
                <option value="warn">Warning</option>
                <option value="error">Error</option>
              </select>
            </div>
          </section>

          {/* Capabilities (read-only) */}
          <section className="config-section">
            <h4>Capabilities</h4>
            <div className="config-row">
              <div className="config-label">
                <span className="config-label-text">Standalone mode</span>
              </div>
              <span className={`config-capability ${agent.capabilities.standaloneMode ? 'capability-enabled' : ''}`}>
                {agent.capabilities.standaloneMode ? 'Supported' : 'Not supported'}
              </span>
            </div>
            <div className="config-row">
              <div className="config-label">
                <span className="config-label-text">Interactive chat</span>
              </div>
              <span className={`config-capability ${agent.capabilities.interactiveChat ? 'capability-enabled' : ''}`}>
                {agent.capabilities.interactiveChat ? 'Supported' : 'Not supported'}
              </span>
            </div>
            <div className="config-row">
              <div className="config-label">
                <span className="config-label-text">Notifications</span>
              </div>
              <span className={`config-capability ${agent.capabilities.notifications ? 'capability-enabled' : ''}`}>
                {agent.capabilities.notifications ? 'Enabled' : 'Disabled'}
              </span>
            </div>
          </section>

          {/* Footer with save/cancel */}
          <div className="config-footer">
            <button className="btn-secondary" onClick={handleCancel}>
              Cancel
            </button>
            <button
              className="btn-primary"
              onClick={handleSave}
              disabled={!isDirty}
            >
              Save Changes
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
