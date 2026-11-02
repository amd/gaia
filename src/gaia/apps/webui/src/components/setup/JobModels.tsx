// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useId, useState, type ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';
import { useT } from '../../i18n/useT';
import type { SetupCatalogEntry, SetupPlan } from '../../types';
import { formatSize } from './format';
import { statusText, tooBigText } from './ModelChecklist';
import {
    canCreateImages, catalogOf, isEmbedding, isNpu, isOptional, isVision, pickChat, pickVision, resolveVision,
    toggle, VISION_NONE, type Selection,
} from './selection';

function Support({ entry }: { entry: SetupCatalogEntry }) {
    const t = useT();
    if (entry.support === 'verified') return <span className="mc-chip is-verified">{t('setup.label.verified')}</span>;
    if (entry.support === 'untested') return <span className="mc-chip is-untested">{t('setup.label.untested')}</span>;
    return null;
}

interface RadioRowProps {
    name: string;
    entry: SetupCatalogEntry;
    checked: boolean;
    recommended: boolean;
    disabled?: boolean;
    onPick: () => void;
    children?: ReactNode;
}

function RadioRow({ name, entry: e, checked, recommended, disabled, onPick, children }: RadioRowProps) {
    const t = useT();
    return (
        <li className={`mc-row${checked ? ' is-ticked' : ''}`}>
            <label className="mc-row-main">
                <input type="radio" className="mc-radio" name={name} checked={checked} disabled={disabled} onChange={onPick} />
                <span className="mc-text">
                    <span className="mc-name-line">
                        <span className="mc-name" title={e.id}><bdi>{e.name}</bdi></span>
                        <span className="mc-chips">
                            {recommended && <span className="mc-pill">{t('setup.models.recommended')}</span>}
                            <Support entry={e} />
                            {isNpu(e) && <bdi className="mc-chip is-npu">{t('setup.models.npu')}</bdi>}
                        </span>
                    </span>
                    <span className="mc-status">{statusText(e, t)}</span>
                    {e.fits === false && <span className="mc-warn is-big" role="note"><AlertTriangle size={13} aria-hidden="true" /> {tooBigText(e, t)}</span>}
                    {children}
                </span>
            </label>
        </li>
    );
}

interface CheckRowProps {
    label: string;
    note: string;
    size: string;
    checked: boolean;
    disabled?: boolean;
    warning?: string | null;
    chips?: ReactNode;
    onChange: (on: boolean) => void;
}

function CheckRow({ label, note, size, checked, disabled, warning, chips, onChange }: CheckRowProps) {
    return (
        <li className={`mc-row${checked ? ' is-ticked' : ''}${disabled ? ' is-off' : ''}`}>
            <label className="mc-row-main">
                <input type="checkbox" className="mc-input" checked={checked} disabled={disabled} onChange={(ev) => onChange(ev.target.checked)} />
                <span className="mc-box" aria-hidden="true">
                    <svg viewBox="0 0 16 16" width="14" height="14"><path className="mc-tick" d="M3.5 8.5l3 3 6-7" /></svg>
                </span>
                <span className="mc-text">
                    <span className="mc-name-line">
                        <span className="mc-name"><bdi>{label}</bdi></span>
                        <span className="mc-chips">{chips}</span>
                    </span>
                    <span className="mc-status"><bdi dir="ltr">{size}</bdi></span>
                    <span className="mc-note">{note}</span>
                    {warning && <span className="mc-warn" role="note"><AlertTriangle size={13} aria-hidden="true" /> {warning}</span>}
                </span>
            </label>
        </li>
    );
}

interface Props {
    plan: SetupPlan;
    selection: Selection;
    onSelection: (selection: Selection) => void;
    disabled?: boolean;
}

/**
 * Models by the job they do. Each job depends on the chat model, so picking one
 * updates the others here rather than leaving the user to work the links out.
 */
export function JobModels({ plan, selection, onSelection, disabled }: Props) {
    const t = useT();
    const groupId = useId();
    const catalog = catalogOf(plan);
    const [showVision, setShowVision] = useState(false);
    const chat = catalog.find((e) => e.id === selection.defaultChat);
    const chatRows = catalog.filter((e) => e.chat_choice && !isEmbedding(e) && (e.gaia_model || e.downloaded));
    const verified = chatRows.filter((e) => e.support === 'verified');
    const untested = chatRows.filter((e) => e.support !== 'verified');
    const visionPick = resolveVision(catalog, selection);
    const builtIn = !!chat?.reads_images;
    const visionRows = catalog.filter((e) => isVision(e) && e.id !== chat?.id);
    const embedder = catalog.find(isEmbedding);
    const extra = (role: string) => catalog.find((e) => isOptional(e) && e.roles.includes(role as never));
    const sizeOf = (e: SetupCatalogEntry) => (e.downloaded ? t('setup.models.status.onPc') : e.size_gb != null ? formatSize(e.size_gb) : t('setup.contents.sizeOnDownload'));
    const set = (id: string, on: boolean) => onSelection(toggle(selection, catalog, id, on));
    const chooseChat = (id: string) => onSelection(pickChat(selection, catalog, id));
    const chooseVision = (id: string | null) => onSelection(pickVision(selection, catalog, id));
    const speech = extra('speech');
    const voice = extra('voice');
    const images = extra('image_creation');
    const imagesAllowed = canCreateImages(chat);
    const untestedPicked = chat?.support === 'untested';

    return (
        <div className="mc">
            <section className="mc-job" aria-labelledby={`${groupId}-chat`}>
                <h3 className="mc-job-title" id={`${groupId}-chat`}>{t('setup.job.chat')}</h3>
                <p className="setup-hint">{t('setup.job.verifiedNote')}</p>
                <ul className="mc-list" role="radiogroup" aria-labelledby={`${groupId}-chat`}>
                    {verified.map((e) => (
                        <RadioRow key={e.id} name={`${groupId}-chat-model`} entry={e} checked={e.id === selection.defaultChat} recommended={e.id === plan.recommended_model} disabled={disabled} onPick={() => chooseChat(e.id)} />
                    ))}
                </ul>
                {untested.length > 0 && (
                    <>
                        <p className="setup-hint">{t('setup.job.untestedNote')}</p>
                        <ul className="mc-list" role="radiogroup" aria-labelledby={`${groupId}-chat`}>
                            {untested.map((e) => (
                                <RadioRow key={e.id} name={`${groupId}-chat-model`} entry={e} checked={e.id === selection.defaultChat} recommended={false} disabled={disabled} onPick={() => chooseChat(e.id)} />
                            ))}
                        </ul>
                    </>
                )}
                {untestedPicked && <p className="mc-warn" role="note"><AlertTriangle size={13} aria-hidden="true" /> {t('setup.job.untestedPicked')}</p>}
            </section>

            <section className="mc-job" aria-labelledby={`${groupId}-vision`}>
                <h3 className="mc-job-title" id={`${groupId}-vision`}>{t('setup.job.vision')}</h3>
                {builtIn ? (
                    <div className="setup-card">
                        <p className="mc-job-line"><bdi>{t('setup.job.visionBuiltIn', { name: chat?.name ?? '' })}</bdi><span className="setup-ok">{t('setup.job.nothingMore')}</span></p>
                        <button type="button" className="link-btn" aria-expanded={showVision} onClick={() => setShowVision((v) => !v)}>
                            {showVision ? t('setup.job.hideVision') : t('setup.job.otherVision')}
                        </button>
                    </div>
                ) : (
                    <p className="setup-hint">{t('setup.job.visionNeeded', { name: chat?.name ?? '' })}</p>
                )}
                {(!builtIn || showVision) && (
                    <ul className="mc-list" role="radiogroup" aria-labelledby={`${groupId}-vision`}>
                        {visionRows.map((e) => (
                            <RadioRow key={e.id} name={`${groupId}-vision-model`} entry={e} checked={visionPick === e.id} recommended={!builtIn && e.gaia_model && e.support === 'verified'} disabled={disabled} onPick={() => chooseVision(e.id)} />
                        ))}
                        <li className={`mc-row${visionPick === null ? ' is-ticked' : ''}`}>
                            <label className="mc-row-main">
                                <input
                                    type="radio"
                                    className="mc-radio"
                                    name={`${groupId}-vision-model`}
                                    checked={builtIn ? visionPick === chat?.id : visionPick === null}
                                    disabled={disabled}
                                    onChange={() => chooseVision(builtIn ? null : VISION_NONE)}
                                />
                                <span className="mc-text">
                                    <span className="mc-name">{builtIn ? t('setup.job.useChatForVision') : t('setup.job.skipVision')}</span>
                                    {visionPick === null && !builtIn && <span className="mc-warn" role="note"><AlertTriangle size={13} aria-hidden="true" /> {t('setup.job.skipVisionNote')}</span>}
                                </span>
                            </label>
                        </li>
                    </ul>
                )}
            </section>

            {embedder && (
                <section className="mc-job" aria-labelledby={`${groupId}-docs`}>
                    <h3 className="mc-job-title" id={`${groupId}-docs`}>{t('setup.job.documents')}</h3>
                    <ul className="mc-list">
                        <CheckRow
                            label={embedder.name}
                            note={t('setup.models.line.documents')}
                            size={sizeOf(embedder)}
                            checked={selection.ticked.has(embedder.id)}
                            disabled={disabled}
                            warning={selection.ticked.has(embedder.id) ? null : t('setup.job.documentsOff')}
                            chips={<><span className="mc-pill">{t('setup.models.recommended')}</span><Support entry={embedder} /></>}
                            onChange={(on) => set(embedder.id, on)}
                        />
                    </ul>
                </section>
            )}

            <section className="mc-job" aria-labelledby={`${groupId}-extras`}>
                <h3 className="mc-job-title" id={`${groupId}-extras`}>{t('setup.job.extras')}</h3>
                <p className="setup-hint">{t('setup.job.extrasNote')}</p>
                <ul className="mc-list">
                    {speech && <CheckRow label={t('setup.job.speech')} note={t('setup.job.speechNote')} size={sizeOf(speech)} checked={selection.ticked.has(speech.id)} disabled={disabled} onChange={(on) => set(speech.id, on)} />}
                    {voice && <CheckRow label={t('setup.job.voice')} note={t('setup.job.voiceNote')} size={sizeOf(voice)} checked={selection.ticked.has(voice.id)} disabled={disabled} onChange={(on) => set(voice.id, on)} />}
                    {images && (
                        <CheckRow
                            label={t('setup.job.images')}
                            note={t('setup.job.imagesNote')}
                            size={sizeOf(images)}
                            checked={selection.ticked.has(images.id)}
                            disabled={disabled || !imagesAllowed}
                            warning={imagesAllowed ? null : t('setup.job.imagesBlocked', { name: chat?.name ?? '' })}
                            onChange={(on) => set(images.id, on)}
                        />
                    )}
                </ul>
            </section>

            <WhatYouGet plan={plan} selection={selection} />
        </div>
    );
}

/** The result of every choice above, in plain words. */
export function WhatYouGet({ plan, selection }: { plan: SetupPlan; selection: Selection }) {
    const t = useT();
    const catalog = catalogOf(plan);
    const name = (id: string | null | undefined) => catalog.find((e) => e.id === id)?.name ?? id ?? t('setup.get.off');
    const ticked = (role: string) => catalog.find((e) => isOptional(e) && e.roles.includes(role as never) && selection.ticked.has(e.id));
    const embedder = catalog.find((e) => isEmbedding(e) && selection.ticked.has(e.id));
    const chat = catalog.find((e) => e.id === selection.defaultChat);
    const vision = resolveVision(catalog, selection);
    const voice = [ticked('speech') && t('setup.job.speech'), ticked('voice') && t('setup.job.voice')].filter(Boolean).join(', ');
    return (
        <section className="setup-card" aria-label={t('setup.get.title')}>
            <h3 className="mc-job-title">{t('setup.get.title')}</h3>
            <dl className="setup-summary-list">
                <div><dt>{t('setup.job.chat')}</dt><dd><bdi>{name(selection.defaultChat)}</bdi></dd></div>
                <div><dt>{t('setup.job.vision')}</dt><dd><bdi>{vision ? name(vision) : t('setup.get.off')}</bdi></dd></div>
                <div><dt>{t('setup.job.documents')}</dt><dd><bdi>{embedder ? embedder.name : t('setup.get.off')}</bdi></dd></div>
                <div><dt>{t('setup.get.voice')}</dt><dd>{voice || t('setup.get.off')}</dd></div>
                <div><dt>{t('setup.job.images')}</dt><dd>{!canCreateImages(chat) ? t('setup.get.notAvailable') : ticked('image_creation') ? t('setup.get.on') : t('setup.get.off')}</dd></div>
            </dl>
        </section>
    );
}
