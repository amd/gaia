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
import {
    CalendarApproval, CALENDAR_WRITE_TOOLS, unseen, unseenCommand, unseenStrict, visibleCommand, visibleStrict, visibleText,
} from './CalendarApproval';
import { cmdReading, decodedPowerShell } from './shownText';
import { pluralKey, type MessageKey } from '../i18n/registry';
import { useT } from '../i18n/useT';
import { MarkedText, markLtr } from './MarkedText';
import { ChangePreview, fileChange } from './PermissionChangePreview';
import { riskNoteText } from './riskNote';
import './PermissionPrompt.css';

/** One-line description of a tool call, e.g. `run_shell_command · git status`. */
function summarize(n: GaiaNotification): { key: string; text: string } {
    const args = n.toolArgs ?? {};
    // _site: the site a browser-pane read would show the model (SITE_ARG in tool_grants.py).
    const key = ['command', 'file_path', 'path', 'url', '_site', 'query', 'name']
        .find((k) => typeof args[k] === 'string' && (args[k] as string).trim());
    return key ? { key, text: args[key] as string } : { key: '', text: '' };
}

/** A command keeps its layout, a search query reads as prose, and anything else is a name. */
function shownField(key: string, text: string): { shown: string; coded: boolean } {
    if (COMMAND_ARG.test(key)) return { shown: visibleCommand(text), coded: unseenCommand(text) };
    if (key === 'query') return { shown: visibleText(text, true), coded: unseen(text) };
    return { shown: visibleStrict(text), coded: unseenStrict(text) };
}

/** Keys that hold a command, read with the command rule wherever they show. */
const COMMAND_ARG = /^(command|cmd|argv)$/;

/**
 * Keys read as a name or a command: `summarize`'s list less `query` (prose), plus any key
 * shaped like a path, folder, file, URL, name, id or move end, so a tool's `output_path`
 * or an MCP server's `destination` gets the name rule without being listed by hand.
 */
const NAME_LIKE_ARG = new RegExp(
    '^(command|cmd|argv|_site|to|from|href)$'
    + '|(^|_)(files?|urls?|uris?|ids?|guids?|uuids?|cwds?|src|dest|source|destination|target|location|branch|repo)$'
    + '|(paths?|dirs?|director(y|ies)|folders?|names?)(_hint)?$',
);

/** Whether a key, snake_case or camelCase (`baseURI`, `HTTPUrl`, `path2`), names a thing rather than holding prose. */
function nameLikeArg(key: string): boolean {
    const snake = key
        .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
        .replace(/([A-Z])([A-Z][a-z])/g, '$1_$2')
        .toLowerCase()
        .replace(/\d+$/, '');
    return NAME_LIKE_ARG.test(snake);
}

/**
 * Argument values as the main field shows them: each name-like string, alone or in a
 * list, marked with that field's rule, so the expanded view reads like the card above it.
 * The rest — code, prose, the approved-exact text — stay as written for the prose pass.
 */
function shownArgs(args: Record<string, unknown>): Record<string, unknown> {
    const shown = (key: string, value: unknown): unknown => {
        if (typeof value === 'string') return nameLikeArg(key) ? shownField(key, value).shown : value;
        if (Array.isArray(value)) return value.map((item) => shown(key, item));
        if (value && typeof value === 'object') return shownArgs(value as Record<string, unknown>);
        return value;
    };
    return Object.fromEntries(Object.entries(args).map(([key, value]) => [key, shown(key, value)]));
}

/** Tools whose relayed summary is a command and its risk note (`COMMAND_SUMMARY_TOOLS` in sse_translation.py). */
const COMMAND_SUMMARY_TOOLS = new Set(['run_shell_command', 'wait_for_condition']);

/** Tools that run code GAIA hands over whole (`CODE_SUMMARY_TOOLS` in sse_translation.py). */
const CODE_TOOLS = new Set(['run_python', 'execute_python_file']);

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

/** The tool whose prompt carries the file it runs (`SCRIPT_TOOL` in script_preview.py). */
const SCRIPT_TOOL = 'execute_python_file';

/** Why a script is not on its card (`unreadable` in script_preview.py), as message keys. */
const SCRIPT_UNREADABLE = {
    missing: 'prompt.scriptUnreadable.missing',
    not_text: 'prompt.scriptUnreadable.not_text',
    too_large: 'prompt.scriptUnreadable.too_large',
    unreadable: 'prompt.scriptUnreadable.unreadable',
} as const;

/**
 * The script a file-running call runs, as GAIA read it when it built the prompt
 * (`SCRIPT_ARG` in call_risk.py): its text and the lines past the cap, or why there is none.
 */
export function shownScript(tool: string | undefined, args: Record<string, unknown>): {
    text: string | null;
    hiddenLines: number;
    unreadable: keyof typeof SCRIPT_UNREADABLE | null;
} | null {
    const script = args._script;
    if (tool !== SCRIPT_TOOL || typeof script !== 'object' || script === null) return null;
    const { text, hidden_lines: hidden, unreadable } = script as Record<string, unknown>;
    if (typeof text === 'string') {
        return { text, hiddenLines: typeof hidden === 'number' && hidden > 0 ? hidden : 0, unreadable: null };
    }
    const reason = typeof unreadable === 'string' && unreadable in SCRIPT_UNREADABLE
        ? unreadable as keyof typeof SCRIPT_UNREADABLE
        : 'unreadable';
    return { text: null, hiddenLines: 0, unreadable: reason };
}

/** Longest command a button shows whole; a longer one keeps both ends around a visible "…". */
const SCOPE_COMMAND_CHARS = 64;

/** A grant's folder by its name alone, for a button; the full path stays in its tooltip. */
export function shortScope(scope: string): string {
    // A command (gaiacpp quotes it in backticks) is never shortened by its paths: `cp /a/x /b/y`
    // would read `cp y`, and `mkdir /a && rm -rf /b` would read `mkdir b`. It is shown as written,
    // or cut where the cut shows.
    if (scope.includes('`')) {
        if (scope.length <= SCOPE_COMMAND_CHARS) return scope;
        return `${scope.slice(0, SCOPE_COMMAND_CHARS - 20)} … ${scope.slice(-16)}`;
    }
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
    // It names a file or skill the model chose, so its hidden characters show as codes.
    const markedScope = notification.alwaysScope && visibleStrict(notification.alwaysScope);
    const scopeText = alwaysScopeText({ ...notification, alwaysScope: markedScope }, t)
        ?? visibleStrict(notification.alwaysLabel ?? scope ?? '');
    const repeats = useNotificationStore((s) => repeatedAllowCount(s.notifications, notification));
    const suggestAlways = !!scope && repeats > 0;
    const field = summarize(notification);
    const detail = field.text;
    const args = notification.toolArgs ?? {};
    const hasArgs = Object.keys(args).length > 0;
    // A call GAIA's own checks don't cover is approved on its exact text, once:
    // show that text unwrapped, the folder it runs in, and its risk note.
    const riskNote = typeof args._risk_note === 'string' ? riskNoteText(args._risk_note, t) : '';
    const folder = riskNote && typeof args.working_directory === 'string' ? args.working_directory : '';
    // Code is approved as written, so all of it is one click away, never only a summary.
    // A file is approved on its content too: the path alone does not say what runs.
    const script = shownScript(notification.tool, args);
    const code = typeof args.code === 'string' && args.code.trim() ? args.code : script?.text?.trim() ? script.text : '';
    // GAIA screens a script's text but can't see what it does when it runs.
    const codeNote = (code || CODE_TOOLS.has(notification.tool ?? '')) && !riskNote ? t('prompt.codeRisk') : '';
    // The script is on the card already; the raw arguments don't repeat it.
    const detailArgs = script ? Object.fromEntries(Object.entries(args).filter(([key]) => key !== '_script')) : args;
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
    // Characters that don't show are shown as [U+XXXX]: the card is approved as it reads.
    const { heading, question } = permissionPromptText(notification, t, (s) => markLtr(visibleStrict(s)));
    const { shown: shownDetail, coded: detailCoded } = shownField(field.key, detail);
    const command = typeof args.command === 'string' ? args.command : relayed?.command ?? '';
    // What the command runs when its own text hides it: base64 PowerShell, cmd's ^ escapes.
    const decoded = command ? decodedPowerShell(command) : '';
    const reading = command.includes('^') ? cmdReading(command) : '';
    const hidden = detailCoded || [code, relayed?.extras].some(unseen) || [command, decoded].some(unseenCommand)
        || [args.path, folder, relayed?.folder].some(unseenStrict);

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
                    {detail && riskNote && <pre dir="ltr" className="perm-card-pre perm-card-exact">{shownDetail}</pre>}
                    {detail && !riskNote && <p className="perm-card-detail"><code dir="ltr">{shownDetail}</code></p>}
                    {folder && <p className="perm-card-detail">{t.bare('prompt.in')} <code dir="ltr">{visibleStrict(folder)}</code></p>}
                    {riskNote && <p className="perm-card-risk" role="note"><bdi>{riskNote}</bdi></p>}
                    {code && <CodeBlock code={visibleText(code, true)} />}
                    {script?.text != null && !script.text.trim() && (
                        <p className="perm-card-detail" data-testid="perm-script-empty">{t('prompt.scriptEmpty')}</p>
                    )}
                    {!!script?.hiddenLines && (
                        <p className="perm-card-risk" role="note" data-testid="perm-script-more">
                            <bdi>{t.plural('prompt.scriptMoreLines', script.hiddenLines, { n: script.hiddenLines })}</bdi>
                        </p>
                    )}
                    {script?.unreadable && (
                        <p className="perm-card-risk" role="note" data-testid="perm-script-unreadable">
                            <bdi>{t(SCRIPT_UNREADABLE[script.unreadable])}</bdi>
                        </p>
                    )}
                    {codeNote && <p className="perm-card-risk" role="note" data-testid="perm-code-risk"><bdi>{codeNote}</bdi></p>}
                    {mail?.before && <pre dir="ltr" className="perm-card-pre perm-card-exact">{mail.before}</pre>}
                    {mail && <MailFacts header={mail} />}
                    {exactText && <pre dir="ltr" className="perm-card-pre perm-card-exact" data-testid="perm-exact-text">{mail ? mail.body : exactText}</pre>}
                    {relayed && <pre dir="ltr" className="perm-card-pre perm-card-exact">{visibleCommand(relayed.command)}</pre>}
                    {decoded && <p className="perm-card-detail">{t('prompt.decodedCommand')}</p>}
                    {decoded && <pre dir="ltr" className="perm-card-pre perm-card-exact" data-testid="perm-decoded">{visibleCommand(decoded)}</pre>}
                    {reading && reading !== command && <p className="perm-card-detail">{t('prompt.cmdReading')}</p>}
                    {reading && reading !== command && <pre dir="ltr" className="perm-card-pre perm-card-exact" data-testid="perm-cmd-reading">{visibleCommand(reading)}</pre>}
                    {hidden && <p className="perm-card-risk" role="note" data-testid="perm-hidden-note"><bdi>{t('prompt.hiddenChars')}</bdi></p>}
                    {relayed?.folder && <p className="perm-card-detail">{t.bare('prompt.in')} <code dir="ltr">{visibleStrict(relayed.folder)}</code></p>}
                    {relayed?.extras && <p className="perm-card-detail"><bdi>{visibleText(relayed.extras, false)}</bdi></p>}
                    {relayed?.note && <p className="perm-card-risk" role="note"><bdi>{relayed.note}</bdi></p>}
                    {calendar && hasArgs && <CalendarApproval tool={notification.tool!} args={args} />}
                    {calendar && !hasArgs && notification.summary && <pre dir="ltr" className="perm-card-pre perm-card-exact" data-testid="perm-exact-summary">{notification.summary}</pre>}
                    {!detail && !code && !exactText && !calendar && notification.summary && !relayed && <p className="perm-card-detail"><bdi>{visibleText(notification.summary, true)}</bdi></p>}
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
                    {showArgs && <pre dir="ltr" className="perm-card-pre">{visibleText(JSON.stringify(shownArgs(detailArgs), null, 2), true)}</pre>}
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
                            className={`perm-btn is-lasting${suggestAlways ? ' is-suggested' : ''}`}
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
