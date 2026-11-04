// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, useCallback, useRef, useId } from 'react';
import { X, Plus, Clock, Play, Pause, Trash2, ChevronDown, ChevronUp, MessageSquare, Zap } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import { useScheduleStore } from '../stores/scheduleStore';
import { log } from '../utils/logger';
import type { Schedule, ScheduleResult, ParsedSchedule } from '../types';
import { parseScheduleInput } from '../services/api';
import type { MessageKey, Say } from '../i18n/registry';
import { dateLocale, tEn, useT } from '../i18n/useT';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import { useFocusTrap } from '../hooks/useFocusTrap';
import './ScheduleManager.css';

/** How long the Delete button waits for its "Confirm?" click. */
export const CONFIRM_DELETE_MS = 8000;

const STATUS_LABEL: Record<Schedule['status'], MessageKey> = {
    active: 'schedules.status.active',
    paused: 'schedules.status.paused',
    cancelled: 'schedules.status.cancelled',
};

/** Format an ISO date string to a relative or short time; `locale` formats dates (undefined: the PC's). */
function formatTime(iso: string | null, say: Say, now?: Date, locale?: string): string {
    if (!iso) return say('schedules.time.never');
    const d = new Date(iso);
    const current = now || new Date();
    const diffMs = current.getTime() - d.getTime();
    const diffSecs = Math.floor(diffMs / 1000);

    if (diffSecs < 0) {
        // Future time
        const futureSecs = Math.abs(diffSecs);
        if (futureSecs < 60) return say('schedules.time.inSeconds', { n: futureSecs });
        const futureMins = Math.floor(futureSecs / 60);
        if (futureMins < 60) return say('schedules.time.inMinutes', { n: futureMins });
        const hrs = Math.floor(futureMins / 60);
        if (hrs < 24) return say('schedules.time.inHours', { n: hrs });
        return say('schedules.time.inDays', { n: Math.floor(hrs / 24) });
    }
    if (diffSecs < 60) return say('time.justNow');
    const mins = Math.floor(diffSecs / 60);
    if (mins < 60) return say('time.minutesAgo', { n: mins });
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return say('time.hoursAgo', { n: hrs });
    return d.toLocaleDateString(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/** Next run as the user's local clock time, plus how far away it is. */
export function formatNextRun(iso: string | null, now?: Date, say: Say = tEn.bare, locale?: string): string {
    if (!iso) return say('schedules.time.never');
    const relative = formatTime(iso, say, now, locale);
    const d = new Date(iso);
    if (d.getTime() <= (now || new Date()).getTime()) return relative;
    const clock = d.toLocaleString(locale, { weekday: 'short', hour: 'numeric', minute: '2-digit' });
    return say('schedules.time.clockIn', { clock, relative });
}

/** Returns true if the ISO timestamp is less than 60 seconds in the future. */
function isCountdownRange(iso: string | null): boolean {
    if (!iso) return false;
    const diffMs = new Date(iso).getTime() - Date.now();
    return diffMs > 0 && diffMs < 60000;
}

/** Format "HH:MM" to "H:MM AM/PM". */
function formatTimeOfDay(time: string, say: Say): string {
    const [h, m] = time.split(':').map(Number);
    const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
    return say(h >= 12 ? 'schedules.clock.pm' : 'schedules.clock.am', { time: `${h12}:${m.toString().padStart(2, '0')}` });
}

/** Format hour number to "H AM/PM". */
function formatHour(h: number, say: Say): string {
    const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
    return say(h >= 12 ? 'schedules.clock.pm' : 'schedules.clock.am', { time: String(h12) });
}

/** Format days_of_week array to human string. */
function formatDays(days: number[], say: Say): string {
    if (days.length === 5 && !days.includes(5) && !days.includes(6)) return say('schedules.days.weekdays');
    if (days.length === 2 && days.includes(5) && days.includes(6)) return say('schedules.days.weekends');
    if (days.length === 7) return say('schedules.days.every');
    return days.map((d) => say(`schedules.day.${d}` as MessageKey)).join(say('schedules.days.join'));
}

function ScheduleCard({ schedule }: { schedule: Schedule }) {
    const t = useT();
    const { selectedSchedule, selectSchedule, updateScheduleStatus, runScheduleNow, deleteSchedule, results, resultsLoading, loadSchedules, loadResults } = useScheduleStore();
    const { setCurrentSession, setShowSchedules } = useChatStore();
    const isSelected = selectedSchedule === schedule.name;
    const [confirmDelete, setConfirmDelete] = useState(false);
    const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const clearConfirmTimer = useCallback(() => {
        if (confirmTimerRef.current) {
            clearTimeout(confirmTimerRef.current);
            confirmTimerRef.current = null;
        }
    }, []);
    useEffect(() => clearConfirmTimer, [clearConfirmTimer]);

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
        clearConfirmTimer();
        if (!confirmDelete) {
            setConfirmDelete(true);
            confirmTimerRef.current = setTimeout(() => {
                confirmTimerRef.current = null;
                setConfirmDelete(false);
            }, CONFIRM_DELETE_MS);
            return;
        }
        log.ui.info(`Deleting schedule "${schedule.name}"`);
        deleteSchedule(schedule.name);
    }, [schedule.name, confirmDelete, deleteSchedule, clearConfirmTimer]);

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
                <span className="schedule-card-name" dir="auto">{schedule.name}</span>
                <span className={`schedule-card-status ${schedule.status}`}>{t(STATUS_LABEL[schedule.status])}</span>
            </div>
            <div className="schedule-card-prompt" dir="auto">{schedule.prompt}</div>
            <div className="schedule-card-meta">
                <span title={schedule.description}><Clock size={11} /> <bdi>{schedule.description}</bdi></span>
                <span>{t('schedules.card.runs', { n: schedule.run_count })}</span>
                {schedule.error_count > 0 && <span>{t('schedules.card.errors', { n: schedule.error_count })}</span>}
                <span>{t.compose((say) => say('schedules.card.last', { when: formatTime(schedule.last_run_at, say, undefined, dateLocale(t)) }))}</span>
                <span>{t.compose((say) => say('schedules.card.next', { when: formatNextRun(schedule.next_run_at, now, say, dateLocale(t)) }))}</span>
            </div>
            {schedule.last_error && (
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
                <button className={`btn-sm danger`} onClick={handleDelete} title={t('schedules.card.delete')}>
                    <Trash2 size={11} /> {t(confirmDelete ? 'schedules.card.confirmDelete' : 'schedules.card.delete')}
                </button>
            </div>

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
                                    <div className="schedule-result-time">{t.compose((say) => formatTime(r.executed_at, say, undefined, dateLocale(t)))}</div>
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
    const { schedules, loading, error, actionError, loadSchedules, createSchedule, dismissActionError } = useScheduleStore();
    const close = useCallback(() => setShowSchedules(false), [setShowSchedules]);
    useEscapeToClose(true, close);
    const dialogRef = useRef<HTMLDivElement>(null);
    useFocusTrap(dialogRef, true);

    // Create form state
    const [showCreate, setShowCreate] = useState(false);
    const [nlInput, setNlInput] = useState('');
    const [scheduleName, setScheduleName] = useState('');
    const [schedulePrompt, setSchedulePrompt] = useState('');
    const [parsed, setParsed] = useState<ParsedSchedule | null>(null);
    const [parsing, setParsing] = useState(false);
    const [creating, setCreating] = useState(false);
    const [formError, setFormError] = useState<string | null>(null);
    const fieldId = useId();
    const scheduleFieldId = `${fieldId}-when`;
    const scheduleErrorId = `${fieldId}-when-error`;
    const nameFieldId = `${fieldId}-name`;
    const promptFieldId = `${fieldId}-prompt`;
    const scheduleRejected = !parsing && parsed !== null && !parsed.valid;

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
            setFormError(err instanceof Error ? err.message : t('schedules.createFailed'));
        } finally {
            setCreating(false);
        }
    }, [canCreate, scheduleName, nlInput, schedulePrompt, createSchedule, t]);

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
                            <div className="schedule-create-form">
                                {/* NL input */}
                                <div className="schedule-field">
                                    <label className="schedule-field-label" htmlFor={scheduleFieldId}>{t('schedules.when')}</label>
                                    <textarea
                                        id={scheduleFieldId}
                                        className="schedule-nl-input"
                                        placeholder={t.bare('schedules.whenPlaceholder')}
                                        value={nlInput}
                                        onChange={(e) => setNlInput(e.target.value)}
                                        aria-invalid={scheduleRejected}
                                        aria-describedby={scheduleRejected ? scheduleErrorId : undefined}
                                        autoFocus
                                        rows={2}
                                    />
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
                                                    <div className="schedule-parsed-description"><bdi>{parsed.description}</bdi></div>
                                                ) : (
                                                    <div id={scheduleErrorId}>
                                                        <div className="schedule-parsed-description">{t('schedules.notUnderstood')}</div>
                                                        <div className="schedule-parsed-hint"><bdi>{parsed.description}</bdi></div>
                                                    </div>
                                                )}
                                                {parsed.valid && parsed.next_run_at && (
                                                    <div className="schedule-parsed-next">
                                                        {t('schedules.nextRun', { when: new Date(parsed.next_run_at).toLocaleString(dateLocale(t)) })}
                                                    </div>
                                                )}
                                                {parsed.valid && (
                                                    <div className="schedule-parsed-details">
                                                        {parsed.time_of_day && (
                                                            <span className="schedule-parsed-tag">
                                                                <Clock size={10} /> {t.compose((say) => formatTimeOfDay(parsed.time_of_day!, say))}
                                                            </span>
                                                        )}
                                                        {parsed.start_hour != null && parsed.end_hour != null && (
                                                            <span className="schedule-parsed-tag">
                                                                <Clock size={10} /> {t.compose((say) => say('schedules.hours', { start: formatHour(parsed.start_hour!, say), end: formatHour(parsed.end_hour!, say) }))}
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
                                            onChange={(e) => setScheduleName(e.target.value)}
                                            className={!scheduleName.trim() && nlInput.trim() ? 'schedule-field-missing' : ''}
                                        />
                                    </div>
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
                                </div>

                                {formError && <p className="schedule-form-error"><bdi>{formError}</bdi></p>}
                                <div className="schedule-create-actions">
                                    <button className="btn-secondary" onClick={() => { setShowCreate(false); setFormError(null); setParsed(null); setNlInput(''); setScheduleName(''); setSchedulePrompt(''); }}>
                                        {t('schedules.cancel')}
                                    </button>
                                    <button className="btn-primary" onClick={handleCreate} disabled={!canCreate || creating}>
                                        {t(creating ? 'schedules.creating' : 'schedules.create')}
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
                                <span><bdi>{actionError}</bdi></span>
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
