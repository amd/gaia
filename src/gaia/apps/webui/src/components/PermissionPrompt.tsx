// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, ShieldQuestion } from 'lucide-react';
import {
    useNotificationStore,
    selectSessionPermissionPrompt,
    requiresFreshConsent,
    repeatedAllowCount,
    PATH_ACCESS_TOOL,
    PROJECT_TRUST_TOOL,
    permissionPromptText,
    alwaysScopeText,
    type PermissionDecision,
} from '../stores/notificationStore';
import type { GaiaNotification } from '../types/agent';
import { isEscapeClaimed } from '../hooks/useEscapeToClose';
import { CalendarApproval, CALENDAR_WRITE_TOOLS } from './CalendarApproval';
import { pluralKey, type MessageKey } from '../i18n/registry';
import { useT } from '../i18n/useT';
import { MarkedText, markLtr } from './MarkedText';
import { ChangePreview, fileChange } from './PermissionChangePreview';
import './PermissionPrompt.css';

/** One-line description of a tool call, e.g. `run_shell_command · git status`. */
function summarize(n: GaiaNotification): string {
    const args = n.toolArgs ?? {};
    // _site: the site a browser-pane read would show the model (SITE_ARG in tool_grants.py).
    const primary = ['command', 'file_path', 'path', 'url', '_site', 'query', 'name']
        .map((k) => args[k])
        .find((v) => typeof v === 'string' && v.trim());
    return typeof primary === 'string' ? primary : '';
}

/** Tools whose relayed summary is a command and its risk note (`COMMAND_SUMMARY_TOOLS` in sse_translation.py). */
const COMMAND_SUMMARY_TOOLS = new Set(['run_shell_command', 'wait_for_condition']);

/** Tools whose relayed summary is the exact text being approved (`EXACT_TEXT_ARG` in call_risk.py). The flagship runs them from its send-email skill, so they also arrive namespaced. */
const EXACT_MAIL_TOOLS = ['send_now', 'send_draft', 'reply_to_message', 'forward_message', 'discard_draft', 'update_draft'];
const EXACT_SUMMARY_TOOLS = new Set([...EXACT_MAIL_TOOLS, ...EXACT_MAIL_TOOLS.map((tool) => `send-email/${tool}`)]);

// The header lines `render_preview` in outgoing.py writes, longest label first so the regex picks it.
const MAIL_FIELDS: ReadonlyArray<[string, MessageKey]> = [
    ['Attachments left out, not sent', 'permission.mail.leftOut'],
    ['Attachments', 'permission.mail.attachments'],
    ['Subject', 'permission.mail.subject'],
    ['Format', 'permission.mail.format'],
    ['From', 'permission.mail.from'],
    ['Bcc', 'permission.mail.bcc'],
    ['Cc', 'permission.mail.cc'],
    ['To', 'permission.mail.to'],
];
const MAIL_FIELD = new RegExp(`^(${MAIL_FIELDS.map(([label]) => label).join('|')}): (.*)$`);
const MAIL_ADDRESS_FIELDS = new Set(['From', 'To', 'Cc', 'Bcc']);

export interface MailHeader {
    /** Text above the header, such as a note about hidden characters. */
    before: string;
    fields: Array<[label: string, value: string]>;
    body: string;
}

/**
 * An email being approved, split into its header lines and its body so the
 * labels can be translated. Every value stays exactly as it will be sent.
 * `null` when the text is not a header GAIA wrote, which then shows whole.
 */
export function splitMailHeader(text: string): MailHeader | null {
    const lines = text.split('\n');
    const opens = (line: string | undefined) => !!line && /^(From|To): /.test(line);
    // Notes come first, each its own paragraph; the header is the first paragraph that opens like one.
    let start = 0;
    while (!opens(lines[start])) {
        const gap = lines.indexOf('', start);
        if (gap < 0) return null;
        start = gap + 1;
    }
    const fields: MailHeader['fields'] = [];
    let at = start;
    for (; at < lines.length && lines[at] !== ''; at++) {
        const match = MAIL_FIELD.exec(lines[at]);
        if (!match) return null;
        fields.push([match[1], match[2]]);
    }
    if (!fields.some(([label]) => label === 'To')) return null;
    return { before: lines.slice(0, Math.max(0, start - 1)).join('\n'), fields, body: lines.slice(at + 1).join('\n') };
}

function MailFacts({ header }: { header: MailHeader }) {
    const t = useT();
    const key = new Map(MAIL_FIELDS);
    return (
        <dl className="perm-card-facts perm-mail-header" data-testid="perm-mail-header">
            {header.fields.map(([label, value], i) => (
                <Fragment key={i}>
                    <dt>{t(key.get(label)!)}</dt>
                    <dd className={label === 'From' ? 'perm-mail-from' : undefined}>
                        <bdi dir={MAIL_ADDRESS_FIELDS.has(label) ? 'ltr' : 'auto'}>{value}</bdi>
                    </dd>
                </Fragment>
            ))}
        </dl>
    );
}

/**
 * A relayed command prompt with no args: `command[\n…]\nin <folder>[\n(extras)]\n<risk note>`
 * (`render_command_summary` in sse_translation.py). The command keeps its own lines.
 */
export function splitCommandSummary(summary: string): { command: string; folder: string; extras: string; note: string } | null {
    const lines = summary.split('\n');
    if (lines.length < 2) return null;
    // No note when the call carries no risk worth naming: the folder is then last.
    const hasNote = !lines[lines.length - 1].startsWith('in ');
    const note = hasNote ? lines[lines.length - 1].trim() : '';
    const body = hasNote ? lines.slice(0, -1) : lines;
    let at = -1;
    for (let i = body.length - 1; i > 0; i--) {
        if (body[i].startsWith('in ')) { at = i; break; }
    }
    if (at < 0) return { command: body.join('\n'), folder: '', extras: '', note };
    return {
        command: body.slice(0, at).join('\n'),
        folder: body[at].slice(3).trim(),
        extras: body.slice(at + 1).join(' ').trim(),
        note,
    };
}

/** A grant's folder by its name alone, for a button; the full path stays in its tooltip. */
export function shortScope(scope: string): string {
    // A translated scope wraps its path in direction isolates (U+2068 ... U+2069).
    return scope.replace(/(\s\u2068?)((?:[A-Za-z]:)?[\\/][^\u2069]*)(\u2069?)$/, (_, lead: string, path: string, close: string) => {
        const name = path.split(/[\\/]/).filter(Boolean).pop();
        return `${lead}${name ?? path}${close}`;
    });
}

/** Lines of code a prompt shows before "Show all". */
export const CODE_PREVIEW_LINES = 12;

/** Long text, collapsed past `previewLines` lines (the code a gated call runs, a tool's output). */
export function CodeBlock({ code, previewLines = CODE_PREVIEW_LINES, testId = 'perm-code', exact = false }: {
    code: string;
    previewLines?: number;
    testId?: string;
    /** Keep every blank line; only the final line break is dropped. */
    exact?: boolean;
}) {
    const t = useT();
    const [expanded, setExpanded] = useState(false);
    const lines = (exact ? code.replace(/\n$/, '') : code.replace(/^\n+|\n+$/g, '')).split('\n');
    const long = lines.length > previewLines;
    const shown = (long && !expanded ? lines.slice(0, previewLines) : lines).join('\n');
    return (
        <>
            <pre dir="ltr" className={`perm-card-pre perm-card-code${expanded ? ' is-expanded' : ''}`} data-testid={testId}>{shown}</pre>
            {long && (
                <button
                    type="button"
                    className="perm-card-args-toggle"
                    onClick={() => setExpanded((v) => !v)}
                    aria-expanded={expanded}
                >
                    {expanded ? t('prompt.showLess') : t('prompt.showAllLines', { n: lines.length })}
                </button>
            )}
        </>
    );
}

/**
 * The permission question for this chat, inline above the composer: allow once,
 * always allow this exact call in this chat, or deny — the TUI's choices.
 *
 * Enter never answers it: focus moves to the card, not a button, so a keystroke
 * meant for the composer can't approve. Esc denies; Tab reaches the buttons.
 */
export function PermissionPrompt({ sessionId }: { sessionId: string }) {
    const selector = useMemo(() => selectSessionPermissionPrompt(sessionId), [sessionId]);
    const prompt = useNotificationStore(selector);
    if (!prompt) return null;
    return <PromptCard key={prompt.id} notification={prompt} />;
}

/** `45` -> "45s", `600` -> "10:00". */
export function formatCountdown(seconds: number): string {
    if (seconds < 60) return `${seconds}s`;
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${String(secs).padStart(2, '0')}`;
}

function PromptCard({ notification }: { notification: GaiaNotification }) {
    const t = useT();
    const respond = useNotificationStore((s) => s.respondToPermission);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [showArgs, setShowArgs] = useState(false);
    const hasTimeout = notification.timeoutSeconds != null && notification.timeoutSeconds > 0;
    // A deadline, not a tick count: background tabs throttle timers, and the
    // backend denies at its own deadline regardless of how often we ticked.
    // A prompt replayed after a reload keeps the deadline it was asked with.
    const deadline = useRef(hasTimeout ? notification.expiresAt ?? Date.now() + notification.timeoutSeconds! * 1000 : null);
    const [remaining, setRemaining] = useState<number | null>(
        deadline.current === null ? null : Math.max(0, Math.ceil((deadline.current - Date.now()) / 1000)),
    );
    const cardRef = useRef<HTMLElement>(null);
    const freshConsent = requiresFreshConsent(notification.tool);
    const scope = freshConsent ? undefined : notification.alwaysScope;
    // Shell scopes are already the command itself, so only file and skill grants carry a label.
    const scopeText = alwaysScopeText(notification, t) ?? notification.alwaysLabel ?? scope ?? '';
    const repeats = useNotificationStore((s) => repeatedAllowCount(s.notifications, notification));
    const suggestAlways = !!scope && repeats > 0;
    const detail = summarize(notification);
    const args = notification.toolArgs ?? {};
    const hasArgs = Object.keys(args).length > 0;
    // A call GAIA's own checks don't cover is approved on its exact text, once:
    // show that text unwrapped, the folder it runs in, and its risk note.
    const riskNote = typeof args._risk_note === 'string' ? args._risk_note : '';
    const folder = riskNote && typeof args.working_directory === 'string' ? args.working_directory : '';
    // Code is approved as written, so all of it is one click away, never only a summary.
    const code = typeof args.code === 'string' && args.code.trim() ? args.code : '';
    // An email is approved as it will be sent, so all of it shows, uncollapsed.
    // Over the sidecar relay it arrives as the summary.
    const exactText = typeof args._exact_text === 'string'
        ? args._exact_text
        : EXACT_SUMMARY_TOOLS.has(notification.tool ?? '') ? notification.summary ?? '' : '';
    const relayed = !hasArgs && COMMAND_SUMMARY_TOOLS.has(notification.tool ?? '')
        ? splitCommandSummary(notification.summary ?? '')
        : null;
    const mail = exactText ? splitMailHeader(exactText) : null;
    const isPath = notification.tool === PATH_ACCESS_TOOL;
    const { heading, question } = permissionPromptText(notification, t, markLtr);

    const calendar = CALENDAR_WRITE_TOOLS.has(notification.tool ?? '');

    // Take focus from the composer so its Enter can't land on a button here.
    useEffect(() => { cardRef.current?.focus({ preventScroll: true }); }, []);

    // The backend denies on its own when the wait runs out; this only shows it.
    useEffect(() => {
        if (deadline.current === null) return;
        const t = setInterval(() => {
            const left = Math.max(0, Math.ceil((deadline.current! - Date.now()) / 1000));
            setRemaining(left);
            if (left === 0) clearInterval(t);
        }, 1000);
        return () => clearInterval(t);
    }, []);

    const answer = useCallback(async (decision: PermissionDecision) => {
        setBusy(true);
        setError(null);
        try {
            await respond(notification.id, decision);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
            setBusy(false);
        }
    }, [notification.id, respond]);

    // Esc denies. Enter is deliberately unbound — only a focused button approves.
    // Capture phase, so this runs before ChatView's Escape-stops-the-reply.
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key !== 'Escape' || isEscapeClaimed(e)) return;
            if (remaining !== null && remaining <= 0) return;
            // Claimed even mid-answer: a second Esc would otherwise stop the whole reply.
            e.preventDefault();
            if (!busy) void answer('deny');
        };
        window.addEventListener('keydown', onKey, true);
        return () => window.removeEventListener('keydown', onKey, true);
    }, [answer, busy, remaining]);

    const expired = remaining !== null && remaining <= 0;
    return (
        <section
            ref={cardRef}
            tabIndex={-1}
            className="perm-card"
            role="alertdialog"
            aria-labelledby={`perm-${notification.id}`}
        >
            <div className="perm-card-head">
                <ShieldQuestion size={16} className="perm-card-icon" aria-hidden="true" />
                <div className="perm-card-text">
                    <h2 id={`perm-${notification.id}`} className="perm-card-title">
                        <MarkedText text={heading} />
                    </h2>
                    {detail && riskNote && <pre dir="ltr" className="perm-card-pre perm-card-exact">{detail}</pre>}
                    {detail && !riskNote && <p className="perm-card-detail"><code dir="ltr">{detail}</code></p>}
                    {folder && <p className="perm-card-detail">{t.bare('prompt.in')} <code dir="ltr">{folder}</code></p>}
                    {riskNote && <p className="perm-card-risk" role="note"><bdi>{riskNote}</bdi></p>}
                    {code && <CodeBlock code={code} />}
                    {mail?.before && <pre dir="ltr" className="perm-card-pre perm-card-exact">{mail.before}</pre>}
                    {mail && <MailFacts header={mail} />}
                    {exactText && <pre dir="ltr" className="perm-card-pre perm-card-exact" data-testid="perm-exact-text">{mail ? mail.body : exactText}</pre>}
                    {relayed && <pre dir="ltr" className="perm-card-pre perm-card-exact">{relayed.command}</pre>}
                    {relayed?.folder && <p className="perm-card-detail">{t.bare('prompt.in')} <code dir="ltr">{relayed.folder}</code></p>}
                    {relayed?.extras && <p className="perm-card-detail"><bdi>{relayed.extras}</bdi></p>}
                    {relayed?.note && <p className="perm-card-risk" role="note"><bdi>{relayed.note}</bdi></p>}
                    {calendar && hasArgs && <CalendarApproval tool={notification.tool!} args={args} />}
                    {calendar && !hasArgs && notification.summary && <pre dir="ltr" className="perm-card-pre perm-card-exact" data-testid="perm-exact-summary">{notification.summary}</pre>}
                    {!detail && !code && !exactText && !calendar && notification.summary && !relayed && <p className="perm-card-detail"><bdi>{notification.summary}</bdi></p>}
                    {freshConsent && <p className="perm-card-detail perm-card-question"><MarkedText text={question} /></p>}
                    <ChangePreview args={args} />
                </div>
                {remaining !== null && !expired && (
                    <span className="perm-card-timer" title={t('prompt.timer')}>{formatCountdown(remaining)}</span>
                )}
            </div>
            {hasArgs && !exactText && (
                <div className="perm-card-args">
                    <button
                        type="button"
                        className="perm-card-args-toggle"
                        onClick={() => setShowArgs((v) => !v)}
                        aria-expanded={showArgs}
                    >
                        <ChevronRight size={13} className={showArgs ? 'is-open' : ''} aria-hidden="true" />
                        {t(fileChange(args) ? 'prompt.technicalDetails' : 'prompt.details')}
                    </button>
                    {showArgs && <pre dir="ltr" className="perm-card-pre">{JSON.stringify(args, null, 2)}</pre>}
                </div>
            )}
            {suggestAlways && !expired && (
                <p className="perm-card-hint">
                    {t.compose((say) => `${say(pluralKey('prompt.repeated', repeats), { n: repeats })} ${say('prompt.alwaysHint', { scope: scopeText })}`)}
                </p>
            )}
            {expired ? (
                <p className="perm-card-detail">{t('prompt.expired')}</p>
            ) : (
                <div className="perm-card-actions">
                    <button type="button" className="perm-btn is-primary" onClick={() => answer('allow')} disabled={busy}>
                        {/* Granting a path lasts for the chat, as the question says. */}
                        {t(notification.tool === PROJECT_TRUST_TOOL ? 'prompt.trust.allow' : isPath ? 'prompt.allowForChat' : 'prompt.allowOnce')}
                    </button>
                    {scope && (
                        <button
                            type="button"
                            className={`perm-btn${suggestAlways ? ' is-suggested' : ''}`}
                            onClick={() => answer('always')}
                            disabled={busy}
                            title={scopeText}
                        >
                            {t('prompt.alwaysAllow', { scope: shortScope(scopeText) })}
                        </button>
                    )}
                    <button type="button" className="perm-btn" onClick={() => answer('deny')} disabled={busy}>
                        {t('prompt.deny')}
                    </button>
                </div>
            )}
            {error && <p className="perm-card-error" role="alert">{error}</p>}
        </section>
    );
}
