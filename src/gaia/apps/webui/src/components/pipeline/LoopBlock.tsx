// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * LoopBlock - Visual loop block ("LEGO block") between stages.
 *
 * Displays loop configuration, iteration counter, and condition.
 * Renders as a rounded block with iteration count and loop path visualization.
 */

import { memo, useState } from 'react';
import { Repeat, X, AlertTriangle, ChevronDown, ChevronRight } from 'lucide-react';
import type { CanvasNode, GateCondition } from '../../types';
import { usePipelineCanvasStore } from '../../stores/pipelineCanvasStore';

interface LoopBlockProps {
    node: CanvasNode;
}

function LoopBlockInner({ node }: LoopBlockProps) {
    const { removeNode, setSelectedNode, selectedNodeId, maxIterations } = usePipelineCanvasStore((s) => ({
        removeNode: s.removeNode,
        setSelectedNode: s.setSelectedNode,
        selectedNodeId: s.selectedNodeId,
        maxIterations: s.maxIterations,
    }));

    const [expanded, setExpanded] = useState(false);
    const isSelected = selectedNodeId === node.id;

    // Derive iteration info from execution state
    const currentIteration = node.status === 'running' ? 1 : node.status === 'complete' ? maxIterations : 0;
    const iterationLimit = maxIterations;
    const progress = iterationLimit > 0 ? (currentIteration / iterationLimit) * 100 : 0;
    const isNearLimit = currentIteration >= iterationLimit - 1;

    const handleDragStart = (e: React.DragEvent) => {
        e.dataTransfer.setData('application/x-canvas-node', node.id);
        e.dataTransfer.effectAllowed = 'move';
        e.stopPropagation();
    };

    // Decode source/target from node data
    const sourceStage = node.assignedStage || 'unknown';
    const targetStage = node.decisionCondition || 'domain_analysis';

    return (
        <div
            className={`pc-loop-block pc-loop-${node.status}${isSelected ? ' selected' : ''}${isNearLimit ? ' pc-loop-near-limit' : ''}`}
            draggable
            onDragStart={handleDragStart}
            onClick={() => setSelectedNode(node.id)}
            style={{
                left: node.position.x,
                top: node.position.y,
            }}
        >
            {/* Loop icon and label */}
            <div className="pc-loop-header">
                <div className="pc-loop-icon">
                    <Repeat size={16} className={node.status === 'running' ? 'spin' : ''} />
                </div>
                <span className="pc-loop-label">{node.label || 'Loop'}</span>
                <button
                    className="pc-loop-remove"
                    onClick={(e) => {
                        e.stopPropagation();
                        removeNode(node.id);
                    }}
                    title="Remove loop"
                >
                    <X size={12} />
                </button>
            </div>

            {/* Iteration counter */}
            <div className="pc-loop-iteration">
                <span className="pc-loop-iter-count">
                    {currentIteration} / {iterationLimit}
                </span>
                {isNearLimit && (
                    <span className="pc-loop-warning">
                        <AlertTriangle size={10} />
                        Near limit
                    </span>
                )}
            </div>

            {/* Progress bar */}
            <div className="pc-loop-progress">
                <div
                    className="pc-loop-progress-fill"
                    style={{ width: `${Math.min(progress, 100)}%` }}
                />
            </div>

            {/* Loop path info */}
            <div className="pc-loop-path">
                <span className="pc-loop-path-from">{sourceStage}</span>
                <Repeat size={10} className="pc-loop-path-arrow" />
                <span className="pc-loop-path-to">{targetStage}</span>
            </div>

            {/* Expanded config */}
            {expanded && (
                <div className="pc-loop-config">
                    <div className="pc-loop-config-section">
                        <label>Loop Condition</label>
                        <span className="pc-loop-condition">{node.gateCondition || node.decisionCondition || 'quality_below_threshold'}</span>
                    </div>
                    <div className="pc-loop-config-section">
                        <label>Max Iterations</label>
                        <span className="pc-loop-max-iter">{iterationLimit}</span>
                    </div>
                </div>
            )}

            {/* Expand toggle */}
            <button
                className="pc-loop-expand"
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

export const LoopBlock = memo(LoopBlockInner);
