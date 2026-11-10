// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Check, Minus } from 'lucide-react';
import { useT, type Translate } from '../i18n/useT';
import { useChatStore, type RecordingSection, type RecordingStage } from '../stores/chatStore';
import type { MessageKey } from '../i18n/registry';
import './RecordingProgress.css';

// Same order as RECORDING_STAGES in audio_tools.py: events carry the position.
const STAGES = ['download', 'decode', 'transcribe', 'voices', 'prepare', 'analyze'] as const;
const DOWNLOAD = 0;
const ANALYZE = 5;

export const STAGE_RUNNING = 0;
export const STAGE_DONE = 1;
export const STAGE_SKIPPED = 2;

/** "About 12 min left", "Under a minute left"; empty before there is an estimate. */
export function timeLeft(seconds: number | undefined, t: Translate): string {
    if (seconds == null) return '';
    if (seconds < 60) return t('recording.leftUnderMinute');
    return t('recording.leftMinutes', { minutes: Math.ceil(seconds / 60) });
}

/** The stages to draw: optional ones only once the run reaches them. */
function shownStages(reached: Record<number, RecordingStage>): number[] {
    return STAGES.map((_, index) => index).filter(
        (index) => (index !== DOWNLOAD && index !== ANALYZE) || index in reached,
    );
}

function StageMark({ state }: { state: number | undefined }) {
    if (state === STAGE_DONE) return <Check size={14} aria-hidden="true" />;
    if (state === STAGE_SKIPPED) return <Minus size={14} aria-hidden="true" />;
    return <span className="recording-progress-dot" aria-hidden="true" />;
}

function StageRow({ index, stage }: { index: number; stage: RecordingStage | undefined }) {
    const t = useT();
    const label = t(`recording.stage.${STAGES[index]}` as MessageKey);
    const running = stage?.state === STAGE_RUNNING;
    const percent = stage?.percent;
    const left = running ? timeLeft(stage?.etaSeconds, t) : '';
    const status = stage?.state === STAGE_SKIPPED ? t('recording.skipped') : left;
    return (
        <li className={`recording-progress-row${running ? ' is-running' : ''}${stage ? '' : ' is-waiting'}${stage?.state === STAGE_DONE ? ' is-done' : ''}`}>
            <span className="recording-progress-mark"><StageMark state={stage?.state} /></span>
            <span className="recording-progress-label">{label}</span>
            {running && percent != null && (
                <bdi className="recording-progress-percent" dir="ltr">{percent}%</bdi>
            )}
            {status && <span className="recording-progress-left">{status}</span>}
            {running && (
                <div
                    className={`recording-progress-track${percent == null ? ' is-indeterminate' : ''}`}
                    role="progressbar"
                    aria-label={label}
                    aria-valuemin={percent == null ? undefined : 0}
                    aria-valuemax={percent == null ? undefined : 100}
                    aria-valuenow={percent ?? undefined}
                >
                    <div className="recording-progress-fill" style={percent == null ? undefined : { inlineSize: `${percent}%` }} />
                </div>
            )}
        </li>
    );
}

// Same as BOUNDARY_GAP_SECONDS in transcript_analysis.py: nearer than this, one item.
const SAME_ITEM_SECONDS = 120;

/**
 * Sections in recording order, each with only the items an earlier section has
 * not already shown: neighbouring sections overlap, so an item near a seam is
 * found twice until the finished report merges it.
 */
export function foundSoFar(sections: Record<number, RecordingSection>): RecordingSection[] {
    const shown: { title: string; seconds: number }[] = [];
    return Object.values(sections)
        .sort((a, b) => a.section - b.section)
        .map((section) => {
            const items = section.items.filter((item) => {
                const title = item.title.trim().toLowerCase();
                const repeat = shown.some((s) => s.title === title && Math.abs(s.seconds - item.seconds) < SAME_ITEM_SECONDS);
                if (!repeat) shown.push({ title, seconds: item.seconds });
                return !repeat;
            });
            return { ...section, items };
        });
}

/** What the sections read so far contain, so there is something to read while the rest are read. */
function FoundSoFar({ sections }: { sections: Record<number, RecordingSection> }) {
    const t = useT();
    const shown = foundSoFar(sections);
    const total = shown[0]?.sections ?? 0;
    return (
        <section className="recording-found" aria-label={t('recording.found.title')}>
            <h4 className="recording-found-title">
                {t('recording.found.title')}
                <span className="recording-found-count">{t('recording.found.count', { done: shown.length, total })}</span>
            </h4>
            {shown.map((section) => (
                <div key={section.section} className="recording-found-section">
                    <p className="recording-found-span">
                        <bdi dir="ltr" className="recording-found-time">{section.start}–{section.end}</bdi>
                        {section.summary && <span dir="auto">{section.summary}</span>}
                    </p>
                    {section.items.length > 0 && (
                        <ul className="recording-found-items">
                            {section.items.map((item) => (
                                <li key={`${item.seconds}-${item.title}`}>
                                    <bdi dir="ltr" className="recording-found-time">{item.start}</bdi>
                                    <span dir="auto">{item.title}</span>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            ))}
        </section>
    );
}

/** Every stage of the recording being worked on, with a bar on the one running. */
export function RecordingProgress() {
    const t = useT();
    const stages = useChatStore((s) => s.recordingStages);
    const sections = useChatStore((s) => s.recordingSections);
    if (!stages) return null;
    return (
        <section className="recording-progress" aria-label={t('recording.title')}>
            <ol className="recording-progress-list">
                {shownStages(stages).map((index) => (
                    <StageRow key={index} index={index} stage={stages[index]} />
                ))}
            </ol>
            {sections && <FoundSoFar sections={sections} />}
        </section>
    );
}
