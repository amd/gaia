// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { useT, dateLocale, type Translate } from '../../i18n/useT';
import { isMessageKey, type MessageKey } from '../../i18n/registry';
import { ApiError } from '../../services/api';
import {
    listAutomations,
    removeAutomation,
    setAutomationPaused,
    type Automation,
    type AutomationList,
} from '../../services/automationsApi';
import { useChatStore } from '../../stores/chatStore';
import { ConfirmSheet } from '../setup/ConfirmSheet';

function errorText(err: unknown): string {
    if (err instanceof ApiError) return err.detail || err.message;
    return err instanceof Error ? err.message : String(err);
}

const UNITS: Array<[string, number]> = [['week', 604800], ['day', 86400], ['hour', 3600], ['minute', 60]];

/** "Every 30 minutes" in the UI language; null for a shape with no wording. */
export function everyPhrase(seconds: number, t: Translate): string | null {
    const unit = UNITS.find(([, size]) => seconds % size === 0) ?? UNITS[UNITS.length - 1];
    const n = Math.max(1, Math.round(seconds / unit[1]));
    const key = `schedules.span.${unit[0]}.${new Intl.PluralRules(t.locale).select(n)}`;
    if (!isMessageKey(key)) return null;
    return t.compose((say) => say('schedules.desc.every', { span: say(key as MessageKey, { n }) }));
}

function formatWhen(epochSeconds: number, t: Translate): string {
    return new Intl.DateTimeFormat(dateLocale(t), {
        weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
    }).format(new Date(epochSeconds * 1000));
}

const DAY_CODES = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];

/** The store's "mon,tue,…" as "Weekdays" or "Mon, Wed" in the UI language; the raw text for anything else. */
export function daysPhrase(days: string, t: Translate): string {
    const idx = days.split(',').map((d) => DAY_CODES.indexOf(d.trim()));
    if (idx.some((i) => i < 0)) return days;
    return t.compose((say) => {
        if (idx.length === 7) return say('schedules.days.every');
        if (idx.length === 5 && idx.every((i) => i < 5)) return say('schedules.days.weekdays');
        if (idx.length === 2 && idx.includes(5) && idx.includes(6)) return say('schedules.days.weekends');
        return idx.map((i) => say(`schedules.day.${i}` as MessageKey)).join(say('schedules.days.join'));
    });
}

function kindLabel(item: Automation, t: Translate): string {
    if (item.heartbeat) return t('settings.automations.kind.heartbeat');
    return t(`settings.automations.kind.${item.kind}` as MessageKey);
}

function AutomationCard({ item, busy, onToggle, onRemove }: {
    item: Automation;
    busy: boolean;
    onToggle: () => void;
    onRemove: () => void;
}) {
    const t = useT();
    const when = <bdi>{formatWhen(item.next_at, t)}</bdi>;
    const repeat = item.every_seconds > 0 ? everyPhrase(item.every_seconds, t) : null;
    const detail = item.say || item.prompt;
    return (
        <li className={`settings-card automation-card${item.paused ? ' is-paused' : ''}`} data-automation={item.id}>
            <div className="automation-head">
                <h3 className="settings-label automation-name"><bdi>{item.name}</bdi></h3>
                <span className="automation-kind">{kindLabel(item, t)}</span>
                {item.paused && <span className="automation-paused">{t('settings.automations.paused')}</span>}
            </div>
            <p className="settings-hint automation-when">
                {item.every_seconds > 0 ? (
                    <>
                        {repeat && <span>{repeat}</span>}
                        {!item.paused && <span>{t('settings.automations.next')} {when}</span>}
                    </>
                ) : (
                    <span>{t('settings.automations.once')} {when}</span>
                )}
                {item.quiet && <span>{t('settings.automations.quiet')} <bdi dir="ltr">{item.quiet}</bdi></span>}
                {item.days && <span>{t('settings.automations.days')} {daysPhrase(item.days, t)}</span>}
            </p>
            {detail && <p className="automation-detail"><bdi>{detail}</bdi></p>}
            {item.check && (
                <p className="settings-hint automation-check">
                    {t('settings.automations.check')} <code dir="ltr">{item.check}</code>
                </p>
            )}
            {item.failures > 0 && (
                <p className="settings-error" role="status">
                    {t.plural('settings.automations.failing', item.failures)}
                </p>
            )}
            <div className="settings-row">
                <button type="button" className="btn-secondary" onClick={onToggle} disabled={busy}>
                    {busy && <Loader2 size={13} className="spin" aria-hidden="true" />}
                    {item.paused ? t('settings.automations.resume') : t('settings.automations.pause')}
                </button>
                <button type="button" className="btn-danger" onClick={onRemove} disabled={busy}>
                    {t('settings.automations.remove')}
                </button>
            </div>
        </li>
    );
}

/** Settings > Automations: see, pause, resume and remove what GAIA runs on its own. */
export function AutomationsSettings() {
    const t = useT();
    const openSettings = useChatStore((s) => s.openSettings);
    const [list, setList] = useState<AutomationList | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);
    const [busy, setBusy] = useState<ReadonlySet<string>>(new Set());
    const [confirm, setConfirm] = useState<Automation | null>(null);
    const latest = useRef(0);

    const load = useCallback(() => {
        // Only the newest request may show: an older reply can arrive last.
        const request = ++latest.current;
        listAutomations()
            .then((l) => { if (request === latest.current) { setLoadError(null); setList(l); } })
            .catch((err) => { if (request === latest.current) setLoadError(errorText(err)); });
    }, []);

    useEffect(load, [load]);

    const act = async (id: string, call: () => Promise<unknown>) => {
        setBusy((ids) => new Set(ids).add(id));
        setActionError(null);
        try {
            await call();
        } catch (err) {
            setActionError(errorText(err));
        } finally {
            setBusy((ids) => { const next = new Set(ids); next.delete(id); return next; });
            load();
        }
    };

    const remove = (item: Automation) => {
        setConfirm(null);
        void act(item.id, () => removeAutomation(item.id));
    };

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">{t('settings.section.automations')}</h2>
            <p className="settings-pane-lede">{t('settings.automations.lede')}</p>
            {loadError && (
                <>
                    <p className="settings-error" role="alert">{t('settings.automations.loadFailed', { error: loadError })}</p>
                    <button type="button" className="btn-secondary" onClick={load}>{t('settings.automations.retry')}</button>
                </>
            )}
            {!list && !loadError && (
                <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.loading')}</p>
            )}
            {list && list.delivers === null && list.automations.length > 0 && (
                <p className="settings-hint automation-unchecked">{t('settings.automations.deliveryUnknown')}</p>
            )}
            {list && list.delivers === false && list.automations.length > 0 && (
                <div className="settings-warning">
                    <p>{t('settings.automations.noDelivery')}</p>
                    <button type="button" className="btn-secondary automation-messaging" onClick={() => openSettings('messaging')}>
                        {t('settings.automations.openMessaging')}
                    </button>
                </div>
            )}
            {actionError && <p className="settings-error" role="alert">{actionError}</p>}
            {list && list.automations.length === 0 && (
                <p className="settings-hint automation-empty">{t('settings.automations.empty')}</p>
            )}
            {list && list.automations.length > 0 && (
                <ul className="automation-list">
                    {list.automations.map((item) => (
                        <AutomationCard
                            key={item.id}
                            item={item}
                            busy={busy.has(item.id)}
                            onToggle={() => void act(item.id, () => setAutomationPaused(item.id, !item.paused))}
                            onRemove={() => setConfirm(item)}
                        />
                    ))}
                </ul>
            )}
            {confirm && (
                <ConfirmSheet
                    title={t('settings.automations.removeTitle', { name: confirm.name })}
                    confirmLabel={t('settings.automations.remove')}
                    cancelLabel={t('settings.automations.cancel')}
                    danger
                    onConfirm={() => remove(confirm)}
                    onCancel={() => setConfirm(null)}
                >
                    <p>{t('settings.automations.removeBody')}</p>
                </ConfirmSheet>
            )}
        </div>
    );
}
