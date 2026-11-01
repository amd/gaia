// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import { useT } from '../../i18n/useT';
import type { PermissionMode, SessionPermissions } from '../../types';
import { announcePermissionMode } from '../../utils/permissionModeEvents';
import { SettingsDisclosure } from './SettingsDisclosure';

/** Every chat with full access on or "always allow" grants, and the way to take them back. */
export function PermissionsSettings() {
    const t = useT();
    const sessions = useChatStore((s) => s.sessions);
    const [rows, setRows] = useState<SessionPermissions[] | null>(null);
    const [defaultMode, setDefaultMode] = useState<PermissionMode | null>(null);
    const [confirming, setConfirming] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setError(null);
        try {
            const all = await api.listAllPermissions();
            setRows(all.sessions);
            setDefaultMode(all.default_mode);
            // The new-chat chip follows the saved default without a reload.
            useChatStore.getState().setDefaultPermissionMode(all.default_mode);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        }
    }, []);
    useEffect(() => { void load(); }, [load]);

    const act = useCallback(async (fn: () => Promise<unknown>) => {
        setError(null);
        try {
            await fn();
            await load();
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        }
    }, [load]);

    const saveDefault = useCallback(async (mode: PermissionMode) => {
        setConfirming(false);
        await act(() => api.setDefaultPermissionMode(mode));
    }, [act]);

    const onToggle = () => {
        if (defaultMode === 'full_access') void saveDefault('ask');
        else setConfirming((v) => !v);
    };

    const title = (id: string) => sessions.find((s) => s.id === id)?.title ?? id;

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">{t('settings.section.permissions')}</h2>
            <p className="settings-pane-lede">{t('settings.permissions.lede')}</p>
            <details className="settings-more">
                <summary><ChevronDown size={13} className="settings-disclosure-chevron" aria-hidden="true" /> {t('settings.model.howItWorks')}</summary>
                <p className="settings-hint">{t('settings.permissions.perChat')}</p>
            </details>
            <section className="settings-card" aria-label={t('settings.permissions.newChats')}>
                <label className="setting-row">
                    <span className="settings-label">{t('settings.permissions.fullAccess')}</span>
                    <span className="toggle-switch">
                        <input
                            type="checkbox"
                            checked={defaultMode === 'full_access'}
                            onChange={onToggle}
                            disabled={defaultMode === null}
                            aria-label={t('settings.permissions.fullAccess')}
                        />
                        <span className="toggle-track" />
                    </span>
                </label>
                <p className="settings-hint">
                    {defaultMode === 'full_access' ? t('settings.permissions.on') : t('settings.permissions.off')}
                </p>
                {confirming && (
                    <div className="settings-warning" role="alert">
                        <p>{t('settings.permissions.warning')}</p>
                        <div className="settings-row">
                            <button type="button" className="btn-danger" onClick={() => void saveDefault('full_access')}>
                                {t('settings.permissions.turnOn')}
                            </button>
                            <button type="button" className="btn-secondary" onClick={() => setConfirming(false)}>
                                {t('settings.key.cancel')}
                            </button>
                        </div>
                    </div>
                )}
            </section>

            {error && <p className="settings-error" role="alert">{error}</p>}
            {rows && rows.length === 0 && (
                <p className="settings-hint">{t('settings.permissions.none')}</p>
            )}
            {!!rows?.length && (
                <SettingsDisclosure title={t('settings.permissions.chats')} count={rows.length} defaultOpen>
                {rows.map((r) => (
                    <section key={r.session_id} className="settings-field permission-chat" aria-label={title(r.session_id)}>
                        <div className="settings-row settings-row-between">
                            <span className="settings-label">{title(r.session_id)}</span>
                            {r.mode === 'full_access' && (
                                <button
                                    type="button"
                                    className="btn-secondary"
                                    onClick={() => act(async () => {
                                        const saved = (await api.setPermissionMode(r.session_id, 'ask')).mode;
                                        announcePermissionMode(r.session_id, saved);
                                    })}
                                >
                                    {t('settings.permissions.turnOff')}
                                </button>
                            )}
                        </div>
                        {r.grants.length > 0 && (
                            <ul className="grant-list">
                                {r.grants.map((g) => (
                                    <li key={g.key} className="grant-row">
                                        <code>{g.label}</code>
                                        <button
                                            type="button"
                                            className="link-btn"
                                            onClick={() => act(() => api.revokeGrants(r.session_id, g.key))}
                                            aria-label={t('settings.permissions.revokeAria', { name: g.label })}
                                        >
                                            {t('settings.permissions.revoke')}
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        )}
                        {r.grants.length > 1 && (
                            <button type="button" className="link-btn" onClick={() => act(() => api.revokeGrants(r.session_id))}>
                                {t('settings.permissions.revokeAll')}
                            </button>
                        )}
                    </section>
                ))}
                </SettingsDisclosure>
            )}
        </div>
    );
}
