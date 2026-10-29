// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useMemo, useState } from 'react';
import { Check, Copy, Minus, Play, Plus, Printer, Ruler, Share2 } from 'lucide-react';
import { UnsupportedCard } from '../UnsupportedCard';
import { isGuideWire, parseGuideDoc } from './guideDoc';
import type { GuideDoc, GuideWire } from './guideDoc';
import { formatGuideText, itemDisplay, servingsLabel, stepText } from './guideText';
import type { GuideViewState } from './guideText';
import { canShare, copyGuide, printGuide, shareGuide } from './guideActions';
import { StepText } from './StepText';
import { GuidedView } from './GuidedView';
import { useTimers } from './useTimers';
import type { UnitSystem } from './units';
import './guide.css';

const MAX_SERVINGS = 99;
const NOTICE_MS = 2500;

const storageKey = (doc: GuideDoc) => `gaia:guide:${doc.key}`;

function loadChecked(doc: GuideDoc): Set<number> {
    try {
        const raw = localStorage.getItem(storageKey(doc));
        const parsed: unknown = raw ? JSON.parse(raw) : [];
        return new Set(Array.isArray(parsed) ? parsed.filter((n): n is number => Number.isInteger(n)) : []);
    } catch {
        return new Set();
    }
}

function saveChecked(doc: GuideDoc, checked: ReadonlySet<number>): void {
    try {
        if (checked.size) localStorage.setItem(storageKey(doc), JSON.stringify([...checked]));
        else localStorage.removeItem(storageKey(doc));
    } catch {
        // Storage can be full or blocked; the checks still hold for this view.
    }
}

const SYSTEM_LABEL: Record<UnitSystem, string> = { us: 'US', metric: 'Metric' };

export function GuideCard({ data }: { data: unknown }) {
    if (!isGuideWire(data)) return <UnsupportedCard variant="invalid" render="guide" data={data} />;
    return <Guide wire={data} />;
}

function Guide({ wire }: { wire: GuideWire }) {
    const doc = useMemo(() => parseGuideDoc(wire), [wire]);
    const [servings, setServings] = useState(doc.baseServings);
    const [system, setSystem] = useState<UnitSystem>(doc.nativeSystem);
    const [checked, setChecked] = useState(() => loadChecked(doc));
    const [guided, setGuided] = useState(false);
    const [notice, setNotice] = useState<string | null>(null);
    const timers = useTimers();
    const state: GuideViewState = { servings, system, checked };
    const { features } = doc;

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

    const fail = (verb: string) => (err: unknown) =>
        setNotice(`Couldn't ${verb}: ${err instanceof Error ? err.message : String(err)}`);

    const onCopy = () => {
        copyGuide(formatGuideText(doc, state)).then(() => setNotice('Copied'), fail('copy'));
    };
    const onShare = () => {
        shareGuide(doc.title, formatGuideText(doc, state)).catch(fail('share'));
    };
    const onPrint = () => {
        try {
            printGuide(doc.title, formatGuideText(doc, state));
        } catch (err) {
            fail('print')(err);
        }
    };

    const other: UnitSystem = system === 'us' ? 'metric' : 'us';
    const showUnits = features.units && doc.convertible;

    return (
        <article className="render-guide" aria-label={doc.title}>
            <header className="render-guide__header">
                <h3 className="render-guide__title">{doc.title}</h3>
                {doc.description && <p className="render-guide__description">{doc.description}</p>}
            </header>

            <div className="render-guide__toolbar">
                {features.scaling && servings !== null && (
                    <div className="render-guide__servings" role="group" aria-label="Servings">
                        <button
                            type="button"
                            className="render-guide__icon"
                            onClick={() => setServings(Math.max(1, servings - 1))}
                            disabled={servings <= 1}
                            aria-label="Fewer servings"
                        >
                            <Minus size={14} />
                        </button>
                        <output className="render-guide__servings-count" aria-live="polite">
                            {servingsLabel(servings)}
                        </output>
                        <button
                            type="button"
                            className="render-guide__icon"
                            onClick={() => setServings(Math.min(MAX_SERVINGS, servings + 1))}
                            disabled={servings >= MAX_SERVINGS}
                            aria-label="More servings"
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
                            onClick={() => setSystem(other)}
                            aria-label={`Units: ${SYSTEM_LABEL[system]}. Switch to ${SYSTEM_LABEL[other]}`}
                            title={`Switch to ${SYSTEM_LABEL[other]} units`}
                        >
                            <Ruler size={15} />
                            <span>{SYSTEM_LABEL[system]}</span>
                        </button>
                    )}
                    {canShare() && (
                        <button type="button" className="render-guide__icon" onClick={onShare} aria-label="Share" title="Share">
                            <Share2 size={15} />
                        </button>
                    )}
                    <button type="button" className="render-guide__icon" onClick={onPrint} aria-label="Print" title="Print">
                        <Printer size={15} />
                    </button>
                    <button type="button" className="render-guide__icon" onClick={onCopy} aria-label="Copy" title="Copy">
                        {notice === 'Copied' ? <Check size={15} /> : <Copy size={15} />}
                    </button>
                </div>
                {features.startLabel && doc.steps.length > 0 && (
                    <button type="button" className="render-guide__start" onClick={() => setGuided(true)}>
                        <Play size={14} />
                        {features.startLabel}
                    </button>
                )}
            </div>
            <p className="render-guide__notice" role="status">{notice}</p>

            {doc.items.length > 0 && (
                <section className="render-guide__section">
                    <h4 className="render-guide__label">{features.itemsLabel}</h4>
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
                    <h4 className="render-guide__label">{features.stepsLabel}</h4>
                    <ol className="render-guide__steps">
                        {doc.steps.map((step, i) => (
                            <li key={i} className="render-guide__step">
                                <span className="render-guide__step-number" aria-hidden="true">{i + 1}</span>
                                <div className="render-guide__step-body">
                                    {step.title && <span className="render-guide__step-title">{step.title}</span>}
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
                        ))}
                    </ol>
                </section>
            )}

            {doc.notes && (
                <aside className="render-guide__notes">
                    <h4 className="render-guide__label">Notes</h4>
                    <p>{stepText(doc, doc.notes, system)}</p>
                </aside>
            )}

            {guided && (
                <GuidedView doc={doc} state={state} timers={timers} onClose={() => setGuided(false)} />
            )}
        </article>
    );
}
