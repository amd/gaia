// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, useCallback, useRef, useId } from 'react';
import { X, Plus, Clock, Play, Pause, Trash2, ChevronDown, ChevronUp, MessageSquare, Zap, Pencil } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import { useScheduleStore } from '../stores/scheduleStore';
import { log } from '../utils/logger';
import type { Schedule, ScheduleResult, ParsedSchedule } from '../types';
import { ApiError } from '../services/api';
import { parseScheduleInput } from '../services/scheduleApi';
import { stripIsolates, type MessageKey, type Say } from '../i18n/registry';
import { dateLocale, translator, useT } from '../i18n/useT';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import { useFocusTrap } from '../hooks/useFocusTrap';
import { useDialogFocus } from '../hooks/useDialogFocus';
import { ConfirmSheet } from './setup/ConfirmSheet';
import './ScheduleManager.css';

const STATUS_LABEL: Record<Schedule['status'], MessageKey> = {
    active: 'schedules.status.active',
    paused: 'schedules.status.paused',
    cancelled: 'schedules.status.cancelled',
};

import { describeSchedule, formatClock, formatNextRun, formatTime, formatTimeOfDay, isCountdownRange } from '../utils/scheduleText';

// The session panel words a schedule the same way, without loading this dialog.
export { describeSchedule, formatNextRun };

/** Format days_of_week array to human string. */
function formatDays(days: number[], say: Say): string {
    if (days.length === 5 && !days.includes(5) && !days.includes(6)) return say('schedules.days.weekdays');
    if (days.length === 2 && days.includes(5) && days.includes(6)) return say('schedules.days.weekends');
    if (days.length === 7) return say('schedules.days.every');
    return days.map((d) => say(`schedules.day.${d}` as MessageKey)).join(say('schedules.days.join'));
}

/** Quick picks for "When to run"; the value is what the schedule parser reads. */
const WHEN_PRESETS: Array<{ label: MessageKey; value: string }> = [
    { label: 'schedules.preset.morning', value: 'daily at 8am' },
    { label: 'schedules.preset.weekdays', value: 'weekdays at 9am' },
    { label: 'schedules.preset.hourly', value: 'every 1h' },
];

/** What the user typed when the schedule was made, so Edit starts from their own words. */
export function scheduleInputText(schedule: Schedule): string {
    if (schedule.schedule_config) {
        try {
            const raw = (JSON.parse(schedule.schedule_config) as { raw_input?: unknown }).raw_input;
            if (typeof raw === 'string' && raw.trim()) return raw;
        } catch (err) {
            log.ui.warn(`Schedule "${schedule.name}" has an unreadable schedule_config`, err);
        }
    }
    // Rows saved before the input was kept: the description reads back for most shapes, and the preview says if not.
    return schedule.description;
}

function ScheduleCard({ schedule, onEdit }: { schedule: Schedule; onEdit: (schedule: Schedule) => void }) {
    const t = useT();
    const { selectedSchedule, selectSchedule, updateScheduleStatus, runScheduleNow, deleteSchedule, results, resultsLoading, loadSchedules, loadResults } = useScheduleStore();
    const { setCurrentSession, setShowSchedules } = useChatStore();
    const isSelected = selectedSchedule === schedule.name;
    const described = describeSchedule(schedule.description_parts, t);
    const [askingDelete, setAskingDelete] = useState(false);

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

    const handleRunNow = useCallback((e: React.MouseEvent) => {
        e.stopPropagation();
        log.ui.info(`Schedule "${schedule.name}": run now`);
        void runScheduleNow(schedule.name);
    }, [schedule.name, runScheduleNow]);

    const handleDelete = useCallback((e: React.MouseEvent) => {
        e.stopPropagation();
        setAskingDelete(true);
    }, []);

    const confirmDelete = useCallback(() => {
        setAskingDelete(false);
        log.ui.info(`Deleting schedule "${schedule.name}"`);
        void deleteSchedule(schedule.name);
    }, [schedule.name, deleteSchedule]);

    const handleEdit = useCallback((e: React.MouseEvent) => {
        e.stopPropagation();
        onEdit(schedule);
    }, [onEdit, schedule]);

    const handleToggleHistory = useCallback((e: React.MouseEvent) => {
        e.stopPropagation();
        selectSchedule(isSelected ? null : schedule.name);
    }, [isSelected, schedule.name, selectSchedule]);

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
            // A click in the delete dialog reaches here through its portal; it isn't a card click.
            onClick={(e) => { if (e.currentTarget.contains(e.target as Node)) selectSchedule(isSelected ? null : schedule.name); }}
        >
            <div className="schedule-card-header">
                <span className="schedule-card-name" dir="auto" title={schedule.name}>{schedule.name}</span>
                <span className={`schedule-card-status ${schedule.status}`}>{t(STATUS_LABEL[schedule.status])}</span>
            </div>
            <div className="schedule-card-prompt" dir="auto" title={schedule.prompt}>{schedule.prompt}</div>
            <div className="schedule-card-meta">
                <span title={stripIsolates(described ?? schedule.description)}><Clock size={11} /> {described ?? <bdi>{schedule.description}</bdi>}</span>
                <span>{t('schedules.card.runs', { n: schedule.run_count })}</span>
                {schedule.error_count > 0 && <span>{t('schedules.card.errors', { n: schedule.error_count })}</span>}
                <span>{t.compose((say, code) => say('schedules.card.last', { when: formatTime(schedule.last_run_at, say, undefined, dateLocale(translator(code))) }))}</span>
                <span>{t.compose((say, code) => say('schedules.card.next', { when: schedule.status === 'paused' ? say('schedules.status.paused') : formatNextRun(schedule.next_run_at, now, say, dateLocale(translator(code))) }))}</span>
            </div>
            {schedule.last_error && !(isSelected && !resultsLoading && results.some((r) => r.error)) && (
                <div className="schedule-card-error">{t('schedules.card.lastFailed', { error: schedule.last_error })}</div>
            )}
            <div className="schedule-card-actions">
                {schedule.session_id && (
                    <button className="btn-sm chat" onClick={handleOpenChat} title={t('schedules.card.openChatTitle')}>
                        <MessageSquare size={11} /> {t('schedules.card.openChat')}
                    </button>
                )}
                <button
                    className="btn-sm"
                    onClick={handleRunNow}
                    disabled={schedule.running}
                    title={t('schedules.card.runNowTitle')}
                >
                    <Zap size={11} /> {t(schedule.running ? 'schedules.card.running' : 'schedules.card.runNow')}
                </button>
                <button className="btn-sm" onClick={handleToggleStatus} title={t(schedule.status === 'active' ? 'schedules.card.pause' : 'schedules.card.resume')}>
                    {schedule.status === 'active'
                        ? <><Pause size={11} /> {t('schedules.card.pause')}</>
                        : <><Play size={11} /> {t('schedules.card.resume')}</>}
                </button>
                <button className="btn-sm" onClick={handleEdit} disabled={schedule.running} title={t('schedules.card.editTitle')}>
                    <Pencil size={11} /> {t('schedules.card.edit')}
                </button>
                <button className={`btn-sm danger`} onClick={handleDelete} title={t('schedules.card.delete')}>
                    <Trash2 size={11} /> {t('schedules.card.delete')}
                </button>
                <button className="btn-sm schedule-history-toggle" onClick={handleToggleHistory} aria-expanded={isSelected}>
                    {isSelected ? <ChevronUp size={11} /> : <ChevronDown size={11} />} {t('schedules.card.pastRuns')}
                </button>
            </div>
            {askingDelete && (
                <ConfirmSheet
                    title={t('schedules.delete.ask', { name: schedule.name })}
                    confirmLabel={t('schedules.delete.confirm')}
                    cancelLabel={t('schedules.delete.cancel')}
                    onConfirm={confirmDelete}
                    onCancel={() => setAskingDelete(false)}
                    danger
                >
                    <p>{t('schedules.delete.what')}</p>
                    {schedule.session_id && <p>{t('schedules.delete.chatStays')}</p>}
                    {schedule.running && <p>{t('schedules.delete.runStops')}</p>}
                </ConfirmSheet>
            )}

            {isSelected && (
                <div className="schedule-results">
                    <h4>{t('schedules.history')}</h4>
                    {resultsLoading ? (
                        <p className="schedule-results-empty">{t('schedules.historyLoading')}</p>
                    ) : results.length === 0 ? (
                        <p className="schedule-results-empty">{t('schedules.historyEmpty')}</p>
                    ) : (
                        <div className="schedule-results-list">
                            {results.map((r) => (
                                <div key={r.id} className="schedule-result-item">
                                    <div className="schedule-result-time">{t.compose((say, code) => formatTime(r.executed_at, say, undefined, dateLocale(translator(code))))}</div>
                                    {r.error ? (
                                        <div className="schedule-result-content schedule-result-error"><bdi>{r.error}</bdi></div>
                                    ) : (
                                        <div className="schedule-result-content">{r.result ? <bdi>{r.result}</bdi> : t('schedules.noOutput')}</div>
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
    const t = useT();
    const { setShowSchedules } = useChatStore();
    const { schedules, loading, error, actionError, loadSchedules, createSchedule, editSchedule, dismissActionError } = useScheduleStore();
    const close = useCallback(() => setShowSchedules(false), [setShowSchedules]);
    useEscapeToClose(true, close);
    const dialogRef = useRef<HTMLDivElement>(null);
    useFocusTrap(dialogRef, true);
    useDialogFocus(dialogRef, true);

    // Create form state
    const [draft] = useState(() => useScheduleStore.getState().draft);
    const [showCreate, setShowCreate] = useState(draft !== null);
    const [nlInput, setNlInput] = useState(draft?.when ?? '');
    const [scheduleName, setScheduleName] = useState(draft?.name ?? '');
    const [schedulePrompt, setSchedulePrompt] = useState(draft?.prompt ?? '');
    const [parsed, setParsed] = useState<ParsedSchedule | null>(null);
    const [parsing, setParsing] = useState(false);
    const [creating, setCreating] = useState(false);
    const [formError, setFormError] = useState<string | null>(null);
    // The schedule the form is changing; null when it makes a new one.
    const [editing, setEditing] = useState<string | null>(null);
    const formRef = useRef<HTMLDivElement>(null);
    const [nameTaken, setNameTaken] = useState(false);
    const fieldId = useId();
    const scheduleFieldId = `${fieldId}-when`;
    const scheduleErrorId = `${fieldId}-when-error`;
    const scheduleHintId = `${fieldId}-when-hint`;
    const nameFieldId = `${fieldId}-name`;
    const nameErrorId = `${fieldId}-name-error`;
    const promptFieldId = `${fieldId}-prompt`;
    const scheduleRejected = !parsing && parsed !== null && !parsed.valid;
    // The parser's reason is English; with no English in the input, the hint says what to fix.
    const showRejectReason = scheduleRejected && /[A-Za-z]/.test(nlInput);

    useEffect(() => {
        // A draft is an unsaved new schedule; an edit in progress is not one.
        if (editing) return;
        const filled = nlInput.trim() || scheduleName.trim() || schedulePrompt.trim();
        useScheduleStore.getState().setDraft(filled ? { when: nlInput, name: scheduleName, prompt: schedulePrompt } : null);
    }, [editing, nlInput, scheduleName, schedulePrompt]);

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
            dismissActionError();
        };
    }, [loadSchedules, dismissActionError]);

    // Allow creation if all 3 fields are filled. parsed?.valid is a bonus
    // (shows preview) but not a hard gate — the backend validates on create.
    const canCreate = nlInput.trim() && scheduleName.trim() && schedulePrompt.trim()
        && (parsed === null || parsed.valid);  // block only if parse explicitly returned invalid

    const closeForm = useCallback(() => {
        setShowCreate(false);
        setEditing(null);
        setFormError(null);
        setNameTaken(false);
        setParsed(null);
        setNlInput('');
        setScheduleName('');
        setSchedulePrompt('');
    }, []);

    const startEdit = useCallback((schedule: Schedule) => {
        setEditing(schedule.name);
        setScheduleName(schedule.name);
        setSchedulePrompt(schedule.prompt);
        setNlInput(scheduleInputText(schedule));
        setFormError(null);
        setShowCreate(true);
        log.ui.info(`Editing schedule "${schedule.name}"`);
    }, []);

    // The form sits above the list, so bring it into view when a card opens it.
    useEffect(() => {
        if (editing) formRef.current?.scrollIntoView?.({ block: 'nearest' });
    }, [editing]);

    const handleCreate = useCallback(async () => {
        if (!canCreate) return;
        setCreating(true);
        setFormError(null);
        setNameTaken(false);
        try {
            // Pass the raw NL input as interval - backend will re-parse it
            if (editing) {
                await editSchedule(editing, nlInput.trim(), schedulePrompt.trim());
                log.ui.info(`Schedule "${editing}" edited`);
            } else {
                await createSchedule(scheduleName.trim(), nlInput.trim(), schedulePrompt.trim());
                useScheduleStore.getState().setDraft(null);
                log.ui.info(`Schedule "${scheduleName}" created`);
            }
            closeForm();
        } catch (err) {
            if (!editing && err instanceof ApiError && err.httpStatus === 409) setNameTaken(true);
            else setFormError(err instanceof Error ? err.message : t(editing ? 'schedules.editFailed' : 'schedules.createFailed'));
        } finally {
            setCreating(false);
        }
    }, [canCreate, editing, scheduleName, nlInput, schedulePrompt, createSchedule, editSchedule, closeForm, t]);

    return (
        <div ref={dialogRef} className="modal-overlay" onClick={() => setShowSchedules(false)} role="dialog" aria-modal="true" aria-label={t('schedules.title')}>
            <div className="modal-panel schedule-modal" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3>{t('schedules.title')}</h3>
                    <button className="btn-icon" onClick={() => setShowSchedules(false)} aria-label={t('schedules.close')}>
                        <X size={18} />
                    </button>
                </div>

                <div className="modal-body">
                    {/* Create new schedule */}
                    <section className="schedule-section">
                        {!showCreate ? (
                            <button className="btn-primary" onClick={() => setShowCreate(true)}>
                                <Plus size={14} /> {t('schedules.new')}
                            </button>
                        ) : (
                            <div className="schedule-create-form" ref={formRef}>
                                {editing && <h4 className="schedule-form-title">{t('schedules.editingTitle', { name: editing })}</h4>}
                                {/* NL input */}
                                <div className="schedule-field">
                                    <label className="schedule-field-label" htmlFor={scheduleFieldId}>{t('schedules.when')}</label>
                                    <div className="schedule-presets">
                                        {WHEN_PRESETS.map((preset) => (
                                            <button
                                                key={preset.value}
                                                type="button"
                                                className={`schedule-preset${nlInput.trim() === preset.value ? ' is-picked' : ''}`}
                                                onClick={() => setNlInput(preset.value)}
                                                aria-pressed={nlInput.trim() === preset.value}
                                            >
                                                {t(preset.label)}
                                            </button>
                                        ))}
                                    </div>
                                    <textarea
                                        id={scheduleFieldId}
                                        className="schedule-nl-input"
                                        placeholder={t.bare('schedules.whenPlaceholder')}
                                        value={nlInput}
                                        onChange={(e) => setNlInput(e.target.value)}
                                        aria-invalid={scheduleRejected}
                                        aria-describedby={scheduleRejected ? `${scheduleErrorId} ${scheduleHintId}` : scheduleHintId}
                                        autoFocus
                                        rows={2}
                                    />
                                    <div id={scheduleHintId} className="schedule-field-hint">{t('schedules.whenHint')}</div>
                                </div>

                                {/* Parsed preview */}
                                {(parsed || parsing) && (
                                    <div
                                        className={`schedule-parsed-preview ${parsed?.valid ? 'valid' : 'invalid'}`}
                                        aria-live="polite"
                                    >
                                        {parsing ? (
                                            <span className="schedule-parsed-loading">{t('schedules.parsing')}</span>
                                        ) : parsed ? (
                                            <>
                                                {parsed.valid ? (
                                                    <div className="schedule-parsed-description">
                                                        {describeSchedule(parsed.description_parts, t) ?? <bdi>{parsed.description}</bdi>}
                                                    </div>
                                                ) : (
                                                    <div id={scheduleErrorId}>
                                                        <div className="schedule-parsed-description">{t('schedules.notUnderstood')}</div>
                                                        {showRejectReason && <div className="schedule-parsed-hint"><bdi>{parsed.description}</bdi></div>}
                                                    </div>
                                                )}
                                                {parsed.valid && parsed.next_run_at && (
                                                    <div className="schedule-parsed-next">
                                                        {t.compose((say, code) => say('schedules.nextRun', { when: formatNextRun(parsed.next_run_at, undefined, say, dateLocale(translator(code))) }))}
                                                    </div>
                                                )}
                                                {parsed.valid && (
                                                    <div className="schedule-parsed-details">
                                                        {parsed.time_of_day && (
                                                            <span className="schedule-parsed-tag">
                                                                <Clock size={10} /> {formatTimeOfDay(parsed.time_of_day, t)}
                                                            </span>
                                                        )}
                                                        {parsed.start_hour != null && parsed.end_hour != null && (
                                                            <span className="schedule-parsed-tag">
                                                                <Clock size={10} /> {t.compose((say, code) => say('schedules.hours', { start: formatClock(translator(code), parsed.start_hour!), end: formatClock(translator(code), parsed.end_hour!) }))}
                                                            </span>
                                                        )}
                                                        {parsed.days_of_week && (
                                                            <span className="schedule-parsed-tag">
                                                                {t.compose((say) => formatDays(parsed.days_of_week!, say))}
                                                            </span>
                                                        )}
                                                    </div>
                                                )}
                                            </>
                                        ) : null}
                                    </div>
                                )}

                                {/* Name and prompt fields - always shown */}
                                <div className="schedule-field">
                                    <label className="schedule-field-label" htmlFor={nameFieldId}>{t('schedules.name')}</label>
                                    <div className="schedule-form-row">
                                        <input
                                            id={nameFieldId}
                                            type="text"
                                            placeholder={t('schedules.namePlaceholder')}
                                            value={scheduleName}
                                            onChange={(e) => { setScheduleName(e.target.value); setNameTaken(false); }}
                                            readOnly={editing !== null}
                                            className={!scheduleName.trim() && nlInput.trim() ? 'schedule-field-missing' : ''}
                                            aria-invalid={nameTaken}
                                            aria-describedby={nameTaken ? nameErrorId : undefined}
                                        />
                                    </div>
                                    {nameTaken && <p id={nameErrorId} className="schedule-form-error" role="alert">{t('schedules.nameTaken')}</p>}
                                </div>
                                <div className="schedule-field">
                                    <label className="schedule-field-label" htmlFor={promptFieldId}>{t('schedules.prompt')}</label>
                                    <textarea
                                        id={promptFieldId}
                                        className={`schedule-prompt-input ${!schedulePrompt.trim() && nlInput.trim() ? 'schedule-field-missing' : ''}`}
                                        placeholder={t('schedules.promptPlaceholder')}
                                        value={schedulePrompt}
                                        onChange={(e) => setSchedulePrompt(e.target.value)}
                                    />
                                    <div className="schedule-prompt-hint">{t('schedules.promptHint')}</div>
                                </div>

                                {formError && <p className="schedule-form-error"><bdi>{formError}</bdi></p>}
                                <div className="schedule-create-actions">
                                    <button className="btn-secondary" onClick={closeForm}>
                                        {t('schedules.cancel')}
                                    </button>
                                    <button className="btn-primary" onClick={handleCreate} disabled={!canCreate || creating}>
                                        {editing
                                            ? t(creating ? 'schedules.saving' : 'schedules.save')
                                            : t(creating ? 'schedules.creating' : 'schedules.create')}
                                    </button>
                                </div>
                            </div>
                        )}
                    </section>

                    {/* Schedule list */}
                    <section className="schedule-section">
                        <h4>{t('schedules.list')}</h4>
                        {actionError && (
                            <div className="schedule-action-error" role="alert">
                                <span><bdi>{typeof actionError === 'string' ? actionError : t(actionError.ref, actionError.params)}</bdi></span>
                                <button className="btn-icon" onClick={dismissActionError} aria-label={t('schedules.dismissError')}>
                                    <X size={14} />
                                </button>
                            </div>
                        )}
                        {loading ? (
                            <p className="schedule-empty">{t('schedules.loading')}</p>
                        ) : error ? (
                            <p className="schedule-empty">{t('schedules.loadFailed')}</p>
                        ) : schedules.length === 0 ? (
                            <p className="schedule-empty">{t('schedules.empty')}</p>
                        ) : (
                            <div className="schedule-list">
                                {schedules.map((s) => (
                                    <ScheduleCard key={s.id} schedule={s} onEdit={startEdit} />
                                ))}
                            </div>
                        )}
                    </section>
                </div>
            </div>
        </div>
    );
}
