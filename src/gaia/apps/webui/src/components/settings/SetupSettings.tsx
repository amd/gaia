// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { useT } from '../../i18n/useT';
import * as api from '../../services/api';
import { useChatStore, type SettingsSection } from '../../stores/chatStore';
import { ConfirmSheet } from '../setup/ConfirmSheet';
import { formatSize } from '../setup/format';
import type { PreflightReport, SetupPlan } from '../../types';

interface Row {
    label: string;
    value: string | null;
    section: SettingsSection;
}

/** Everything setup chose, each with its home in Settings, and setup again. */
export function SetupSettings() {
    const t = useT();
    const openSettings = useChatStore((s) => s.openSettings);
    const closeSettings = useChatStore((s) => s.closeSettings);
    const runSetupAgain = useChatStore((s) => s.runSetupAgain);
    const [plan, setPlan] = useState<SetupPlan | null>(null);
    const [hardware, setHardware] = useState<PreflightReport | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [asking, setAsking] = useState(false);

    useEffect(() => {
        api.getSetupPlan().then(setPlan).catch((err) => setError(err instanceof Error ? err.message : String(err)));
        api.getOnboardingPreflight().then(setHardware).catch((err) => setError(err instanceof Error ? err.message : String(err)));
    }, []);

    const chat = plan?.catalog?.find((e) => e.id === plan.chat_model.id);
    const installed = (plan?.catalog ?? []).filter((e) => e.downloaded);
    const installedGb = installed.reduce((sum, e) => sum + (e.full_size_gb ?? 0), 0);
    const device = [hardware?.gpu_name, hardware?.npu_detected ? t('setup.checks.npu') : null].filter(Boolean).join(' · ');
    const groups: Array<{ title: string; rows: Row[] }> = [
        {
            title: t('settings.setup.where'),
            rows: [
                { label: t('settings.setup.chatRuns'), value: chat?.name ?? null, section: 'model' },
                { label: t('settings.setup.cloudKeys'), value: null, section: 'model' },
            ],
        },
        {
            title: t('setup.pc.step'),
            rows: [
                { label: t('settings.setup.detected'), value: [device, hardware?.ram_gb != null ? `${hardware.ram_gb} GB` : null].filter(Boolean).join(' · ') || null, section: 'advanced' },
                { label: t('settings.setup.server'), value: plan?.lemonade.mode === 'external' ? t('settings.setup.serverYours') : t('settings.setup.serverOwn'), section: 'advanced' },
                { label: t('setup.gpu.title'), value: null, section: 'model' },
            ],
        },
        {
            title: t('setup.models.step'),
            rows: [
                { label: t('settings.setup.models'), value: plan?.catalog ? t('settings.setup.modelsValue', { n: String(installed.length), size: formatSize(installedGb) }) : null, section: 'model' },
                { label: t('settings.setup.context'), value: null, section: 'model' },
            ],
        },
        {
            title: t('settings.setup.after'),
            rows: [
                { label: t('settings.section.connectors'), value: null, section: 'connectors' },
                { label: t('settings.section.memory'), value: null, section: 'memory' },
                { label: t('settings.language'), value: null, section: 'general' },
                { label: t('settings.setup.signIn'), value: null, section: 'general' },
            ],
        },
    ];

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">{t('settings.section.setup')}</h2>
            <p className="settings-hint">{t('settings.setup.lede')}</p>
            <div className="settings-setup-status">
                <span>
                    <strong>{t('settings.setup.ready')}</strong>
                    {chat && <span className="settings-hint"> <bdi>{t('settings.setup.running', { name: chat.name })}</bdi></span>}
                </span>
                <button type="button" className="btn-secondary" data-testid="setup-run-again" onClick={() => setAsking(true)}>
                    {t('settings.setup.runAgain')}
                </button>
            </div>
            {error && <p className="setup-error" role="alert">{error}</p>}
            {groups.map((g) => (
                <section key={g.title} className="settings-setup-group" aria-label={g.title}>
                    <h3 className="settings-label">{g.title}</h3>
                    {g.rows.map((r) => (
                        <button key={r.label} type="button" className="settings-setup-row" onClick={() => openSettings(r.section)}>
                            <span>
                                <span className="settings-setup-row-label">{r.label}</span>
                                {r.value && <bdi className="settings-hint settings-setup-row-value">{r.value}</bdi>}
                            </span>
                            <ChevronRight size={14} className="settings-setup-chevron" aria-hidden="true" />
                        </button>
                    ))}
                </section>
            ))}
            {asking && (
                <ConfirmSheet
                    title={t('settings.setup.confirmTitle')}
                    confirmLabel={t('settings.setup.confirmYes')}
                    cancelLabel={t('setup.again.notNow')}
                    onConfirm={() => { setAsking(false); closeSettings(); runSetupAgain(true); }}
                    onCancel={() => setAsking(false)}
                >
                    <p>{t('settings.setup.confirmBody')}</p>
                </ConfirmSheet>
            )}
        </div>
    );
}
