// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * PipelineRunner - Run pipelines and view real-time SSE streaming progress.
 *
 * Provides a form to configure and execute pipeline runs, with a live event
 * log showing each SSE event (status, step, thinking, tool calls, done, error).
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import {
  Play,
  Loader2,
  Square,
  Trash2,
  Terminal,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import { usePipelineStore } from '../../stores/pipelineStore';
import { useChatStore } from '../../stores/chatStore';
import { useTemplateStore } from '../../stores/templateStore';
import './PipelineRunner.css';

const EVENT_ICONS: Record<string, React.ReactNode> = {
  status: <Terminal size={14} />,
  step: <ChevronRight size={14} />,
  thinking: <AlertTriangle size={14} />,
  tool_start: <ChevronRight size={14} />,
  tool_end: <CheckCircle2 size={14} />,
  tool_result: <Terminal size={14} />,
  done: <CheckCircle2 size={14} />,
  error: <XCircle size={14} />,
};

const EVENT_COLORS: Record<string, string> = {
  status: 'event-status',
  step: 'event-step',
  thinking: 'event-thinking',
  tool_start: 'event-tool',
  tool_end: 'event-tool-end',
  tool_result: 'event-result',
  done: 'event-done',
  error: 'event-error',
};

const STATUS_COLORS: Record<string, string> = {
  starting: '#8b5cf6',
  running: '#3b82f6',
  completed: '#10b981',
  failed: '#ef4444',
  cancelled: '#f59e0b',
};

export function PipelineRunner({ onViewChange }: { onViewChange?: (view: string) => void }) {
  const sessions = useChatStore((s) => s.sessions);
  const currentSessionId = useChatStore((s) => s.currentSessionId);

  const {
    executions,
    activeExecution,
    isRunning,
    lastError,
    runPipeline,
    cancelPipeline,
    clearExecution,
    clearAllExecutions,
    setLastError,
  } = usePipelineStore();

  const { templates, fetchTemplates } = useTemplateStore((s) => ({
    templates: s.templates,
    fetchTemplates: s.fetchTemplates,
  }));

  const [taskDescription, setTaskDescription] = useState('');
  const [autoSpawn, setAutoSpawn] = useState(true);
  const [templateName, setTemplateName] = useState('');
  const [sessionId, setSessionId] = useState(currentSessionId || '');
  // Use array for collapsed event keys (immutable, React-detectable)
  const [collapsedEvents, setCollapsedEvents] = useState<string[]>([]);

  const eventLogRef = useRef<HTMLDivElement>(null);

  // Fetch templates on mount
  useEffect(() => {
    fetchTemplates();
  }, [fetchTemplates]);

  // Sync session ID when current session changes
  useEffect(() => {
    if (currentSessionId) {
      setSessionId(currentSessionId);
    }
  }, [currentSessionId]);

  // Auto-scroll event log
  useEffect(() => {
    if (eventLogRef.current && activeExecution) {
      eventLogRef.current.scrollTop = eventLogRef.current.scrollHeight;
    }
  }, [activeExecution?.events.length]);

  const handleRun = useCallback(() => {
    if (!taskDescription.trim() || !sessionId || isRunning) return;

    const result = runPipeline({
      session_id: sessionId,
      task_description: taskDescription,
      auto_spawn: autoSpawn,
      template_name: templateName || undefined,
      stream: true,
    });

    if (result) {
      setTaskDescription('');
    }
  }, [taskDescription, sessionId, isRunning, autoSpawn, templateName, runPipeline]);

  const handleCancel = useCallback(() => {
    cancelPipeline();
  }, [cancelPipeline]);

  const toggleEventCollapse = useCallback((eventId: string) => {
    setCollapsedEvents((prev) =>
      prev.includes(eventId)
        ? prev.filter((id) => id !== eventId)
        : [...prev, eventId]
    );
  }, []);

  const formatTime = (ts: number) => {
    const d = new Date(ts);
    return d.toLocaleTimeString();
  };

  const stageProgress = () => {
    if (!activeExecution) return { current: 0, total: 5 };
    const stageOrder = [
      'domain_analysis',
      'workflow_modeling',
      'loom_building',
      'gap_detection',
      'pipeline_execution',
    ];
    const idx = stageOrder.indexOf(activeExecution.currentStage || 'pipeline_execution');
    return { current: Math.max(0, idx) + 1, total: 5 };
  };

  const progress = stageProgress();

  return (
    <div className="pipeline-runner">
      {/* Header */}
      <div className="pr-header">
        <h1>Pipeline Execution</h1>
        <p>Run the 5-stage autonomous agent spawning pipeline</p>
      </div>

      {/* Configuration Form */}
      <div className="pr-form-card">
        <h2>Configure Pipeline</h2>

        <div className="pr-field">
          <label htmlFor="pr-task">Task Description</label>
          <textarea
            id="pr-task"
            placeholder="Describe what you want the pipeline to analyze or build..."
            value={taskDescription}
            onChange={(e) => setTaskDescription(e.target.value)}
            disabled={isRunning}
            rows={3}
          />
        </div>

        <div className="pr-field">
          <label htmlFor="pr-session">Session</label>
          <select
            id="pr-session"
            value={sessionId}
            onChange={(e) => setSessionId(e.target.value)}
            disabled={isRunning}
          >
            <option value="">-- Select session --</option>
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title || s.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </div>

        <div className="pr-field pr-field-row">
          <label className="pr-checkbox-label">
            <input
              type="checkbox"
              checked={autoSpawn}
              onChange={(e) => setAutoSpawn(e.target.checked)}
              disabled={isRunning}
            />
            Auto-spawn agents for gaps
          </label>

          <div>
            <label htmlFor="pr-template" style={{ marginRight: 8 }}>
              Template (optional)
            </label>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <select
                id="pr-template"
                value={templateName}
                onChange={(e) => setTemplateName(e.target.value)}
                disabled={isRunning}
                style={{ maxWidth: 200 }}
              >
                <option value="">Default</option>
                {templates.map((t) => (
                  <option key={t.name} value={t.name}>
                    {t.name}
                  </option>
                ))}
              </select>
              <button
                className="pr-btn pr-btn-secondary"
                onClick={() => onViewChange?.('templates')}
                style={{ padding: '4px 8px', fontSize: 12, height: 32 }}
                title="Manage Templates"
              >
                Manage Templates
              </button>
            </div>
          </div>
        </div>

        <div className="pr-actions">
          <button
            className="pr-btn pr-btn-primary"
            onClick={handleRun}
            disabled={!taskDescription.trim() || !sessionId || isRunning}
          >
            {isRunning ? (
              <>
                <Loader2 size={16} className="spin" />
                Running...
              </>
            ) : (
              <>
                <Play size={16} />
                Run Pipeline
              </>
            )}
          </button>

          {isRunning && (
            <button className="pr-btn pr-btn-danger" onClick={handleCancel}>
              <Square size={14} />
              Cancel
            </button>
          )}

          {executions.length > 0 && !isRunning && (
            <button
              className="pr-btn pr-btn-secondary"
              onClick={clearAllExecutions}
            >
              <Trash2 size={14} />
              Clear History
            </button>
          )}
        </div>

        {lastError && (
          <div className="pr-error" role="alert">
            <AlertTriangle size={16} />
            <span>{lastError}</span>
            <button onClick={() => setLastError(null)}>Dismiss</button>
          </div>
        )}
      </div>

      {/* Stage Progress Indicator */}
      {activeExecution && (
        <div className="pr-stages">
          {['Domain Analysis', 'Workflow Modeling', 'Loom Building', 'Gap Detection', 'Execution'].map(
            (stage, i) => {
              const stageKeys = [
                'domain_analysis',
                'workflow_modeling',
                'loom_building',
                'gap_detection',
                'pipeline_execution',
              ];
              const isActive = activeExecution.currentStage === stageKeys[i];
              const isComplete =
                activeExecution.status === 'completed' ||
                progress.current > i + 1;

              return (
                <div
                  key={stage}
                  className={`pr-stage ${isActive ? 'active' : ''} ${isComplete ? 'complete' : ''}`}
                >
                  <div className="pr-stage-dot">
                    {isComplete ? <CheckCircle2 size={14} /> : i + 1}
                  </div>
                  <span className="pr-stage-label">{stage}</span>
                </div>
              );
            }
          )}
        </div>
      )}

      {/* Execution History */}
      {executions.length === 0 && (
        <div className="pr-empty">
          <Terminal size={48} strokeWidth={1} />
          <h3>No pipeline runs</h3>
          <p>Configure and run a pipeline to see results here.</p>
        </div>
      )}

      {executions.map((exec) => {
        const isActive = exec.id === activeExecution?.id;
        const statusColor = STATUS_COLORS[exec.status] || '#6b7280';

        return (
          <div key={exec.id} className={`pr-execution ${isActive ? 'active' : ''}`}>
            <div className="pr-execution-header">
              <div className="pr-execution-title">
                <span
                  className="pr-status-dot"
                  style={{ backgroundColor: statusColor }}
                />
                <span className="pr-execution-id">
                  Run {exec.id.slice(0, 8)}
                </span>
                <span className="pr-execution-status">
                  {exec.status}
                </span>
              </div>
              <div className="pr-execution-meta">
                <span>{formatTime(exec.startTime)}</span>
                {exec.endTime && <span>{formatTime(exec.endTime)}</span>}
                {!isActive && (
                  <button
                    className="pr-btn-icon"
                    onClick={() => clearExecution(exec.id)}
                    title="Clear"
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
            </div>

            <div className="pr-execution-task">{exec.taskDescription}</div>

            {/* Event Log */}
            {exec.events.length > 0 && (
              <div className="pr-event-log" ref={isActive ? eventLogRef : undefined}>
                <div className="pr-event-log-header">
                  <span>Event Log ({exec.events.length} events)</span>
                </div>
                <div className="pr-event-list">
                  {exec.events.map((event, i) => {
                    const eventType =
                      'type' in event ? (event as any).type : 'status';
                    const eventKey = `${exec.id}-${i}`;
                    const isCollapsed = collapsedEvents.includes(eventKey);
                    const icon = EVENT_ICONS[eventType] || <Terminal size={14} />;
                    const colorClass = EVENT_COLORS[eventType] || 'event-status';
                    const content =
                      'content' in event ? (event as any).content : '';
                    const message =
                      'message' in event ? (event as any).message : '';
                    const toolName =
                      'tool' in event ? (event as any).tool : '';

                    return (
                      <div
                        key={eventKey}
                        className={`pr-event ${colorClass}`}
                      >
                        <div
                          className="pr-event-summary"
                          onClick={() => toggleEventCollapse(eventKey)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              toggleEventCollapse(eventKey);
                            }
                          }}
                          role="button"
                          tabIndex={0}
                          aria-expanded={!isCollapsed}
                        >
                          <button className="pr-event-toggle">
                            {isCollapsed ? (
                              <ChevronRight size={12} />
                            ) : (
                              <ChevronDown size={12} />
                            )}
                          </button>
                          {icon}
                          <span className="pr-event-type">{eventType}</span>
                          {toolName && (
                            <span className="pr-event-tool">{toolName}</span>
                          )}
                          <span className="pr-event-text">
                            {message || content}
                          </span>
                        </div>
                        {!isCollapsed && content && content !== message && (
                          <pre className="pr-event-detail">
                            {typeof content === 'string'
                              ? content
                              : JSON.stringify(content, null, 2)}
                          </pre>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
