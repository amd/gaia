// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * PipelineCanvas - Visual drag-and-drop pipeline builder.
 *
 * Provides a canvas where users can:
 * - Drag agents from the palette onto pipeline stage zones
 * - See agent execution status during pipeline runs (live overlay)
 * - Save/load canvas state as pipeline templates
 * - Run the pipeline directly from the canvas
 */

import { memo, useEffect, useCallback, useRef, useState } from 'react';
import { Play, Save, Trash2, Download, Upload, Loader2, AlertTriangle, Layers, ZoomIn, ZoomOut, Grid, Undo, Redo, Minimize2 } from 'lucide-react';
import { usePipelineCanvasStore, PIPELINE_STAGES } from '../../stores/pipelineCanvasStore';
import { usePipelineStore } from '../../stores/pipelineStore';
import { useTemplateStore } from '../../stores/templateStore';
import { useChatStore } from '../../stores/chatStore';
import { AgentPalette } from './AgentPalette';
import { StageZone } from './StageZone';
import './PipelineCanvas.css';

function PipelineCanvasInner() {
    const {
        nodes,
        edges,
        templateName,
        qualityThreshold,
        maxIterations,
        agents,
        resetCanvas,
        loadTemplateAsCanvas,
        saveCanvasAsTemplate,
        applyExecutionState,
        setLastError,
        setQualityThreshold,
        setMaxIterations,
        lastError,
        isSaving,
        zoom,
        pan,
        showGrid,
        snapToGrid,
        setZoom,
        setPan,
        resetView,
        pushHistory,
        undo,
        redo,
        clearSelection,
        setShowGrid,
        setSnapToGrid,
    } = usePipelineCanvasStore((s) => ({
        nodes: s.nodes,
        edges: s.edges,
        templateName: s.templateName,
        qualityThreshold: s.qualityThreshold,
        maxIterations: s.maxIterations,
        agents: s.agents,
        resetCanvas: s.resetCanvas,
        loadTemplateAsCanvas: s.loadTemplateAsCanvas,
        saveCanvasAsTemplate: s.saveCanvasAsTemplate,
        applyExecutionState: s.applyExecutionState,
        setLastError: s.setLastError,
        setQualityThreshold: s.setQualityThreshold,
        setMaxIterations: s.setMaxIterations,
        lastError: s.lastError,
        isSaving: s.isSaving,
        zoom: s.zoom,
        pan: s.pan,
        showGrid: s.showGrid,
        snapToGrid: s.snapToGrid,
        setZoom: s.setZoom,
        setPan: s.setPan,
        resetView: s.resetView,
        pushHistory: s.pushHistory,
        undo: s.undo,
        redo: s.redo,
        clearSelection: s.clearSelection,
        setShowGrid: s.setShowGrid,
        setSnapToGrid: s.setSnapToGrid,
    }));

    const { templates, fetchTemplates } = useTemplateStore((s) => ({
        templates: s.templates,
        fetchTemplates: s.fetchTemplates,
    }));

    const sessions = useChatStore((s) => s.sessions);
    const currentSessionId = useChatStore((s) => s.currentSessionId);
    const { runPipeline, isRunning, activeExecution } = usePipelineStore((s) => ({
        runPipeline: s.runPipeline,
        isRunning: s.isRunning,
        activeExecution: s.activeExecution,
    }));

    const [sessionId, setSessionId] = useState(currentSessionId || '');
    const [saveName, setSaveName] = useState('');
    const [showSaveDialog, setShowSaveDialog] = useState(false);
    const svgRef = useRef<SVGSVGElement>(null);
    const canvasRef = useRef<HTMLDivElement>(null);
    const isPanning = useRef(false);
    const panStart = useRef({ x: 0, y: 0 });

    // Zoom on wheel
    const handleWheel = useCallback((e: React.WheelEvent) => {
        e.preventDefault();
        const delta = e.deltaY > 0 ? -0.1 : 0.1;
        const newZoom = Math.max(0.25, Math.min(3, zoom + delta));
        setZoom(newZoom);
    }, [zoom, setZoom]);

    // Pan on mouse drag
    const handleMouseDown = useCallback((e: React.MouseEvent) => {
        if (e.button === 0 && e.target === canvasRef.current) {
            isPanning.current = true;
            panStart.current = { x: e.clientX - pan.x, y: e.clientY - pan.y };
        }
    }, [pan]);

    const handleMouseMove = useCallback((e: React.MouseEvent) => {
        if (isPanning.current) {
            setPan({ x: e.clientX - panStart.current.x, y: e.clientY - panStart.current.y });
        }
    }, []);

    const handleMouseUp = useCallback(() => {
        isPanning.current = false;
    }, []);

    // Sync session ID
    useEffect(() => {
        if (currentSessionId) setSessionId(currentSessionId);
    }, [currentSessionId]);

    // Load templates list
    useEffect(() => {
        fetchTemplates();
    }, [fetchTemplates]);

    // Apply execution state from pipeline store
    useEffect(() => {
        if (activeExecution && activeExecution.events.length > 0) {
            applyExecutionState(activeExecution.events as unknown as Array<Record<string, unknown>>);
        }
    }, [activeExecution?.events?.length, applyExecutionState]);

    // Initialize canvas with stage nodes
    useEffect(() => {
        if (nodes.length === 0) {
            resetCanvas();
        }
    }, [nodes.length, resetCanvas]);

    // Compute edge SVG paths
    const computeEdgePath = useCallback((edge: typeof edges[number]): string => {
        const source = nodes.find((n) => n.id === edge.source);
        const target = nodes.find((n) => n.id === edge.target);
        if (!source || !target) return '';

        const canvasEl = canvasRef.current;
        if (!canvasEl) return '';

        // For stage-to-stage edges: from bottom of source to top of target
        const startX = source.position.x + 20;
        const startY = source.position.y + 50;
        const endX = target.position.x + 20;
        const endY = target.position.y;

        const midY = (startY + endY) / 2;
        return `M ${startX} ${startY} C ${startX} ${midY}, ${endX} ${midY}, ${endX} ${endY}`;
    }, [nodes]);

    // Group agent nodes by stage
    const stageAgentNodes = PIPELINE_STAGES.map((stage) => ({
        stage,
        agents: nodes.filter((n) => n.type === 'agent' && n.assignedStage === stage.key),
    }));

    // Run pipeline from canvas
    const handleRun = useCallback(() => {
        if (!sessionId || isRunning) return;

        // Collect task description from template or user
        const taskDescription = templateName
            ? `Execute pipeline using template: ${templateName}`
            : `Execute canvas pipeline (${nodes.filter((n) => n.type === 'agent').length} agents)`;

        runPipeline({
            session_id: sessionId,
            task_description: taskDescription,
            auto_spawn: true,
            template_name: templateName || undefined,
            stream: true,
        });
    }, [sessionId, isRunning, templateName, nodes, runPipeline]);

    // Save canvas as template
    const handleSave = useCallback(() => {
        if (!saveName.trim()) return;
        saveCanvasAsTemplate(saveName.trim(), `Canvas pipeline: ${nodes.filter((n) => n.type === 'agent').length} agents`);
        setShowSaveDialog(false);
        setSaveName('');
    }, [saveName, nodes, saveCanvasAsTemplate]);

    // Load template onto canvas
    const handleLoadTemplate = useCallback((name: string) => {
        loadTemplateAsCanvas(name);
    }, [loadTemplateAsCanvas]);

    // Count agents per category
    const agentCategoryCounts = nodes
        .filter((n) => n.type === 'agent')
        .reduce((acc, n) => {
            const cat = n.category || 'other';
            acc[cat] = (acc[cat] || 0) + 1;
            return acc;
        }, {} as Record<string, number>);

    const supervisorCount = nodes.filter((n) => n.type === 'supervisor').length;
    const gateCount = nodes.filter((n) => n.type === 'gate').length;
    const loopCount = nodes.filter((n) => n.type === 'loop').length;

    return (
        <div className="pipeline-canvas">
            {/* Toolbar */}
            <div className="pc-toolbar">
                <div className="pc-toolbar-left">
                    <h2 className="pc-toolbar-title">
                        <Layers size={18} />
                        Pipeline Canvas
                    </h2>
                    {templateName && (
                        <span className="pc-toolbar-template">
                            Loaded: {templateName}
                        </span>
                    )}
                    <span className="pc-toolbar-stats">
                        {nodes.filter((n) => n.type === 'agent').length} agents
                        {supervisorCount > 0 && ` · ${supervisorCount} supervisor${supervisorCount !== 1 ? 's' : ''}`}
                        {gateCount > 0 && ` · ${gateCount} gate${gateCount !== 1 ? 's' : ''}`}
                        {loopCount > 0 && ` · ${loopCount} loop${loopCount !== 1 ? 's' : ''}`}
                        {Object.keys(agentCategoryCounts).length > 0 &&
                            ` in ${Object.keys(agentCategoryCounts).length} categories`}
                    </span>
                </div>

                <div className="pc-toolbar-right">
                    {/* Session selector */}
                    <select
                        className="pc-session-select"
                        value={sessionId}
                        onChange={(e) => setSessionId(e.target.value)}
                        disabled={isRunning}
                    >
                        <option value="">-- Session --</option>
                        {sessions.map((s) => (
                            <option key={s.id} value={s.id}>
                                {s.title || s.id.slice(0, 8)}
                            </option>
                        ))}
                    </select>

                    {/* Load template dropdown */}
                    <div className="pc-dropdown">
                        <button
                            className="pc-btn pc-btn-secondary"
                            title="Load template"
                            disabled={isRunning}
                        >
                            <Upload size={14} />
                            Load
                        </button>
                        {templates.length > 0 && (
                            <div className="pc-dropdown-menu">
                                {templates.map((t) => (
                                    <button
                                        key={t.name}
                                        className="pc-dropdown-item"
                                        onClick={() => handleLoadTemplate(t.name)}
                                    >
                                        {t.name}
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Save template */}
                    <button
                        className="pc-btn pc-btn-secondary"
                        onClick={() => {
                            setSaveName(templateName || '');
                            setShowSaveDialog(true);
                        }}
                        disabled={isSaving}
                        title="Save as template"
                    >
                        {isSaving ? <Loader2 size={14} className="spin" /> : <Save size={14} />}
                        Save
                    </button>

                    {/* Reset canvas */}
                    <button
                        className="pc-btn pc-btn-secondary"
                        onClick={() => {
                            resetCanvas();
                            setSaveName('');
                        }}
                        title="Reset canvas"
                    >
                        <Trash2 size={14} />
                    </button>

                    {/* Tier 2: Undo/Redo */}
                    <button
                        className="pc-btn pc-btn-secondary"
                        onClick={undo}
                        title="Undo (Ctrl+Z)"
                    >
                        <Undo size={14} />
                    </button>
                    <button
                        className="pc-btn pc-btn-secondary"
                        onClick={redo}
                        title="Redo (Ctrl+Y)"
                    >
                        <Redo size={14} />
                    </button>

                    {/* Tier 2: Zoom controls */}
                    <div className="pc-zoom-controls">
                        <button
                            className="pc-btn pc-btn-secondary pc-zoom-btn"
                            onClick={() => setZoom(Math.max(0.25, zoom - 0.1))}
                            title="Zoom out"
                        >
                            <ZoomOut size={14} />
                        </button>
                        <span className="pc-zoom-level">{(zoom * 100).toFixed(0)}%</span>
                        <button
                            className="pc-btn pc-btn-secondary pc-zoom-btn"
                            onClick={() => setZoom(Math.min(3, zoom + 0.1))}
                            title="Zoom in"
                        >
                            <ZoomIn size={14} />
                        </button>
                        <button
                            className="pc-btn pc-btn-secondary pc-zoom-btn"
                            onClick={resetView}
                            title="Fit to view"
                        >
                            <Minimize2 size={14} />
                        </button>
                    </div>

                    {/* Tier 2: Grid toggle */}
                    <button
                        className={`pc-btn pc-btn-secondary ${showGrid ? 'pc-btn-active' : ''}`}
                        onClick={() => setShowGrid(!showGrid)}
                        title="Toggle grid"
                    >
                        <Grid size={14} />
                    </button>

                    {/* Run pipeline */}
                    <button
                        className="pc-btn pc-btn-primary"
                        onClick={handleRun}
                        disabled={!sessionId || isRunning}
                    >
                        {isRunning ? (
                            <>
                                <Loader2 size={14} className="spin" />
                                Running
                            </>
                        ) : (
                            <>
                                <Play size={14} />
                                Run Pipeline
                            </>
                        )}
                    </button>
                </div>
            </div>

            {/* Error display */}
            {lastError && (
                <div className="pc-error-bar">
                    <AlertTriangle size={14} />
                    <span>{lastError}</span>
                    <button onClick={() => setLastError(null)}>Dismiss</button>
                </div>
            )}

            {/* Main layout: palette + canvas */}
            <div className="pc-layout">
                {/* Agent Palette (left sidebar) */}
                <div className="pc-palette-sidebar">
                    <div className="pc-palette-sidebar-header">
                        <span>Agent Palette</span>
                    </div>
                    <AgentPalette />
                </div>

                {/* Canvas area */}
                <div
                    className={`pc-canvas-area${showGrid ? ' pc-canvas-grid' : ''}`}
                    ref={canvasRef}
                    onWheel={handleWheel}
                    onMouseDown={handleMouseDown}
                    onMouseMove={handleMouseMove}
                    onMouseUp={handleMouseUp}
                    onMouseLeave={handleMouseUp}
                >
                    {/* Zoom/pan transform wrapper */}
                    <div
                        className="pc-canvas-content"
                        style={{
                            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
                            transformOrigin: 'top left',
                        }}
                    >
                    {/* SVG overlay for edges */}
                    <svg ref={svgRef} className="pc-canvas-edges">
                        {edges.map((edge) => {
                            const path = computeEdgePath(edge);
                            if (!path) return null;
                            return (
                                <g key={edge.id}>
                                    <path
                                        d={path}
                                        className={`pc-edge${edge.animated ? ' pc-edge-animated' : ''}${edge.status === 'loop-back' ? ' pc-edge-loop' : ''}`}
                                        fill="none"
                                        stroke={
                                            edge.status === 'loop-back'
                                                ? '#ef4444'
                                                : edge.status === 'active'
                                                ? '#3b82f6'
                                                : edge.status === 'complete'
                                                ? '#10b981'
                                                : '#4b5563'
                                        }
                                        strokeWidth={edge.status === 'loop-back' || edge.status === 'active' ? 2.5 : 1.5}
                                        strokeDasharray={edge.animated ? '6 4' : edge.status === 'loop-back' ? '4 4' : 'none'}
                                        markerEnd="url(#arrowhead)"
                                    />
                                    {edge.label && (
                                        <text
                                            x={(edge.source ? 100 : 100)}
                                            y={100}
                                            className="pc-edge-label"
                                            fill="#9ca3af"
                                            fontSize={11}
                                        >
                                            {edge.label}
                                        </text>
                                    )}
                                </g>
                            );
                        })}
                        <defs>
                            <marker
                                id="arrowhead"
                                markerWidth="10"
                                markerHeight="7"
                                refX="10"
                                refY="3.5"
                                orient="auto"
                            >
                                <polygon
                                    points="0 0, 10 3.5, 0 7"
                                    fill="#4b5563"
                                />
                            </marker>
                            <marker
                                id="arrowhead-loop"
                                markerWidth="10"
                                markerHeight="7"
                                refX="10"
                                refY="3.5"
                                orient="auto"
                            >
                                <polygon
                                    points="0 0, 10 3.5, 0 7"
                                    fill="#ef4444"
                                />
                            </marker>
                        </defs>
                    </svg>

                    {/* Stage zones */}
                    <div className="pc-stages">
                        {stageAgentNodes.map(({ stage, agents: stageAgents }, index) => (
                            <StageZone
                                key={stage.key}
                                stage={stage}
                                agentNodes={stageAgents}
                                index={index}
                            />
                        ))}
                    </div>
                    </div> {/* end pc-canvas-content */}
                </div>
            </div>

            {/* Save dialog */}
            {showSaveDialog && (
                <div className="pc-modal-overlay" onClick={() => setShowSaveDialog(false)}>
                    <div className="pc-modal" onClick={(e) => e.stopPropagation()}>
                        <h3>Save as Pipeline Template</h3>
                        <div className="pc-modal-field">
                            <label htmlFor="pc-save-name">Template Name</label>
                            <input
                                id="pc-save-name"
                                type="text"
                                value={saveName}
                                onChange={(e) => setSaveName(e.target.value)}
                                placeholder="my-pipeline"
                                autoFocus
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter') handleSave();
                                }}
                            />
                        </div>
                        <div className="pc-modal-actions">
                            <button
                                className="pc-btn pc-btn-secondary"
                                onClick={() => setShowSaveDialog(false)}
                            >
                                Cancel
                            </button>
                            <button
                                className="pc-btn pc-btn-primary"
                                onClick={handleSave}
                                disabled={!saveName.trim() || isSaving}
                            >
                                {isSaving ? <Loader2 size={14} className="spin" /> : <Save size={14} />}
                                Save
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Pipeline settings bar */}
            <div className="pc-settings-bar">
                <div className="pc-setting">
                    <label>Quality Threshold</label>
                    <input
                        type="range"
                        min={0.5}
                        max={1}
                        step={0.05}
                        value={qualityThreshold}
                        onChange={(e) => {
                            setQualityThreshold(Number(e.target.value));
                        }}
                    />
                    <span className="pc-setting-value">{(qualityThreshold * 100).toFixed(0)}%</span>
                </div>
                <div className="pc-setting">
                    <label>Max Iterations</label>
                    <input
                        type="number"
                        min={1}
                        max={10}
                        value={maxIterations}
                        onChange={(e) => {
                            setMaxIterations(Number(e.target.value));
                        }}
                        style={{ width: 60 }}
                    />
                </div>
            </div>
        </div>
    );
}

export const PipelineCanvas = memo(PipelineCanvasInner);
