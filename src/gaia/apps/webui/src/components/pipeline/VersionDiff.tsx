// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * VersionDiff - Side-by-side comparison of two template version snapshots.
 *
 * Compares:
 * - Agent categories (added/removed agents)
 * - Routing rules
 * - Quality threshold
 * - Max iterations
 * - Quality weights
 *
 * Uses custom diff logic - no heavy diff library.
 */

import { memo } from 'react';
import { ArrowLeft, ArrowRight, CheckCircle2, AlertCircle, XCircle, FileText } from 'lucide-react';
import type { TemplateVersion, RoutingRule } from '../../types';
import './VersionDiff.css';

interface VersionDiffProps {
  versionA: TemplateVersion;
  versionB: TemplateVersion;
  onClose: () => void;
}

interface DiffItem {
  label: string;
  valueA: string;
  valueB: string;
  changed: boolean;
}

interface AgentCategoryDiff {
  category: string;
  onlyInA: string[];
  onlyInB: string[];
  common: string[];
}

function VersionDiffInner({ versionA, versionB, onClose }: VersionDiffProps) {
  // Compute field-level diffs
  const fieldDiffs: DiffItem[] = [
    {
      label: 'Description',
      valueA: versionA.snapshot.description,
      valueB: versionB.snapshot.description,
      changed: versionA.snapshot.description !== versionB.snapshot.description,
    },
    {
      label: 'Quality Threshold',
      valueA: `${(versionA.snapshot.quality_threshold * 100).toFixed(0)}%`,
      valueB: `${(versionB.snapshot.quality_threshold * 100).toFixed(0)}%`,
      changed: versionA.snapshot.quality_threshold !== versionB.snapshot.quality_threshold,
    },
    {
      label: 'Max Iterations',
      valueA: String(versionA.snapshot.max_iterations),
      valueB: String(versionB.snapshot.max_iterations),
      changed: versionA.snapshot.max_iterations !== versionB.snapshot.max_iterations,
    },
  ];

  // Compute agent category diffs
  const agentDiffs: AgentCategoryDiff[] = (() => {
    const catsA = versionA.snapshot.agent_categories || {};
    const catsB = versionB.snapshot.agent_categories || {};
    const allCats = new Set([...Object.keys(catsA), ...Object.keys(catsB)]);
    const result: AgentCategoryDiff[] = [];

    for (const cat of allCats) {
      const agentsA = catsA[cat] || [];
      const agentsB = catsB[cat] || [];
      const setA = new Set(agentsA);
      const setB = new Set(agentsB);

      const onlyInA = agentsA.filter((a) => !setB.has(a));
      const onlyInB = agentsB.filter((a) => !setA.has(a));
      const common = agentsA.filter((a) => setB.has(a));

      result.push({ category: cat, onlyInA, onlyInB, common });
    }

    return result;
  })();

  // Compute routing rule diffs
  const ruleDiffs = computeRuleDiff(versionA.snapshot.routing_rules, versionB.snapshot.routing_rules);

  // Compute quality weight diffs
  const weightDiffs = computeWeightDiff(versionA.snapshot.quality_weights, versionB.snapshot.quality_weights);

  const hasChanges = fieldDiffs.some((d) => d.changed) ||
    agentDiffs.some((d) => d.onlyInA.length > 0 || d.onlyInB.length > 0) ||
    ruleDiffs.changed;

  return (
    <div className="vd-diff-panel">
      {/* Header */}
      <div className="vd-header">
        <div className="vd-header-left">
          <FileText size={16} />
          <h3>Version Comparison</h3>
        </div>

        <div className="vd-header-versions">
          <span className="vd-version-badge vd-version-a">
            Version {versionA.version}
          </span>
          <ArrowRight size={14} className="vd-arrow" />
          <span className="vd-version-badge vd-version-b">
            Version {versionB.version}
          </span>
        </div>

        <div className="vd-header-right">
          {!hasChanges && (
            <span className="vd-no-changes">
              <CheckCircle2 size={14} />
              No changes
            </span>
          )}
          <button className="vd-btn-close" onClick={onClose} title="Close comparison">
            <XCircle size={16} />
          </button>
        </div>
      </div>

      {/* Summary */}
      {hasChanges && (
        <div className="vd-summary">
          {fieldDiffs.filter((d) => d.changed).length > 0 && (
            <span className="vd-summary-chip">
              <AlertCircle size={12} />
              {fieldDiffs.filter((d) => d.changed).length} field{fieldDiffs.filter((d) => d.changed).length !== 1 ? 's' : ''} changed
            </span>
          )}
          {agentDiffs.filter((d) => d.onlyInA.length > 0 || d.onlyInB.length > 0).length > 0 && (
            <span className="vd-summary-chip vd-chip-agents">
              Agents modified
            </span>
          )}
          {ruleDiffs.changed && (
            <span className="vd-summary-chip vd-chip-rules">
              Routing rules changed
            </span>
          )}
        </div>
      )}

      {/* No changes state */}
      {!hasChanges && (
        <div className="vd-empty">
          <CheckCircle2 size={32} strokeWidth={1} />
          <h4>No differences found</h4>
          <p>These two versions are identical.</p>
        </div>
      )}

      {/* Diff content */}
      {hasChanges && (
        <div className="vd-diff-content">
          {/* Field Diffs */}
          {fieldDiffs.filter((d) => d.changed).length > 0 && (
            <section className="vd-section">
              <h4 className="vd-section-title">Fields</h4>
              <div className="vd-fields">
                {fieldDiffs.filter((d) => d.changed).map((field) => (
                  <div key={field.label} className="vd-field-row">
                    <span className="vd-field-label">{field.label}</span>
                    <span className="vd-field-value vd-field-old">{field.valueA}</span>
                    <ArrowRight size={12} className="vd-field-arrow" />
                    <span className="vd-field-value vd-field-new">{field.valueB}</span>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Agent Category Diffs */}
          {agentDiffs.filter((d) => d.onlyInA.length > 0 || d.onlyInB.length > 0).length > 0 && (
            <section className="vd-section">
              <h4 className="vd-section-title">Agent Categories</h4>
              {agentDiffs.filter((d) => d.onlyInA.length > 0 || d.onlyInB.length > 0).map((cat) => (
                <div key={cat.category} className="vd-agent-category">
                  <span className="vd-category-name">{cat.category}</span>
                  <div className="vd-agent-columns">
                    <div className="vd-agent-column vd-agent-old">
                      {cat.onlyInA.length > 0 && (
                        <div className="vd-agent-group">
                          <span className="vd-agent-label">Removed</span>
                          {cat.onlyInA.map((agent) => (
                            <span key={agent} className="vd-agent-chip vd-agent-chip-removed">
                              {agent}
                            </span>
                          ))}
                        </div>
                      )}
                      {cat.common.length > 0 && (
                        <div className="vd-agent-group">
                          <span className="vd-agent-label">Unchanged</span>
                          {cat.common.map((agent) => (
                            <span key={agent} className="vd-agent-chip vd-agent-chip-unchanged">
                              {agent}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="vd-agent-column vd-agent-new">
                      {cat.onlyInB.length > 0 && (
                        <div className="vd-agent-group">
                          <span className="vd-agent-label">Added</span>
                          {cat.onlyInB.map((agent) => (
                            <span key={agent} className="vd-agent-chip vd-agent-chip-added">
                              {agent}
                            </span>
                          ))}
                        </div>
                      )}
                      {cat.common.length > 0 && (
                        <div className="vd-agent-group">
                          <span className="vd-agent-label">Unchanged</span>
                          {cat.common.map((agent) => (
                            <span key={agent} className="vd-agent-chip vd-agent-chip-unchanged">
                              {agent}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </section>
          )}

          {/* Routing Rules Diffs */}
          {ruleDiffs.changed && (
            <section className="vd-section">
              <h4 className="vd-section-title">Routing Rules</h4>
              <div className="vd-rules-columns">
                <div className="vd-rules-column vd-rules-old">
                  <h5 className="vd-rules-column-title">Version {versionA.version}</h5>
                  {ruleDiffs.rulesA.length === 0 ? (
                    <span className="vd-empty-rules">No rules</span>
                  ) : (
                    ruleDiffs.rulesA.map((rule, i) => (
                      <div key={i} className="vd-rule">
                        <span className="vd-rule-condition">{rule.condition}</span>
                        <ArrowRight size={10} />
                        <span className="vd-rule-target">{rule.route_to}</span>
                        {rule.loop_back && <span className="vd-rule-loop">loop</span>}
                      </div>
                    ))
                  )}
                </div>
                <div className="vd-rules-column vd-rules-new">
                  <h5 className="vd-rules-column-title">Version {versionB.version}</h5>
                  {ruleDiffs.rulesB.length === 0 ? (
                    <span className="vd-empty-rules">No rules</span>
                  ) : (
                    ruleDiffs.rulesB.map((rule, i) => (
                      <div key={i} className="vd-rule">
                        <span className="vd-rule-condition">{rule.condition}</span>
                        <ArrowRight size={10} />
                        <span className="vd-rule-target">{rule.route_to}</span>
                        {rule.loop_back && <span className="vd-rule-loop">loop</span>}
                      </div>
                    ))
                  )}
                </div>
              </div>
            </section>
          )}

          {/* Quality Weights Diffs */}
          {weightDiffs.hasChanges && (
            <section className="vd-section">
              <h4 className="vd-section-title">Quality Weights</h4>
              <div className="vd-weights-columns">
                <div className="vd-weights-column vd-weights-old">
                  {weightDiffs.weightsA.map((w) => (
                    <div key={w.dimension} className="vd-weight-row">
                      <span className="vd-weight-dimension">{w.dimension}</span>
                      <span className="vd-weight-value">{w.value}</span>
                    </div>
                  ))}
                </div>
                <div className="vd-weights-column vd-weights-new">
                  {weightDiffs.weightsB.map((w) => (
                    <div key={w.dimension} className="vd-weight-row">
                      <span className={`vd-weight-dimension ${w.changed ? 'vd-weight-changed' : ''}`}>{w.dimension}</span>
                      <span className={`vd-weight-value ${w.changed ? 'vd-weight-changed' : ''}`}>{w.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Diff Helpers ───────────────────────────────────────────────────────── */

interface RuleDiffResult {
  changed: boolean;
  rulesA: RoutingRule[];
  rulesB: RoutingRule[];
}

function computeRuleDiff(rulesA: RoutingRule[], rulesB: RoutingRule[]): RuleDiffResult {
  const rulesAList = rulesA || [];
  const rulesBList = rulesB || [];

  if (rulesAList.length !== rulesBList.length) {
    return { changed: true, rulesA: rulesAList, rulesB: rulesBList };
  }

  for (let i = 0; i < rulesAList.length; i++) {
    const a = rulesAList[i];
    const b = rulesBList[i];
    if (
      a.condition !== b.condition ||
      a.route_to !== b.route_to ||
      a.priority !== b.priority ||
      a.loop_back !== b.loop_back ||
      a.guidance !== b.guidance
    ) {
      return { changed: true, rulesA: rulesAList, rulesB: rulesBList };
    }
  }

  return { changed: false, rulesA: rulesAList, rulesB: rulesBList };
}

interface WeightEntry {
  dimension: string;
  value: number;
  changed: boolean;
}

interface WeightDiffResult {
  hasChanges: boolean;
  weightsA: WeightEntry[];
  weightsB: WeightEntry[];
}

function computeWeightDiff(
  weightsA: Record<string, number>,
  weightsB: Record<string, number>,
): WeightDiffResult {
  const allDims = new Set([...Object.keys(weightsA || {}), ...Object.keys(weightsB || {})]);
  const entriesA: WeightEntry[] = [];
  const entriesB: WeightEntry[] = [];
  let hasChanges = false;

  for (const dim of allDims) {
    const valA = (weightsA || {})[dim] ?? 0;
    const valB = (weightsB || {})[dim] ?? 0;
    const changed = valA !== valB;

    if (changed) hasChanges = true;

    entriesA.push({ dimension: dim, value: valA, changed });
    entriesB.push({ dimension: dim, value: valB, changed });
  }

  return { hasChanges, weightsA: entriesA, weightsB: entriesB };
}

export const VersionDiff = memo(VersionDiffInner);
