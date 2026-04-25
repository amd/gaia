// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Zustand store for Pipeline Canvas state.
 *
 * Manages visual canvas state (nodes, edges), agent palette data,
 * and synchronization with pipeline templates.
 */

import { create } from 'zustand';
import type {
    CanvasNode,
    CanvasEdge,
    CanvasState,
    PaletteDragData,
    AgentRegistryEntry,
    PipelineTemplate,
    LoopNodeConfig,
    SupervisorNodeConfig,
    GateCondition,
} from '../types';
import * as canvasApi from '../services/pipelineCanvas';
import { usePipelineStore } from './pipelineStore';
import { log } from '../utils/logger';

// ── Unique ID Generator ────────────────────────────────────────────────

/** Replace Date.now() to avoid ID collision during batch operations. */
let _idCounter = 0;
function genId(prefix: string): string {
    return `${prefix}-${Date.now().toString(36)}-${(++_idCounter).toString(36)}`;
}

// ── Pipeline Stage Definitions ─────────────────────────────────────────

export const PIPELINE_STAGES = [
    { key: 'domain_analysis', label: 'Domain Analysis', description: 'Analyze the problem domain and requirements', agentCategory: 'analysis', order: 1 },
    { key: 'workflow_modeling', label: 'Workflow Modeling', description: 'Model workflows and process flows', agentCategory: 'analysis', order: 2 },
    { key: 'loom_building', label: 'Loom Building', description: 'Build orchestration loom', agentCategory: 'orchestration', order: 3 },
    { key: 'gap_detection', label: 'Gap Detection', description: 'Detect gaps and missing agents', agentCategory: 'orchestration', order: 4 },
    { key: 'pipeline_execution', label: 'Pipeline Execution', description: 'Execute the pipeline with spawned agents', agentCategory: 'orchestration', order: 5 },
] as const;

// ── State Interface ────────────────────────────────────────────────────

interface CanvasStoreState {
    // Canvas data
    nodes: CanvasNode[];
    edges: CanvasEdge[];
    templateName?: string;
    qualityThreshold: number;
    maxIterations: number;

    // Palette
    agents: AgentRegistryEntry[];
    agentCategories: Record<string, string[]>;
    paletteLoading: boolean;

    // UI state
    isLoading: boolean;
    isSaving: boolean;
    lastError: string | null;
    selectedNodeId: string | null;
    dragOverStage: string | null;

    // Tier 2: Navigation
    zoom: number;
    pan: { x: number; y: number };

    // Tier 2: Undo/Redo
    history: Array<{ nodes: CanvasNode[]; edges: CanvasEdge[] }>;
    historyIndex: number;

    // Tier 2: Multi-select
    selectedNodeIds: string[];

    // Tier 2: Grid
    showGrid: boolean;
    snapToGrid: boolean;
    gridSize: number;

    // Actions - canvas data
    setNodes: (nodes: CanvasNode[]) => void;
    setEdges: (edges: CanvasEdge[]) => void;
    addAgentToStage: (agent: PaletteDragData, stageKey: string) => void;
    addSupervisorBetweenStages: (agent: PaletteDragData, afterStageKey: string) => void;
    addGateBetweenStages: (condition: string, afterStageKey: string) => void;
    addLoopBlock: (sourceStage: string, targetStage: string, condition: string) => void;
    /** Add a free-floating supervisor at arbitrary canvas position */
    addFreeSupervisor: (agent: PaletteDragData, position: { x: number; y: number }) => void;
    /** Add a free-floating loop block with self-contained config */
    addFreeLoop: (config: Partial<LoopNodeConfig>, position: { x: number; y: number }) => void;
    /** Update a loop node's configuration */
    updateLoopConfig: (loopNodeId: string, partial: Partial<LoopNodeConfig>) => void;
    /** Update a supervisor node's configuration */
    updateSupervisorConfig: (supervisorNodeId: string, partial: Partial<SupervisorNodeConfig>) => void;
    /** Update a decision gate's condition */
    updateGateCondition: (gateNodeId: string, condition: string) => void;
    /** Move a free-floating node to a new position */
    moveNodeToPosition: (nodeId: string, position: { x: number; y: number }) => void;
    removeNode: (nodeId: string) => void;
    moveNodeToStage: (nodeId: string, stageKey: string) => void;
    resetCanvas: () => void;

    // Actions - palette
    fetchAgents: () => Promise<void>;

    // Actions - template sync
    loadTemplateAsCanvas: (name: string) => Promise<void>;
    saveCanvasAsTemplate: (name: string, description: string) => Promise<void>;
    updateCurrentTemplate: () => Promise<void>;

    // Actions - live execution
    applyExecutionState: (events: Array<Record<string, unknown>>) => void;

    // Actions - UI
    setSelectedNode: (id: string | null) => void;
    setDragOverStage: (stageKey: string | null) => void;
    setLastError: (error: string | null) => void;
    setQualityThreshold: (value: number) => void;
    setMaxIterations: (value: number) => void;

    // Tier 2: Navigation
    setZoom: (zoom: number) => void;
    setPan: (pan: { x: number; y: number }) => void;
    resetView: () => void;

    // Tier 2: Undo/Redo
    pushHistory: () => void;
    undo: () => void;
    redo: () => void;
    canUndo: boolean;
    canRedo: boolean;

    // Tier 2: Multi-select
    toggleNodeSelection: (id: string) => void;
    clearSelection: () => void;

    // Tier 2: Grid
    setShowGrid: (show: boolean) => void;
    setSnapToGrid: (snap: boolean) => void;
    setGridSize: (size: number) => void;
}

// ── Helper: compute node position from stage ──────────────────────────

function computePosition(stageOrder: number, agentIndex: number): { x: number; y: number } {
    const stageY = 80 + stageOrder * 160;
    const agentX = 60 + agentIndex * 220;
    return { x: agentX, y: stageY };
}

function buildStageNode(stage: typeof PIPELINE_STAGES[number], nodeIndex: number): CanvasNode {
    return {
        id: `stage-${stage.key}`,
        type: 'stage',
        label: stage.label,
        category: stage.agentCategory,
        status: 'idle',
        position: { x: 40, y: 60 + nodeIndex * 160 },
        assignedStage: stage.key,
    };
}

function buildEdge(fromId: string, toId: string, status: CanvasEdge['status'] = 'idle'): CanvasEdge {
    return { id: `edge-${fromId}-${toId}`, source: fromId, target: toId, status, animated: false };
}

// ── Store Implementation ───────────────────────────────────────────────

export const usePipelineCanvasStore = create<CanvasStoreState>((set, get) => ({
    // Initial state
    nodes: [],
    edges: [],
    qualityThreshold: 0.9,
    maxIterations: 3,

    agents: [],
    agentCategories: {},
    paletteLoading: false,

    isLoading: false,
    isSaving: false,
    lastError: null,
    selectedNodeId: null,
    dragOverStage: null,

    // Tier 2: Navigation
    zoom: 1,
    pan: { x: 0, y: 0 },

    // Tier 2: Undo/Redo
    history: [],
    historyIndex: -1,

    // Tier 2: Multi-select
    selectedNodeIds: [],

    // Tier 2: Grid
    showGrid: true,
    snapToGrid: true,
    gridSize: 20,

    // ── Canvas data ────────────────────────────────────────────────────

    setNodes: (nodes) => set({ nodes }),

    setEdges: (edges) => set({ edges }),

    addAgentToStage: (agent, stageKey) => {
        const stage = PIPELINE_STAGES.find((s) => s.key === stageKey);
        if (!stage) {
            set({ lastError: `Unknown stage: ${stageKey}` });
            return;
        }

        const existingAgents = get().nodes.filter(
            (n) => n.type === 'agent' && n.assignedStage === stageKey,
        );
        const stageIndex = PIPELINE_STAGES.indexOf(stage);
        const position = computePosition(stageIndex, existingAgents.length);

        const newNode: CanvasNode = {
            id: genId(`agent-${agent.agentId}`),
            type: 'agent',
            agentId: agent.agentId,
            label: agent.name,
            category: agent.category,
            modelId: agent.modelId,
            status: 'idle',
            position,
            assignedStage: stageKey,
            agentData: {
                id: agent.agentId,
                name: agent.name,
                category: agent.category,
                description: agent.description || '',
                model_id: agent.modelId || null,
                capabilities: agent.capabilities || [],
                keywords: agent.keywords || [],
                phases: agent.phases || [],
                complexity_range: '0-1',
                tools: [],
                enabled: true,
                version: '1.0.0',
                source: 'yaml',
                templates_using: [],
            },
        };

        // Edge from stage node to new agent node
        const stageNode = get().nodes.find((n) => n.type === 'stage' && n.assignedStage === stageKey);
        const newEdge = stageNode ? buildEdge(stageNode.id, newNode.id) : null;

        const newNodes = [...get().nodes, newNode];
        const newEdges = newEdge ? [...get().edges, newEdge] : get().edges;

        set({ nodes: newNodes, edges: newEdges });
        log.ui.info(`[canvas] Added agent ${agent.agentId} to stage ${stageKey}`);
    },

    addSupervisorBetweenStages: (agent, afterStageKey) => {
        const afterStage = PIPELINE_STAGES.find((s) => s.key === afterStageKey);
        if (!afterStage) {
            set({ lastError: `Unknown stage: ${afterStageKey}` });
            return;
        }

        const stageIndex = PIPELINE_STAGES.indexOf(afterStage);
        const position = { x: 280, y: 80 + stageIndex * 160 + 80 };

        const supervisorId = genId(`supervisor-${agent.agentId}`);
        const newNode: CanvasNode = {
            id: supervisorId,
            type: 'supervisor',
            agentId: agent.agentId,
            label: `${agent.name} (Supervisor)`,
            category: agent.category,
            modelId: agent.modelId,
            status: 'idle',
            position,
            assignedStage: afterStageKey,
            decisionType: 'CONTINUE',
            decisionCondition: 'quality_below_threshold',
            supervisorConfig: {
                supervisor_id: supervisorId,
                label: `${agent.name} (Supervisor)`,
                agent_id: agent.agentId,
                decision_condition: 'quality_below_threshold',
                decision_type: 'CONTINUE',
                monitoring_targets: [afterStageKey],
            },
            agentData: {
                id: agent.agentId,
                name: agent.name,
                category: agent.category,
                description: agent.description || '',
                model_id: agent.modelId || null,
                capabilities: agent.capabilities || [],
                keywords: agent.keywords || [],
                phases: agent.phases || [],
                complexity_range: '0-1',
                tools: [],
                enabled: true,
                version: '1.0.0',
                source: 'yaml',
                templates_using: [],
            },
        };

        const newNodes = [...get().nodes, newNode];
        const stageNode = newNodes.find((n) => n.type === 'stage' && n.assignedStage === afterStageKey);
        const newEdges = stageNode ? [...get().edges, buildEdge(stageNode.id, newNode.id)] : get().edges;

        set({ nodes: newNodes, edges: newEdges });
        log.ui.info(`[canvas] Added supervisor ${agent.agentId} after stage ${afterStageKey}`);
    },

    addGateBetweenStages: (condition, afterStageKey) => {
        const afterStage = PIPELINE_STAGES.find((s) => s.key === afterStageKey);
        if (!afterStage) {
            set({ lastError: `Unknown stage: ${afterStageKey}` });
            return;
        }

        const stageIndex = PIPELINE_STAGES.indexOf(afterStage);
        const nextStage = PIPELINE_STAGES[stageIndex + 1];
        const position = { x: 280, y: 80 + stageIndex * 160 + 80 };

        const newNode: CanvasNode = {
            id: genId('gate'),
            type: 'gate',
            label: `Gate: ${condition}`,
            status: 'idle',
            position,
            assignedStage: afterStageKey,
            gateCondition: condition as GateCondition,
            branchTargets: {
                pass: nextStage?.key || afterStageKey,
                fail: 'domain_analysis',
            },
        };

        const newNodes = [...get().nodes, newNode];
        const stageNode = newNodes.find((n) => n.type === 'stage' && n.assignedStage === afterStageKey);
        const newEdges = stageNode ? [...get().edges, buildEdge(stageNode.id, newNode.id)] : get().edges;

        set({ nodes: newNodes, edges: newEdges });
        log.ui.info(`[canvas] Added decision gate after stage ${afterStageKey}`);
    },

    addLoopBlock: (sourceStage, targetStage, condition) => {
        const source = PIPELINE_STAGES.find((s) => s.key === sourceStage);
        const target = PIPELINE_STAGES.find((s) => s.key === targetStage);
        if (!source || !target) {
            set({ lastError: `Invalid loop: ${sourceStage} -> ${targetStage}` });
            return;
        }

        const sourceIndex = PIPELINE_STAGES.indexOf(source);
        const targetIndex = PIPELINE_STAGES.indexOf(target);
        const midIndex = Math.max(sourceIndex, targetIndex);
        const position = { x: 10, y: 80 + midIndex * 160 + 40 };

        const newNode: CanvasNode = {
            id: genId('loop'),
            type: 'loop',
            label: `Loop: ${source.label} → ${target.label}`,
            status: 'idle',
            position,
            assignedStage: sourceStage,
            decisionCondition: targetStage,
            gateCondition: condition as GateCondition,
        };

        const newNodes = [...get().nodes, newNode];
        const sourceStageNode = newNodes.find((n) => n.type === 'stage' && n.assignedStage === sourceStage);
        const targetStageNode = newNodes.find((n) => n.type === 'stage' && n.assignedStage === targetStage);
        const newEdges = [...get().edges];
        if (sourceStageNode) newEdges.push(buildEdge(sourceStageNode.id, newNode.id));
        if (targetStageNode) newEdges.push({ ...buildEdge(newNode.id, targetStageNode.id), status: 'loop-back', animated: true, label: `loop (${condition})` });

        set({ nodes: newNodes, edges: newEdges });
        log.ui.info(`[canvas] Added loop block ${sourceStage} -> ${targetStage}`);
    },

    addFreeSupervisor: (agent, position) => {
        const supervisorId = genId(`sup-${agent.agentId}`);
        const newNode: CanvasNode = {
            id: supervisorId,
            type: 'supervisor',
            agentId: agent.agentId,
            label: `${agent.name} (Supervisor)`,
            category: agent.category,
            modelId: agent.modelId,
            status: 'idle',
            position,
            // No assignedStage - free-floating
            decisionType: 'CONTINUE',
            decisionCondition: 'quality_below_threshold',
            supervisorConfig: {
                supervisor_id: supervisorId,
                label: `${agent.name} (Supervisor)`,
                agent_id: agent.agentId,
                decision_condition: 'quality_below_threshold',
                decision_type: 'CONTINUE',
                monitoring_targets: [],
            },
            agentData: {
                id: agent.agentId,
                name: agent.name,
                category: agent.category,
                description: agent.description || '',
                model_id: agent.modelId || null,
                capabilities: agent.capabilities || [],
                keywords: agent.keywords || [],
                phases: agent.phases || [],
                complexity_range: '0-1',
                tools: [],
                enabled: true,
                version: '1.0.0',
                source: 'yaml',
                templates_using: [],
            },
        };

        set({ nodes: [...get().nodes, newNode] });
        log.ui.info(`[canvas] Added free supervisor ${agent.agentId} at (${position.x}, ${position.y})`);
    },

    addFreeLoop: (config, position) => {
        const loopId = genId('loop');
        const sourceStage = config.source_stage || '';
        const targetStage = config.target_stage || '';
        const source = PIPELINE_STAGES.find((s) => s.key === sourceStage);
        const target = PIPELINE_STAGES.find((s) => s.key === targetStage);
        const label = config.label || (source && target ? `Loop: ${source.label} → ${target.label}` : 'Loop');

        const loopNodeId = genId('loop-node');
        const newNode: CanvasNode = {
            id: loopNodeId,
            type: 'loop',
            label,
            status: 'idle',
            position,
            // No assignedStage - free-floating
            loopConfig: {
                loop_id: loopId,
                label,
                agent_ids: config.agent_ids || [],
                max_iterations: config.max_iterations ?? get().maxIterations,
                quality_threshold: config.quality_threshold,
                source_stage: sourceStage,
                target_stage: targetStage,
                condition: config.condition || 'quality_below_threshold',
            },
        };

        const newNodes = [...get().nodes, newNode];
        const newEdges: CanvasEdge[] = [...get().edges];
        // Add edges if source/target stages exist
        if (sourceStage) {
            const sourceStageNode = newNodes.find((n) => n.type === 'stage' && n.assignedStage === sourceStage);
            if (sourceStageNode) newEdges.push(buildEdge(sourceStageNode.id, loopNodeId));
        }
        if (targetStage) {
            const targetStageNode = newNodes.find((n) => n.type === 'stage' && n.assignedStage === targetStage);
            if (targetStageNode) newEdges.push({ ...buildEdge(loopNodeId, targetStageNode.id), status: 'loop-back', animated: true, label: `loop` });
        }

        set({ nodes: newNodes, edges: newEdges });
        log.ui.info(`[canvas] Added free loop "${label}" at (${position.x}, ${position.y})`);
    },

    updateLoopConfig: (loopNodeId, partial) => {
        const nodes = get().nodes.map((n) => {
            if (n.id !== loopNodeId || !n.loopConfig) return n;
            return { ...n, loopConfig: { ...n.loopConfig, ...partial } };
        });
        set({ nodes });
        log.ui.info(`[canvas] Updated loop config for ${loopNodeId}`);
    },

    updateSupervisorConfig: (supervisorNodeId, partial) => {
        const nodes: CanvasNode[] = get().nodes.map((n) => {
            if (n.id !== supervisorNodeId || n.type !== 'supervisor') return n;
            // Handle both supervisorConfig and flat decision fields
            if (n.supervisorConfig) {
                return { ...n, supervisorConfig: { ...n.supervisorConfig, ...partial } };
            }
            return {
                ...n,
                ...(partial.decision_type !== undefined ? { decisionType: partial.decision_type } : {}),
                ...(partial.decision_condition !== undefined ? { decisionCondition: partial.decision_condition } : {}),
            } as CanvasNode;
        });
        set({ nodes });
        log.ui.info(`[canvas] Updated supervisor config for ${supervisorNodeId}`);
    },

    updateGateCondition: (gateNodeId, condition) => {
        const nodes = get().nodes.map((n) => {
            if (n.id !== gateNodeId || n.type !== 'gate') return n;
            const gateCondition = condition as typeof n.gateCondition;
            return {
                ...n,
                gateCondition,
                label: `Gate: ${condition}`,
            };
        });
        set({ nodes });
        log.ui.info(`[canvas] Updated gate condition for ${gateNodeId} -> ${condition}`);
    },

    moveNodeToPosition: (nodeId, position) => {
        const nodes = get().nodes.map((n) =>
            n.id === nodeId ? { ...n, position } : n,
        );
        set({ nodes });
    },

    removeNode: (nodeId) => {
        const nodes = get().nodes.filter((n) => n.id !== nodeId);
        const edges = get().edges.filter((e) => e.source !== nodeId && e.target !== nodeId);
        set({ nodes, edges, selectedNodeId: null });
    },

    moveNodeToStage: (nodeId, stageKey) => {
        const node = get().nodes.find((n) => n.id === nodeId);
        if (!node || node.type !== 'agent') return;

        const stage = PIPELINE_STAGES.find((s) => s.key === stageKey);
        if (!stage) return;

        const stageIndex = PIPELINE_STAGES.indexOf(stage);
        const existingAgents = get().nodes.filter(
            (n) => n.type === 'agent' && n.assignedStage === stageKey && n.id !== nodeId,
        );
        const position = computePosition(stageIndex, existingAgents.length);

        const nodes = get().nodes.map((n) =>
            n.id === nodeId
                ? { ...n, assignedStage: stageKey, position }
                : n,
        );

        // Update edges: remove old edges to this node, add new one from new stage
        const edges = get().edges.filter((e) => e.target !== nodeId);
        const newStageNode = nodes.find((n) => n.type === 'stage' && n.assignedStage === stageKey);
        if (newStageNode) {
            edges.push(buildEdge(newStageNode.id, nodeId));
        }

        set({ nodes, edges });
    },

    resetCanvas: () => {
        // Build default canvas with stage nodes, supervisor slots, and decision gates
        const nodes: CanvasNode[] = [];
        const edges: CanvasEdge[] = [];

        // Add stage nodes
        PIPELINE_STAGES.forEach((stage, i) => {
            nodes.push(buildStageNode(stage, i));
        });

        // Add edges between stages
        for (let i = 0; i < PIPELINE_STAGES.length - 1; i++) {
            edges.push(buildEdge(`stage-${PIPELINE_STAGES[i].key}`, `stage-${PIPELINE_STAGES[i + 1].key}`));
        }

        // Add default supervisor node after each stage (except last)
        for (let i = 0; i < PIPELINE_STAGES.length - 1; i++) {
            const stage = PIPELINE_STAGES[i];
            const supervisor: CanvasNode = {
                id: `supervisor-default-${stage.key}`,
                type: 'supervisor',
                label: `Quality Supervisor`,
                status: 'idle',
                position: { x: 280, y: 80 + i * 160 + 80 },
                assignedStage: stage.key,
                decisionType: 'CONTINUE',
                decisionCondition: 'quality_below_threshold',
            };
            nodes.push(supervisor);
            edges.push(buildEdge(`stage-${stage.key}`, supervisor.id));

            // Add decision gate after supervisor
            const gate: CanvasNode = {
                id: `gate-default-${stage.key}`,
                type: 'gate',
                label: `Quality Gate`,
                status: 'idle',
                position: { x: 500, y: 80 + i * 160 + 80 },
                assignedStage: stage.key,
                gateCondition: 'quality_below_threshold',
                branchTargets: {
                    pass: PIPELINE_STAGES[i + 1]?.key || stage.key,
                    fail: 'domain_analysis',
                },
            };
            nodes.push(gate);
            edges.push(buildEdge(supervisor.id, gate.id));
        }

        set({ nodes, edges, templateName: undefined, selectedNodeId: null });
    },

    // ── Palette ────────────────────────────────────────────────────────

    fetchAgents: async () => {
        set({ paletteLoading: true, lastError: null });
        try {
            const data = await canvasApi.fetchAgents();
            set({ agents: data.agents, agentCategories: data.categories, paletteLoading: false });
            log.ui.info(`[canvas] Loaded ${data.total} agents across ${Object.keys(data.categories).length} categories`);
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            set({ lastError: `Failed to load agents: ${message}`, paletteLoading: false });
            log.ui.error('[canvas] Failed to load agents:', err);
        }
    },

    // ── Template sync ──────────────────────────────────────────────────

    loadTemplateAsCanvas: async (name) => {
        set({ isLoading: true, lastError: null });
        try {
            const { template, yaml } = await canvasApi.loadTemplateAsCanvas(name);
            const parsed = canvasApi.parseTemplateYaml(yaml);

            // Build nodes from agent categories
            const nodes: CanvasNode[] = [];
            const edges: CanvasEdge[] = [];

            // Add stage nodes
            PIPELINE_STAGES.forEach((stage, i) => {
                nodes.push(buildStageNode(stage, i));
            });

            // Add chain edges between stages
            for (let i = 0; i < PIPELINE_STAGES.length - 1; i++) {
                edges.push(buildEdge(
                    `stage-${PIPELINE_STAGES[i].key}`,
                    `stage-${PIPELINE_STAGES[i + 1].key}`,
                ));
            }

            // Add agent nodes from template
            const agentCats = parsed.agent_categories || {};
            for (const [catKey, agentIds] of Object.entries(agentCats)) {
                const stage = PIPELINE_STAGES.find((s) => s.agentCategory === catKey);
                const stageIndex = stage ? PIPELINE_STAGES.indexOf(stage) : 0;
                (agentIds as string[]).forEach((agentId, agentIndex) => {
                    const agent = get().agents.find((a) => a.id === agentId);
                    const position = computePosition(stageIndex, agentIndex);
                    nodes.push({
                        id: `agent-${agentId}`,
                        type: 'agent',
                        agentId,
                        label: agent?.name || agentId,
                        category: catKey,
                        modelId: agent?.model_id || undefined,
                        status: 'idle',
                        position,
                        assignedStage: stage?.key,
                        agentData: agent,
                    });
                    // Edge from stage to agent
                    const stageNode = nodes.find((n) => n.type === 'stage' && n.assignedStage === stage?.key);
                    if (stageNode) {
                        edges.push(buildEdge(stageNode.id, `agent-${agentId}`));
                    }
                });
            }

            // Reconstruct free-floating loop nodes from canvas_loops
            const canvasLoops = (parsed as any).canvas_loops;
            if (canvasLoops && Array.isArray(canvasLoops)) {
                for (const loopData of canvasLoops) {
                    const loopNodeId = `loop-node-${loopData.loop_id || Date.now()}`;
                    const pos = loopData.position || { x: 10, y: 200 };
                    nodes.push({
                        id: loopNodeId,
                        type: 'loop',
                        label: loopData.label || `Loop`,
                        status: 'idle',
                        position: pos,
                        loopConfig: {
                            loop_id: loopData.loop_id || loopNodeId,
                            label: loopData.label || '',
                            agent_ids: loopData.agent_ids || [],
                            max_iterations: loopData.max_iterations ?? get().maxIterations,
                            quality_threshold: loopData.quality_threshold,
                            source_stage: loopData.source_stage,
                            target_stage: loopData.target_stage,
                            condition: loopData.condition || 'quality_below_threshold',
                        },
                    });
                    // Add edges to source/target stages if specified
                    if (loopData.source_stage) {
                        const srcNode = nodes.find((n) => n.type === 'stage' && n.assignedStage === loopData.source_stage);
                        if (srcNode) edges.push(buildEdge(srcNode.id, loopNodeId));
                    }
                    if (loopData.target_stage) {
                        const tgtNode = nodes.find((n) => n.type === 'stage' && n.assignedStage === loopData.target_stage);
                        if (tgtNode) edges.push({ ...buildEdge(loopNodeId, tgtNode.id), status: 'loop-back', animated: true, label: 'loop' });
                    }
                }
            }

            // Reconstruct free-floating supervisor nodes from canvas_supervisors
            const canvasSupervisors = (parsed as any).canvas_supervisors;
            if (canvasSupervisors && Array.isArray(canvasSupervisors)) {
                for (const supData of canvasSupervisors) {
                    const supNodeId = `sup-node-${supData.supervisor_id || Date.now()}`;
                    const pos = supData.position || { x: 280, y: 200 };
                    nodes.push({
                        id: supNodeId,
                        type: 'supervisor',
                        agentId: supData.agent_id,
                        label: supData.label || 'Supervisor',
                        status: 'idle',
                        position: pos,
                        decisionType: (supData.decision_type as any) || 'CONTINUE',
                        decisionCondition: supData.decision_condition || 'quality_below_threshold',
                        supervisorConfig: {
                            supervisor_id: supData.supervisor_id || supNodeId,
                            label: supData.label || '',
                            agent_id: supData.agent_id,
                            decision_condition: supData.decision_condition || 'quality_below_threshold',
                            decision_type: supData.decision_type || 'CONTINUE',
                            monitoring_targets: supData.monitoring_targets || [],
                        },
                    });
                }
            }

            set({
                nodes,
                edges,
                templateName: name,
                qualityThreshold: parsed.quality_threshold ?? 0.9,
                maxIterations: parsed.max_iterations ?? 3,
                isLoading: false,
            });
            log.ui.info(`[canvas] Loaded template "${name}" with ${nodes.length} nodes`);
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            set({ lastError: `Failed to load template: ${message}`, isLoading: false });
            log.ui.error('[canvas] Failed to load template:', err);
        }
    },

    saveCanvasAsTemplate: async (name, description) => {
        set({ isSaving: true, lastError: null });
        try {
            // Collect agent nodes grouped by category
            const agentNodes = get().nodes.filter((n) => n.type === 'agent');
            const agentCategories: Record<string, string[]> = {};
            for (const node of agentNodes) {
                const cat = node.category || 'other';
                if (!agentCategories[cat]) agentCategories[cat] = [];
                if (node.agentId && !agentCategories[cat].includes(node.agentId)) {
                    agentCategories[cat].push(node.agentId);
                }
            }

            // Build routing rules from loop-back edges
            const loopBackEdges = get().edges.filter((e) => e.status === 'loop-back');
            const routingRules = loopBackEdges.map((edge, i) => ({
                condition: 'quality_below_threshold',
                route_to: 'domain_analysis',
                priority: i,
                loop_back: true,
                guidance: 'Improve quality score above threshold',
            }));

            // Collect free-floating loop configs
            const loopNodes = get().nodes.filter((n) => n.type === 'loop' && n.loopConfig);
            const canvasLoops = loopNodes.map((n) => ({
                loop_id: n.loopConfig!.loop_id,
                label: n.loopConfig!.label,
                agent_ids: n.loopConfig!.agent_ids,
                max_iterations: n.loopConfig!.max_iterations,
                quality_threshold: n.loopConfig!.quality_threshold,
                source_stage: n.loopConfig!.source_stage,
                target_stage: n.loopConfig!.target_stage,
                condition: n.loopConfig!.condition || 'quality_below_threshold',
                position: n.position,
            }));

            // Collect free-floating supervisor configs
            const supervisorNodes = get().nodes.filter((n) => n.type === 'supervisor' && n.supervisorConfig);
            const canvasSupervisors = supervisorNodes.map((n) => ({
                supervisor_id: n.supervisorConfig!.supervisor_id,
                label: n.supervisorConfig!.label,
                agent_id: n.supervisorConfig!.agent_id,
                position: n.position,
                decision_condition: n.supervisorConfig!.decision_condition,
                decision_type: n.supervisorConfig!.decision_type,
                monitoring_targets: n.supervisorConfig!.monitoring_targets || [],
            }));

            const canvasExport = {
                name,
                description,
                quality_threshold: get().qualityThreshold,
                max_iterations: get().maxIterations,
                agent_categories: agentCategories,
                routing_rules: routingRules,
                canvas_loops: canvasLoops,
                canvas_supervisors: canvasSupervisors,
            };

            await canvasApi.saveCanvasAsTemplate(name, canvasExport);

            // Also update the templateStore list
            const { useTemplateStore } = await import('./templateStore');
            await useTemplateStore.getState().fetchTemplates();

            set({ templateName: name, isSaving: false });
            log.ui.info(`[canvas] Saved as template "${name}"`);
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            set({ lastError: `Failed to save template: ${message}`, isSaving: false });
            log.ui.error('[canvas] Failed to save template:', err);
        }
    },

    updateCurrentTemplate: async () => {
        const name = get().templateName;
        if (!name) {
            set({ lastError: 'No template loaded to update' });
            return;
        }
        set({ isSaving: true, lastError: null });
        try {
            const agentNodes = get().nodes.filter((n) => n.type === 'agent');
            const agentCategories: Record<string, string[]> = {};
            for (const node of agentNodes) {
                const cat = node.category || 'other';
                if (!agentCategories[cat]) agentCategories[cat] = [];
                if (node.agentId && !agentCategories[cat].includes(node.agentId)) {
                    agentCategories[cat].push(node.agentId);
                }
            }

            // Collect free-floating loop configs
            const loopNodes = get().nodes.filter((n) => n.type === 'loop' && n.loopConfig);
            const canvasLoops = loopNodes.map((n) => ({
                loop_id: n.loopConfig!.loop_id,
                label: n.loopConfig!.label,
                agent_ids: n.loopConfig!.agent_ids,
                max_iterations: n.loopConfig!.max_iterations,
                quality_threshold: n.loopConfig!.quality_threshold,
                source_stage: n.loopConfig!.source_stage,
                target_stage: n.loopConfig!.target_stage,
                condition: n.loopConfig!.condition || 'quality_below_threshold',
                position: n.position,
            }));

            // Collect free-floating supervisor configs
            const supervisorNodes = get().nodes.filter((n) => n.type === 'supervisor' && n.supervisorConfig);
            const canvasSupervisors = supervisorNodes.map((n) => ({
                supervisor_id: n.supervisorConfig!.supervisor_id,
                label: n.supervisorConfig!.label,
                agent_id: n.supervisorConfig!.agent_id,
                position: n.position,
                decision_condition: n.supervisorConfig!.decision_condition,
                decision_type: n.supervisorConfig!.decision_type,
                monitoring_targets: n.supervisorConfig!.monitoring_targets || [],
            }));

            const canvasExport = {
                name,
                description: `Pipeline canvas: ${agentNodes.length} agents across ${Object.keys(agentCategories).length} categories`,
                quality_threshold: get().qualityThreshold,
                max_iterations: get().maxIterations,
                agent_categories: agentCategories,
                routing_rules: [],
                canvas_loops: canvasLoops,
                canvas_supervisors: canvasSupervisors,
            };

            await canvasApi.updateTemplateFromCanvas(name, canvasExport);
            set({ isSaving: false });
            log.ui.info(`[canvas] Updated template "${name}"`);
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            set({ lastError: `Failed to update template: ${message}`, isSaving: false });
            log.ui.error('[canvas] Failed to update template:', err);
        }
    },

    // ── Live execution ─────────────────────────────────────────────────

    applyExecutionState: (events) => {
        const pipelineExec = usePipelineStore.getState().activeExecution;
        if (!pipelineExec) return;

        const nodes = get().nodes.map((node) => {
            // Find the latest event for this node
            const relevantEvents = events.filter(
                (e) => {
                    const agent = (e as any).agent || (e as any).agent_id || '';
                    const tool = (e as any).tool || '';
                    const stepMsg = (e as any).message || '';
                    return agent === node.agentId || tool.includes(node.agentId || '') || stepMsg.includes(node.label || '');
                },
            );

            if (relevantEvents.length === 0) return node;

            const lastEvent = relevantEvents[relevantEvents.length - 1];
            const eventType = (lastEvent as any).type || '';

            let status: CanvasNode['status'] = node.status;
            let updates: Partial<CanvasNode> = {};

            if (eventType === 'tool_start' || eventType === 'step' || eventType === 'iteration_start') status = 'running';
            else if (eventType === 'tool_end' || eventType === 'done' || eventType === 'iteration_end') status = 'complete';
            else if (eventType === 'error') status = 'error';

            // Handle quality score events
            const qualityEvents = events.filter((e) => (e as any).type === 'quality_score' && (e as any).quality_score !== undefined);
            if (qualityEvents.length > 0) {
                const latestQuality = qualityEvents[qualityEvents.length - 1];
                updates.qualityScore = (latestQuality as any).quality_score;
            }

            // Handle supervisor decision events
            if (node.type === 'supervisor') {
                const decisionEvents = events.filter((e) => (e as any).type === 'loop_back' || (e as any).decision);
                if (decisionEvents.length > 0) {
                    const lastDecision = decisionEvents[decisionEvents.length - 1];
                    if ((lastDecision as any).type === 'loop_back') {
                        updates.decisionType = 'LOOP_BACK';
                        status = 'complete';
                    }
                }
            }

            // Handle gate pass/fail based on quality vs threshold
            if (node.type === 'gate') {
                const threshold = get().qualityThreshold;
                if (updates.qualityScore !== undefined) {
                    status = updates.qualityScore >= threshold ? 'complete' : 'error';
                }
            }

            // Handle loop iteration counting - correlate by loop_id when available
            if (node.type === 'loop') {
                const loopId = node.loopConfig?.loop_id;
                let iterationEvents: Array<Record<string, unknown>> = [];
                if (loopId) {
                    // Filter by loop_id for multi-loop support
                    iterationEvents = events.filter(
                        (e) => (e as any).type === 'iteration_start' && (e as any).loop_id === loopId,
                    );
                } else {
                    // Fallback: all iteration events (legacy single-loop)
                    iterationEvents = events.filter((e) => (e as any).type === 'iteration_start');
                }
                if (iterationEvents.length > 0) {
                    status = 'running';
                }
            }

            return { ...node, status, ...updates };
        });

        // Update edges for loop-back detection
        const edges = get().edges.map((edge) => {
            const loopBackEvents = events.filter((e) => (e as any).type === 'loop_back');
            if (loopBackEvents.length > 0) {
                return { ...edge, status: 'loop-back' as const, animated: true };
            }
            return edge;
        });

        set({ nodes, edges });
    },

    // ── UI ─────────────────────────────────────────────────────────────

    setSelectedNode: (id) => set({ selectedNodeId: id }),
    setDragOverStage: (stageKey) => set({ dragOverStage: stageKey }),
    setLastError: (error) => set({ lastError: error }),
    setQualityThreshold: (value) => set({ qualityThreshold: value }),
    setMaxIterations: (value) => set({ maxIterations: value }),

    // ── Tier 2: Navigation ─────────────────────────────────────────────

    setZoom: (zoom) => set({ zoom }),
    setPan: (pan) => set({ pan }),
    resetView: () => set({ zoom: 1, pan: { x: 0, y: 0 } }),

    // ── Tier 2: Undo/Redo ──────────────────────────────────────────────

    pushHistory: () => {
        const { nodes, edges, history, historyIndex } = get();
        // Truncate any future history if we've undone
        const newHistory = history.slice(0, historyIndex + 1);
        newHistory.push({ nodes: [...nodes], edges: [...edges] });
        // Limit history depth
        if (newHistory.length > 50) newHistory.shift();
        set({ history: newHistory, historyIndex: newHistory.length - 1 });
    },

    undo: () => {
        const { history, historyIndex } = get();
        if (historyIndex <= 0) return;
        const previous = history[historyIndex - 1];
        set({ nodes: previous.nodes, edges: previous.edges, historyIndex: historyIndex - 1 });
    },

    redo: () => {
        const { history, historyIndex } = get();
        if (historyIndex >= history.length - 1) return;
        const next = history[historyIndex + 1];
        set({ nodes: next.nodes, edges: next.edges, historyIndex: historyIndex + 1 });
    },

    get canUndo() {
        return get().historyIndex > 0;
    },

    get canRedo() {
        return get().historyIndex < get().history.length - 1;
    },

    // ── Tier 2: Multi-select ───────────────────────────────────────────

    toggleNodeSelection: (id) => {
        const { selectedNodeIds } = get();
        const isSelected = selectedNodeIds.includes(id);
        set({
            selectedNodeIds: isSelected
                ? selectedNodeIds.filter((nid) => nid !== id)
                : [...selectedNodeIds, id],
            selectedNodeId: id,
        });
    },

    clearSelection: () => set({ selectedNodeIds: [], selectedNodeId: null }),

    // ── Tier 2: Grid ───────────────────────────────────────────────────

    setShowGrid: (show) => set({ showGrid: show }),
    setSnapToGrid: (snap) => set({ snapToGrid: snap }),
    setGridSize: (size) => set({ gridSize: size }),
}));
