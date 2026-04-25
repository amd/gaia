// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * StageZone - Drop zone for a pipeline stage.
 *
 * Renders a horizontal band for each stage. Agents can be dropped from the
 * palette onto the stage zone. Displays assigned agent nodes.
 */

import { memo } from 'react';
import { FolderPlus, Play, Shield, GitBranch } from 'lucide-react';
import type { CanvasNode, GateCondition } from '../../types';
import { AgentNode } from './AgentNode';
import { SupervisorNode } from './SupervisorNode';
import { DecisionGate } from './DecisionGate';
import { PIPELINE_STAGES } from '../../stores/pipelineCanvasStore';
import { usePipelineCanvasStore } from '../../stores/pipelineCanvasStore';

const STAGE_ICONS: Record<string, React.ReactNode> = {
    domain_analysis: <Play size={14} />,
    workflow_modeling: <Play size={14} />,
    loom_building: <Play size={14} />,
    gap_detection: <Play size={14} />,
    pipeline_execution: <Play size={14} />,
};

interface StageZoneProps {
    stage: typeof PIPELINE_STAGES[number];
    agentNodes: CanvasNode[];
    index: number;
}

function StageZoneInner({ stage, agentNodes, index }: StageZoneProps) {
    const { addAgentToStage, dragOverStage, setDragOverStage, nodes, edges, addSupervisorBetweenStages, addGateBetweenStages, addLoopBlock } = usePipelineCanvasStore((s) => ({
        addAgentToStage: s.addAgentToStage,
        dragOverStage: s.dragOverStage,
        setDragOverStage: s.setDragOverStage,
        nodes: s.nodes,
        edges: s.edges,
        addSupervisorBetweenStages: s.addSupervisorBetweenStages,
        addGateBetweenStages: s.addGateBetweenStages,
        addLoopBlock: s.addLoopBlock,
    }));

    const isDragOver = dragOverStage === stage.key;
    const stageNode = nodes.find((n) => n.type === 'stage' && n.assignedStage === stage.key);
    const stageStatus = stageNode?.status || 'idle';

    // Get non-agent nodes for this stage (supervisors with assignedStage, gates)
    // Free-floating supervisors and loops (no assignedStage) render at canvas level
    const supervisorNodes = nodes.filter((n) => n.type === 'supervisor' && n.assignedStage === stage.key);
    const gateNodes = nodes.filter((n) => n.type === 'gate' && n.assignedStage === stage.key);

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
        setDragOverStage(stage.key);
    };

    const handleDragLeave = (e: React.DragEvent) => {
        // Only clear if we actually left the zone
        const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
        const { clientX, clientY } = e;
        if (clientX < rect.left || clientX > rect.right || clientY < rect.top || clientY > rect.bottom) {
            setDragOverStage(null);
        }
    };

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault();
        setDragOverStage(null);

        // Check for palette drag (new agent)
        const agentData = e.dataTransfer.getData('application/x-agent');
        if (agentData) {
            try {
                const parsed = JSON.parse(agentData);
                // Check if it's a special block type
                const blockType = parsed.blockType;
                if (blockType === 'supervisor') {
                    addSupervisorBetweenStages(parsed, stage.key);
                } else if (blockType === 'gate') {
                    addGateBetweenStages(parsed.condition || 'quality_below_threshold', stage.key);
                } else if (blockType === 'loop') {
                    const nextStageKey = PIPELINE_STAGES[index + 1]?.key || 'domain_analysis';
                    addLoopBlock(stage.key, nextStageKey, parsed.condition || 'quality_below_threshold');
                } else {
                    addAgentToStage(parsed, stage.key);
                }
            } catch {
                // Invalid drag data - ignore
            }
            return;
        }

        // Check for canvas node drag (move existing)
        const nodeId = e.dataTransfer.getData('application/x-canvas-node');
        if (nodeId) {
            const { moveNodeToStage } = usePipelineCanvasStore.getState();
            moveNodeToStage(nodeId, stage.key);
        }
    };

    return (
        <div
            className={`pc-stage-zone${isDragOver ? ' pc-stage-zone-drag-over' : ''} pc-stage-${stageStatus}`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
        >
            {/* Stage header */}
            <div className="pc-stage-header">
                <div className="pc-stage-header-left">
                    <span className="pc-stage-number">{index + 1}</span>
                    <div className="pc-stage-icon">{STAGE_ICONS[stage.key]}</div>
                    <div>
                        <span className="pc-stage-label">{stage.label}</span>
                        <span className="pc-stage-description">{stage.description}</span>
                    </div>
                </div>
                <div className="pc-stage-header-right">
                    <span className="pc-stage-agent-count">
                        {agentNodes.length} agent{agentNodes.length !== 1 ? 's' : ''}
                    </span>
                    {stageStatus === 'running' && (
                        <span className="pc-stage-running-badge">Running</span>
                    )}
                    {stageStatus === 'complete' && (
                        <span className="pc-stage-complete-badge">Done</span>
                    )}
                </div>
            </div>

            {/* Supervisor nodes */}
            {supervisorNodes.length > 0 && (
                <div className="pc-stage-supervisors">
                    {supervisorNodes.map((node) => (
                        <SupervisorNode key={node.id} node={node} />
                    ))}
                </div>
            )}

            {/* Decision gate nodes */}
            {gateNodes.length > 0 && (
                <div className="pc-stage-gates">
                    {gateNodes.map((node) => (
                        <DecisionGate key={node.id} node={node} />
                    ))}
                </div>
            )}

            {/* Agent slots */}
            <div className="pc-stage-agents">
                {agentNodes.length === 0 ? (
                    <div className="pc-stage-empty">
                        <FolderPlus size={20} strokeWidth={1} />
                        <span>Drag agents here</span>
                    </div>
                ) : (
                    agentNodes.map((node) => (
                        <AgentNode key={node.id} node={node} />
                    ))
                )}
            </div>
        </div>
    );
}

export const StageZone = memo(StageZoneInner);
