// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * SupervisorNode - Visual node representing a supervisor agent between stages.
 *
 * Supervisors evaluate output quality and decide: CONTINUE, LOOP_BACK, PAUSE, COMPLETE, or FAIL.
 * Displayed as a diamond-shaped node between stage zones.
 */

import { memo, useState } from 'react';
import { Shield, X, CheckCircle, AlertCircle, Loader2, ChevronDown, ChevronRight } from 'lucide-react';
import type { CanvasNode, DecisionType } from '../../types';
import { usePipelineCanvasStore } from '../../stores/pipelineCanvasStore';

const DECISION_COLORS: Record<DecisionType, string> = {
    CONTINUE: '#10b981',
    LOOP_BACK: '#f59e0b',
    PAUSE: '#3b82f6',
    COMPLETE: '#8b5cf6',
    FAIL: '#ef4444',
};

const DECISION_ICONS: Record<DecisionType, React.ReactNode> = {
    CONTINUE: <CheckCircle size={12} />,
    LOOP_BACK: <Loader2 size={12} className="spin" />,
    PAUSE: <AlertCircle size={12} />,
    COMPLETE: <CheckCircle size={12} />,
    FAIL: <AlertCircle size={12} />,
};

const AVAILABLE_DECISIONS: DecisionType[] = ['CONTINUE', 'LOOP_BACK', 'PAUSE', 'COMPLETE', 'FAIL'];

interface SupervisorNodeProps {
    node: CanvasNode;
}

function SupervisorNodeInner({ node }: SupervisorNodeProps) {
    const { removeNode, setSelectedNode, selectedNodeId, nodes } = usePipelineCanvasStore((s) => ({
        removeNode: s.removeNode,
        setSelectedNode: s.setSelectedNode,
        selectedNodeId: s.selectedNodeId,
        nodes: s.nodes,
    }));

    const [expanded, setExpanded] = useState(false);
    const isSelected = selectedNodeId === node.id;

    const handleDragStart = (e: React.DragEvent) => {
        e.dataTransfer.setData('application/x-canvas-node', node.id);
        e.dataTransfer.effectAllowed = 'move';
        e.stopPropagation();
    };

    const handleDragEnd = () => {
        // Cleanup if needed
    };

    // Determine active decision based on node status during execution
    const activeDecision = node.status === 'complete'
        ? (node.decisionType || 'CONTINUE')
        : node.status === 'error'
            ? 'FAIL'
            : node.decisionType || 'CONTINUE';

    // Find connected stages for context
    const connectedEdges = nodes.filter(n => n.type === 'stage' && n.assignedStage === node.assignedStage);

    return (
        <div
            className={`pc-supervisor-node pc-supervisor-${node.status}${isSelected ? ' selected' : ''}`}
            draggable
            onDragStart={handleDragStart}
            onDragEnd={handleDragEnd}
            onClick={() => setSelectedNode(node.id)}
            style={{
                left: node.position.x,
                top: node.position.y,
            }}
        >
            {/* Decision indicator bar */}
            <div className="pc-supervisor-decision-bar" style={{ backgroundColor: DECISION_COLORS[activeDecision] }} />

            {/* Main body */}
            <div className="pc-supervisor-body">
                <div className="pc-supervisor-icon">
                    <Shield size={18} />
                </div>
                <div className="pc-supervisor-info">
                    <span className="pc-supervisor-label">{node.label}</span>
                    {node.agentId && (
                        <span className="pc-supervisor-id">{node.agentId}</span>
                    )}
                </div>
                <button
                    className="pc-supervisor-remove"
                    onClick={(e) => {
                        e.stopPropagation();
                        removeNode(node.id);
                    }}
                    title="Remove supervisor"
                >
                    <X size={14} />
                </button>
            </div>

            {/* Decision type display */}
            <div className="pc-supervisor-decision">
                <span className="pc-supervisor-decision-label">Decision:</span>
                <span
                    className="pc-supervisor-decision-value"
                    style={{ color: DECISION_COLORS[activeDecision] }}
                >
                    {DECISION_ICONS[activeDecision]}
                    {activeDecision}
                </span>
            </div>

            {/* Expandable config panel */}
            {expanded && (
                <div className="pc-supervisor-config">
                    <div className="pc-supervisor-config-section">
                        <label>Decision Types</label>
                        <div className="pc-supervisor-decisions-list">
                            {AVAILABLE_DECISIONS.map((decision) => (
                                <span
                                    key={decision}
                                    className={`pc-supervisor-decision-tag${decision === activeDecision ? ' active' : ''}`}
                                    style={{
                                        borderColor: DECISION_COLORS[decision],
                                        ...(decision === activeDecision ? { backgroundColor: DECISION_COLORS[decision] + '20' } : {}),
                                    }}
                                >
                                    {decision}
                                </span>
                            ))}
                        </div>
                    </div>
                    {node.decisionCondition && (
                        <div className="pc-supervisor-config-section">
                            <label>Condition</label>
                            <span className="pc-supervisor-condition">{node.decisionCondition}</span>
                        </div>
                    )}
                    {connectedEdges.length > 0 && (
                        <div className="pc-supervisor-config-section">
                            <label>Monitoring</label>
                            <span className="pc-supervisor-monitoring">
                                {connectedEdges.map(n => n.label).join(', ')}
                            </span>
                        </div>
                    )}
                </div>
            )}

            {/* Expand toggle */}
            <button
                className="pc-supervisor-expand"
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

export const SupervisorNode = memo(SupervisorNodeInner);
