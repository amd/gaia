// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * AgentNode - Draggable agent card on the canvas.
 *
 * Displays agent info, execution status, and can be dragged between stages.
 */

import { memo, useState } from 'react';
import { Cpu, X, CheckCircle, AlertCircle, Loader2, RotateCcw, Clock } from 'lucide-react';
import type { CanvasNode } from '../../types';
import { usePipelineCanvasStore } from '../../stores/pipelineCanvasStore';

const STATUS_ICONS: Record<CanvasNode['status'], React.ReactNode> = {
    idle: <Clock size={14} />,
    running: <Loader2 size={14} className="spin" />,
    complete: <CheckCircle size={14} />,
    error: <AlertCircle size={14} />,
    waiting: <RotateCcw size={14} />,
};

const STATUS_COLORS: Record<CanvasNode['status'], string> = {
    idle: '#6b7280',
    running: '#3b82f6',
    complete: '#10b981',
    error: '#ef4444',
    waiting: '#f59e0b',
};

interface AgentNodeProps {
    node: CanvasNode;
}

function AgentNodeInner({ node }: AgentNodeProps) {
    const { removeNode, setSelectedNode, selectedNodeId } = usePipelineCanvasStore((s) => ({
        removeNode: s.removeNode,
        setSelectedNode: s.setSelectedNode,
        selectedNodeId: s.selectedNodeId,
    }));

    const [dragging, setDragging] = useState(false);
    const isSelected = selectedNodeId === node.id;

    const handleDragStart = (e: React.DragEvent) => {
        setDragging(true);
        e.dataTransfer.setData('application/x-canvas-node', node.id);
        e.dataTransfer.effectAllowed = 'move';
        e.stopPropagation();
    };

    const handleDragEnd = () => {
        setDragging(false);
    };

    return (
        <div
            className={`pc-agent-node pc-agent-${node.status}${isSelected ? ' selected' : ''}${dragging ? ' dragging' : ''}`}
            draggable
            onDragStart={handleDragStart}
            onDragEnd={handleDragEnd}
            onClick={() => setSelectedNode(node.id)}
            style={{
                left: node.position.x,
                top: node.position.y,
            }}
        >
            {/* Status bar */}
            <div className="pc-agent-status" style={{ backgroundColor: STATUS_COLORS[node.status] }}>
                {STATUS_ICONS[node.status]}
                <span className="pc-agent-status-label">{node.status}</span>
            </div>

            {/* Header */}
            <div className="pc-agent-header">
                <div className="pc-agent-icon">
                    <Cpu size={16} />
                </div>
                <div className="pc-agent-info">
                    <span className="pc-agent-label">{node.label}</span>
                    {node.agentId && (
                        <span className="pc-agent-id">{node.agentId}</span>
                    )}
                </div>
                <button
                    className="pc-agent-remove"
                    onClick={(e) => {
                        e.stopPropagation();
                        removeNode(node.id);
                    }}
                    title="Remove from canvas"
                >
                    <X size={14} />
                </button>
            </div>

            {/* Details */}
            <div className="pc-agent-details">
                {node.modelId && (
                    <div className="pc-agent-detail">
                        <span className="pc-agent-detail-label">Model</span>
                        <span className="pc-agent-detail-value">{node.modelId}</span>
                    </div>
                )}
                {node.category && (
                    <div className="pc-agent-detail">
                        <span className="pc-agent-detail-label">Category</span>
                        <span className="pc-agent-detail-value">{node.category}</span>
                    </div>
                )}
                {node.qualityScore !== undefined && (
                    <div className="pc-agent-detail">
                        <span className="pc-agent-detail-label">Quality</span>
                        <span className={`pc-agent-quality ${node.qualityScore >= 0.9 ? 'pass' : 'fail'}`}>
                            {(node.qualityScore * 100).toFixed(0)}%
                        </span>
                    </div>
                )}
            </div>

            {/* Capabilities */}
            {node.agentData?.capabilities && node.agentData.capabilities.length > 0 && (
                <div className="pc-agent-capabilities">
                    {node.agentData.capabilities.slice(0, 3).map((cap) => (
                        <span key={cap} className="pc-agent-capability">
                            {cap}
                        </span>
                    ))}
                    {node.agentData.capabilities.length > 3 && (
                        <span className="pc-agent-capability pc-agent-more">
                            +{node.agentData.capabilities.length - 3}
                        </span>
                    )}
                </div>
            )}
        </div>
    );
}

export const AgentNode = memo(AgentNodeInner);
