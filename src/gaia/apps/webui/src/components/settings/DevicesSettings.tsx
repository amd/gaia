// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Check, Copy, Hourglass } from 'lucide-react';
import QRCode from 'qrcode';
import { useT } from '../../i18n/useT';
import type { Translate } from '../../i18n/translator';
import type { MessageKey } from '../../i18n/registry';
import { ApiError } from '../../services/api';
import {
    allowDevice,
    closeDevicePairing,
    openDevicePairing,
    removeDevice,
    resetDevicePairing,
    startDeviceListener,
    stopDeviceListener,
    type DevicePairingOffer,
    type PairedDevice,
} from '../../services/devicesApi';
import { useDeviceStore } from '../../stores/deviceStore';
import { formatCountdown } from '../../utils/permissionPrompts.core';
import { log } from '../../utils/logger';
import { pairingLink } from '../../utils/pairingLink';
import { MarkedText, markLtr } from '../MarkedText';
import { ConfirmSheet } from '../setup/ConfirmSheet';
import './DevicesSettings.css';

const REASON_KEYS = {
    expired: 'settings.devices.reason.expired',
    used: 'settings.devices.reason.used',
    wrong: 'settings.devices.reason.wrong',
    closed: 'settings.devices.reason.closed',
    rate_limited: 'settings.devices.reason.rateLimited',
} as const;

const PLATFORM_KEYS = {
    ios: 'settings.devices.platform.ios',
    ipados: 'settings.devices.platform.ipados',
    android: 'settings.devices.platform.android',
} as const;

function errorText(err: unknown): string {
    if (err instanceof ApiError) return err.detail || err.message;
    return err instanceof Error ? err.message : String(err);
}

/** A code from the API the catalog doesn't map is contract drift: show it raw and say so in the log. */
function mapped<K extends string>(table: Record<K, string>, code: string, t: Translate, what: string): string {
    if (code in table) return t(table[code as K] as MessageKey);
    log.ui.error(`Unmapped device ${what} from the API: ${code}`);
    return code;
}

export const platformName = (platform: string, t: Translate) => mapped(PLATFORM_KEYS, platform, t, 'platform');

function ago(iso: string, now: number, t: Translate): string {
    const secs = Math.max(0, Math.floor((now - Date.parse(iso)) / 1000));
    if (secs < 60) return t('time.justNow');
    const mins = Math.floor(secs / 60);
    if (mins < 60) return t('time.minutesAgo', { n: mins });
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return t('time.hoursAgo', { n: hrs });
    return t('time.daysAgo', { n: Math.floor(hrs / 24) });
}

const secondsLeft = (deadlineMs: number, now: number) => Math.max(0, Math.ceil((deadlineMs - now) / 1000));

function useNow(active: boolean): number {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        if (!active) return;
        const timer = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(timer);
    }, [active]);
    return now;
}

/** The pairing code as a QR image, the theme's ink on its paper, as Mobile access draws its code. */
function PairingQr({ offer }: { offer: DevicePairingOffer }) {
    const t = useT();
    const [src, setSrc] = useState<string | null>(null);
    const [failed, setFailed] = useState<string | null>(null);
    useEffect(() => {
        let live = true;
        // The raster needs literal colors: read the tokens as they resolve now.
        const css = getComputedStyle(document.documentElement);
        const ink = css.getPropertyValue('--text-primary').trim();
        const paper = css.getPropertyValue('--bg-primary').trim();
        if (!ink || !paper) {
            log.ui.error('Pairing QR code skipped: --text-primary/--bg-primary did not resolve; styles/index.css must load first.');
            setFailed('--text-primary/--bg-primary');
            return;
        }
        QRCode.toDataURL(JSON.stringify(offer), { width: 200, margin: 2, color: { dark: ink, light: paper } })
            .then((url: string) => { if (live) setSrc(url); })
            .catch((err: unknown) => {
                log.ui.error('Pairing QR code could not be drawn', err);
                if (live) setFailed(errorText(err));
            });
        return () => { live = false; };
    }, [offer]);
    if (failed) return <p className="settings-error" role="alert">{failed}</p>;
    return <div className="devices-qr">{src && <img src={src} width={200} height={200} alt={t('settings.devices.qrAlt')} />}</div>;
}

type Confirm = { kind: 'remove'; device: PairedDevice } | { kind: 'reset' };

/** Settings > Phones and tablets: let paired devices connect over the home network, pair, allow and remove them. */
export function DevicesSettings() {
    const t = useT();
    const { status, devices, loadError, refresh, setStatus } = useDeviceStore();
    const [offer, setOffer] = useState<DevicePairingOffer | null>(null);
    const [expired, setExpired] = useState(false);
    const [busy, setBusy] = useState(false);
    const [actionError, setActionError] = useState<string | null>(null);
    const [confirm, setConfirm] = useState<Confirm | null>(null);
    const [copied, setCopied] = useState<'ok' | 'failed' | null>(null);

    useEffect(() => { void refresh(); }, [refresh]);

    const pending = devices.filter((d) => d.status === 'pending');
    const allowed = devices.filter((d) => d.status === 'allowed');
    const now = useNow(offer !== null || pending.length > 0);
    const listening = status?.listening === true;

    // The window closes on the first pairing (or on the PC's Cancel): the code must vanish with it.
    useEffect(() => {
        if (offer && status && !status.pairing_open) setOffer(null);
    }, [offer, status]);
    useEffect(() => {
        if (offer && secondsLeft(offer.exp * 1000, now) === 0) {
            setOffer(null);
            setExpired(true);
        }
    }, [offer, now]);

    // A copied or failed notice belongs to one code.
    useEffect(() => setCopied(null), [offer]);
    useEffect(() => {
        if (copied !== 'ok') return;
        const timer = setTimeout(() => setCopied(null), 2000);
        return () => clearTimeout(timer);
    }, [copied]);

    const act = useCallback(async (run: () => Promise<unknown>) => {
        setBusy(true);
        setActionError(null);
        try {
            await run();
        } catch (err) {
            log.ui.error('Device action failed', err);
            setActionError(errorText(err));
        } finally {
            setBusy(false);
            await refresh();
        }
    }, [refresh]);

    const toggle = () => act(async () => {
        if (listening) setOffer(null);
        setStatus(await (listening ? stopDeviceListener() : startDeviceListener()));
    });
    const pair = () => act(async () => {
        setExpired(false);
        const opened = await openDevicePairing();
        // In the same render as the code, or the closed-window effect clears it before the refresh lands.
        if (status) setStatus({ ...status, pairing_open: true, pairing_expires_at: new Date(opened.exp * 1000).toISOString() });
        setOffer(opened);
    });
    const copyLink = (current: DevicePairingOffer) => {
        navigator.clipboard.writeText(pairingLink(current)).then(
            () => setCopied('ok'),
            (err: unknown) => {
                log.ui.error('Pairing link could not be copied', err);
                setCopied('failed');
            },
        );
    };
    const cancelPairing = () => act(async () => {
        setOffer(null);
        setStatus(await closeDevicePairing());
    });

    if (!status) {
        return (
            <div className="settings-pane">
                <h2 className="settings-pane-title">{t('settings.section.devices')}</h2>
                {loadError ? <p className="settings-error" role="alert">{loadError}</p> : <p className="settings-hint">{t('settings.loading')}</p>}
            </div>
        );
    }

    const connectLabel = t('settings.devices.connect');
    const listenError = status.error_code === 'port_in_use'
        ? t('settings.devices.portInUse', { port: markLtr(String(status.port)) })
        : status.error_code ? t('settings.devices.listenFailed', { detail: status.error_detail ?? status.error_code }) : null;
    const failed = status.failed_redemptions ?? [];

    return (
        <div className="settings-pane devices-pane">
            <h2 className="settings-pane-title">{t('settings.section.devices')}</h2>
            <p className="settings-pane-lede">{t('settings.devices.lede')}</p>

            <section className="settings-card" aria-label={connectLabel}>
                <div>
                    <label className="setting-row">
                        <strong>{connectLabel}</strong>
                        <span className="toggle-switch">
                            <input
                                type="checkbox"
                                role="switch"
                                checked={listening}
                                aria-describedby="devices-connect-hint"
                                onChange={() => void toggle()}
                                aria-busy={busy}
                            />
                            <span className="toggle-track" />
                        </span>
                    </label>
                    <p className="settings-hint" id="devices-connect-hint">{t('settings.devices.connectHint')}</p>
                </div>
                {listenError && <p className="settings-error" role="alert"><MarkedText text={listenError} /></p>}
            </section>

            <section className="settings-card" aria-label={t('settings.devices.pair')}>
                {!offer && (
                    <div className="settings-field">
                        <div className="settings-row">
                            <button type="button" className="btn-primary" disabled={!listening || busy} onClick={() => void pair()}>
                                {expired ? t('settings.devices.newCode') : t('settings.devices.pair')}
                            </button>
                        </div>
                        {expired && <p className="settings-hint" role="status">{t('settings.devices.expired')}</p>}
                        {!listening && <p className="settings-hint">{t('settings.devices.pairOffHint', { label: connectLabel })}</p>}
                    </div>
                )}
                {offer && (
                    <div className="devices-pairing">
                        <PairingQr offer={offer} />
                        <div className="settings-field">
                            <p className="settings-note">{t('settings.devices.scan')}</p>
                            <p className="settings-hint">
                                <MarkedText text={t('settings.devices.expiresIn', { time: markLtr(formatCountdown(secondsLeft(offer.exp * 1000, now))) })} />
                            </p>
                            <div className="settings-row">
                                <button type="button" className="btn-secondary" onClick={() => copyLink(offer)}>
                                    {copied === 'ok' ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
                                    {copied === 'ok' ? t('settings.messaging.copied') : t('settings.devices.copyLink')}
                                </button>
                                <button type="button" className="btn-secondary" disabled={busy} onClick={() => void cancelPairing()}>
                                    {t('settings.devices.cancel')}
                                </button>
                            </div>
                            <p className="settings-hint">
                                <MarkedText text={t('settings.devices.linkHint', { time: markLtr(formatCountdown(secondsLeft(offer.exp * 1000, now))) })} />
                            </p>
                            {copied === 'failed' && <p className="settings-error" role="alert">{t('settings.devices.copyLinkFailed')}</p>}
                        </div>
                    </div>
                )}
            </section>

            {pending.length > 0 && (
                <section className="settings-card" aria-labelledby="devices-waiting-title">
                    <h3 className="settings-label" id="devices-waiting-title">{t('settings.devices.waitingTitle')}</h3>
                    <ul className="devices-list" role="status">
                        {pending.map((d) => (
                            <li key={d.id} className="devices-row" data-device-id={d.id}>
                                <Hourglass size={16} aria-hidden="true" className="devices-row-icon" />
                                <div className="devices-row-text">
                                    <span className="devices-name"><bdi>{d.name}</bdi></span>
                                    <span className="settings-hint">
                                        {t('settings.devices.pairedAt', { platform: platformName(d.platform, t), when: ago(d.created_at, now, t) })}
                                        {d.expires_at && (
                                            <> · <span className="perm-card-timer" title={t('prompt.timer')}><bdi dir="ltr">{formatCountdown(secondsLeft(Date.parse(d.expires_at), now))}</bdi></span></>
                                        )}
                                    </span>
                                </div>
                                <div className="devices-row-actions">
                                    <button type="button" className="btn-primary" disabled={busy} aria-label={t('settings.devices.allowAria', { name: d.name })} onClick={() => void act(() => allowDevice(d.id))}>
                                        {t('settings.devices.allow')}
                                    </button>
                                    <button type="button" className="btn-secondary" disabled={busy} aria-label={t('settings.devices.denyAria', { name: d.name })} onClick={() => void act(() => removeDevice(d.id))}>
                                        {t('settings.devices.deny')}
                                    </button>
                                </div>
                            </li>
                        ))}
                    </ul>
                    <p className="settings-hint">{t('settings.devices.waitingHint')}</p>
                </section>
            )}

            <section className="settings-card" aria-labelledby="devices-paired-title">
                <h3 className="settings-label" id="devices-paired-title">{t('settings.devices.pairedTitle')}</h3>
                {allowed.length === 0 ? (
                    <p className="settings-hint">{t('settings.devices.none')}</p>
                ) : (
                    <ul className="devices-list">
                        {allowed.map((d) => (
                            <li key={d.id} className="devices-row" data-device-id={d.id}>
                                <div className="devices-row-text">
                                    <span className="devices-name"><bdi>{d.name}</bdi></span>
                                    <span className="settings-hint">
                                        {d.last_seen_at
                                            ? t('settings.devices.lastConnected', { platform: platformName(d.platform, t), when: ago(d.last_seen_at, now, t) })
                                            : t('settings.devices.neverConnected', { platform: platformName(d.platform, t) })}
                                    </span>
                                </div>
                                <div className="devices-row-actions">
                                    <button type="button" className="btn-secondary" disabled={busy} aria-label={t('settings.devices.removeAria', { name: d.name })} onClick={() => setConfirm({ kind: 'remove', device: d })}>
                                        {t('settings.devices.remove')}
                                    </button>
                                </div>
                            </li>
                        ))}
                    </ul>
                )}
                {allowed.length > 0 && !listening && <p className="settings-hint">{t('settings.devices.offHint', { label: connectLabel })}</p>}
                <p className="settings-hint">{t.plural('settings.devices.autoRemove', status.idle_expiry_days)}</p>
            </section>

            {failed.length > 0 && (
                <section className="settings-card" aria-labelledby="devices-failed-title">
                    <h3 className="settings-label" id="devices-failed-title">{t('settings.devices.failedTitle')}</h3>
                    <ul className="devices-list">
                        {failed.map((f) => (
                            <li key={`${f.at}-${f.address}-${f.reason}`} className="devices-failed">
                                <AlertTriangle size={14} aria-hidden="true" />
                                <span>
                                    <MarkedText text={t('settings.devices.failedRow', {
                                        when: ago(f.at, now, t),
                                        reason: mapped(REASON_KEYS, f.reason, t, 'failure reason'),
                                        address: markLtr(f.address),
                                    })} />
                                </span>
                            </li>
                        ))}
                    </ul>
                </section>
            )}

            <section className="settings-card" aria-label={t('settings.devices.reset')}>
                <div className="settings-row">
                    <button type="button" className="btn-danger" disabled={busy} onClick={() => setConfirm({ kind: 'reset' })}>
                        {t('settings.devices.reset')}
                    </button>
                </div>
                <p className="settings-hint">{t('settings.devices.resetHint')}</p>
            </section>

            {actionError && <p className="settings-error" role="alert">{actionError}</p>}
            <p className="settings-note">{t('settings.devices.browserNote', { label: t('sidebar.mobileAccess') })}</p>

            {confirm?.kind === 'remove' && (
                <ConfirmSheet
                    title={t('settings.devices.removeTitle', { name: confirm.device.name })}
                    confirmLabel={t('settings.devices.remove')}
                    cancelLabel={t('settings.devices.cancel')}
                    danger
                    busy={busy}
                    onCancel={() => setConfirm(null)}
                    onConfirm={() => void act(() => removeDevice(confirm.device.id)).then(() => setConfirm(null))}
                >
                    {t('settings.devices.removeBody')}
                </ConfirmSheet>
            )}
            {confirm?.kind === 'reset' && (
                <ConfirmSheet
                    title={t('settings.devices.resetTitle')}
                    confirmLabel={t('settings.devices.resetYes')}
                    cancelLabel={t('settings.devices.cancel')}
                    danger
                    busy={busy}
                    onCancel={() => setConfirm(null)}
                    onConfirm={() => void act(async () => { setOffer(null); setStatus(await resetDevicePairing()); }).then(() => setConfirm(null))}
                >
                    {t('settings.devices.resetBody')}
                </ConfirmSheet>
            )}
        </div>
    );
}
