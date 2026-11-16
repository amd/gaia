// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useMemo, useState } from 'react';
import { Check, Copy, Lightbulb, Minus, Play, Plus, Printer, Ruler, Share2, X } from 'lucide-react';
import { UnsupportedCard } from '../UnsupportedCard';
import { isGuideWire, parseGuideDoc } from './guideDoc';
import type { GuideDoc, GuideWire } from './guideDoc';
import { formatGuideText, itemDisplay, servingsLabel, stepText, stepTitle } from './guideText';
import type { GuideViewState } from './guideText';
import { canShare, copyGuide, printGuide, shareGuide } from './guideActions';
import { StepText } from './StepText';
import { GuidedView } from './GuidedView';
import { useTimers } from './useTimers';
import { dismissGuideTip, useGuideTipDismissed } from './guideTip';
import type { UnitSystem } from './units';
import './guide.css';
import { useT } from '../../../i18n/useT';

const MAX_SERVINGS = 99;
const NOTICE_MS = 2500;

const storageKey = (doc: GuideDoc) => `gaia:guide:${doc.key}`;

function loadChecked(doc: GuideDoc): Set<number> {
    try {
        const raw = localStorage.getItem(storageKey(doc));
        const parsed: unknown = raw ? JSON.parse(raw) : [];
        return new Set(Array.isArray(parsed) ? parsed.filter((n): n is number => Number.isInteger(n)) : []);
    } catch (err) {
        console.warn(`[guide] could not read saved checks for "${doc.title}"; starting unchecked`, err);
        return new Set();
    }
}

function saveChecked(doc: GuideDoc, checked: ReadonlySet<number>): void {
    try {
        if (checked.size) localStorage.setItem(storageKey(doc), JSON.stringify([...checked]));
        else localStorage.removeItem(storageKey(doc));
    } catch (err) {
        console.warn(`[guide] could not save checks for "${doc.title}"; they hold until the chat closes`, err);
    }
}

export function GuideCard({ data }: { data: unknown }) {
    if (!isGuideWire(data)) return <UnsupportedCard variant="invalid" render="guide" data={data} />;
    return <Guide wire={data} />;
}

function Guide({ wire }: { wire: GuideWire }) {
    const t = useT();
    const doc = useMemo(() => parseGuideDoc(wire), [wire]);
    const [servings, setServings] = useState(doc.baseServings);
    const [system, setSystem] = useState<UnitSystem>(doc.nativeSystem);
    const [checked, setChecked] = useState(() => loadChecked(doc));
    const [guided, setGuided] = useState(false);
    const [notice, setNotice] = useState<string | null>(null);
    const timers = useTimers();
    const tipDismissed = useGuideTipDismissed();
    const state: GuideViewState = { servings, system, checked };
    const { features } = doc;
    const copiedNotice = t('cards.copied');

    useEffect(() => {
        if (!notice) return;
        const id = setTimeout(() => setNotice(null), NOTICE_MS);
        return () => clearTimeout(id);
    }, [notice]);

    const toggleItem = (i: number) => {
        const next = new Set(checked);
        if (next.has(i)) next.delete(i);
        else next.add(i);
        setChecked(next);
        saveChecked(doc, next);
    };

    const fail = (key: 'cards.copyFailed' | 'cards.shareFailed' | 'cards.printFailed') => (err: unknown) =>
        setNotice(t(key, { error: err instanceof Error ? err.message : String(err) }));

    const onCopy = () => {
        copyGuide(formatGuideText(doc, state, t)).then(() => setNotice(copiedNotice), fail('cards.copyFailed'));
    };
    const onShare = () => {
        shareGuide(doc.title, formatGuideText(doc, state, t)).catch(fail('cards.shareFailed'));
    };
    const onPrint = () => {
        try {
            printGuide(doc.title, formatGuideText(doc, state, t));
        } catch (err) {
            fail('cards.printFailed')(err);
        }
    };

    const other: UnitSystem = system === 'us' ? 'metric' : 'us';
    const systemLabel = (s: UnitSystem) => t.bare(s === 'us' ? 'cards.system.us' : 'cards.system.metric');
    const showUnits = features.units && doc.convertible;

    return (
        <article className="render-guide" aria-label={doc.title}>
            <header className="render-guide__header">
                <h3 className="render-guide__title">{doc.title}</h3>
                {doc.description && <p className="render-guide__description">{doc.description}</p>}
            </header>

            <div className="render-guide__toolbar">
                {features.scaling && servings !== null && (
                    <div className="render-guide__servings" role="group" aria-label={t('cards.servings')}>
                        <button
                            type="button"
                            className="render-guide__icon"
                            onClick={() => {
                                dismissGuideTip();
                                setServings((n) => Math.max(1, (n ?? 1) - 1));
                            }}
                            disabled={servings <= 1}
                            aria-label={t('cards.fewerServings')}
                        >
                            <Minus size={14} />
                        </button>
                        <output className="render-guide__servings-count" aria-live="polite">
                            {servingsLabel(t, servings)}
                        </output>
                        <button
                            type="button"
                            className="render-guide__icon"
                            onClick={() => {
                                dismissGuideTip();
                                setServings((n) => Math.min(MAX_SERVINGS, (n ?? 1) + 1));
                            }}
                            disabled={servings >= MAX_SERVINGS}
                            aria-label={t('cards.moreServings')}
                        >
                            <Plus size={14} />
                        </button>
                    </div>
                )}
                <div className="render-guide__tools">
                    {showUnits && (
                        <button
                            type="button"
                            className="render-guide__tool"
                            onClick={() => {
                                dismissGuideTip();
                                setSystem(other);
                            }}
                            aria-label={t('cards.unitsSwitch', { current: systemLabel(system), other: systemLabel(other) })}
                            title={t('cards.unitsTitle', { other: systemLabel(other) })}
                        >
                            <Ruler size={15} />
                            <span>{systemLabel(system)}</span>
                        </button>
                    )}
                    {canShare() && (
                        <button type="button" className="render-guide__icon" onClick={onShare} aria-label={t('cards.share')} title={t('cards.share')}>
                            <Share2 size={15} />
                        </button>
                    )}
                    <button type="button" className="render-guide__icon" onClick={onPrint} aria-label={t('cards.print')} title={t('cards.print')}>
                        <Printer size={15} />
                    </button>
                    <button type="button" className="render-guide__icon" onClick={onCopy} aria-label={t('cards.copy')} title={t('cards.copy')}>
                        {notice === copiedNotice ? <Check size={15} /> : <Copy size={15} />}
                    </button>
                </div>
                {features.startLabel && doc.steps.length > 0 && (
                    <button type="button" className="render-guide__start" onClick={() => {
                        dismissGuideTip();
                        setGuided(true);
                    }}>
                        <Play size={14} />
                        {t(features.startLabel)}
                    </button>
                )}
            </div>
            <p className="render-guide__notice" role="status">{notice}</p>

            {doc.items.length > 0 && (
                <section className="render-guide__section">
                    <h4 className="render-guide__label">{t(features.itemsLabel)}</h4>
                    <ul className="render-guide__items">
                        {doc.items.map((_, i) => {
                            const { qty, text } = itemDisplay(doc, i, state);
                            const content = (
                                <>
                                    {qty && <span className="render-guide__qty">{qty}</span>}
                                    {qty && ' '}
                                    {stepText(doc, text, system)}
                                </>
                            );
                            return (
                                <li key={i} className={`render-guide__item${checked.has(i) ? ' is-checked' : ''}`}>
                                    {features.checkItems ? (
                                        <label className="render-guide__check">
                                            <input type="checkbox" checked={checked.has(i)} onChange={() => toggleItem(i)} />
                                            <span>{content}</span>
                                        </label>
                                    ) : content}
                                </li>
                            );
                        })}
                    </ul>
                </section>
            )}

            {doc.steps.length > 0 && features.stepsLabel && (
                <section className="render-guide__section">
                    <h4 className="render-guide__label">{t(features.stepsLabel)}</h4>
                    <ol className={`render-guide__steps${features.timeline ? ' is-timeline' : ''}`}>
                        {doc.steps.map((step, i) => {
                            const title = stepTitle(doc, i, (n) => t('cards.dayN', { n }));
                            return (
                                <li key={i} className="render-guide__step">
                                    {features.timeline ? (
                                        <span className="render-guide__step-dot" aria-hidden="true" />
                                    ) : (
                                        <span className="render-guide__step-number" aria-hidden="true">{i + 1}</span>
                                    )}
                                    <div className="render-guide__step-body">
                                        {title && <span className="render-guide__step-title">{title}</span>}
                                        <span>
                                            <StepText
                                                text={stepText(doc, step.body, system)}
                                                stepIndex={i}
                                                timers={timers}
                                                withTimers={features.timers}
                                            />
                                        </span>
                                    </div>
                                </li>
                            );
                        })}
                    </ol>
                </section>
            )}

            {doc.notes && (
                <aside className="render-guide__notes">
                    <h4 className="render-guide__label">{t('cards.notes')}</h4>
                    <p>{stepText(doc, doc.notes, system)}</p>
                </aside>
            )}

            {features.tip && !tipDismissed && (
                <div className="render-guide__tip" role="note">
                    <Lightbulb size={15} aria-hidden="true" />
                    <span>{t(features.tip)}</span>
                    <button type="button" className="render-guide__icon" onClick={dismissGuideTip} aria-label={t('cards.dismissTip')}>
                        <X size={14} />
                    </button>
                </div>
            )}

            {guided && (
                <GuidedView doc={doc} state={state} timers={timers} onClose={() => setGuided(false)} />
            )}
        </article>
    );
}
