// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * VersionHistory - Timeline view of template version snapshots.
 *
 * Features:
 * - Vertical timeline of versions (newest first)
 * - Current/live version highlighted
 * - Create Snapshot button
 * - View, Restore, Compare actions per version
 * - Compare checkboxes for selecting two versions to diff
 */

import { memo, useState, useCallback, useEffect, useRef } from 'react';
import {
  GitBranch,
  Clock,
  Eye,
  RotateCcw,
  Plus,
  X,
  CheckSquare,
  Square,
  GitCompare,
  Loader2,
  AlertTriangle,
  CheckCircle2,
  Tag,
} from 'lucide-react';
import { useTemplateStore } from '../../stores/templateStore';
import type { TemplateVersion } from '../../types';
import './VersionHistory.css';

interface VersionHistoryProps {
  templateName: string;
  onClose: () => void;
  onCompare?: (versionA: TemplateVersion, versionB: TemplateVersion) => void;
}

function VersionHistoryInner({ templateName, onClose, onCompare }: VersionHistoryProps) {
  const {
    versions,
    isLoading,
    isSaving,
    lastError,
    setLastError,
    fetchVersions,
    createVersion,
    restoreVersion,
    setSelectedVersion,
  } = useTemplateStore((s) => ({
    versions: s.versions,
    isLoading: s.isLoading,
    isSaving: s.isSaving,
    lastError: s.lastError,
    setLastError: s.setLastError,
    fetchVersions: s.fetchVersions,
    createVersion: s.createVersion,
    restoreVersion: s.restoreVersion,
    setSelectedVersion: s.setSelectedVersion,
  }));

  const [compareMode, setCompareMode] = useState(false);
  const [compareA, setCompareA] = useState<number | null>(null);
  const [compareB, setCompareB] = useState<number | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  // Fetch versions on mount
  const versionsFetched = useRef(false);
  useEffect(() => {
    if (!versionsFetched.current) {
      versionsFetched.current = true;
      fetchVersions(templateName);
    }
  }, [templateName, fetchVersions]);

  // Fire compare callback when both versions are selected
  const versionA = versions.find((v) => v.version === compareA);
  const versionB = versions.find((v) => v.version === compareB);
  const hasComparePair = compareA !== null && compareB !== null && versionA && versionB;

  useEffect(() => {
    if (hasComparePair && onCompare) {
      onCompare(versionA, versionB);
    }
  }, [hasComparePair, versionA, versionB, onCompare]);

  const handleCreateSnapshot = useCallback(async () => {
    await createVersion(templateName);
    setCompareMode(false);
    setCompareA(null);
    setCompareB(null);
  }, [createVersion, templateName]);

  const handleRestore = useCallback(async (version: TemplateVersion) => {
    if (!confirm(`Restore ${templateName} to version ${version.version}? This will overwrite the current template.`)) {
      return;
    }
    await restoreVersion(templateName, version);
  }, [restoreVersion, templateName]);

  const handleView = useCallback((version: TemplateVersion) => {
    setSelectedVersion(version);
  }, [setSelectedVersion]);

  const handleCompareCheck = useCallback((versionNum: number) => {
    if (compareA === null) {
      setCompareA(versionNum);
    } else if (compareB === null && versionNum !== compareA) {
      setCompareB(versionNum);
    } else if (versionNum === compareA) {
      setCompareA(null);
    } else if (versionNum === compareB) {
      setCompareB(null);
    }
  }, [compareA, compareB]);

  const handleClearCompare = useCallback(() => {
    setCompareMode(false);
    setCompareA(null);
    setCompareB(null);
  }, []);

  const formatDate = (timestamp: number): string => {
    const d = new Date(timestamp * 1000);
    return d.toLocaleString();
  };

  const formatRelative = (timestamp: number): string => {
    const now = Date.now() / 1000;
    const diff = now - timestamp;
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  };

  return (
    <div className="vh-version-history">
      {/* Header */}
      <div className="vh-header">
        <div className="vh-header-left">
          <GitBranch size={16} />
          <h3>Versions - {templateName}</h3>
          <span className="vh-version-count">{versions.length} snapshot{versions.length !== 1 ? 's' : ''}</span>
        </div>

        <div className="vh-header-actions">
          {/* Compare mode toggle */}
          {!compareMode ? (
            <button
              className="vh-btn vh-btn-secondary vh-btn-small"
              onClick={() => setCompareMode(true)}
              title="Compare versions"
            >
              <GitCompare size={12} />
              Compare
            </button>
          ) : (
            <div className="vh-compare-controls">
              <span className="vh-compare-label">
                {compareA !== null && compareB !== null
                  ? 'Click View Diff to compare'
                  : 'Select two versions to compare'}
              </span>
              <button
                className="vh-btn vh-btn-small"
                onClick={handleClearCompare}
              >
                <X size={12} />
                Cancel
              </button>
            </div>
          )}

          {/* Create snapshot */}
          <button
            className="vh-btn vh-btn-primary vh-btn-small"
            onClick={handleCreateSnapshot}
            disabled={isSaving}
            title="Create snapshot of current template"
          >
            {isSaving ? <Loader2 size={12} className="spin" /> : <Plus size={12} />}
            Snapshot
          </button>

          {/* Refresh */}
          <button
            className="vh-btn vh-btn-secondary vh-btn-small"
            onClick={() => fetchVersions(templateName)}
            disabled={isLoading}
            title="Refresh versions"
          >
            <Clock size={12} />
          </button>

          {/* Close */}
          <button className="vh-btn-icon" onClick={onClose} title="Close">
            <X size={16} />
          </button>
        </div>
      </div>

      {/* Error display */}
      {lastError && (
        <div className="vh-error-bar">
          <AlertTriangle size={14} />
          <span>{lastError}</span>
          <button onClick={() => setLastError(null)} className="vh-error-dismiss">Dismiss</button>
        </div>
      )}

      {/* Loading state */}
      {isLoading && versions.length === 0 && (
        <div className="vh-loading">
          <Loader2 size={20} className="spin" />
          <span>Loading versions...</span>
        </div>
      )}

      {/* Empty state */}
      {!isLoading && versions.length === 0 && (
        <div className="vh-empty">
          <GitBranch size={32} strokeWidth={1} />
          <h4>No versions yet</h4>
          <p>Create a snapshot to save the current template state.</p>
        </div>
      )}

      {/* Version Timeline */}
      {versions.length > 0 && (
        <div className="vh-timeline">
          {/* Current (live) version indicator */}
          <div className="vh-timeline-item vh-timeline-current">
            <div className="vh-timeline-marker">
              <div className="vh-marker-dot vh-marker-live">
                <CheckCircle2 size={12} />
              </div>
              <div className="vh-timeline-line vh-line-live" />
            </div>
            <div className="vh-timeline-content">
              <div className="vh-version-label">
                <Tag size={12} />
                Current (Live)
              </div>
              <span className="vh-version-time">Latest template state</span>
            </div>
          </div>

          {/* Version entries (newest first) */}
          {versions.map((version, index) => {
            const isExpanded = expandedId === version.version;
            const isCompareA = compareA === version.version;
            const isCompareB = compareB === version.version;
            const isLast = index === versions.length - 1;

            return (
              <div
                key={version.version}
                className={`vh-timeline-item ${isCompareA || isCompareB ? 'vh-timeline-compare' : ''}`}
              >
                <div className="vh-timeline-marker">
                  <div className={`vh-marker-dot vh-marker-version ${isCompareA ? 'vh-marker-a' : ''} ${isCompareB ? 'vh-marker-b' : ''}`}>
                    {version.version}
                  </div>
                  {!isLast && <div className="vh-timeline-line" />}
                </div>

                <div className="vh-timeline-content">
                  {/* Version header */}
                  <div className="vh-version-header">
                    {compareMode ? (
                      <button
                        className="vh-checkbox"
                        onClick={() => handleCompareCheck(version.version)}
                        title={isCompareA || isCompareB ? 'Deselect for comparison' : 'Select for comparison'}
                      >
                        {(isCompareA || isCompareB) ? <CheckSquare size={14} /> : <Square size={14} />}
                      </button>
                    ) : null}

                    <span className="vh-version-label">
                      <Tag size={12} />
                      Version {version.version}
                    </span>

                    {version.description && (
                      <span className="vh-version-desc">{version.description}</span>
                    )}

                    <span className="vh-version-time" title={formatDate(version.created_at)}>
                      {formatRelative(version.created_at)}
                    </span>
                  </div>

                  {/* Summary */}
                  <div className="vh-version-summary">
                    <span className="vh-summary-item">
                      Quality: {(version.snapshot.quality_threshold * 100).toFixed(0)}%
                    </span>
                    <span className="vh-summary-item">
                      Max {version.snapshot.max_iterations} iterations
                    </span>
                    <span className="vh-summary-item">
                      {Object.values(version.snapshot.agent_categories).flat().length} agents
                    </span>
                  </div>

                  {/* Expanded details */}
                  {isExpanded && (
                    <div className="vh-version-details">
                      {Object.entries(version.snapshot.agent_categories).map(([category, agents]) => (
                        <div key={category} className="vh-detail-row">
                          <span className="vh-detail-label">{category}:</span>
                          <span className="vh-detail-agents">{agents.join(', ')}</span>
                        </div>
                      ))}
                      {version.snapshot.routing_rules && version.snapshot.routing_rules.length > 0 && (
                        <div className="vh-detail-row">
                          <span className="vh-detail-label">Rules:</span>
                          <span className="vh-detail-rules">
                            {version.snapshot.routing_rules.length} routing rule{version.snapshot.routing_rules.length !== 1 ? 's' : ''}
                          </span>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Action buttons */}
                  <div className="vh-version-actions">
                    <button
                      className="vh-btn vh-btn-small vh-btn-ghost"
                      onClick={() => setExpandedId(isExpanded ? null : version.version)}
                      title={isExpanded ? 'Collapse details' : 'Expand details'}
                    >
                      <Eye size={12} />
                      {isExpanded ? 'Hide' : 'Details'}
                    </button>
                    <button
                      className="vh-btn vh-btn-small vh-btn-ghost"
                      onClick={() => handleView(version)}
                      title="View this version"
                    >
                      <Eye size={12} />
                      View
                    </button>
                    <button
                      className="vh-btn vh-btn-small vh-btn-ghost vh-btn-warning"
                      onClick={() => handleRestore(version)}
                      disabled={isSaving}
                      title="Restore this version"
                    >
                      <RotateCcw size={12} />
                      Restore
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export const VersionHistory = memo(VersionHistoryInner);
