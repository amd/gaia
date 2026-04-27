// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, useCallback, useMemo } from 'react';
import {
  Shield,
  ShieldCheck,
  ShieldAlert,
  RotateCcw,
  ChevronDown,
  Search,
  Eye,
  Zap,
  AlertTriangle,
  Info,
} from 'lucide-react';
import { useAgentStore } from '../stores/agentStore';
import type { PermissionTier, ToolPermission } from '../types/agent';
import './PermissionManager.css';

// ── Props ────────────────────────────────────────────────────────────────

interface PermissionManagerProps {
  agentId: string;
}

// ── Mock data until IPC is wired ─────────────────────────────────────────
// In production, permissions will come from agent IPC / config files.

function getDefaultPermissions(_agentId: string): ToolPermission[] {
  // Default permissions based on common agent tools.
  // In production, permissions will come from agent IPC / config files keyed by _agentId.
  const commonTools: ToolPermission[] = [
    { tool: 'read_file', defaultTier: 'auto' },
    { tool: 'write_file', defaultTier: 'confirm' },
    { tool: 'search_files', defaultTier: 'auto' },
    { tool: 'run_shell_command', defaultTier: 'escalate' },
    { tool: 'query_documents', defaultTier: 'auto' },
    { tool: 'index_document', defaultTier: 'auto' },
    { tool: 'web_search', defaultTier: 'confirm' },
    { tool: 'execute_code', defaultTier: 'escalate' },
    { tool: 'list_directory', defaultTier: 'auto' },
    { tool: 'delete_file', defaultTier: 'escalate' },
  ];
  return commonTools;
}

// ── Tier helpers ─────────────────────────────────────────────────────────

const TIER_ORDER: PermissionTier[] = ['auto', 'confirm', 'escalate'];

interface TierMeta {
  label: string;
  description: string;
  color: string;
  bgColor: string;
  borderColor: string;
  icon: React.ReactNode;
}

function getTierMeta(tier: PermissionTier): TierMeta {
  switch (tier) {
    case 'auto':
      return {
        label: 'Auto',
        description: 'Runs without prompting',
        color: '#22c55e',
        bgColor: 'rgba(34, 197, 94, 0.08)',
        borderColor: 'rgba(34, 197, 94, 0.2)',
        icon: <Zap size={12} />,
      };
    case 'confirm':
      return {
        label: 'Confirm',
        description: 'Requires user approval',
        color: '#f59e0b',
        bgColor: 'rgba(245, 158, 11, 0.08)',
        borderColor: 'rgba(245, 158, 11, 0.2)',
        icon: <Eye size={12} />,
      };
    case 'escalate':
      return {
        label: 'Escalate',
        description: 'Blocked — admin review required',
        color: '#ef4444',
        bgColor: 'rgba(239, 68, 68, 0.08)',
        borderColor: 'rgba(239, 68, 68, 0.2)',
        icon: <ShieldAlert size={12} />,
      };
  }
}

function getNextTier(current: PermissionTier): PermissionTier {
  const idx = TIER_ORDER.indexOf(current);
  return TIER_ORDER[(idx + 1) % TIER_ORDER.length];
}

// ── TierBadge subcomponent ───────────────────────────────────────────────

function TierBadge({ tier, size = 'normal' }: { tier: PermissionTier; size?: 'normal' | 'small' }) {
  const meta = getTierMeta(tier);
  return (
    <span
      className={`tier-badge ${size}`}
      style={{
        '--tier-color': meta.color,
        '--tier-bg': meta.bgColor,
        '--tier-border': meta.borderColor,
      } as React.CSSProperties}
      title={meta.description}
    >
      {meta.icon}
      <span className="tier-badge-label">{meta.label}</span>
    </span>
  );
}

// ── TierDropdown subcomponent ────────────────────────────────────────────

interface TierDropdownProps {
  currentTier: PermissionTier;
  onSelect: (tier: PermissionTier) => void;
  disabled?: boolean;
}

function TierDropdown({ currentTier, onSelect, disabled }: TierDropdownProps) {
  const [open, setOpen] = useState(false);

  const handleSelect = useCallback((tier: PermissionTier) => {
    onSelect(tier);
    setOpen(false);
  }, [onSelect]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      setOpen(false);
    }
  }, []);

  return (
    <div className="tier-dropdown-wrap" onKeyDown={handleKeyDown}>
      <button
        className="tier-dropdown-trigger"
        onClick={() => setOpen(!open)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Permission tier: ${currentTier}`}
      >
        <TierBadge tier={currentTier} size="small" />
        <ChevronDown size={12} className={`tier-dropdown-chevron ${open ? 'open' : ''}`} />
      </button>
      {open && (
        <>
          <div className="tier-dropdown-backdrop" onClick={() => setOpen(false)} />
          <div className="tier-dropdown-menu" role="listbox">
            {TIER_ORDER.map((tier) => {
              const meta = getTierMeta(tier);
              return (
                <button
                  key={tier}
                  className={`tier-dropdown-item ${tier === currentTier ? 'selected' : ''}`}
                  onClick={() => handleSelect(tier)}
                  role="option"
                  aria-selected={tier === currentTier}
                >
                  <span className="tier-dropdown-icon" style={{ color: meta.color }}>
                    {meta.icon}
                  </span>
                  <div className="tier-dropdown-text">
                    <span className="tier-dropdown-label">{meta.label}</span>
                    <span className="tier-dropdown-desc">{meta.description}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

// ── Main Component ───────────────────────────────────────────────────────

export function PermissionManager({ agentId }: PermissionManagerProps) {
  const agent = useAgentStore((s) => s.agents[agentId]);
  const [permissions, setPermissions] = useState<ToolPermission[]>(() => getDefaultPermissions(agentId));
  const [searchQuery, setSearchQuery] = useState('');
  const [filterTier, setFilterTier] = useState<PermissionTier | 'all'>('all');

  // Filter permissions
  const filteredPermissions = useMemo(() => {
    let result = permissions;
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      result = result.filter((p) => p.tool.toLowerCase().includes(query));
    }
    if (filterTier !== 'all') {
      result = result.filter((p) => (p.overrideTier || p.defaultTier) === filterTier);
    }
    return result;
  }, [permissions, searchQuery, filterTier]);

  // Count overrides
  const overrideCount = useMemo(
    () => permissions.filter((p) => p.overrideTier).length,
    [permissions]
  );

  // Override a tool's tier
  const setToolOverride = useCallback((toolName: string, tier: PermissionTier) => {
    setPermissions((prev) =>
      prev.map((p) => {
        if (p.tool !== toolName) return p;
        // If setting back to default, clear the override
        if (tier === p.defaultTier) {
          return { ...p, overrideTier: undefined };
        }
        return { ...p, overrideTier: tier };
      })
    );
  }, []);

  // Reset single tool
  const resetTool = useCallback((toolName: string) => {
    setPermissions((prev) =>
      prev.map((p) => (p.tool === toolName ? { ...p, overrideTier: undefined } : p))
    );
  }, []);

  // Cycle through tiers (auto → confirm → escalate → back to default)
  const cycleTier = useCallback((toolName: string) => {
    setPermissions((prev) =>
      prev.map((p) => {
        if (p.tool !== toolName) return p;
        const currentTier = p.overrideTier || p.defaultTier;
        const next = getNextTier(currentTier);
        // If cycling lands back on default tier, clear the override
        if (next === p.defaultTier) {
          return { ...p, overrideTier: undefined };
        }
        return { ...p, overrideTier: next };
      })
    );
  }, []);

  // Reset all overrides
  const resetAll = useCallback(() => {
    setPermissions((prev) => prev.map((p) => ({ ...p, overrideTier: undefined })));
  }, []);

  return (
    <div className="permission-manager">
      {/* Header */}
      <div className="perm-header">
        <div className="perm-header-left">
          <Shield size={18} className="perm-header-icon" />
          <div>
            <h3 className="perm-title">Tools & Permissions</h3>
            <p className="perm-subtitle">
              {agent?.name || agentId} — {permissions.length} tools
              {overrideCount > 0 && (
                <span className="perm-override-count">
                  {overrideCount} override{overrideCount !== 1 ? 's' : ''}
                </span>
              )}
            </p>
          </div>
        </div>
        {overrideCount > 0 && (
          <button className="btn-secondary perm-reset-all" onClick={resetAll}>
            <RotateCcw size={13} />
            Reset All
          </button>
        )}
      </div>

      {/* Tier legend */}
      <div className="perm-legend">
        {TIER_ORDER.map((tier) => {
          const meta = getTierMeta(tier);
          return (
            <div key={tier} className="perm-legend-item" title={meta.description}>
              <span className="perm-legend-dot" style={{ background: meta.color }} />
              <span className="perm-legend-label">{meta.label}</span>
              <span className="perm-legend-desc">— {meta.description}</span>
            </div>
          );
        })}
      </div>

      {/* Filters */}
      <div className="perm-filters">
        <div className="perm-search">
          <Search size={14} className="perm-search-icon" />
          <input
            type="text"
            placeholder="Filter tools..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="perm-search-input"
            aria-label="Filter tools by name"
          />
        </div>
        <div className="perm-filter-tier">
          <button
            className={`perm-filter-btn ${filterTier === 'all' ? 'active' : ''}`}
            onClick={() => setFilterTier('all')}
          >
            All
          </button>
          {TIER_ORDER.map((tier) => {
            const meta = getTierMeta(tier);
            return (
              <button
                key={tier}
                className={`perm-filter-btn ${filterTier === tier ? 'active' : ''}`}
                onClick={() => setFilterTier(filterTier === tier ? 'all' : tier)}
                style={filterTier === tier ? { '--filter-color': meta.color } as React.CSSProperties : undefined}
              >
                {meta.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Permission table */}
      <div className="perm-table-wrap">
        <table className="perm-table">
          <thead>
            <tr>
              <th className="perm-th-tool">Tool</th>
              <th className="perm-th-default">Default</th>
              <th className="perm-th-override">Override</th>
              <th className="perm-th-action">Action</th>
            </tr>
          </thead>
          <tbody>
            {filteredPermissions.map((tool) => {
              const effectiveTier = tool.overrideTier || tool.defaultTier;
              const hasOverride = !!tool.overrideTier;

              return (
                <tr key={tool.tool} className={`perm-row ${hasOverride ? 'has-override' : ''}`}>
                  <td className="perm-cell-tool">
                    <code className="perm-tool-name">{tool.tool}</code>
                  </td>
                  <td className="perm-cell-default">
                    <TierBadge tier={tool.defaultTier} size="small" />
                  </td>
                  <td className="perm-cell-override">
                    {hasOverride ? (
                      <TierBadge tier={tool.overrideTier!} size="small" />
                    ) : (
                      <span className="perm-default-label">(default)</span>
                    )}
                  </td>
                  <td className="perm-cell-action">
                    <div className="perm-action-group">
                      <TierDropdown
                        currentTier={effectiveTier}
                        onSelect={(tier) => setToolOverride(tool.tool, tier)}
                      />
                      {hasOverride && (
                        <button
                          className="perm-reset-btn"
                          onClick={() => resetTool(tool.tool)}
                          title="Reset to default"
                          aria-label={`Reset ${tool.tool} to default`}
                        >
                          <RotateCcw size={12} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {filteredPermissions.length === 0 && (
          <div className="perm-empty">
            <Info size={16} />
            <span>No tools match your filter.</span>
          </div>
        )}
      </div>

      {/* Info banner */}
      <div className="perm-info-banner">
        <AlertTriangle size={13} />
        <span>
          <strong>Escalate</strong> tier tools are blocked and require admin approval.
          Changes to permissions are applied immediately but can be reset at any time.
        </span>
      </div>
    </div>
  );
}
