// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState, useCallback, useRef, useId } from 'react';
import { X, Plus, Clock, Play, Pause, Trash2, ChevronDown, ChevronUp, MessageSquare, Zap } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import { useScheduleStore } from '../stores/scheduleStore';
import { log } from '../utils/logger';
import type { Schedule, ScheduleResult, ParsedSchedule, ScheduleDescription } from '../types';
import { ApiError, parseScheduleInput } from '../services/api';
import { isMessageKey, stripIsolates, type MessageKey, type Say } from '../i18n/registry';
import { dateLocale, tEn, translator, useT, type Translate } from '../i18n/useT';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import { useFocusTrap } from '../hooks/useFocusTrap';
import { useDialogFocus } from '../hooks/useDialogFocus';
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

/** A clock time as the UI language writes it, 12h or 24h. */
function formatClock(t: Translate, hour: number, minute = 0): string {
    const at = new Date(2000, 0, 1, hour, minute);
    return new Intl.DateTimeFormat(dateLocale(t), { hour: 'numeric', minute: '2-digit' }).format(at);
}

function formatTimeOfDay(time: string, t: Translate): string {
    const [h, m] = time.split(':').map(Number);
    return formatClock(t, h, m);
}

/** `base.<CLDR plural form>` for `n` in the UI language; null when that form has no message. */
function countedKey(base: string, n: number, t: Translate): MessageKey | null {
    const key = `${base}.${new Intl.PluralRules(t.locale).select(n)}`;
    return isMessageKey(key) ? key : null;
}

const isWeekdays = (days: number[]) => days.length === 5 && days.every((d) => d < 5);
const isWeekends = (days: number[]) => days.length === 2 && days.includes(5) && days.includes(6);

/** Days after an interval or window: "weekdays", "Monday, Wednesday". */
function dayPhrase(days: number[], t: Translate, say: Say): string | null {
    if (isWeekdays(days)) return say('schedules.desc.days.weekdays');
    if (isWeekends(days)) return say('schedules.desc.days.weekends');
    if (days.length === 7) return say('schedules.desc.days.every');
    const key = countedKey('schedules.desc.dayList', days.length, t);
    if (!key) return null;
    const names = days.map((d) => say(`schedules.dayName.${d}` as MessageKey)).join(say('schedules.desc.dayJoin'));
    return say(key, { days: names });
}

/**
 * One language's wording of `parts`; null when a shape has no wording here.
 * `t` picks the plural form (the UI language's), `clock` writes the times.
 */
function schedulePhrase(parts: ScheduleDescription, t: Translate, say: Say, clock: Translate): string | null {
    const { kind, count, unit, days, time, start_hour, end_hour } = parts;
    const at = (when: string) => (time ? say('schedules.desc.at', { when, time: formatTimeOfDay(time, clock) }) : when);
    if (kind === 'every' || kind === 'window') {
        const key = count && unit ? countedKey(`schedules.span.${unit}`, count, t) : null;
        const on = days ? dayPhrase(days, t, say) : undefined;
        if (!key || on === null) return null;
        const span = say(key, { n: count! });
        if (kind === 'every') {
            return on ? say('schedules.desc.everyOn', { span, days: on }) : say('schedules.desc.every', { span });
        }
        if (start_hour == null || end_hour == null) return null;
        const hours = say('schedules.hours', { start: formatClock(clock, start_hour), end: formatClock(clock, end_hour) });
        return on ? say('schedules.desc.windowOn', { span, hours, days: on }) : say('schedules.desc.window', { span, hours });
    }
    if (kind === 'daily') return time ? at(say('schedules.desc.daily')) : null;
    if (kind !== 'days' || !days?.length) return null;
    if (days.length === 7) return at(say('schedules.desc.daily'));
    if (isWeekdays(days)) return at(say('schedules.desc.weekdays'));
    if (isWeekends(days)) return at(say('schedules.desc.weekends'));
    const on = dayPhrase(days, t, say);
    return on && at(say('schedules.desc.onDays', { days: on }));
}

/**
 * When a schedule runs, worded in the UI language from the backend's parts;
 * null when a shape has no wording here, so the caller shows the English.
 */
export function describeSchedule(parts: ScheduleDescription | null | undefined, t: Translate): string | null {
    if (!parts || schedulePhrase(parts, t, t.bare, t) === null) return null;
    return t.compose((say, code) => schedulePhrase(parts, t, say, translator(code)) ?? '');
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
    const described = describeSchedule(schedule.description_parts, t);
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
                <span title={stripIsolates(described ?? schedule.description)}><Clock size={11} /> {described ?? <bdi>{schedule.description}</bdi>}</span>
                <span>{t('schedules.card.runs', { n: schedule.run_count })}</span>
                {schedule.error_count > 0 && <span>{t('schedules.card.errors', { n: schedule.error_count })}</span>}
                <span>{t.compose((say, code) => say('schedules.card.last', { when: formatTime(schedule.last_run_at, say, undefined, dateLocale(translator(code))) }))}</span>
                <span>{t.compose((say, code) => say('schedules.card.next', { when: schedule.status === 'paused' ? say('schedules.status.paused') : formatNextRun(schedule.next_run_at, now, say, dateLocale(translator(code))) }))}</span>
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
    const { schedules, loading, error, actionError, loadSchedules, createSchedule, dismissActionError } = useScheduleStore();
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
        const filled = nlInput.trim() || scheduleName.trim() || schedulePrompt.trim();
        useScheduleStore.getState().setDraft(filled ? { when: nlInput, name: scheduleName, prompt: schedulePrompt } : null);
    }, [nlInput, scheduleName, schedulePrompt]);

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
        setNameTaken(false);
        try {
            // Pass the raw NL input as interval - backend will re-parse it
            await createSchedule(scheduleName.trim(), nlInput.trim(), schedulePrompt.trim());
            useScheduleStore.getState().setDraft(null);
            setNlInput('');
            setScheduleName('');
            setSchedulePrompt('');
            setParsed(null);
            setShowCreate(false);
            log.ui.info(`Schedule "${scheduleName}" created`);
        } catch (err) {
            if (err instanceof ApiError && err.httpStatus === 409) setNameTaken(true);
            else setFormError(err instanceof Error ? err.message : t('schedules.createFailed'));
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
                                                        {t.compose((say, code) => say('schedules.nextRun', { when: new Date(parsed.next_run_at!).toLocaleString(dateLocale(translator(code))) }))}
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
                                </div>

                                {formError && <p className="schedule-form-error"><bdi>{formError}</bdi></p>}
                                <div className="schedule-create-actions">
                                    <button className="btn-secondary" onClick={() => { setShowCreate(false); setFormError(null); setNameTaken(false); setParsed(null); setNlInput(''); setScheduleName(''); setSchedulePrompt(''); }}>
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
