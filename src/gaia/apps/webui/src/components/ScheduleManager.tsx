// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, useCallback, useRef } from 'react';
import { X, Plus, Clock, Play, Pause, Trash2, ChevronDown, ChevronUp, MessageSquare } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import { useScheduleStore } from '../stores/scheduleStore';
import { log } from '../utils/logger';
import type { Schedule, ScheduleResult, ParsedSchedule } from '../types';
import { parseScheduleInput } from '../services/api';
import './ScheduleManager.css';

/** Format an interval in seconds to a human-readable string. */
function formatInterval(seconds: number): string {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
    return `${Math.round(seconds / 86400)}d`;
}

/** Format an ISO date string to a relative or short time. */
function formatTime(iso: string | null, now?: Date): string {
    if (!iso) return 'never';
    const d = new Date(iso);
    const current = now || new Date();
    const diffMs = current.getTime() - d.getTime();
    const diffSecs = Math.floor(diffMs / 1000);

    if (diffSecs < 0) {
        // Future time
        const futureSecs = Math.abs(diffSecs);
        if (futureSecs < 60) return `in ${futureSecs}s`;
        const futureMins = Math.floor(futureSecs / 60);
        if (futureMins < 60) return `in ${futureMins}m`;
        const hrs = Math.floor(futureMins / 60);
        if (hrs < 24) return `in ${hrs}h`;
        return `in ${Math.floor(hrs / 24)}d`;
    }
    if (diffSecs < 60) return 'just now';
    const mins = Math.floor(diffSecs / 60);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/** Returns true if the ISO timestamp is less than 60 seconds in the future. */
function isCountdownRange(iso: string | null): boolean {
    if (!iso) return false;
    const diffMs = new Date(iso).getTime() - Date.now();
    return diffMs > 0 && diffMs < 60000;
}

/** Format "HH:MM" to "H:MM AM/PM". */
function formatTimeOfDay(time: string): string {
    const [h, m] = time.split(':').map(Number);
    const ampm = h >= 12 ? 'PM' : 'AM';
    const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
    return `${h12}:${m.toString().padStart(2, '0')} ${ampm}`;
}

/** Format hour number to "H AM/PM". */
function formatHour(h: number): string {
    const ampm = h >= 12 ? 'PM' : 'AM';
    const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
    return `${h12} ${ampm}`;
}

/** Format days_of_week array to human string. */
function formatDays(days: number[]): string {
    const names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    if (days.length === 5 && !days.includes(5) && !days.includes(6)) return 'Weekdays';
    if (days.length === 2 && days.includes(5) && days.includes(6)) return 'Weekends';
    if (days.length === 7) return 'Every day';
    return days.map(d => names[d]).join(', ');
}

function ScheduleCard({ schedule }: { schedule: Schedule }) {
    const { selectedSchedule, selectSchedule, updateScheduleStatus, deleteSchedule, results, resultsLoading, loadSchedules, loadResults } = useScheduleStore();
    const { setCurrentSession, setShowSchedules } = useChatStore();
    const isSelected = selectedSchedule === schedule.name;
    const [confirmDelete, setConfirmDelete] = useState(false);

    // Live countdown: tick every second when next_run_at is < 60s away
    const [now, setNow] = useState(() => new Date());
    const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

    useEffect(() => {
        const needsTick = schedule.status === 'active' && isCountdownRange(schedule.next_run_at);
        if (needsTick && !tickRef.current) {
            tickRef.current = setInterval(() => {
                setNow(new Date());
                // When countdown reaches 0, refresh schedule list to get updated run info
                if (schedule.next_run_at && new Date(schedule.next_run_at).getTime() <= Date.now()) {
                    loadSchedules();
                }
            }, 1000);
        } else if (!needsTick && tickRef.current) {
            clearInterval(tickRef.current);
            tickRef.current = null;
        }
        return () => {
            if (tickRef.current) {
                clearInterval(tickRef.current);
                tickRef.current = null;
            }
        };
    }, [schedule.status, schedule.next_run_at, loadSchedules]);

    // Auto-refresh results when a new run completes (last_run_at changes)
    const prevRunRef = useRef(schedule.last_run_at);
    useEffect(() => {
        if (isSelected && schedule.last_run_at !== prevRunRef.current) {
            prevRunRef.current = schedule.last_run_at;
            loadResults(schedule.name);
        }
    }, [isSelected, schedule.last_run_at, schedule.name, loadResults]);

    // Flash effect when a run just completed (within last 5 seconds)
    const justRan = schedule.last_run_at && (Date.now() - new Date(schedule.last_run_at).getTime()) < 5000;

    const handleToggleStatus = useCallback((e: React.MouseEvent) => {
        e.stopPropagation();
        const newStatus = schedule.status === 'active' ? 'paused' : 'active';
        log.ui.info(`Schedule "${schedule.name}": ${schedule.status} -> ${newStatus}`);
        updateScheduleStatus(schedule.name, newStatus);
    }, [schedule.name, schedule.status, updateScheduleStatus]);

    const handleDelete = useCallback((e: React.MouseEvent) => {
        e.stopPropagation();
        if (!confirmDelete) {
            setConfirmDelete(true);
            setTimeout(() => setConfirmDelete(false), 3000);
            return;
        }
        log.ui.info(`Deleting schedule "${schedule.name}"`);
        deleteSchedule(schedule.name);
    }, [schedule.name, confirmDelete, deleteSchedule]);

    const handleOpenChat = useCallback((e: React.MouseEvent) => {
        e.stopPropagation();
        if (schedule.session_id) {
            log.ui.info(`Opening chat session for schedule "${schedule.name}"`);
            setCurrentSession(schedule.session_id);
            setShowSchedules(false);
        }
    }, [schedule.session_id, schedule.name, setCurrentSession, setShowSchedules]);

    return (
        <div
            className={`schedule-card ${isSelected ? 'selected' : ''} ${justRan ? 'just-ran' : ''}`}
            onClick={() => selectSchedule(isSelected ? null : schedule.name)}
        >
            <div className="schedule-card-header">
                <span className="schedule-card-name">{schedule.name}</span>
                <span className={`schedule-card-status ${schedule.status}`}>{schedule.status}</span>
            </div>
            <div className="schedule-card-prompt">{schedule.prompt}</div>
            <div className="schedule-card-meta">
                <span><Clock size={11} /> {formatInterval(schedule.interval_seconds)}</span>
                <span>Runs: {schedule.run_count}</span>
                {schedule.error_count > 0 && <span>Errors: {schedule.error_count}</span>}
                <span>Last: {formatTime(schedule.last_run_at)}</span>
                <span>Next: {formatTime(schedule.next_run_at, now)}</span>
            </div>
            <div className="schedule-card-actions">
                {schedule.session_id && (
                    <button className="btn-sm chat" onClick={handleOpenChat} title="Open chat session">
                        <MessageSquare size={11} /> Open Chat
                    </button>
                )}
                <button className="btn-sm" onClick={handleToggleStatus} title={schedule.status === 'active' ? 'Pause' : 'Resume'}>
                    {schedule.status === 'active' ? <><Pause size={11} /> Pause</> : <><Play size={11} /> Resume</>}
                </button>
                <button className={`btn-sm danger`} onClick={handleDelete} title="Delete">
                    <Trash2 size={11} /> {confirmDelete ? 'Confirm?' : 'Delete'}
                </button>
            </div>

            {isSelected && (
                <div className="schedule-results">
                    <h4>Execution History</h4>
                    {resultsLoading ? (
                        <p className="schedule-results-empty">Loading...</p>
                    ) : results.length === 0 ? (
                        <p className="schedule-results-empty">No executions yet</p>
                    ) : (
                        <div className="schedule-results-list">
                            {results.map((r) => (
                                <div key={r.id} className="schedule-result-item">
                                    <div className="schedule-result-time">{formatTime(r.executed_at)}</div>
                                    {r.error ? (
                                        <div className="schedule-result-content schedule-result-error">{r.error}</div>
                                    ) : (
                                        <div className="schedule-result-content">{r.result || '(no output)'}</div>
                                    )}
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

export function ScheduleManager() {
    const { setShowSchedules } = useChatStore();
    const { schedules, loading, error, loadSchedules, createSchedule } = useScheduleStore();

    // Create form state
    const [showCreate, setShowCreate] = useState(false);
    const [nlInput, setNlInput] = useState('');
    const [scheduleName, setScheduleName] = useState('');
    const [schedulePrompt, setSchedulePrompt] = useState('');
    const [parsed, setParsed] = useState<ParsedSchedule | null>(null);
    const [parsing, setParsing] = useState(false);
    const [creating, setCreating] = useState(false);
    const [formError, setFormError] = useState<string | null>(null);

    // Debounced parse of NL input
    const parseTimerRef = useRef<number | null>(null);

    useEffect(() => {
        if (!nlInput.trim()) {
            setParsed(null);
            return;
        }
        if (parseTimerRef.current) clearTimeout(parseTimerRef.current);
        parseTimerRef.current = window.setTimeout(async () => {
            setParsing(true);
            try {
                const result = await parseScheduleInput(nlInput);
                setParsed(result);
            } catch {
                setParsed(null);
            } finally {
                setParsing(false);
            }
        }, 400);
        return () => { if (parseTimerRef.current) clearTimeout(parseTimerRef.current); };
    }, [nlInput]);

    // Load on mount + poll every 5s so we see updates when tasks fire
    const pollRef = useRef<number | null>(null);

    useEffect(() => {
        log.ui.info('Schedule Manager opened');
        loadSchedules();
        pollRef.current = window.setInterval(() => loadSchedules(), 5000);
        return () => {
            if (pollRef.current) clearInterval(pollRef.current);
        };
    }, [loadSchedules]);

    // Allow creation if all 3 fields are filled. parsed?.valid is a bonus
    // (shows preview) but not a hard gate — the backend validates on create.
    const canCreate = nlInput.trim() && scheduleName.trim() && schedulePrompt.trim()
        && (parsed === null || parsed.valid);  // block only if parse explicitly returned invalid

    const handleCreate = useCallback(async () => {
        if (!canCreate) return;
        setCreating(true);
        setFormError(null);
        try {
            // Pass the raw NL input as interval - backend will re-parse it
            await createSchedule(scheduleName.trim(), nlInput.trim(), schedulePrompt.trim());
            setNlInput('');
            setScheduleName('');
            setSchedulePrompt('');
            setParsed(null);
            setShowCreate(false);
            log.ui.info(`Schedule "${scheduleName}" created`);
        } catch (err) {
            const msg = err instanceof Error ? err.message : 'Failed to create schedule';
            setFormError(msg);
        } finally {
            setCreating(false);
        }
    }, [canCreate, scheduleName, nlInput, schedulePrompt, createSchedule]);

    return (
        <div className="modal-overlay" onClick={() => setShowSchedules(false)} role="dialog" aria-modal="true" aria-label="Schedule Manager">
            <div className="modal-panel schedule-modal" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3>Scheduled Tasks</h3>
                    <button className="btn-icon" onClick={() => setShowSchedules(false)} aria-label="Close schedule manager">
                        <X size={18} />
                    </button>
                </div>

                <div className="modal-body">
                    {/* Create new schedule */}
                    <section className="schedule-section">
                        {!showCreate ? (
                            <button className="btn-primary" onClick={() => setShowCreate(true)}>
                                <Plus size={14} /> New Schedule
                            </button>
                        ) : (
                            <div className="schedule-create-form">
                                {/* NL input */}
                                <textarea
                                    className="schedule-nl-input"
                                    placeholder={'Describe your schedule in natural language...\nExamples: "every 30m", "daily at 9pm", "every hour from 8am to 6pm on weekdays"'}
                                    value={nlInput}
                                    onChange={(e) => setNlInput(e.target.value)}
                                    autoFocus
                                    rows={2}
                                />

                                {/* Parsed preview */}
                                {(parsed || parsing) && (
                                    <div className={`schedule-parsed-preview ${parsed?.valid ? 'valid' : 'invalid'}`}>
                                        {parsing ? (
                                            <span className="schedule-parsed-loading">Parsing...</span>
                                        ) : parsed ? (
                                            <>
                                                <div className="schedule-parsed-description">
                                                    {parsed.valid ? parsed.description : 'Could not parse schedule'}
                                                </div>
                                                {parsed.valid && parsed.next_run_at && (
                                                    <div className="schedule-parsed-next">
                                                        Next run: {new Date(parsed.next_run_at).toLocaleString()}
                                                    </div>
                                                )}
                                                {parsed.valid && (
                                                    <div className="schedule-parsed-details">
                                                        {parsed.time_of_day && (
                                                            <span className="schedule-parsed-tag">
                                                                <Clock size={10} /> {formatTimeOfDay(parsed.time_of_day)}
                                                            </span>
                                                        )}
                                                        {parsed.start_hour != null && parsed.end_hour != null && (
                                                            <span className="schedule-parsed-tag">
                                                                <Clock size={10} /> {formatHour(parsed.start_hour)} - {formatHour(parsed.end_hour)}
                                                            </span>
                                                        )}
                                                        {parsed.days_of_week && (
                                                            <span className="schedule-parsed-tag">
                                                                {formatDays(parsed.days_of_week)}
                                                            </span>
                                                        )}
                                                    </div>
                                                )}
                                            </>
                                        ) : null}
                                    </div>
                                )}

                                {/* Name and prompt fields - always shown */}
                                <div className="schedule-form-row">
                                    <input
                                        type="text"
                                        placeholder="Schedule name (e.g. daily-summary)"
                                        value={scheduleName}
                                        onChange={(e) => setScheduleName(e.target.value)}
                                        className={!scheduleName.trim() && nlInput.trim() ? 'schedule-field-missing' : ''}
                                    />
                                </div>
                                <textarea
                                    className={`schedule-prompt-input ${!schedulePrompt.trim() && nlInput.trim() ? 'schedule-field-missing' : ''}`}
                                    placeholder="Prompt to execute on each run..."
                                    value={schedulePrompt}
                                    onChange={(e) => setSchedulePrompt(e.target.value)}
                                />

                                {formError && <p className="schedule-form-error">{formError}</p>}
                                <div className="schedule-create-actions">
                                    <button className="btn-secondary" onClick={() => { setShowCreate(false); setFormError(null); setParsed(null); setNlInput(''); setScheduleName(''); setSchedulePrompt(''); }}>
                                        Cancel
                                    </button>
                                    <button className="btn-primary" onClick={handleCreate} disabled={!canCreate || creating}>
                                        {creating ? 'Creating...' : 'Create Schedule'}
                                    </button>
                                </div>
                            </div>
                        )}
                    </section>

                    {/* Schedule list */}
                    <section className="schedule-section">
                        <h4>Active Schedules</h4>
                        {loading ? (
                            <p className="schedule-empty">Loading schedules...</p>
                        ) : error ? (
                            <p className="schedule-empty">{error}</p>
                        ) : schedules.length === 0 ? (
                            <p className="schedule-empty">No scheduled tasks yet. Create one to get started.</p>
                        ) : (
                            <div className="schedule-list">
                                {schedules.map((s) => (
                                    <ScheduleCard key={s.id} schedule={s} />
                                ))}
                            </div>
                        )}
                    </section>
                </div>
            </div>
        </div>
    );
}
