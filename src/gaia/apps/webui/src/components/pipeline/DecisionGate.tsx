// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * DecisionGate - Diamond-shaped gate node between stages.
 *
 * Evaluates conditions and routes execution: pass -> next stage, fail -> loop back.
 */

import { memo, useState } from 'react';
import { GitBranch, X, CheckCircle, AlertCircle, ChevronDown, ChevronRight } from 'lucide-react';
import type { CanvasNode, GateCondition } from '../../types';
import { usePipelineCanvasStore } from '../../stores/pipelineCanvasStore';

const CONDITION_ICONS: Record<GateCondition, React.ReactNode> = {
    quality_below_threshold: <CheckCircle size={14} />,
    error_detected: <AlertCircle size={14} />,
    manual_review: <GitBranch size={14} />,
    iteration_limit: <AlertCircle size={14} />,
};

const CONDITION_LABELS: Record<GateCondition, string> = {
    quality_below_threshold: 'Quality < Threshold',
    error_detected: 'Error Detected',
    manual_review: 'Manual Review',
    iteration_limit: 'Iteration Limit',
};

interface DecisionGateProps {
    node: CanvasNode;
}

function DecisionGateInner({ node }: DecisionGateProps) {
    const { removeNode, setSelectedNode, selectedNodeId } = usePipelineCanvasStore((s) => ({
        removeNode: s.removeNode,
        setSelectedNode: s.setSelectedNode,
        selectedNodeId: s.selectedNodeId,
    }));

    const [expanded, setExpanded] = useState(false);
    const isSelected = selectedNodeId === node.id;
    const condition = node.gateCondition || 'quality_below_threshold';
    const passed = node.status === 'complete';
    const failed = node.status === 'error';

    const handleDragStart = (e: React.DragEvent) => {
        e.dataTransfer.setData('application/x-canvas-node', node.id);
        e.dataTransfer.effectAllowed = 'move';
        e.stopPropagation();
    };

    return (
        <div
            className={`pc-decision-gate pc-gate-${node.status}${isSelected ? ' selected' : ''}${passed ? ' gate-passed' : ''}${failed ? ' gate-failed' : ''}`}
            draggable
            onDragStart={handleDragStart}
            onClick={() => setSelectedNode(node.id)}
            style={{
                left: node.position.x,
                top: node.position.y,
            }}
        >
            {/* Diamond shape indicator */}
            <div className="pc-gate-diamond">
                {CONDITION_ICONS[condition]}
            </div>

            {/* Gate info */}
            <div className="pc-gate-info">
                <span className="pc-gate-label">{node.label}</span>
                <span className="pc-gate-condition">{CONDITION_LABELS[condition]}</span>
            </div>

            {/* Remove button */}
            <button
                className="pc-gate-remove"
                onClick={(e) => {
                    e.stopPropagation();
                    removeNode(node.id);
                }}
                title="Remove gate"
            >
                <X size={12} />
            </button>

            {/* Branch indicators */}
            {node.branchTargets && (
                <div className="pc-gate-branches">
                    <span className="pc-gate-branch pc-gate-pass">
                        Pass → {node.branchTargets.pass}
                    </span>
                    <span className="pc-gate-branch pc-gate-fail">
                        Fail → {node.branchTargets.fail}
                    </span>
                </div>
            )}

            {/* Expanded config */}
            {expanded && (
                <div className="pc-gate-config">
                    <div className="pc-gate-config-section">
                        <label>Condition Type</label>
                        <select
                            className="pc-gate-select"
                            onClick={(e) => e.stopPropagation()}
                        >
                            {(Object.keys(CONDITION_LABELS) as GateCondition[]).map((cond) => (
                                <option key={cond} value={cond} selected={cond === condition}>
                                    {CONDITION_LABELS[cond]}
                                </option>
                            ))}
                        </select>
                    </div>
                    {node.qualityScore !== undefined && (
                        <div className="pc-gate-config-section">
                            <label>Quality Score</label>
                            <span className={`pc-gate-quality ${node.qualityScore >= 0.9 ? 'pass' : 'fail'}`}>
                                {(node.qualityScore * 100).toFixed(0)}%
                            </span>
                        </div>
                    )}
                </div>
            )}

            {/* Expand toggle */}
            <button
                className="pc-gate-expand"
                onClick={(e) => {
                    e.stopPropagation();
                    setExpanded(!expanded);
                }}
            >
                {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            </button>
        </div>
    );
}

export const DecisionGate = memo(DecisionGateInner);
