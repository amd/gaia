// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Settings › Phone: the provider, the phone book with its code checks, rules,
 * quiet hours, limits and texts, and recent alerts. The daemon owns phone; this
 * reads and writes it through the UI server. Changes work only at the PC.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Check, ExternalLink, Loader2, Phone, PhoneCall, MessageSquare, Send, Trash2 } from 'lucide-react';
import { dateLocale, useT } from '../../i18n/useT';
import { isolate, type MessageKey } from '../../i18n/registry';
import {
    AFTER_MINUTES_RANGE, LIMIT_RANGES, PHONE_RULES, PHONE_STEPS, SIMULATED_OUTCOMES,
    addPhoneNumber, confirmPhoneNumber, confirmRemovePhoneNumber, forgetTwilioKeys, getPhoneAlerts, getPhoneSettings,
    getPhoneStatus, getSimulatedLog, markAlertHandled, maskNumber, phoneFailure, removePhoneNumber, sendTestAlert,
    setTwilioKeys, spokenText, updatePhoneSettings, useSimulatedPhone,
    type CodeStarted, type PhoneAlert, type PhoneFailure, type PhoneLimit, type PhoneProvider, type PhoneSettings as Settings,
    type PhoneSettingsPatch, type PhoneStatus, type SimulatedLog,
} from '../../services/phoneApi';
import { setVisibleInterval } from '../../utils/visibleInterval';
import { SettingsDisclosure } from './SettingsDisclosure';
import { PhoneTalkSettings, talkStatus } from './PhoneTalkSettings';
import { alertTitle, awakeText, heldText, outcomeText, phoneErrorText, providerDetail } from './phoneText';
import './PhoneSettings.css';

const REFRESH_MS = 15_000;
const TWILIO_SIGNUP = 'https://www.twilio.com/try-twilio';
const SIM_OUTCOME_KEY: Record<string, MessageKey> = {
    human: 'phone.simulated.human', machine_start: 'phone.simulated.machine_start',
    'no-answer': 'phone.simulated.noAnswer', busy: 'phone.simulated.busy',
};

type Area = 'power' | 'provider' | 'book' | 'talk' | 'rules' | 'when' | 'alerts';

/** A section to open when Settings › Phone shows next (the alert banner's Open). */
let pendingFocus: 'alerts' | null = null;
export function focusPhoneAlertsNext(): void {
    pendingFocus = 'alerts';
}

/** A code check in progress: adding a number, or removing one. */
type Pending = (CodeStarted & { purpose: 'add' }) | (CodeStarted & { purpose: 'remove' });

function Failure({ failure }: { failure: PhoneFailure | null | undefined }) {
    const t = useT();
    if (!failure) return null;
    const extra = providerDetail(failure);
    return (
        <div className="settings-error phone-error" role="alert">
            <span>{phoneErrorText(failure, t)}</span>
            {extra && <span className="phone-error-detail" dir="auto">{t('phone.error.provider', { message: extra })}</span>}
        </div>
    );
}

function Toggle({ label, checked, disabled, onChange, hint }: { label: string; checked: boolean; disabled?: boolean; onChange: () => void; hint?: ReactNode }) {
    return (
        <div className="phone-toggle">
            <label className="setting-row">
                <span>{label}</span>
                <span className="toggle-switch">
                    <input type="checkbox" checked={checked} disabled={disabled} onChange={onChange} aria-label={label} />
                    <span className="toggle-track" />
                </span>
            </label>
            {hint && <p className="settings-hint">{hint}</p>}
        </div>
    );
}

/** A whole-number field saved when it loses focus, only if it changed and is in range. */
function NumberField({ id, label, value, min, max, disabled, onCommit, suffix }: {
    id: string; label: string; value: number; min: number; max: number; disabled?: boolean; onCommit: (n: number) => void; suffix?: string;
}) {
    const t = useT();
    const [text, setText] = useState(String(value));
    useEffect(() => setText(String(value)), [value]);
    const n = Number(text);
    const valid = text.trim() !== '' && Number.isInteger(n) && n >= min && n <= max;
    const commit = () => {
        if (valid && n !== value) onCommit(n);
        else if (!valid) setText(String(value));
    };
    return (
        <div className="phone-number-field">
            <label className="settings-label" htmlFor={id}>{label}</label>
            <span className="phone-number-input">
                <input
                    id={id}
                    className="settings-input"
                    type="number"
                    inputMode="numeric"
                    min={min}
                    max={max}
                    dir="ltr"
                    value={text}
                    disabled={disabled}
                    aria-invalid={!valid}
                    onChange={(e) => setText(e.target.value)}
                    onBlur={commit}
                    onKeyDown={(e) => { if (e.key === 'Enter') commit(); }}
                />
                {suffix && <span className="settings-hint">{suffix}</span>}
            </span>
            <span className="settings-hint">{t('phone.limits.range', { low: min, high: max })}</span>
        </div>
    );
}

export function PhoneSettings() {
    const t = useT();
    const [status, setStatus] = useState<PhoneStatus | null>(null);
    const [settings, setSettings] = useState<Settings | null>(null);
    const [alerts, setAlerts] = useState<PhoneAlert[]>([]);
    const [simLog, setSimLog] = useState<SimulatedLog | null>(null);
    const [loadError, setLoadError] = useState<PhoneFailure | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [errors, setErrors] = useState<Partial<Record<Area, PhoneFailure>>>({});
    const [notices, setNotices] = useState<Partial<Record<Area, string>>>({});
    const [provider, setProvider] = useState<PhoneProvider | null>(null);
    const [sid, setSid] = useState('');
    const [token, setToken] = useState('');
    const [fromNumber, setFromNumber] = useState('');
    const [twilioMissing, setTwilioMissing] = useState(false);
    const [addNumber, setAddNumber] = useState('');
    const [addLabel, setAddLabel] = useState('');
    const [addVia, setAddVia] = useState<'call' | 'text'>('call');
    const [pending, setPending] = useState<Pending | null>(null);
    const [code, setCode] = useState('');
    const [confirmCode, setConfirmCode] = useState('');
    const [codeError, setCodeError] = useState<string | null>(null);
    const [focusAlerts] = useState(() => { const f = pendingFocus === 'alerts'; pendingFocus = null; return f; });
    const alertsRef = useRef<HTMLDivElement>(null);

    const refresh = useCallback(async () => {
        try {
            const [s, cfg, a] = await Promise.all([getPhoneStatus(), getPhoneSettings(), getPhoneAlerts()]);
            setStatus(s);
            setSettings(cfg);
            setAlerts(a.alerts);
            setLoadError(null);
            setSimLog(s.provider === 'simulated' && s.configured ? await getSimulatedLog() : null);
        } catch (err) {
            setLoadError(phoneFailure(err));
        }
    }, []);

    useEffect(() => {
        void refresh();
        return setVisibleInterval(() => void refresh(), REFRESH_MS);
    }, [refresh]);

    useEffect(() => {
        if (focusAlerts && status) alertsRef.current?.scrollIntoView({ block: 'start' });
    }, [focusAlerts, status]);

    /** Run a change, show its failure in `area`, and reread everything after. */
    const act = async (area: Area, label: string, fn: () => Promise<unknown>, done?: string) => {
        setBusy(label);
        setErrors((e) => ({ ...e, [area]: undefined }));
        setNotices((n) => ({ ...n, [area]: undefined }));
        try {
            await fn();
            if (done) setNotices((n) => ({ ...n, [area]: done }));
            await refresh();
            return true;
        } catch (err) {
            setErrors((e) => ({ ...e, [area]: phoneFailure(err) }));
            return false;
        } finally {
            setBusy(null);
        }
    };

    const save = (area: Area, patch: PhoneSettingsPatch) => act(area, 'settings', async () => setSettings(await updatePhoneSettings(patch)));

    if (loadError && !status) {
        return (
            <div className="settings-pane">
                <h2 className="settings-pane-title">{t('settings.section.phone')}</h2>
                <p className="settings-pane-lede">{t('phone.lede')}</p>
                <Failure failure={loadError} />
            </div>
        );
    }
    if (!status || !settings) {
        return <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.loading')}</p>;
    }

    const chosen = provider ?? status.provider;
    const simulated = status.provider === 'simulated' && status.configured;
    const firstTime = !status.configured || status.contacts.length === 0;
    const working = busy !== null;
    const openCount = alerts.filter((a) => a.state === 'open').length;
    const fmt = new Intl.DateTimeFormat(dateLocale(t), { dateStyle: 'medium', timeStyle: 'short' });

    const startPending = (started: CodeStarted, purpose: 'add' | 'remove') => {
        setPending({ ...started, purpose });
        setCode('');
        setConfirmCode('');
        setCodeError(null);
    };

    const submitCode = () => {
        if (!pending) return;
        const sixDigits = /^\d{6}$/;
        if (!sixDigits.test(code.trim()) || (pending.needs_confirm_code && !sixDigits.test(confirmCode.trim()))) {
            setCodeError(t('phone.code.sixDigits'));
            return;
        }
        setCodeError(null);
        const vid = pending.verification_id;
        if (pending.purpose === 'add') {
            void act('book', 'code', () => confirmPhoneNumber(vid, code.trim(), pending.needs_confirm_code ? confirmCode.trim() : undefined), t('phone.code.added'))
                .then((ok) => { if (ok) { setPending(null); setAddNumber(''); setAddLabel(''); } });
        } else {
            void act('book', 'code', () => confirmRemovePhoneNumber(vid, code.trim()), t('phone.remove.done'))
                .then((ok) => { if (ok) setPending(null); });
        }
    };

    // A failed code ends the request when the service says to start again.
    const codeFailure = errors.book;
    const requestOver = codeFailure && (codeFailure.code === 'verification_expired' || codeFailure.code === 'not_found'
        || (codeFailure.code === 'verification_failed' && codeFailure.triesLeft == null && /start again/i.test(codeFailure.message)));

    return (
        <div className="settings-pane phone-settings">
            <h2 className="settings-pane-title">{t('settings.section.phone')}</h2>
            <p className="settings-pane-lede">{t('phone.lede')}</p>

            {firstTime && (
                <section className="settings-card phone-intro" aria-labelledby="phone-intro-title">
                    <h3 className="settings-label" id="phone-intro-title">{t('phone.intro.title')}</h3>
                    <p className="settings-hint">{t('phone.intro.what')}</p>
                    <p className="settings-hint">{t('phone.intro.leaves')}</p>
                    <p className="settings-hint">{t('phone.intro.cost', { month: isolate('$1.15', 'ltr'), minute: isolate('1.4¢', 'ltr') })}</p>
                </section>
            )}

            <section className="settings-card" aria-label={t('phone.power')}>
                <Toggle
                    label={t('phone.power')}
                    checked={status.enabled}
                    disabled={working}
                    onChange={() => void save('power', { enabled: !status.enabled })}
                />
                <p className="settings-hint phone-summary">
                    {status.provider === 'twilio' ? <bdi>{t.bare('phone.provider.twilio')}</bdi> : <span>{t('phone.provider.simulated')}</span>}
                    <span aria-hidden="true">·</span>
                    <span>{t.plural('phone.numbers', status.contacts.length)}</span>
                </p>
                {(status.calls_today > 0 || status.texts_today > 0) && (
                    <p className="settings-hint">{t('phone.today', { calls: isolate(String(status.calls_today), 'ltr'), texts: isolate(String(status.texts_today), 'ltr') })}</p>
                )}
                {status.last_error && <p className="settings-hint phone-last">{t('phone.lastProblem', { text: isolate(status.last_error, 'ltr') })}</p>}
                <div className="phone-actions">
                    <button
                        type="button"
                        className="btn-secondary phone-btn"
                        disabled={working || !status.enabled}
                        onClick={() => void act('power', 'test', sendTestAlert, t('phone.testSent'))}
                    >
                        {busy === 'test' ? <Loader2 size={13} className="spin" /> : <Send size={13} />}
                        {t('phone.test')}
                    </button>
                    {!status.enabled && <span className="settings-hint">{t('phone.testNeedsOn')}</span>}
                </div>
                <Failure failure={errors.power} />
                {notices.power && <p className="settings-ok" role="status">{notices.power}</p>}
            </section>

            <SettingsDisclosure title={t('phone.provider.title')} hint={t(status.provider === 'twilio' && status.configured ? 'phone.provider.twilio' : 'phone.provider.simulated')} defaultOpen={!status.configured}>
                <div className="phone-providers" role="radiogroup" aria-label={t('phone.provider.title')}>
                    {(['simulated', 'twilio'] as const).map((p) => (
                        <label key={p} className={`phone-provider${chosen === p ? ' is-chosen' : ''}`}>
                            <input
                                type="radio"
                                name="phone-provider"
                                value={p}
                                checked={chosen === p}
                                disabled={working}
                                onChange={() => {
                                    setProvider(p);
                                    if (p === 'simulated' && !(status.provider === 'simulated' && status.configured)) {
                                        void act('provider', 'simulated', useSimulatedPhone, t('phone.provider.using'));
                                    }
                                }}
                            />
                            <span className="phone-provider-text">
                                {p === 'twilio'
                                    ? <bdi className="phone-provider-name">{t.bare('phone.provider.twilio')}</bdi>
                                    : <span className="phone-provider-name">{t('phone.provider.simulated')}</span>}
                                <span className="settings-hint">{t(p === 'twilio' ? 'phone.provider.twilioHint' : 'phone.provider.simulatedHint')}</span>
                            </span>
                        </label>
                    ))}
                </div>

                {chosen === 'simulated' && !simulated && (
                    <button
                        type="button"
                        className="btn-primary phone-btn"
                        disabled={working}
                        onClick={() => void act('provider', 'simulated', useSimulatedPhone, t('phone.provider.using'))}
                    >
                        {busy === 'simulated' && <Loader2 size={13} className="spin" />}
                        {t('phone.provider.useSimulated')}
                    </button>
                )}

                {chosen === 'simulated' && simulated && (
                    // Nested: a field directly in a disclosure hides its label, and this one needs it.
                    <div className="phone-field"><div className="settings-field">
                        <label className="settings-label" htmlFor="phone-sim-outcome">{t('phone.simulated.outcome')}</label>
                        <select
                            id="phone-sim-outcome"
                            className="settings-input"
                            value={settings.simulated_outcome}
                            disabled={working}
                            onChange={(e) => void save('provider', { simulated_outcome: e.target.value as Settings['simulated_outcome'] })}
                        >
                            {SIMULATED_OUTCOMES.map((o) => <option key={o} value={o}>{t.bare(SIM_OUTCOME_KEY[o])}</option>)}
                        </select>
                    </div></div>
                )}

                {chosen === 'twilio' && (
                    <div className="phone-twilio">
                        <p className="settings-warning phone-warning" role="note">{t('phone.twilio.warning')}</p>
                        <p className="settings-hint">
                            {t('phone.twilio.trial')}{' '}
                            <a href={TWILIO_SIGNUP} target="_blank" rel="noreferrer" className="phone-link">
                                {t('phone.twilio.signup')}<ExternalLink size={12} aria-hidden="true" />
                            </a>
                        </p>
                        {status.provider === 'twilio' && status.configured ? (
                            <>
                                <p className="settings-hint">
                                    <Check size={13} aria-hidden="true" className="phone-ok-icon" />{' '}
                                    {t('phone.twilio.from', { number: isolate(maskNumber(status.from_number), 'ltr') })}
                                </p>
                                <button
                                    type="button"
                                    className="btn-secondary phone-btn"
                                    disabled={working}
                                    onClick={() => void act('provider', 'forget', forgetTwilioKeys, t('phone.twilio.forgotten'))}
                                >
                                    <Trash2 size={13} />
                                    {t('phone.twilio.forget')}
                                </button>
                            </>
                        ) : (
                            <form
                                className="phone-form"
                                onSubmit={(e) => {
                                    e.preventDefault();
                                    if (!sid.trim() || !token.trim() || !fromNumber.trim()) {
                                        setErrors((x) => ({ ...x, provider: undefined }));
                                        setTwilioMissing(true);
                                        return;
                                    }
                                    setTwilioMissing(false);
                                    void act('provider', 'keys', () => setTwilioKeys(sid.trim(), token.trim(), fromNumber.trim()), t('phone.twilio.saved'))
                                        // The token never stays in the page once it is saved.
                                        .then((ok) => { if (ok) { setSid(''); setToken(''); setFromNumber(''); } else setToken(''); });
                                }}
                            >
                                <div className="settings-field">
                                    <label className="settings-label" htmlFor="phone-sid">{t('phone.twilio.sid')}</label>
                                    <input id="phone-sid" className="settings-input" dir="ltr" autoComplete="off" spellCheck={false} value={sid} onChange={(e) => setSid(e.target.value)} />
                                </div>
                                <div className="settings-field">
                                    <label className="settings-label" htmlFor="phone-token">{t('phone.twilio.token')}</label>
                                    <input id="phone-token" className="settings-input" type="password" dir="ltr" autoComplete="new-password" value={token} onChange={(e) => setToken(e.target.value)} />
                                </div>
                                <div className="settings-field">
                                    <label className="settings-label" htmlFor="phone-from">{t('phone.twilio.number')}</label>
                                    <input id="phone-from" className="settings-input" type="tel" dir="ltr" autoComplete="off" value={fromNumber} onChange={(e) => setFromNumber(e.target.value)} />
                                </div>
                                <button type="submit" className="btn-primary phone-btn" disabled={working}>
                                    {busy === 'keys' && <Loader2 size={13} className="spin" />}
                                    {busy === 'keys' ? t('phone.twilio.checking') : t('phone.twilio.save')}
                                </button>
                                {twilioMissing && <p className="settings-error" role="alert">{t('phone.twilio.missing')}</p>}
                            </form>
                        )}
                    </div>
                )}
                <Failure failure={errors.provider} />
                {notices.provider && <p className="settings-ok" role="status">{notices.provider}</p>}
            </SettingsDisclosure>

            <SettingsDisclosure
                title={t('phone.book.title')}
                count={status.contacts.length}
                hint={t('phone.book.hint')}
                defaultOpen={status.configured && status.contacts.length === 0}
                forceOpen={pending !== null}
            >
                {status.contacts.length === 0 ? (
                    <p className="settings-hint">{t('phone.book.empty')}</p>
                ) : (
                    <ul className="phone-book">
                        {status.contacts.map((c) => (
                            <li key={c.id}>
                                <span className="phone-book-who">
                                    {c.label ? <bdi className="phone-book-label">{c.label}</bdi> : <span className="phone-book-label">{t('phone.book.noLabel')}</span>}
                                    <bdi dir="ltr" className="phone-book-number">{c.number}</bdi>
                                </span>
                                {c.primary && <span className="phone-badge">{t('phone.book.primary')}</span>}
                                <button
                                    type="button"
                                    className="btn-secondary phone-btn"
                                    disabled={working || pending !== null}
                                    aria-label={t('phone.book.removeAria', { number: c.number })}
                                    onClick={() => void act('book', 'remove', async () => startPending(await removePhoneNumber(c.id), 'remove'))}
                                >
                                    {t('phone.book.remove')}
                                </button>
                            </li>
                        ))}
                    </ul>
                )}

                {pending ? (
                    <form className="phone-form phone-code" onSubmit={(e) => { e.preventDefault(); submitCode(); }}>
                        <h4 className="settings-label">
                            {pending.purpose === 'add' ? t('phone.code.title') : t('phone.remove.title', { number: isolate(pending.number, 'ltr') })}
                        </h4>
                        <p className="settings-hint">
                            {pending.purpose === 'add' ? t('phone.code.note') : t('phone.remove.note', { number: isolate(pending.number, 'ltr') })}
                            {pending.needs_confirm_code && pending.confirm_sent_to && ` ${t('phone.code.secondNote', { number: isolate(pending.confirm_sent_to, 'ltr') })}`}
                        </p>
                        <div className="settings-field">
                            <label className="settings-label" htmlFor="phone-code">{t('phone.code.sentTo', { number: isolate(pending.number, 'ltr') })}</label>
                            <input id="phone-code" className="settings-input phone-code-input" inputMode="numeric" autoComplete="one-time-code" maxLength={6} dir="ltr" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} />
                        </div>
                        {pending.needs_confirm_code && pending.confirm_sent_to && (
                            <div className="settings-field">
                                <label className="settings-label" htmlFor="phone-confirm-code">{t('phone.code.secondSentTo', { number: isolate(pending.confirm_sent_to, 'ltr') })}</label>
                                <input id="phone-confirm-code" className="settings-input phone-code-input" inputMode="numeric" autoComplete="one-time-code" maxLength={6} dir="ltr" value={confirmCode} onChange={(e) => setConfirmCode(e.target.value.replace(/\D/g, ''))} />
                            </div>
                        )}
                        {simulated && <p className="settings-hint">{t('phone.code.simulatedNote')}</p>}
                        <div className="phone-actions">
                            {!requestOver && (
                                <button type="submit" className="btn-primary phone-btn" disabled={working}>
                                    {busy === 'code' && <Loader2 size={13} className="spin" />}
                                    {busy === 'code' ? t('phone.code.checking') : t('phone.code.confirm')}
                                </button>
                            )}
                            <button type="button" className="btn-secondary phone-btn" disabled={working} onClick={() => { setPending(null); setErrors((x) => ({ ...x, book: undefined })); }}>
                                {t('phone.code.cancel')}
                            </button>
                        </div>
                        {codeError && <p className="settings-error" role="alert">{codeError}</p>}
                    </form>
                ) : status.configured ? (
                    <form
                        className="phone-form phone-add"
                        onSubmit={(e) => {
                            e.preventDefault();
                            if (!addNumber.trim()) {
                                setCodeError(t('phone.add.typeNumber'));
                                return;
                            }
                            setCodeError(null);
                            void act('book', 'add', async () => startPending(await addPhoneNumber(addNumber.trim(), addLabel.trim(), addVia), 'add'));
                        }}
                    >
                        <h4 className="settings-label">{t('phone.add.title')}</h4>
                        <div className="phone-add-row">
                            <div className="settings-field">
                                <label className="settings-label" htmlFor="phone-add-number">{t('phone.add.number')}</label>
                                <input id="phone-add-number" className="settings-input" type="tel" dir="ltr" autoComplete="tel" value={addNumber} onChange={(e) => setAddNumber(e.target.value)} />
                            </div>
                            <div className="settings-field">
                                <label className="settings-label" htmlFor="phone-add-label">{t('phone.add.label')}</label>
                                <input id="phone-add-label" className="settings-input" dir="auto" maxLength={40} placeholder={t.bare('phone.add.labelPlaceholder')} value={addLabel} onChange={(e) => setAddLabel(e.target.value)} />
                            </div>
                        </div>
                        <span className="settings-hint">{t('phone.add.numberHint')}</span>
                        <fieldset className="phone-via">
                            <legend className="settings-label">{t('phone.add.via')}</legend>
                            {(['call', 'text'] as const).map((v) => (
                                <label key={v} className="phone-via-option">
                                    <input type="radio" name="phone-via" value={v} checked={addVia === v} onChange={() => setAddVia(v)} />
                                    {v === 'call' ? <PhoneCall size={13} aria-hidden="true" /> : <MessageSquare size={13} aria-hidden="true" />}
                                    <span>{t(v === 'call' ? 'phone.add.viaCall' : 'phone.add.viaText')}</span>
                                </label>
                            ))}
                        </fieldset>
                        <button type="submit" className="btn-primary phone-btn" disabled={working}>
                            {busy === 'add' ? <Loader2 size={13} className="spin" /> : <Phone size={13} />}
                            {busy === 'add' ? t('phone.add.sending') : t('phone.add.send')}
                        </button>
                        {codeError && <p className="settings-error" role="alert">{codeError}</p>}
                    </form>
                ) : (
                    <p className="settings-hint">{t('phone.add.needsProvider')}</p>
                )}
                <Failure failure={errors.book} />
                {notices.book && <p className="settings-ok" role="status">{notices.book}</p>}
            </SettingsDisclosure>

            <SettingsDisclosure
                title={t('phone.talk.title')}
                hint={t(talkStatus(settings).state === 'ready' ? 'phone.talk.ready' : talkStatus(settings).state === 'off' ? 'phone.talk.off' : talkStatus(settings).needsPin ? 'phone.talk.needsPin' : 'phone.talk.needsAccess')}
            >
                <PhoneTalkSettings
                    settings={settings}
                    disabled={working}
                    busy={busy}
                    act={(label, fn, done) => act('talk', label, fn, done)}
                    save={(patch) => save('talk', patch)}
                />
                <Failure failure={errors.talk} />
                {notices.talk && <p className="settings-ok" role="status">{notices.talk}</p>}
            </SettingsDisclosure>

            <SettingsDisclosure title={t('phone.rules.title')} hint={t('phone.rules.hint')}>
                <ul className="phone-rules">
                    {PHONE_RULES.map((key) => {
                        const rule = settings.rules[key];
                        const label = t(`phone.rule.${key}` as MessageKey);
                        return (
                            <li key={key} className={`phone-rule${rule.on ? '' : ' is-off'}`}>
                                <Toggle
                                    label={label}
                                    checked={rule.on}
                                    disabled={working}
                                    onChange={() => void save('rules', { rules: { [key]: { on: !rule.on } } })}
                                    hint={key === 'agent' ? t('phone.rules.agentNote') : undefined}
                                />
                                <div className="phone-rule-controls">
                                    <select
                                        className="settings-input settings-select-compact"
                                        aria-label={t.bare('phone.rules.steps', { rule: t.bare(`phone.rule.${key}` as MessageKey) })}
                                        value={rule.steps}
                                        disabled={working || !rule.on}
                                        onChange={(e) => void save('rules', { rules: { [key]: { steps: e.target.value as typeof rule.steps } } })}
                                    >
                                        {PHONE_STEPS.map((s) => <option key={s} value={s}>{t.bare(`phone.steps.${s}` as MessageKey)}</option>)}
                                    </select>
                                    <label className="phone-check">
                                        <input
                                            type="checkbox"
                                            checked={rule.urgent}
                                            disabled={working || !rule.on}
                                            onChange={() => void save('rules', { rules: { [key]: { urgent: !rule.urgent } } })}
                                        />
                                        <span>{t('phone.rules.urgent')}</span>
                                    </label>
                                    {key === 'approval_waiting' && rule.after_minutes != null && (
                                        <NumberField
                                            id="phone-approval-after"
                                            label={t('phone.rules.after')}
                                            value={rule.after_minutes}
                                            min={AFTER_MINUTES_RANGE[0]}
                                            max={AFTER_MINUTES_RANGE[1]}
                                            suffix={t('phone.rules.minutes')}
                                            disabled={working || !rule.on}
                                            onCommit={(n) => void save('rules', { rules: { approval_waiting: { after_minutes: n } } })}
                                        />
                                    )}
                                </div>
                            </li>
                        );
                    })}
                </ul>
                <Failure failure={errors.rules} />
            </SettingsDisclosure>

            <SettingsDisclosure title={t('phone.when.title')}>
                <Toggle
                    label={t('phone.quiet.label')}
                    checked={settings.quiet_hours !== null}
                    disabled={working}
                    onChange={() => void save('when', { quiet_hours: settings.quiet_hours ? null : { start: '22:00', end: '07:00' } })}
                    hint={t('phone.quiet.hint')}
                />
                {settings.quiet_hours && (
                    <div className="phone-quiet">
                        {(['start', 'end'] as const).map((end) => (
                            <div key={end} className="settings-field">
                                <label className="settings-label" htmlFor={`phone-quiet-${end}`}>{t(end === 'start' ? 'phone.quiet.from' : 'phone.quiet.to')}</label>
                                <input
                                    id={`phone-quiet-${end}`}
                                    className="settings-input"
                                    type="time"
                                    dir="ltr"
                                    disabled={working}
                                    defaultValue={settings.quiet_hours?.[end]}
                                    key={settings.quiet_hours?.[end]}
                                    onBlur={(e) => {
                                        const qh = settings.quiet_hours;
                                        const v = e.target.value;
                                        if (!qh || !/^\d\d:\d\d$/.test(v) || v === qh[end]) return;
                                        void save('when', { quiet_hours: { ...qh, [end]: v } });
                                    }}
                                />
                            </div>
                        ))}
                    </div>
                )}

                <h4 className="settings-label">{t('phone.limits.title')}</h4>
                <p className="settings-hint">{t('phone.limits.hint')}</p>
                <div className="phone-limits">
                    {(Object.keys(LIMIT_RANGES) as PhoneLimit[]).map((name) => (
                        <NumberField
                            key={name}
                            id={`phone-limit-${name}`}
                            label={t(`phone.limits.${name}` as MessageKey)}
                            value={settings.limits[name]}
                            min={LIMIT_RANGES[name][0]}
                            max={LIMIT_RANGES[name][1]}
                            disabled={working}
                            onCommit={(n) => void save('when', { limits: { [name]: n } })}
                        />
                    ))}
                </div>

                <Toggle
                    label={t('phone.texts.label')}
                    checked={settings.texts_enabled}
                    disabled={working}
                    onChange={() => void save('when', { texts_enabled: !settings.texts_enabled })}
                    hint={t('phone.texts.us')}
                />
                <Toggle
                    label={t('phone.awake.label')}
                    checked={settings.keep_awake}
                    disabled={working}
                    onChange={() => void save('when', { keep_awake: !settings.keep_awake })}
                    hint={awakeText(settings.keep_awake ? status.awake : null, t)}
                />
                <Failure failure={errors.when} />
            </SettingsDisclosure>

            <div ref={alertsRef} id="phone-alerts" className="phone-alerts-anchor">
                <SettingsDisclosure title={t('phone.alerts.title')} count={openCount} defaultOpen={focusAlerts || openCount > 0}>
                    {alerts.length === 0 ? (
                        <p className="settings-hint">{t('phone.alerts.empty')}</p>
                    ) : (
                        <ul className="phone-alerts">
                            {alerts.slice(0, 20).map((a) => (
                                <li key={a.id} className={`phone-alert${a.state === 'open' ? ' is-open' : ''}`}>
                                    <div className="phone-alert-head">
                                        <span className="phone-alert-title">{alertTitle(a.kind, t)}</span>
                                        {a.urgent && <span className="phone-badge is-urgent">{t('phone.alerts.urgent')}</span>}
                                        <span className={`phone-badge${a.state === 'open' ? ' is-open' : ''}`}>
                                            {t(a.state === 'open' ? 'phone.alerts.open' : 'phone.alerts.handled')}
                                        </span>
                                        <time className="phone-alert-time" dateTime={new Date(a.created_at * 1000).toISOString()}>
                                            {fmt.format(new Date(a.created_at * 1000))}
                                        </time>
                                    </div>
                                    {a.reason && <p className="phone-alert-reason"><bdi>{a.reason}</bdi></p>}
                                    {a.count > 1 && <p className="settings-hint">{t('phone.alerts.repeats', { n: isolate(String(a.count), 'ltr') })}</p>}
                                    {a.held && <p className="settings-hint phone-held">{heldText(a.held, t)}</p>}
                                    {a.deliveries.length > 0 ? (
                                        <ul className="phone-deliveries">
                                            {a.deliveries.map((d, i) => (
                                                <li key={i}>
                                                    {d.channel === 'call' ? <PhoneCall size={12} aria-hidden="true" /> : <MessageSquare size={12} aria-hidden="true" />}
                                                    <span>{t(d.channel === 'call' ? 'phone.delivery.call' : 'phone.delivery.text', { number: isolate(d.number, 'ltr') })}</span>
                                                    <span className={`phone-outcome${d.reached ? ' is-reached' : d.state === 'failed' ? ' is-failed' : ''}`}>{outcomeText(d, t)}</span>
                                                </li>
                                            ))}
                                        </ul>
                                    ) : a.state === 'open' ? (
                                        <p className="settings-hint">{t('phone.alerts.notSent')}</p>
                                    ) : null}
                                    {a.state === 'open' && (
                                        <button
                                            type="button"
                                            className="btn-secondary phone-btn"
                                            disabled={working}
                                            onClick={() => void act('alerts', 'handled', () => markAlertHandled(a.id), t('phone.alerts.markedHandled'))}
                                        >
                                            {t('phone.alerts.markHandled')}
                                        </button>
                                    )}
                                </li>
                            ))}
                        </ul>
                    )}
                    <Failure failure={errors.alerts} />
                    {notices.alerts && <p className="settings-ok" role="status">{notices.alerts}</p>}
                </SettingsDisclosure>
            </div>

            {simulated && simLog && (
                <SettingsDisclosure
                    title={t('phone.sim.title')}
                    hint={t('phone.sim.hint')}
                    count={simLog.calls.length + simLog.texts.length}
                    forceOpen={pending !== null}
                >
                    {simLog.calls.length + simLog.texts.length === 0 ? (
                        <p className="settings-hint">{t('phone.sim.empty')}</p>
                    ) : (
                        <ul className="phone-sim">
                            {[...simLog.calls.map((c) => ({ kind: 'call' as const, to: c.to, text: spokenText(c.script), sid: c.sid })),
                              ...simLog.texts.map((x) => ({ kind: 'text' as const, to: x.to, text: x.body, sid: x.sid }))]
                                .reverse()
                                .slice(0, 20)
                                .map((m) => (
                                    <li key={m.sid}>
                                        <span className="phone-sim-to">
                                            {m.kind === 'call' ? <PhoneCall size={12} aria-hidden="true" /> : <MessageSquare size={12} aria-hidden="true" />}
                                            {t(m.kind === 'call' ? 'phone.sim.call' : 'phone.sim.text', { number: isolate(maskNumber(m.to), 'ltr') })}
                                        </span>
                                        <bdi className="phone-sim-said" lang="en" dir="ltr">{`“${m.text}”`}</bdi>
                                    </li>
                                ))}
                        </ul>
                    )}
                </SettingsDisclosure>
            )}
        </div>
    );
}
