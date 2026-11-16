// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { useT } from '../../i18n/useT';
import { RadioGroup } from '../RadioGroup';
import { formatCost, formatPerMillion } from './cloudPrice';
import {
    HYBRID_PROVIDERS, hybridModelName, hybridProviderName,
} from '../../utils/hybrid';
import type {
    HybridKind, HybridModelOption, HybridPolicy, HybridPreview, HybridRules, HybridSettings as Settings, HybridSettingsUpdate,
    HybridStats, HybridTemplate, ProviderId,
} from '../../types';
import '../SettingsModal.css';
import './HybridSettings.css';

const POLICIES: HybridPolicy[] = ['quality', 'balanced', 'custom'];
const DIFFICULTIES = [0, 1, 2, 3, 4, 5];
const MIN_TIMEOUT = 1;
const MAX_TIMEOUT = 120;
/** Providers with a tab in Settings > Model, where a missing one is set up. */
const TAB_PROVIDERS: ReadonlySet<string> = new Set<ProviderId>(['local', 'amd', 'fireworks']);

type Field = keyof HybridSettingsUpdate;
const FIELDS: Field[] = ['cheap_model', 'strong_model', 'classifier_model', 'classifier_timeout', 'policy', 'rules'];

interface Draft {
    cheap_model: string;
    strong_model: string;
    classifier_model: string;
    classifier_timeout: string;
    policy: HybridPolicy;
    rules: HybridRules;
}

function draftOf(s: Settings): Draft {
    return {
        cheap_model: s.cheap_model,
        strong_model: s.strong_model,
        classifier_model: s.classifier_model,
        classifier_timeout: String(s.classifier_timeout),
        policy: s.policy,
        // A custom policy saved without rules starts from Quality; config_error says what's wrong.
        rules: s.rules ?? s.presets.quality,
    };
}

function sameRules(a: HybridRules, b: HybridRules | null): boolean {
    return JSON.stringify(a) === JSON.stringify(b);
}

/** The field a save error is about: the 422's `loc`, else the setting its message names. */
function fieldOf(err: unknown): Field | null {
    const e = err as { fields?: unknown; detail?: unknown; message?: unknown };
    const fields = Array.isArray(e?.fields) ? e.fields : [];
    const fromLoc = FIELDS.find((f) => fields.includes(f));
    if (fromLoc) return fromLoc;
    const text = `${typeof e?.detail === 'string' ? e.detail : ''} ${typeof e?.message === 'string' ? e.message : ''}`;
    return FIELDS.find((f) => text.includes(`hybrid_${f}`)) ?? null;
}

function messageOf(err: unknown): string {
    return err instanceof Error ? err.message : String(err);
}

/** The settings, the change it would send, and why it can't be sent yet. */
function pendingUpdate(saved: Settings, draft: Draft): { update: HybridSettingsUpdate; timeoutValid: boolean } {
    const update: HybridSettingsUpdate = {};
    if (draft.cheap_model !== saved.cheap_model) update.cheap_model = draft.cheap_model;
    if (draft.strong_model !== saved.strong_model) update.strong_model = draft.strong_model;
    if (draft.classifier_model !== saved.classifier_model) update.classifier_model = draft.classifier_model;
    const timeout = draft.classifier_timeout.trim() === '' ? NaN : Number(draft.classifier_timeout);
    const timeoutValid = Number.isFinite(timeout) && timeout >= MIN_TIMEOUT && timeout <= MAX_TIMEOUT;
    if (timeoutValid && timeout !== saved.classifier_timeout) update.classifier_timeout = timeout;
    if (draft.policy !== saved.policy) update.policy = draft.policy;
    // Custom needs its whole table; the presets carry their own.
    if (draft.policy === 'custom' && (saved.policy !== 'custom' || !sameRules(draft.rules, saved.rules))) {
        update.rules = draft.rules;
    }
    return { update, timeoutValid };
}

interface Props {
    onSaved?: () => void;
    /** Called with each settings answer, e.g. so the provider tabs can follow hybrid's models. */
    onLoaded?: (settings: Settings) => void;
    /** Open a provider's tab to set it up. */
    onShowProvider?: (provider: ProviderId) => void;
}

/** Which models hybrid turns run on: templates, the models, the routing policy, a try-it box, and what it saved. */
export function HybridSettings({ onSaved, onLoaded, onShowProvider }: Props) {
    const t = useT();
    const id = useId();
    const [saved, setSaved] = useState<Settings | null>(null);
    const [draft, setDraft] = useState<Draft | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState<{ field: Field | null; message: string } | null>(null);
    const [savedNotice, setSavedNotice] = useState(false);
    const [stats, setStats] = useState<HybridStats | null>(null);
    const [statsError, setStatsError] = useState<string | null>(null);
    const [request, setRequest] = useState('');
    const [previewing, setPreviewing] = useState(false);
    const [preview, setPreview] = useState<HybridPreview | null>(null);
    const [previewError, setPreviewError] = useState<string | null>(null);
    const [templates, setTemplates] = useState<HybridTemplate[] | null>(null);
    const [templatesError, setTemplatesError] = useState<string | null>(null);
    const [applying, setApplying] = useState<string | null>(null);
    const [applyError, setApplyError] = useState<{ id: string; message: string } | null>(null);
    const [applied, setApplied] = useState<string | null>(null);
    const onLoadedRef = useRef(onLoaded);
    onLoadedRef.current = onLoaded;

    const show = useCallback((s: Settings) => {
        setSaved(s);
        setDraft(draftOf(s));
        onLoadedRef.current?.(s);
    }, []);

    const load = useCallback(async () => {
        setLoadError(null);
        try {
            show(await api.getHybridSettings());
        } catch (err) {
            setLoadError(messageOf(err));
        }
    }, [show]);

    const loadTemplates = useCallback(async () => {
        setTemplatesError(null);
        try {
            setTemplates(await api.getHybridTemplates());
        } catch (err) {
            setTemplatesError(messageOf(err));
        }
    }, []);

    const loadStats = useCallback(async () => {
        setStatsError(null);
        try {
            setStats(await api.getHybridStats());
        } catch (err) {
            setStatsError(messageOf(err));
        }
    }, []);

    useEffect(() => {
        void load();
        void loadTemplates();
        void loadStats();
    }, [load, loadTemplates, loadStats]);

    const applyTemplate = useCallback(async (tpl: HybridTemplate) => {
        setApplying(tpl.id);
        setApplyError(null);
        setApplied(null);
        setSaveError(null);
        setSavedNotice(false);
        try {
            show(await api.applyHybridTemplate(tpl.id));
            setApplied(tpl.name);
            onSaved?.();
        } catch (err) {
            setApplyError({ id: tpl.id, message: messageOf(err) });
        } finally {
            setApplying(null);
        }
    }, [onSaved, show]);

    const pending = useMemo(() => (saved && draft ? pendingUpdate(saved, draft) : null), [saved, draft]);
    const dirty = !!pending && Object.keys(pending.update).length > 0;
    const edited = dirty || (!!saved && !!draft && draft.classifier_timeout !== String(saved.classifier_timeout));

    const edit = (change: Partial<Draft>) => {
        setDraft((d) => (d ? { ...d, ...change } : d));
        setSavedNotice(false);
    };

    const save = useCallback(async () => {
        if (!pending || !pending.timeoutValid || !dirty) return;
        setSaving(true);
        setSaveError(null);
        setSavedNotice(false);
        try {
            show(await api.updateHybridSettings(pending.update));
            setApplied(null);
            setSavedNotice(true);
            onSaved?.();
        } catch (err) {
            setSaveError({ field: fieldOf(err), message: messageOf(err) });
        } finally {
            setSaving(false);
        }
    }, [dirty, onSaved, pending, show]);

    const tryRequest = useCallback(async () => {
        const text = request.trim();
        if (!text) return;
        setPreviewing(true);
        setPreviewError(null);
        setPreview(null);
        try {
            setPreview(await api.previewHybridRoute(text));
        } catch (err) {
            setPreviewError(messageOf(err));
        } finally {
            setPreviewing(false);
        }
    }, [request]);

    if (!saved || !draft) {
        return loadError
            ? (
                <div className="settings-field">
                    <p className="settings-error" role="alert">{loadError}</p>
                    <button type="button" className="btn-secondary" onClick={() => void load()}>{t('settings.hybrid.retry')}</button>
                </div>
            )
            : <p className="settings-hint" role="status"><Loader2 size={13} className="spin" /> {t('settings.loading')}</p>;
    }

    const modelName = (model: string) => hybridModelName(saved, model);
    const kindLabel = (kind: HybridKind | null) => (kind ? t(`settings.hybrid.kind.${kind}`) : t('settings.hybrid.preview.none'));
    const fieldError = (field: Field) => (saveError?.field === field ? saveError.message : null);
    const errorId = (field: Field) => `${id}-${field}-error`;

    const optionText = (m: HybridModelOption) => {
        if (!m.available) {
            return m.reason
                ? t.bare('settings.hybrid.unavailableBecause', { model: m.display_name, reason: m.reason })
                : t.bare('settings.hybrid.unavailable', { model: m.display_name });
        }
        // A local model costs nothing per token; a cloud one without a known price says so.
        const price = m.provider === 'local'
            ? t.bare('settings.hybrid.onThisPc')
            : formatPerMillion(m.input_per_million, m.output_per_million, t);
        return `${m.display_name} · ${price}`;
    };

    const modelSelect = (field: 'cheap_model' | 'strong_model' | 'classifier_model', label: string) => {
        const value = draft[field];
        const listed = saved.models.some((m) => m.id === value);
        const error = fieldError(field);
        return (
            <div className="settings-field">
                <label className="settings-label" htmlFor={`${id}-${field}`}>{label}</label>
                <select
                    id={`${id}-${field}`}
                    className="settings-input"
                    value={value ?? ''}
                    onChange={(e) => edit({ [field]: e.target.value } as Partial<Draft>)}
                    disabled={saving}
                    aria-invalid={!!error}
                    aria-describedby={error ? errorId(field) : undefined}
                >
                    {!listed && <option value={value}>{t.bare('settings.hybrid.currentSetting', { model: value })}</option>}
                    {HYBRID_PROVIDERS.map((provider) => {
                        const models = saved.models.filter((m) => m.provider === provider);
                        if (!models.length) return null;
                        return (
                            <optgroup key={provider} label={hybridProviderName(t, provider)}>
                                {models.map((m) => (
                                    <option key={m.id} value={m.id} disabled={!m.available && m.id !== value}>
                                        {optionText(m)}
                                    </option>
                                ))}
                            </optgroup>
                        );
                    })}
                </select>
                {error && <p className="settings-error" id={errorId(field)} role="alert">{error}</p>}
            </div>
        );
    };

    const timeoutError = !pending?.timeoutValid
        ? t('settings.hybrid.timeoutRange', { min: MIN_TIMEOUT, max: MAX_TIMEOUT })
        : fieldError('classifier_timeout');
    const generalError = saveError && saveError.field === null ? saveError.message : null;

    return (
        <div className="hybrid-settings">
            {saved.config_error && <p className="settings-error" role="alert"><bdi>{saved.config_error}</bdi></p>}
            {saved.is_active !== null && (
                <p className="settings-hint">{saved.is_active ? t('settings.hybrid.active') : t('settings.hybrid.inactive')}</p>
            )}

            <section className="settings-field" aria-labelledby={`${id}-templates`}>
                <span className="settings-label" id={`${id}-templates`}>{t('settings.hybrid.templates')}</span>
                {templatesError && (
                    <div className="settings-row">
                        <p className="settings-error" role="alert">{templatesError}</p>
                        <button type="button" className="btn-secondary" onClick={() => void loadTemplates()}>{t('settings.hybrid.retry')}</button>
                    </div>
                )}
                {!templates && !templatesError && <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.loading')}</p>}
                {templates && (
                    <ul className="hybrid-templates">
                        {templates.map((tpl) => {
                            const titleId = `${id}-tpl-${tpl.id}`;
                            const inUse = tpl.cheap_model === saved.cheap_model && tpl.strong_model === saved.strong_model
                                && tpl.policy === saved.policy && tpl.classifier_model === saved.classifier_model;
                            const setUp = tpl.available ? [] : tpl.providers_needed.filter((p) => TAB_PROVIDERS.has(p)) as ProviderId[];
                            return (
                                <li key={tpl.id} className={`hybrid-template${tpl.available ? '' : ' is-unavailable'}`} aria-labelledby={titleId}>
                                    <div className="settings-row settings-row-between">
                                        <span className="hybrid-template-name" id={titleId}>{tpl.name}</span>
                                        <span className={`hybrid-badge${tpl.measured ? ' is-measured' : ''}`}>
                                            {tpl.measured ? t('settings.hybrid.measured') : t('settings.hybrid.notMeasured')}
                                        </span>
                                    </div>
                                    <p className="settings-hint">{tpl.description}</p>
                                    <p className="hybrid-template-pair">
                                        {tpl.cheap_model && tpl.strong_model && (
                                            <><bdi>{t('settings.hybrid.pair', { cheap: modelName(tpl.cheap_model), strong: modelName(tpl.strong_model) })}</bdi>{' · '}</>
                                        )}
                                        {t(`settings.hybrid.policy.${tpl.policy}`)}
                                    </p>
                                    {tpl.measured_note && <p className="settings-hint"><bdi>{tpl.measured_note}</bdi></p>}
                                    {tpl.classifier_note && <p className="settings-hint"><bdi>{tpl.classifier_note}</bdi></p>}
                                    {!tpl.available && tpl.reason && <p className="settings-hint" id={`${titleId}-why`}>{tpl.reason}</p>}
                                    <div className="settings-row">
                                        <button
                                            type="button"
                                            className="btn-secondary"
                                            disabled={!tpl.available || applying !== null || saving}
                                            aria-describedby={!tpl.available && tpl.reason ? `${titleId}-why` : undefined}
                                            aria-label={t.bare('settings.hybrid.useTemplateAria', { name: tpl.name })}
                                            onClick={() => void applyTemplate(tpl)}
                                        >
                                            {applying === tpl.id ? <Loader2 size={13} className="spin" /> : null} {t('settings.hybrid.useTemplate')}
                                        </button>
                                        {inUse && <span className="settings-ok">{t('settings.hybrid.inUse')}</span>}
                                        {onShowProvider && setUp.map((p) => (
                                            <button key={p} type="button" className="link-btn" onClick={() => onShowProvider(p)}>
                                                {t('settings.hybrid.setUp', { provider: hybridProviderName(t, p) })}
                                            </button>
                                        ))}
                                    </div>
                                    {applyError?.id === tpl.id && <p className="settings-error" role="alert">{applyError.message}</p>}
                                </li>
                            );
                        })}
                    </ul>
                )}
                {applied && <p className="settings-ok" role="status">{t('settings.hybrid.applied', { name: applied })}</p>}
            </section>

            <form
                className="hybrid-settings-form"
                onSubmit={(e) => { e.preventDefault(); void save(); }}
                aria-label={t('settings.hybrid.title')}
            >
                <div className="settings-grid">
                    {modelSelect('cheap_model', t('settings.hybrid.cheap'))}
                    {modelSelect('strong_model', t('settings.hybrid.strong'))}
                    {modelSelect('classifier_model', t('settings.hybrid.classifier'))}
                    <div className="settings-field">
                        <label className="settings-label" htmlFor={`${id}-timeout`}>{t('settings.hybrid.timeout')}</label>
                        <input
                            id={`${id}-timeout`}
                            className="settings-input"
                            type="number"
                            min={MIN_TIMEOUT}
                            max={MAX_TIMEOUT}
                            step="1"
                            inputMode="numeric"
                            value={draft.classifier_timeout}
                            onChange={(e) => edit({ classifier_timeout: e.target.value })}
                            disabled={saving}
                            aria-invalid={!!timeoutError}
                            aria-describedby={timeoutError ? errorId('classifier_timeout') : undefined}
                        />
                        {timeoutError && (
                            <p className="settings-error" id={errorId('classifier_timeout')} role="alert">{timeoutError}</p>
                        )}
                    </div>
                </div>

                {saved.models_error && <p className="settings-error" role="alert">{saved.models_error}</p>}
                {saved.prices_error && <p className="settings-hint">{t('settings.hybrid.pricesStale', { reason: saved.prices_error })}</p>}
                {(saved.measured_note || saved.classifier_note) && (
                    <ul className="hybrid-notes" aria-label={t('settings.hybrid.notes')}>
                        {saved.measured_note && <li><bdi>{saved.measured_note}</bdi></li>}
                        {saved.classifier_note && <li><bdi>{saved.classifier_note}</bdi></li>}
                    </ul>
                )}

                <div className="settings-field">
                    <span className="settings-label" id={`${id}-policy`}>{t('settings.hybrid.policy')}</span>
                    <RadioGroup className="provider-tabs" aria-labelledby={`${id}-policy`}>
                        {POLICIES.map((p) => (
                            <button
                                key={p}
                                type="button"
                                role="radio"
                                aria-checked={draft.policy === p}
                                className={`provider-tab${draft.policy === p ? ' is-active' : ''}`}
                                onClick={() => edit({ policy: p })}
                                disabled={saving}
                            >
                                {t(`settings.hybrid.policy.${p}`)}
                            </button>
                        ))}
                    </RadioGroup>
                    <p className="settings-hint">{t(`settings.hybrid.policy.${draft.policy}.hint`)}</p>
                    {fieldError('policy') && <p className="settings-error" role="alert">{fieldError('policy')}</p>}
                </div>

                {draft.policy === 'custom' && (
                    <div className="settings-field">
                        <div className="settings-row">
                            <button type="button" className="btn-secondary" disabled={saving} onClick={() => edit({ rules: saved.presets.quality })}>
                                {t('settings.hybrid.fromQuality')}
                            </button>
                            <button type="button" className="btn-secondary" disabled={saving} onClick={() => edit({ rules: saved.presets.balanced })}>
                                {t('settings.hybrid.fromBalanced')}
                            </button>
                        </div>
                        <table className="hybrid-rules" aria-describedby={fieldError('rules') ? errorId('rules') : undefined}>
                            <caption className="settings-label">{t('settings.hybrid.rules')}</caption>
                            <thead>
                                <tr>
                                    <th scope="col">{t('settings.hybrid.rules.kind')}</th>
                                    <th scope="col">{t('settings.hybrid.rules.upTo')}</th>
                                    <th scope="col">{t('settings.hybrid.rules.highStakes')}</th>
                                </tr>
                            </thead>
                            <tbody>
                                {saved.kinds.map((kind) => {
                                    const rule = draft.rules[kind];
                                    const label = t(`settings.hybrid.kind.${kind}`);
                                    const setRule = (change: Partial<typeof rule>) =>
                                        edit({ rules: { ...draft.rules, [kind]: { ...rule, ...change } } });
                                    return (
                                        <tr key={kind}>
                                            <th scope="row">{label}</th>
                                            <td>
                                                <select
                                                    className="settings-input settings-select-compact"
                                                    value={String(rule.cheap_max_difficulty)}
                                                    onChange={(e) => setRule({ cheap_max_difficulty: Number(e.target.value) })}
                                                    disabled={saving}
                                                    aria-label={t.bare('settings.hybrid.rules.upToAria', { kind: label })}
                                                >
                                                    {DIFFICULTIES.map((d) => (
                                                        <option key={d} value={String(d)}>
                                                            {d === 0 ? t.bare('settings.hybrid.rules.never') : String(d)}
                                                        </option>
                                                    ))}
                                                </select>
                                            </td>
                                            <td>
                                                <label className="toggle-switch">
                                                    <input
                                                        type="checkbox"
                                                        role="switch"
                                                        checked={rule.allow_high_stakes}
                                                        onChange={(e) => setRule({ allow_high_stakes: e.target.checked })}
                                                        disabled={saving || rule.cheap_max_difficulty === 0}
                                                        aria-label={t.bare('settings.hybrid.rules.highStakesAria', { kind: label })}
                                                    />
                                                    <span className="toggle-track" />
                                                </label>
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                        {fieldError('rules') && <p className="settings-error" id={errorId('rules')} role="alert">{fieldError('rules')}</p>}
                    </div>
                )}

                <p className="settings-note">{t('settings.hybrid.failClosed')}</p>

                <div className="settings-row">
                    <button type="submit" className="btn-primary" disabled={saving || !dirty || !pending?.timeoutValid}>
                        {saving ? <Loader2 size={13} className="spin" /> : null} {t('settings.advanced.save')}
                    </button>
                    {edited && (
                        <button
                            type="button"
                            className="btn-secondary"
                            disabled={saving}
                            onClick={() => { setDraft(draftOf(saved)); setSaveError(null); }}
                        >
                            {t('settings.hybrid.discard')}
                        </button>
                    )}
                </div>
                {generalError && <p className="settings-error" role="alert">{generalError}</p>}
                {savedNotice && <p className="settings-ok" role="status">{t('settings.hybrid.saved')}</p>}
            </form>

            <form
                className="settings-field hybrid-preview"
                onSubmit={(e) => { e.preventDefault(); void tryRequest(); }}
            >
                <label className="settings-label" htmlFor={`${id}-preview`}>{t('settings.hybrid.preview')}</label>
                <textarea
                    id={`${id}-preview`}
                    className="settings-input"
                    rows={3}
                    value={request}
                    onChange={(e) => setRequest(e.target.value)}
                    placeholder={t.bare('settings.hybrid.preview.placeholder')}
                    onKeyDown={(e) => {
                        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void tryRequest(); }
                    }}
                />
                <div className="settings-row">
                    <button type="submit" className="btn-secondary" disabled={previewing || !request.trim()}>
                        {previewing ? <Loader2 size={13} className="spin" /> : null} {t('settings.hybrid.preview.run')}
                    </button>
                    {dirty && <span className="settings-hint">{t('settings.hybrid.preview.unsaved')}</span>}
                </div>
                {previewError && <p className="settings-error" role="alert">{previewError}</p>}
                {preview && (
                    <div role="status" aria-label={t('settings.hybrid.preview.result')}>
                        {!preview.classified && <p className="settings-hint">{t('settings.hybrid.preview.failedClosed')}</p>}
                        {!preview.classified && preview.fix && <p className="settings-hint"><bdi>{preview.fix}</bdi></p>}
                        <dl className="hybrid-preview-result">
                            <dt>{t('settings.hybrid.preview.model')}</dt>
                            <dd><bdi>{modelName(preview.model)}</bdi></dd>
                            <dt>{t('settings.hybrid.preview.kind')}</dt>
                            <dd>{kindLabel(preview.kind)}</dd>
                            <dt>{t('settings.hybrid.preview.difficulty')}</dt>
                            <dd>{preview.difficulty ?? t('settings.hybrid.preview.none')}</dd>
                            <dt>{t('settings.hybrid.preview.stakes')}</dt>
                            <dd>{preview.stakes ? t(`settings.hybrid.stakes.${preview.stakes}`) : t('settings.hybrid.preview.none')}</dd>
                            <dt>{t('settings.hybrid.preview.reason')}</dt>
                            <dd><bdi>{t('msg.route', { reason: preview.reason })}</bdi></dd>
                            <dt>{t('settings.hybrid.preview.time')}</dt>
                            <dd><bdi dir="ltr">{t('settings.hybrid.preview.seconds', { s: preview.seconds.toFixed(1) })}</bdi></dd>
                        </dl>
                    </div>
                )}
            </form>

            <section className="settings-field" aria-label={t('settings.hybrid.stats')}>
                <span className="settings-label">{t('settings.hybrid.stats')}</span>
                {statsError && (
                    <div className="settings-row">
                        <p className="settings-error" role="alert">{statsError}</p>
                        <button type="button" className="btn-secondary" onClick={() => void loadStats()}>{t('settings.hybrid.retry')}</button>
                    </div>
                )}
                {!stats && !statsError && <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.loading')}</p>}
                {stats?.scope_label && <p className="settings-hint"><bdi>{stats.scope_label}</bdi></p>}
                {stats && stats.turns === 0 && <p className="settings-hint">{t('settings.hybrid.stats.none')}</p>}
                {stats && stats.turns > 0 && (
                    <p className="settings-hint hybrid-stats">
                        <span>
                            {stats.since
                                ? t('settings.hybrid.stats.turnsSince', {
                                    since: new Date(stats.since).toLocaleDateString(t.locale),
                                    turns: stats.turns, cheap: stats.cheap_turns, strong: stats.strong_turns,
                                })
                                : t('settings.hybrid.stats.turns', { turns: stats.turns, cheap: stats.cheap_turns, strong: stats.strong_turns })}
                        </span>
                        {stats.failed_closed > 0 && <span> {t('settings.hybrid.stats.failed', { n: stats.failed_closed })}</span>}
                        <span>
                            {' '}
                            {stats.cost == null || stats.cost_if_all_strong == null
                                ? t('settings.hybrid.stats.costUnknown')
                                : stats.saving_pct != null
                                    ? t('settings.hybrid.stats.cost', {
                                        cost: formatCost(stats.cost), allStrong: formatCost(stats.cost_if_all_strong), pct: Math.round(stats.saving_pct),
                                    })
                                    : t('settings.hybrid.stats.costOnly', { cost: formatCost(stats.cost), allStrong: formatCost(stats.cost_if_all_strong) })}
                        </span>
                        {stats.unpriced_turns > 0 && <span> {t('settings.hybrid.stats.unpriced', { n: stats.unpriced_turns })}</span>}
                    </p>
                )}
            </section>
        </div>
    );
}
