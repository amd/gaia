// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Archive, Check, Copy, FileDown, Loader2, RotateCw, X } from 'lucide-react';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import { useFocusTrap } from '../hooks/useFocusTrap';
import { useT } from '../i18n/useT';
import type { MessageKey } from '../i18n/registry';
import * as api from '../services/api';
import { LOG_SOURCES, useLogsStore, type LogSource } from '../stores/logsStore';
import type { LogFileInfo, LogLineInfo, LogReadResponse } from '../types/api.gen';
import { formatBytes } from './setup/format';
import { formatSize } from '../utils/format';
import './LogsDialog.css';

const POLL_MS = 2000;
// An <option> can't hold a <bdi>: isolate the size so "9.7 MB" stays whole in Arabic.
const LRI = '\u2066';
const PDI = '\u2069';
/** Lines kept on screen while following; older ones scroll off. */
const KEEP_LINES = 5000;

const SOURCE_LABELS: Record<LogSource, MessageKey> = {
    model_server: 'logs.source.modelServer',
    gaia: 'logs.source.gaia',
    service: 'logs.source.service',
    agent: 'logs.source.agent',
    desktop: 'logs.source.desktop',
    other: 'logs.source.other',
};

const SOURCE_HINTS: Record<LogSource, MessageKey> = {
    model_server: 'logs.hint.modelServer',
    gaia: 'logs.hint.gaia',
    service: 'logs.hint.service',
    agent: 'logs.hint.agent',
    desktop: 'logs.hint.desktop',
    other: 'logs.hint.other',
};

const REASONS: Record<string, MessageKey> = {
    no_file: 'logs.reason.noFile',
    unreadable: 'logs.reason.unreadable',
    not_running: 'logs.reason.notRunning',
    remote: 'logs.reason.remote',
    no_stream: 'logs.reason.noStream',
    stream_failed: 'logs.reason.streamFailed',
};

const isProblem = (line: LogLineInfo) => line.severity === 'warning' || line.severity === 'error';

function errorText(err: unknown): string {
    return err instanceof Error ? err.message : String(err);
}

function fileStamp(now: Date): string {
    const p = (n: number) => String(n).padStart(2, '0');
    return `${now.getFullYear()}${p(now.getMonth() + 1)}${p(now.getDate())}-${p(now.getHours())}${p(now.getMinutes())}${p(now.getSeconds())}`;
}

/**
 * One log, newest line at the bottom and followed live. The server redacts
 * secrets first. `file` picks among a source's files; null is its first.
 */
export function LogView({ source, file }: { source: LogSource; file: string | null }) {
    const t = useT();
    const [data, setData] = useState<LogReadResponse | null>(null);
    const [lines, setLines] = useState<LogLineInfo[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [follow, setFollow] = useState(true);
    const [problemsOnly, setProblemsOnly] = useState(false);
    const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
    const cursor = useRef<string | null>(null);
    const reading = useRef(false);
    const box = useRef<HTMLPreElement>(null);
    const pinned = useRef(true);
    /** Where our own scroll left the box: its scroll event, arriving after a resize, isn't the reader's. */
    const autoTop = useRef<number | null>(null);

    const load = useCallback(async (fresh: boolean) => {
        if (reading.current) return;
        reading.current = true;
        if (fresh) setLoading(true);
        try {
            const res = await api.getLog(source, { file, after: fresh ? null : cursor.current });
            if (res.cursor) cursor.current = res.cursor;
            const incoming = res.lines ?? [];
            const replace = fresh || !!res.reset;
            setLines((prev) => (replace ? incoming : [...prev, ...incoming]).slice(-KEEP_LINES));
            // A follow-up read doesn't re-check the server, so keep what the fresh one found.
            setData((prev) => (replace || !prev ? res : { ...res, server_answering: res.server_answering ?? prev.server_answering, dropped: prev.dropped }));
            setError(null);
        } catch (err) {
            setError(errorText(err));
        } finally {
            reading.current = false;
            setLoading(false);
        }
    }, [source, file]);

    useEffect(() => {
        cursor.current = null;
        pinned.current = true;
        setData(null);
        setLines([]);
        setNotice(null);
        void load(true);
    }, [load]);
    useEffect(() => {
        if (!follow) return;
        const id = setInterval(() => { void load(false); }, POLL_MS);
        return () => clearInterval(id);
    }, [follow, load]);

    const shown = problemsOnly ? lines.filter(isProblem) : lines;
    const text = shown.map((l) => l.text).join('\n');

    const stick = useCallback(() => {
        const el = box.current;
        if (!el || !pinned.current) return;
        el.scrollTop = el.scrollHeight;
        autoTop.current = el.scrollTop;
    }, []);

    useEffect(stick, [text, stick]);

    // The box settles its size, and the font its wrapping, after the first lines land; stay on the newest.
    const hasLines = shown.length > 0;
    useEffect(() => {
        const el = box.current;
        if (!el || typeof ResizeObserver === 'undefined') return;
        const watch = new ResizeObserver(stick);
        watch.observe(el);
        if (el.firstElementChild) watch.observe(el.firstElementChild);
        return () => watch.disconnect();
    }, [hasLines, stick]);

    const copy = useCallback(async () => {
        try {
            await navigator.clipboard.writeText(text);
            setNotice({ ok: true, text: t('logs.copied') });
        } catch (err) {
            setNotice({ ok: false, text: t('logs.copyFailed', { error: errorText(err) }) });
        }
    }, [t, text]);

    const save = useCallback(() => {
        const url = URL.createObjectURL(new Blob([`${text}\n`], { type: 'text/plain;charset=utf-8' }));
        const a = document.createElement('a');
        a.href = url;
        a.download = `gaia-${source.replace('_', '-')}-log-${fileStamp(new Date())}.txt`;
        a.click();
        // Later, not now: the desktop app's save dialog reads the URL after the click.
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
        setNotice(null);
    }, [source, text]);

    const isServer = source === 'model_server';
    const reason = data && !data.available && data.reason
        ? (!isServer && data.reason === 'no_file' ? 'logs.reason.notWritten' : REASONS[data.reason])
        : undefined;
    return (
        <div className="log-viewer-view">
            <p className="log-viewer-hint">{t(SOURCE_HINTS[source])}</p>
            {data?.source === 'file' && data.log_path && (
                <p className="log-viewer-source">
                    {t('logs.fromFile')} <bdi dir="ltr">{data.log_path}</bdi>
                </p>
            )}
            {data?.source === 'stream' && (
                <p className="log-viewer-source">
                    {t('logs.fromStream')} <bdi dir="ltr">{data.base_url}</bdi>
                </p>
            )}
            {isServer && data?.source === 'file' && data.server_answering === false && (
                <p className="log-viewer-warning" role="alert">{t('logs.notAnswering')}</p>
            )}
            {reason && (
                <div className="log-viewer-warning" role="alert">
                    <p>{t(reason)}</p>
                    {data?.detail && <p className="log-viewer-detail"><bdi dir="ltr">{data.detail}</bdi></p>}
                </div>
            )}
            {data?.available && data.detail && (
                <p className="log-viewer-hint" role="note"><bdi dir="ltr">{data.detail}</bdi></p>
            )}
            {error && (
                <p className="log-viewer-warning" role="alert">{t('logs.readFailed', { error })}</p>
            )}

            <div className="log-viewer-toolbar">
                <label className="log-viewer-check">
                    <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
                    {t('logs.follow')}
                </label>
                <label className="log-viewer-check">
                    <input type="checkbox" checked={problemsOnly} onChange={(e) => setProblemsOnly(e.target.checked)} />
                    {t('logs.problemsOnly')}
                </label>
                <div className="log-viewer-actions">
                    <button type="button" className="btn-secondary" onClick={() => void load(true)} disabled={loading}>
                        {loading ? <Loader2 size={13} className="spin" aria-hidden="true" /> : <RotateCw size={13} aria-hidden="true" />}
                        {t('logs.refresh')}
                    </button>
                    <button type="button" className="btn-secondary" onClick={() => void copy()} disabled={!text}>
                        {notice?.ok ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
                        {t('logs.copy')}
                    </button>
                    <button type="button" className="btn-secondary" onClick={save} disabled={!text}>
                        <FileDown size={13} aria-hidden="true" />
                        {t('logs.save')}
                    </button>
                </div>
            </div>

            {loading && !data ? (
                <p className="log-viewer-empty" role="status"><Loader2 size={13} className="spin" aria-hidden="true" /> {t('logs.loading')}</p>
            ) : data?.available && shown.length === 0 ? (
                <p className="log-viewer-empty" role="status">
                    {problemsOnly && lines.length > 0 ? t('logs.noProblems') : t('logs.empty')}
                </p>
            ) : shown.length > 0 && (
                <pre
                    ref={box}
                    className="log-viewer-lines"
                    dir="ltr"
                    tabIndex={0}
                    aria-label={t(SOURCE_LABELS[source])}
                    onScroll={(e) => {
                        const el = e.currentTarget;
                        if (el.scrollTop === autoTop.current) return;
                        autoTop.current = null;
                        pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 8;
                    }}
                >
                    <span className="log-viewer-body">
                        {shown.map((line, i) => (
                            <span key={i} className={line.severity ? `is-${line.severity}` : undefined}>{line.text}{'\n'}</span>
                        ))}
                    </span>
                </pre>
            )}
            {(data?.dropped ?? 0) > 0 && <p className="log-viewer-hint">{t('logs.older')}</p>}
            {notice && (
                <p className={notice.ok ? 'settings-ok' : 'log-viewer-warning'} role={notice.ok ? 'status' : 'alert'}>{notice.text}</p>
            )}
        </div>
    );
}

/** "Save all logs": every GAIA log, redacted, with this PC's details, as one file to attach. */
export function SaveAllLogs() {
    const t = useT();
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState<api.SetupReport | null>(null);
    const [error, setError] = useState<string | null>(null);

    const save = useCallback(async () => {
        setSaving(true);
        setError(null);
        setSaved(null);
        try {
            const report = await api.createSetupReport();
            const blob = await api.fetchSetupReport(report.name);
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = report.name;
            a.click();
            // Later, not now: the desktop app's save dialog reads the URL after the click.
            setTimeout(() => URL.revokeObjectURL(url), 60_000);
            setSaved(report);
        } catch (err) {
            setError(t('logs.saveAllFailed', { error: errorText(err) }));
        } finally {
            setSaving(false);
        }
    }, [t]);

    return (
        <div className="log-viewer-save-all">
            <button type="button" className="btn-secondary" onClick={() => void save()} disabled={saving}>
                {saving ? <Loader2 size={13} className="spin" aria-hidden="true" /> : <Archive size={13} aria-hidden="true" />}
                {t('logs.saveAll')}
            </button>
            <span className="log-viewer-hint">{t('setup.logs.reportNote')}</span>
            {saved && (
                <p className="log-viewer-hint" role="status">
                    {t('logs.savedAll', { size: formatBytes(saved.size_bytes) })} <bdi dir="ltr">{saved.path}</bdi>
                </p>
            )}
            {error && <p className="log-viewer-warning" role="alert">{error}</p>}
        </div>
    );
}

/** The viewer: a tab per log group, a file picker when a group has several. */
export function LogsDialog() {
    const t = useT();
    const open = useLogsStore((s) => s.open);
    const source = useLogsStore((s) => s.source);
    const show = useLogsStore((s) => s.show);
    const hide = useLogsStore((s) => s.hide);
    const panel = useRef<HTMLDivElement>(null);
    const closeButton = useRef<HTMLButtonElement>(null);
    const [files, setFiles] = useState<Record<string, LogFileInfo[]>>({});
    const [file, setFile] = useState<string | null>(null);
    useEscapeToClose(open, hide);
    useFocusTrap(panel, open);

    useEffect(() => {
        if (!open) return;
        closeButton.current?.focus();
        api.getLogSources()
            .then((res) => setFiles(Object.fromEntries(res.sources.map((s) => [s.id, s.files ?? []]))))
            .catch((err) => console.warn('[LogsDialog] could not list log files', err));
    }, [open]);
    useEffect(() => { setFile(null); }, [source]);

    if (!open) return null;
    const sourceFiles = files[source] ?? [];
    return createPortal(
        <div className="log-viewer-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) hide(); }}>
            <div ref={panel} className="log-viewer" role="dialog" aria-modal="true" aria-labelledby="log-viewer-title">
                <div className="log-viewer-head">
                    <h2 id="log-viewer-title" className="log-viewer-title">{t('logs.title')}</h2>
                    <button ref={closeButton} type="button" className="log-viewer-close" onClick={hide} aria-label={t('logs.close')} title={t('logs.close')}>
                        <X size={16} aria-hidden="true" />
                    </button>
                </div>
                <div className="log-viewer-tabs" role="tablist" aria-label={t('logs.title')}>
                    {LOG_SOURCES.map((id) => (
                        <button
                            key={id}
                            type="button"
                            role="tab"
                            aria-selected={id === source}
                            className={id === source ? 'is-active' : ''}
                            onClick={() => show(id)}
                        >
                            {/* "GAIA" is a name in every language, not a word to translate. */}
                            {id === 'gaia' ? <bdi>{t(SOURCE_LABELS[id])}</bdi> : t(SOURCE_LABELS[id])}
                        </button>
                    ))}
                </div>
                {sourceFiles.length > 1 && (
                    <label className="log-viewer-file">
                        {t('logs.file')}
                        <select dir="ltr" value={file ?? sourceFiles[0].name} onChange={(e) => setFile(e.target.value)}>
                            {sourceFiles.map((f) => (
                                <option key={f.name} value={f.name}>
                                    {f.name}{f.size_bytes != null ? ` (${LRI}${formatSize(f.size_bytes)}${PDI})` : ''}
                                </option>
                            ))}
                        </select>
                    </label>
                )}
                <div role="tabpanel" className="log-viewer-panel">
                    <LogView key={`${source}:${file ?? ''}`} source={source} file={file} />
                </div>
                <p className="log-viewer-hint">{t('logs.privacy')}</p>
                <SaveAllLogs />
            </div>
        </div>,
        document.body,
    );
}
