// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Check, Copy, ExternalLink, Loader2 } from 'lucide-react';
import { useT } from '../../i18n/useT';
import { ApiError } from '../../services/api';
import {
    connectDiscord,
    disconnectDiscord,
    getDiscordStatus,
    pairDiscordAgain,
    startDiscord,
    stopDiscord,
    restartDiscord,
    forgetDiscordAlways,
    type DiscordStatus,
} from '../../services/messagingApi';
import { setVisibleInterval } from '../../utils/visibleInterval';
import { ConfirmSheet } from '../setup/ConfirmSheet';

const POLL_MS = 3000;
/** States the bridge can leave by itself (a crash included), so the card watches them. */
const LIVE = new Set(['starting', 'pairing', 'connected']);
/** States shown as a problem to fix. */
const PROBLEMS = new Set(['error', 'taken_over', 'token_invalid', 'credentials_unavailable', 'conflict']);
/** Where the card's own sentence can't say why, so the bridge's sentence is shown under it. */
const SHOW_DETAIL = new Set(['error', 'credentials_unavailable']);

/** Button names exactly as Discord shows them, so the user can find what they read. */
const PORTAL = {
    newApp: 'New Application',
    create: 'Create',
    bot: 'Bot',
    reset: 'Reset Token',
    copy: 'Copy',
    publicBot: 'Public Bot',
    message: 'Message',
    createOwn: 'Create My Own',
    forFriends: 'For me and my friends',
};

type Action = 'connect' | 'start' | 'takeOver' | 'stop' | 'restart' | 'forget' | 'pair' | 'disconnect';

function errorText(err: unknown): string {
    if (err instanceof ApiError) return err.detail || err.message;
    return err instanceof Error ? err.message : String(err);
}

/** "ABCD2345" as "ABCD-2345"; the bot accepts it with or without the dash. */
export function formatPairingCode(code: string): string {
    return code.length === 8 ? `${code.slice(0, 4)}-${code.slice(4)}` : code;
}

function LinkButton({ href, children, primary }: { href: string; children: ReactNode; primary?: boolean }) {
    return (
        <a className={`${primary ? 'btn-primary' : 'btn-secondary'} messaging-link`} href={href} target="_blank" rel="noopener noreferrer">
            {children}
            <ExternalLink size={13} aria-hidden="true" className="messaging-link-icon" />
        </a>
    );
}

function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
    return (
        <li className="messaging-step">
            <span className="messaging-step-number" aria-hidden="true"><bdi dir="ltr">{n}</bdi></span>
            <div className="messaging-step-body">
                <h4 className="settings-label">{title}</h4>
                {children}
            </div>
        </li>
    );
}

/** Settings > Messaging: connect a Discord bot that the agent on this PC answers through. */
export function MessagingSettings() {
    const t = useT();
    const [status, setStatus] = useState<DiscordStatus | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [busy, setBusy] = useState<Action | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);
    const [token, setToken] = useState('');
    const [botPublic, setBotPublic] = useState(false);
    const [justPaired, setJustPaired] = useState(false);
    const [copied, setCopied] = useState<'ok' | 'failed' | null>(null);
    const [confirmDisconnect, setConfirmDisconnect] = useState(false);
    const wasPairing = useRef(false);

    const show = useCallback((next: DiscordStatus) => {
        if (wasPairing.current && next.paired && next.state === 'connected') setJustPaired(true);
        wasPairing.current = next.state === 'pairing';
        setStatus(next);
    }, []);

    const load = useCallback(() => {
        getDiscordStatus()
            .then((s) => { setLoadError(null); show(s); })
            .catch((err) => setLoadError(errorText(err)));
    }, [show]);

    useEffect(load, [load]);
    useEffect(() => setCopied(null), [status?.pairing_code]);

    const watching = status !== null && LIVE.has(status.state);
    useEffect(() => {
        if (!watching) return undefined;
        return setVisibleInterval(load, POLL_MS);
    }, [watching, load]);

    const run = async (action: Action, call: () => Promise<DiscordStatus>) => {
        setBusy(action);
        setActionError(null);
        setJustPaired(false);
        try {
            show(await call());
        } catch (err) {
            setActionError(errorText(err));
        } finally {
            setBusy(null);
        }
    };

    const connect = async () => {
        setBusy('connect');
        setActionError(null);
        try {
            const result = await connectDiscord(token.trim());
            setToken('');
            setBotPublic(result.connect.bot_public);
            show(result.status);
        } catch (err) {
            setActionError(errorText(err));
        } finally {
            setBusy(null);
        }
    };

    const copyCode = (code: string) => {
        navigator.clipboard.writeText(code).then(
            () => setCopied('ok'),
            () => setCopied('failed'),
        );
    };

    const spinner = (action: Action) => busy === action && <Loader2 size={13} className="spin" aria-hidden="true" />;

    if (!status) {
        return (
            <div className="settings-pane">
                <h2 className="settings-pane-title">{t('settings.section.messaging')}</h2>
                {loadError ? (
                    <div className="settings-card">
                        <p className="settings-error" role="alert">{t('settings.messaging.loadFailed', { error: loadError })}</p>
                        <button type="button" className="btn-secondary" onClick={load}>{t('settings.messaging.retry')}</button>
                    </div>
                ) : (
                    <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.loading')}</p>
                )}
            </div>
        );
    }

    const s = status;
    const needsToken = !s.connected || s.state === 'token_invalid' || s.state === 'conflict';
    const pairing = s.connected && !needsToken && (s.state === 'pairing' || (s.state === 'connected' && !s.paired));
    const answering = s.connected && s.paired && s.state === 'connected';

    const tokenSteps = (
        <ol className="messaging-steps">
            <Step n={1} title={t('settings.messaging.step1.title')}>
                <ol className="messaging-substeps settings-hint">
                    <li>{t('settings.messaging.step1.a', { label: PORTAL.newApp })}</li>
                    <li>{t('settings.messaging.step1.b', { label: PORTAL.create })}</li>
                    <li>{t('settings.messaging.step1.c', { label: PORTAL.bot })}</li>
                    <li>{t('settings.messaging.step1.d', { reset: PORTAL.reset, copy: PORTAL.copy })}</li>
                </ol>
                <p className="settings-hint">{t('settings.messaging.step1.noIntents')}</p>
                <div className="settings-row">
                    <LinkButton href={s.portal_url}>{t('settings.messaging.openPortal')}</LinkButton>
                </div>
            </Step>
            <Step n={2} title={t('settings.messaging.step2.title')}>
                <form
                    className="settings-row"
                    onSubmit={(e) => { e.preventDefault(); if (token.trim() && !busy) void connect(); }}
                >
                    <input
                        type="password"
                        className="settings-input"
                        aria-label={t('settings.messaging.tokenLabel')}
                        placeholder={t('settings.messaging.tokenPlaceholder')}
                        autoComplete="off"
                        spellCheck={false}
                        value={token}
                        onChange={(e) => setToken(e.target.value)}
                        disabled={busy !== null}
                    />
                    <button type="submit" className="btn-primary" disabled={!token.trim() || busy !== null}>
                        {spinner('connect')}
                        {busy === 'connect' ? t('settings.messaging.connecting') : t('settings.messaging.connect')}
                    </button>
                </form>
                {busy === 'connect' && <p className="settings-hint">{t('settings.messaging.connectingHint')}</p>}
            </Step>
        </ol>
    );

    const pairingSteps = (
        <ol className="messaging-steps" start={3}>
            <Step n={3} title={t('settings.messaging.step3.title')}>
                <p className="settings-hint">{t('settings.messaging.step3.why')}</p>
                <p className="settings-hint">{t('settings.messaging.step3.how', { own: PORTAL.createOwn, friends: PORTAL.forFriends })}</p>
                <div className="settings-row">
                    <LinkButton href={s.invite_url}>{t('settings.messaging.openInvite')}</LinkButton>
                </div>
                <p className="settings-note">{t('settings.messaging.step3.safe')}</p>
            </Step>
            <Step n={4} title={t('settings.messaging.step4.title')}>
                {s.pairing_code ? (
                    <div className="messaging-code-row">
                        <bdi dir="ltr" className="messaging-code" data-testid="discord-pairing-code">{formatPairingCode(s.pairing_code)}</bdi>
                        <button type="button" className="btn-secondary" onClick={() => copyCode(s.pairing_code)}>
                            {copied === 'ok' ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
                            {copied === 'ok' ? t('settings.messaging.copied') : t('settings.messaging.copy')}
                        </button>
                    </div>
                ) : (
                    <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.messaging.waitingCode')}</p>
                )}
                {copied === 'failed' && <p className="settings-error" role="alert">{t('settings.messaging.copyFailed')}</p>}
                {s.bot_profile_url && (
                    <div className="settings-row">
                        <LinkButton href={s.bot_profile_url}>{t('settings.messaging.openBot')}</LinkButton>
                    </div>
                )}
                <p className="settings-hint">{t('settings.messaging.step4.hint', { label: PORTAL.message })}</p>
                <p className="settings-note">{t('settings.messaging.step4.onlyYou')}</p>
            </Step>
        </ol>
    );

    const fix = (() => {
        switch (s.state) {
            case 'stopped':
            case 'error':
                return <button type="button" className="btn-primary" disabled={busy !== null} onClick={() => run('start', () => startDiscord())}>{spinner('start')}{t('settings.messaging.start')}</button>;
            case 'credentials_unavailable':
                return <button type="button" className="btn-primary" disabled={busy !== null} onClick={() => run('start', () => startDiscord())}>{spinner('start')}{t('settings.messaging.retry')}</button>;
            case 'taken_over':
                return <button type="button" className="btn-primary" disabled={busy !== null} onClick={() => run('takeOver', () => startDiscord(true))}>{spinner('takeOver')}{t('settings.messaging.takeOver')}</button>;
            default:
                return null;
        }
    })();

    // A start blocks until the bridge reports in (up to a minute), and status
    // still reads "stopped" meanwhile; say it is starting, not that it is off.
    const startingNow = s.state === 'starting' || busy === 'start' || busy === 'takeOver';
    const explain: Partial<Record<DiscordStatus['state'], string>> = {
        // Stop turns autostart off, so stopped with it on means nobody stopped it.
        stopped: t(s.autostart ? 'settings.messaging.state.crashed' : 'settings.messaging.state.stopped'),
        error: t('settings.messaging.state.error'),
        taken_over: t('settings.messaging.state.takenOver'),
        token_invalid: t('settings.messaging.state.tokenInvalid'),
        credentials_unavailable: t('settings.messaging.state.credentials'),
        conflict: t('settings.messaging.state.conflict'),
    };

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">{t('settings.section.messaging')}</h2>
            <section className="settings-card messaging-card" aria-label={t('settings.messaging.discord')}>
                <div className="settings-row settings-row-between">
                    <h3 className="settings-label messaging-card-title">{t('settings.messaging.discord')}</h3>
                    {s.connected && s.bot_name && (
                        <span className="settings-hint">{t('settings.messaging.botIs')} <bdi>{s.bot_name}</bdi></span>
                    )}
                </div>

                {!s.connected && (
                    <>
                        <p className="settings-pane-lede">{t('settings.messaging.pitch')}</p>
                        <p className="settings-hint">{t('settings.messaging.onlyYou')}</p>
                    </>
                )}

                {explain[s.state] && s.connected && !startingNow && (
                    <div className={PROBLEMS.has(s.state) || (s.state === 'stopped' && s.autostart) ? 'settings-notice' : 'messaging-status'}>
                        <p>{explain[s.state]}</p>
                        {SHOW_DETAIL.has(s.state) && s.detail && <p className="settings-hint"><bdi dir="auto">{s.detail}</bdi></p>}
                        {fix}
                    </div>
                )}
                {!s.connected && s.state === 'conflict' && (
                    <div className="settings-notice">
                        <p>{t('settings.messaging.state.conflict')}</p>
                    </div>
                )}

                {s.connected && startingNow && (
                    <p className="settings-hint" role="status"><Loader2 size={13} className="spin" /> {t('settings.messaging.starting')}</p>
                )}

                {needsToken && tokenSteps}

                {pairing && (
                    <>
                        <p className="settings-hint">{t('settings.messaging.pairingLede')}</p>
                        {pairingSteps}
                        {botPublic && s.bot_page_url && (
                            <div className="settings-notice">
                                <p>{t('settings.messaging.publicTip', { label: PORTAL.publicBot })}</p>
                                <LinkButton href={s.bot_page_url}>{t('settings.messaging.openBotPage')}</LinkButton>
                            </div>
                        )}
                        {s.state === 'connected' && !s.paired && (
                            <button type="button" className="btn-primary" disabled={busy !== null} onClick={() => run('pair', pairDiscordAgain)}>{spinner('pair')}{t('settings.messaging.showCode')}</button>
                        )}
                    </>
                )}

                {answering && (
                    <>
                        {justPaired && <p className="settings-ok" role="status">{t('settings.messaging.paired')}</p>}
                        <p className="messaging-answering">
                            <span className="settings-key-status settings-ok" />
                            <span className="messaging-answering-text">
                                <span>{t('settings.messaging.answering', { owner: s.owner_name, bot: s.bot_name })}</span>
                                {s.model && <bdi className="settings-hint messaging-model">{s.model}</bdi>}
                            </span>
                        </p>
                        <p className="settings-hint">{t('settings.messaging.answeringHint')}</p>
                        <div className="settings-row">
                            {s.bot_profile_url && <LinkButton href={s.bot_profile_url} primary>{t('settings.messaging.openInDiscord')}</LinkButton>}
                            <button type="button" className="btn-secondary" disabled={busy !== null} onClick={() => run('stop', stopDiscord)}>{spinner('stop')}{t('settings.messaging.stop')}</button>
                            <button type="button" className="btn-secondary" disabled={busy !== null} onClick={() => run('restart', restartDiscord)}>{spinner('restart')}{t('settings.messaging.restart')}</button>
                            <button type="button" className="btn-secondary" disabled={busy !== null} onClick={() => run('pair', pairDiscordAgain)}>{spinner('pair')}{t('settings.messaging.pairOther')}</button>
                        </div>
                    </>
                )}

                {s.connected && (s.always_allowed?.length ?? 0) > 0 && (
                    <div className="messaging-status">
                        <p>{t('settings.messaging.alwaysAllowed')}</p>
                        <ul className="settings-hint messaging-always-list">
                            {s.always_allowed.map((label) => <li key={label}><bdi dir="auto">{label}</bdi></li>)}
                        </ul>
                        <div className="settings-row">
                            <button type="button" className="btn-secondary" disabled={busy !== null} onClick={() => run('forget', forgetDiscordAlways)}>{spinner('forget')}{t('settings.messaging.forgetAlways')}</button>
                        </div>
                    </div>
                )}

                {actionError && <p className="settings-error" role="alert"><bdi dir="auto">{actionError}</bdi></p>}

                {s.connected && (
                    <div className="messaging-footer">
                        <button type="button" className="btn-danger" disabled={busy !== null} onClick={() => setConfirmDisconnect(true)}>
                            {spinner('disconnect')}{t('settings.messaging.disconnect')}
                        </button>
                    </div>
                )}
            </section>

            {confirmDisconnect && (
                <ConfirmSheet
                    title={t('settings.messaging.disconnectTitle')}
                    confirmLabel={t('settings.messaging.disconnect')}
                    cancelLabel={t('settings.messaging.cancel')}
                    danger
                    onConfirm={() => { setConfirmDisconnect(false); void run('disconnect', disconnectDiscord); }}
                    onCancel={() => setConfirmDisconnect(false)}
                >
                    <p>{t('settings.messaging.disconnectBody')}</p>
                </ConfirmSheet>
            )}
        </div>
    );
}
