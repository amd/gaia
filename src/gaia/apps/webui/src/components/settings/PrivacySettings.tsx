// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import { syncModelAfterSettingChange, useInferencePlace, useModelStore } from '../../stores/modelStore';
import { useT } from '../../i18n/useT';
import { TroubleshootingReport, type ReportClasses } from '../TroubleshootingReport';
import { ConfirmSheet } from '../setup/ConfirmSheet';
import { SettingsDisclosure } from './SettingsDisclosure';
import { UndoHistorySettings } from './UndoHistorySettings';

const REPORT_CLASSES: ReportClasses = {
    root: 'settings-report',
    button: 'btn-secondary',
    hint: 'settings-hint',
    error: 'settings-error',
};

/** Where data lives and goes, and how to delete it. */
export function PrivacySettings() {
    const t = useT();
    const sessions = useChatStore((s) => s.sessions);
    const removeSession = useChatStore((s) => s.removeSession);
    const place = useInferencePlace();
    const dataDir = useChatStore((s) => s.systemStatus?.data_dir);
    // The count when the dialog opened; the live list shrinks during the delete.
    const [asking, setAsking] = useState<number | null>(null);
    const [deleting, setDeleting] = useState(false);
    const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
    const [localOnly, setLocalOnly] = useState<boolean | null>(null);
    const [savingPolicy, setSavingPolicy] = useState(false);
    const [policyResult, setPolicyResult] = useState<{ ok: boolean; text: string } | null>(null);
    useEffect(() => {
        let active = true;
        void api.getSettings()
            .then((settings) => { if (active) setLocalOnly(settings.network_policy === 'local_ai_only'); })
            .catch((err) => { if (active) setPolicyResult({ ok: false, text: err instanceof Error ? err.message : String(err) }); });
        return () => { active = false; };
    }, []);

    const toggleLocalOnly = useCallback(async () => {
        if (localOnly === null || savingPolicy) return;
        const previous = localOnly;
        const modelBefore = useModelStore.getState().active?.model;
        const next = !previous;
        setLocalOnly(next);
        setSavingPolicy(true);
        setPolicyResult(null);
        try {
            const settings = await api.updateSettings({ network_policy: next ? 'local_ai_only' : 'cloud_allowed' });
            setLocalOnly(settings.network_policy === 'local_ai_only');
            // Turning Local only on drops a cloud model for the local default.
            void syncModelAfterSettingChange(modelBefore);
            const notices = settings.policy_notices ?? [];
            const hasWarning = notices.some((notice) => notice.severity === 'warning');
            const text = notices.length
                ? notices.map((notice) => {
                    if (notice.code === 'users_server_keeps_keys') {
                        return t('settings.privacy.usersServerKeepsKeys', { url: notice.url ?? '' });
                    }
                    if (notice.code === 'runtime_keys_not_dropped') {
                        return t('settings.privacy.runtimeKeysNotDropped', { reason: notice.reason ?? '' });
                    }
                    if (notice.code === 'saved_models_not_checked') {
                        return t('settings.privacy.savedModelsNotChecked', { reason: notice.reason ?? '' });
                    }
                    return t('settings.privacy.policyNoticeUnknown');
                }).join(' ')
                : t(next ? 'settings.privacy.localOnlyOn' : 'settings.privacy.localOnlyOff');
            setPolicyResult({ ok: !hasWarning, text });
        } catch (err) {
            setLocalOnly(previous);
            setPolicyResult({ ok: false, text: err instanceof Error ? err.message : String(err) });
        } finally {
            setSavingPolicy(false);
        }
    }, [localOnly, savingPolicy, t]);

    const openDataFolder = useCallback(async (path: string) => {
        try {
            await api.openFileOrFolder(path, false);
        } catch (err) {
            setResult({ ok: false, text: t('settings.privacy.openFolderFailed', { reason: err instanceof Error ? err.message : String(err) }) });
        }
    }, [t]);

    const clearAll = useCallback(async () => {
        setDeleting(true);
        const failed: string[] = [];
        for (const s of sessions) {
            try {
                await api.deleteSession(s.id);
                removeSession(s.id);
            } catch (err) {
                failed.push(`${s.title}: ${err instanceof Error ? err.message : err}`);
            }
        }
        setDeleting(false);
        setAsking(null);
        setResult(failed.length
            ? { ok: false, text: t('settings.privacy.deleteFailed', { n: failed.length, reason: failed.join('; ') }) }
            : { ok: true, text: t('settings.privacy.deleted') });
    }, [removeSession, sessions, t]);

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">{t('settings.section.privacy')}</h2>
            <section className="settings-card" aria-label={t('settings.privacy.localOnlyTitle')}>
                <div>
                    <label className="setting-row">
                        <strong>{t('settings.privacy.localOnlyTitle')}</strong>
                        <span className="toggle-switch">
                            <input
                                type="checkbox"
                                role="switch"
                                checked={localOnly === true}
                                aria-label={t('settings.privacy.localOnlyTitle')}
                                aria-describedby="privacy-local-only-hint"
                                onChange={() => void toggleLocalOnly()}
                                // Stays enabled while saving so keyboard focus is not dropped.
                                disabled={localOnly === null}
                                aria-busy={savingPolicy}
                            />
                            <span className="toggle-track" />
                        </span>
                    </label>
                    <p className="settings-hint" id="privacy-local-only-hint">{t('settings.privacy.localOnlyHint')}</p>
                </div>
                {policyResult && (
                    <p className={policyResult.ok ? 'settings-ok' : 'settings-error'} role={policyResult.ok ? 'status' : 'alert'}>
                        {policyResult.text}
                    </p>
                )}
            </section>
            <section className="settings-card" aria-labelledby="privacy-where-title">
                <h3 className="settings-label" id="privacy-where-title">{t('settings.privacy.whereTitle')}</h3>
                <p className="settings-note">{t('settings.privacy.stays')}</p>
                <p className={place?.remote ? 'settings-note' : 'settings-hint'}>
                    {place?.remote
                        ? t('settings.privacy.remote', { name: place.label })
                        : place ? t('settings.privacy.local') : t('settings.privacy.unknown')}
                </p>
                {dataDir && (
                    <div className="setting-row setting-row-path">
                        <span>{t('settings.privacy.dataFolder')}</span>
                        <span className="setting-path-actions">
                            <bdi dir="ltr" className="setting-path">{dataDir}</bdi>
                            <button type="button" className="link-btn" onClick={() => void openDataFolder(dataDir)}>
                                {t('settings.privacy.openFolder')}
                            </button>
                        </span>
                    </div>
                )}
            </section>
            <section className="settings-card" aria-labelledby="privacy-report-title">
                <h3 className="settings-label" id="privacy-report-title">{t('settings.privacy.reportTitle')}</h3>
                <p className="settings-note">{t('settings.privacy.reportNote')}</p>
                <TroubleshootingReport classes={REPORT_CLASSES} />
            </section>
            <SettingsDisclosure title={t('settings.privacy.undo')}>
                <UndoHistorySettings />
            </SettingsDisclosure>
            <section className="settings-card" aria-labelledby="privacy-delete-title">
                <h3 className="settings-label" id="privacy-delete-title">{t('settings.privacy.deleteTitle')}</h3>
                <p className="danger-warning">
                    {sessions.length === 0 ? t('settings.privacy.nothingToDelete') : t('settings.privacy.deleteWarning')}
                </p>
                <button type="button" className="btn-danger" onClick={() => { setResult(null); setAsking(sessions.length); }} disabled={sessions.length === 0}>
                    {t('settings.privacy.delete')}
                </button>
            </section>
            {asking !== null && (
                <ConfirmSheet
                    title={t.plural('settings.privacy.deleteAsk', asking)}
                    confirmLabel={t.plural('settings.privacy.deleteYes', asking)}
                    cancelLabel={t('settings.privacy.deleteCancel')}
                    onConfirm={() => void clearAll()}
                    onCancel={() => setAsking(null)}
                    danger
                    busy={deleting}
                >
                    <p>{t('settings.privacy.deleteWarning')}</p>
                    <p>{t('settings.privacy.deleteFinal')}</p>
                </ConfirmSheet>
            )}
            {result && <p className={result.ok ? 'settings-ok' : 'settings-error'} role={result.ok ? 'status' : 'alert'}>{result.text}</p>}
        </div>
    );
}
