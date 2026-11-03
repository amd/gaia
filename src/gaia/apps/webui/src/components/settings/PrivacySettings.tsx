// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import { useInferencePlace } from '../../stores/modelStore';
import { useT } from '../../i18n/useT';
import { SettingsDisclosure } from './SettingsDisclosure';
import { UndoHistorySettings } from './UndoHistorySettings';

/** Where data lives and goes, and how to delete it. */
export function PrivacySettings() {
    const t = useT();
    const sessions = useChatStore((s) => s.sessions);
    const removeSession = useChatStore((s) => s.removeSession);
    const place = useInferencePlace();
    const dataDir = useChatStore((s) => s.systemStatus?.data_dir);
    const [confirm, setConfirm] = useState(false);
    const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
    useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

    const clearAll = useCallback(async () => {
        if (!confirm) {
            setConfirm(true);
            timer.current = setTimeout(() => setConfirm(false), 4000);
            return;
        }
        setConfirm(false);
        const failed: string[] = [];
        for (const s of sessions) {
            try {
                await api.deleteSession(s.id);
                removeSession(s.id);
            } catch (err) {
                failed.push(`${s.title}: ${err instanceof Error ? err.message : err}`);
            }
        }
        setResult(failed.length
            ? { ok: false, text: t('settings.privacy.deleteFailed', { n: failed.length, reason: failed.join('; ') }) }
            : { ok: true, text: t('settings.privacy.deleted') });
    }, [confirm, removeSession, sessions, t]);

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">{t('settings.section.privacy')}</h2>
            <section className="settings-card" aria-label={t('settings.privacy.whereTitle')}>
                <p className="settings-note">{t('settings.privacy.stays')}</p>
                <p className={place?.remote ? 'settings-note' : 'settings-hint'}>
                    {place?.remote
                        ? t('settings.privacy.remote', { name: place.label })
                        : place ? t('settings.privacy.local') : t('settings.privacy.unknown')}
                </p>
                {dataDir && (
                    <div className="setting-row setting-row-path">
                        <span>{t('settings.privacy.dataFolder')}</span>
                        <bdi dir="ltr" className="setting-path">{dataDir}</bdi>
                    </div>
                )}
            </section>
            <SettingsDisclosure title={t('settings.privacy.undo')}>
                <UndoHistorySettings />
            </SettingsDisclosure>
            <section className="settings-card" aria-label={t('settings.privacy.deleteTitle')}>
                <p className="danger-warning">
                    {sessions.length === 0 ? t('settings.privacy.nothingToDelete') : t('settings.privacy.deleteWarning')}
                </p>
                <button type="button" className="btn-danger" onClick={() => void clearAll()} disabled={sessions.length === 0}>
                    {confirm ? t('settings.privacy.deleteConfirm') : t('settings.privacy.delete')}
                </button>
            </section>
            {result && <p className={result.ok ? 'settings-ok' : 'settings-error'} role={result.ok ? 'status' : 'alert'}>{result.text}</p>}
        </div>
    );
}
