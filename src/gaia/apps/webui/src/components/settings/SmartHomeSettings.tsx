// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, ExternalLink, Loader2, XCircle } from 'lucide-react';
import * as api from '../../services/api';
import type { HomeAssistantStatus } from '../../types';
import { useT } from '../../i18n/useT';
import './SmartHomeSettings.css';

export const HOME_ASSISTANT_DOCS = 'https://amd-gaia.ai/docs/connectors/home-assistant';
const ADDRESS_EXAMPLE = 'http://homeassistant.local:8123';

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

type Notice = { ok: boolean; text: string };

/** Settings > Smart home: connect Home Assistant, test it, or forget it. */
export function SmartHomeSettings() {
    const t = useT();
    const [status, setStatus] = useState<HomeAssistantStatus | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [url, setUrl] = useState('');
    const [token, setToken] = useState('');
    const [busy, setBusy] = useState<'connect' | 'test' | 'disconnect' | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<Notice | null>(null);
    const [toolCount, setToolCount] = useState<number | null>(null);
    const [confirming, setConfirming] = useState(false);

    const load = useCallback(async () => {
        setLoadError(null);
        try {
            const s = await api.getHomeAssistant();
            setStatus(s);
            if (s.url) setUrl(s.url);
        } catch (e) {
            setLoadError(message(e));
        }
    }, []);

    useEffect(() => { void load(); }, [load]);

    const connect = async () => {
        setBusy('connect');
        setError(null);
        setNotice(null);
        try {
            const r = await api.connectHomeAssistant(url.trim(), token.trim());
            setStatus(r);
            setToolCount(r.tool_count);
            setToken('');
            setNotice({ ok: true, text: r.detail });
        } catch (e) {
            // Nothing was saved: keep what was typed so only the wrong part needs fixing.
            setError(message(e));
        } finally {
            setBusy(null);
        }
    };

    const test = async () => {
        setBusy('test');
        setNotice(null);
        try {
            const r = await api.testHomeAssistant();
            if (r.tool_count != null) setToolCount(r.tool_count);
            setNotice({ ok: r.ok, text: r.detail });
        } catch (e) {
            setNotice({ ok: false, text: message(e) });
        } finally {
            setBusy(null);
        }
    };

    const disconnect = async () => {
        setBusy('disconnect');
        setNotice(null);
        try {
            const r = await api.disconnectHomeAssistant();
            setStatus(r);
            setToolCount(null);
            setConfirming(false);
            setNotice({ ok: true, text: r.detail });
        } catch (e) {
            setNotice({ ok: false, text: message(e) });
        } finally {
            setBusy(null);
        }
    };

    const connected = !!status?.connected;
    const inUse = connected && status!.active && status!.enabled !== false;

    return (
        <div className="settings-pane smart-home">
            <h2 className="settings-pane-title">{t('settings.section.smartHome')}</h2>
            <p className="settings-pane-lede">
                {t('settings.smartHome.lede')}{' '}
                <a href={HOME_ASSISTANT_DOCS} target="_blank" rel="noopener noreferrer" className="smart-home-link">
                    {t('settings.connectors.learnMore')} <ExternalLink size={11} aria-hidden="true" />
                </a>
            </p>

            {loadError ? (
                <div className="settings-card">
                    <p className="settings-error" role="alert"><bdi>{loadError}</bdi></p>
                    <button type="button" className="btn-secondary smart-home-start" onClick={() => void load()}>
                        {t('settings.smartHome.retry')}
                    </button>
                </div>
            ) : !status ? (
                <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.loading')}</p>
            ) : connected ? (
                <section className="settings-card" aria-label={t('settings.smartHome.connectedTitle')}>
                    <div className="settings-row settings-row-between">
                        <span className="settings-label smart-home-state">
                            <CheckCircle2 size={14} aria-hidden="true" /> {t('settings.smartHome.connectedTitle')}
                        </span>
                        {toolCount != null && (
                            <span className="settings-hint">{t.plural('settings.smartHome.tools', toolCount)}</span>
                        )}
                    </div>
                    <p className="settings-hint">
                        {t('settings.smartHome.address')}: <code dir="ltr">{status.url}</code>
                    </p>
                    {!inUse && <p className="settings-note">{t('settings.smartHome.notInUse')}</p>}
                    {confirming ? (
                        <div className="smart-home-confirm" role="group" aria-label={t('settings.smartHome.disconnect')}>
                            <p className="settings-note">{t('settings.smartHome.confirmDisconnect')}</p>
                            <div className="settings-row">
                                <button type="button" className="btn-danger" disabled={busy !== null} onClick={() => void disconnect()}>
                                    {busy === 'disconnect' && <Loader2 size={13} className="spin" />} {t('settings.smartHome.disconnect')}
                                </button>
                                <button type="button" className="btn-secondary" disabled={busy !== null} onClick={() => setConfirming(false)}>
                                    {t('settings.smartHome.cancel')}
                                </button>
                            </div>
                        </div>
                    ) : (
                        <div className="settings-row">
                            <button type="button" className="btn-secondary" disabled={busy !== null} onClick={() => void test()}>
                                {busy === 'test' && <Loader2 size={13} className="spin" />}{' '}
                                {busy === 'test' ? t('settings.smartHome.testing') : t('settings.smartHome.test')}
                            </button>
                            <button type="button" className="btn-secondary" disabled={busy !== null} onClick={() => setConfirming(true)}>
                                {t('settings.smartHome.disconnect')}
                            </button>
                        </div>
                    )}
                    {notice && <NoticeLine notice={notice} />}
                </section>
            ) : null}

            {status && !loadError && (!connected || !inUse) && (
                <>
                    {!connected && notice && <NoticeLine notice={notice} />}
                    {!connected && (
                        <section className="settings-card" aria-labelledby="smart-home-steps">
                            <h3 className="settings-label" id="smart-home-steps">{t('settings.smartHome.stepsTitle')}</h3>
                            <ol className="smart-home-steps">
                                <li>{t('settings.smartHome.step.integration')}</li>
                                <li>{t('settings.smartHome.step.expose')}</li>
                                <li>{t('settings.smartHome.step.token')}</li>
                            </ol>
                        </section>
                    )}
                    <form
                        className="settings-card"
                        aria-label={t('settings.smartHome.formTitle')}
                        onSubmit={(e) => { e.preventDefault(); if (url.trim() && token.trim() && !busy) void connect(); }}
                    >
                        <div className="settings-field">
                            <label className="settings-label" htmlFor="ha-url">{t('settings.smartHome.address')}</label>
                            <input
                                id="ha-url"
                                className="settings-input"
                                type="text"
                                inputMode="url"
                                dir="ltr"
                                spellCheck={false}
                                autoComplete="off"
                                placeholder={ADDRESS_EXAMPLE}
                                value={url}
                                readOnly={busy === 'connect'}
                                onChange={(e) => setUrl(e.target.value)}
                            />
                        </div>
                        <div className="settings-field">
                            <label className="settings-label" htmlFor="ha-token">{t('settings.smartHome.token')}</label>
                            <input
                                id="ha-token"
                                className="settings-input"
                                type="password"
                                dir="ltr"
                                spellCheck={false}
                                autoComplete="off"
                                value={token}
                                readOnly={busy === 'connect'}
                                onChange={(e) => setToken(e.target.value)}
                            />
                        </div>
                        {error && <p className="settings-error" role="alert"><bdi>{error}</bdi></p>}
                        <button
                            type="submit"
                            className="btn-primary smart-home-start"
                            disabled={busy !== null || !url.trim() || !token.trim()}
                        >
                            {busy === 'connect' && <Loader2 size={13} className="spin" />}{' '}
                            {busy === 'connect' ? t('settings.smartHome.connecting') : t('settings.smartHome.connect')}
                        </button>
                    </form>
                </>
            )}
        </div>
    );
}

function NoticeLine({ notice }: { notice: Notice }) {
    return notice.ok ? (
        <p className="settings-ok smart-home-state" role="status">
            <CheckCircle2 size={13} aria-hidden="true" /> <bdi>{notice.text}</bdi>
        </p>
    ) : (
        <p className="settings-error smart-home-state" role="alert">
            <XCircle size={13} aria-hidden="true" /> <bdi>{notice.text}</bdi>
        </p>
    );
}
