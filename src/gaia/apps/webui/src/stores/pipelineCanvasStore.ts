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
} from '../types';
import * as canvasApi from '../services/pipelineCanvas';
import { usePipelineStore } from './pipelineStore';
import { log } from '../utils/logger';

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

    // Actions - canvas data
    setNodes: (nodes: CanvasNode[]) => void;
    setEdges: (edges: CanvasEdge[]) => void;
    addAgentToStage: (agent: PaletteDragData, stageKey: string) => void;
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
            id: `agent-${agent.agentId}-${Date.now()}`,
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
        // Build default canvas with stage nodes only
        const nodes: CanvasNode[] = PIPELINE_STAGES.map((stage) => buildStageNode(stage, PIPELINE_STAGES.indexOf(stage)));
        const edges: CanvasEdge[] = [];
        for (let i = 0; i < PIPELINE_STAGES.length - 1; i++) {
            edges.push(buildEdge(`stage-${PIPELINE_STAGES[i].key}`, `stage-${PIPELINE_STAGES[i + 1].key}`));
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

            const canvasExport = {
                name,
                description,
                quality_threshold: get().qualityThreshold,
                max_iterations: get().maxIterations,
                agent_categories: agentCategories,
                routing_rules: routingRules,
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

            const canvasExport = {
                name,
                description: `Pipeline canvas: ${agentNodes.length} agents across ${Object.keys(agentCategories).length} categories`,
                quality_threshold: get().qualityThreshold,
                max_iterations: get().maxIterations,
                agent_categories: agentCategories,
                routing_rules: [],
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
            if (eventType === 'tool_start' || eventType === 'step') status = 'running';
            else if (eventType === 'tool_end' || eventType === 'done') status = 'complete';
            else if (eventType === 'error') status = 'error';

            return { ...node, status };
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
}));
