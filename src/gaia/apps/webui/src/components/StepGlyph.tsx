// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useChatStore } from '../stores/chatStore';
import { EYES_GLYPHS, LONGEST_MOOD_MS, messageMood, type EyesGlyph, type EyesPhase } from '../utils/gaiaEyes';
import { useT } from '../i18n/useT';
import type { MessageKey } from '../i18n/registry';
import { GaiaEyes, type EyesReaction } from './GaiaEyes';

/** True when a step row in this turn plays GAIA's mood, so no separate line needs to. */
export const MoodHostContext = createContext(false);

/** How long a host step keeps its eyes after it ends, in case GAIA's mood is on its way. */
const MOOD_GRACE_MS = 400;
/** Matches `--duration-slow`, the time `.mood-line.is-leaving` takes to fold away. */
const LEAVE_MS = 320;

/** True until the clock reaches `deadline` (ms since epoch). */
function useUntil(deadline: number): boolean {
    const [, expire] = useState(0);
    useEffect(() => {
        const left = deadline - Date.now();
        if (left <= 0) return;
        // A little late, so the clock is past the deadline when this re-renders.
        const timer = setTimeout(() => expire(deadline), left + 50);
        return () => clearTimeout(timer);
    }, [deadline]);
    return Date.now() < deadline;
}

/** GAIA's own mood as a reaction for her eyes, for as long as it takes to play; null before and after. */
export function usePlayingMood(): EyesReaction | null {
    const gaiaMood = useChatStore((s) => s.gaiaMood);
    const playing = useUntil(gaiaMood ? gaiaMood.at + LONGEST_MOOD_MS : 0);
    return useMemo(
        () => (gaiaMood && playing ? { mood: gaiaMood.mood, key: `gaia|${gaiaMood.at}`, at: gaiaMood.at } : null),
        [gaiaMood, playing],
    );
}

/** What the eyes react with: GAIA's own mood while it plays, else how the user's message sounded. */
function useEyesReaction(): EyesReaction | null {
    const asked = useChatStore((s) => {
        for (let i = s.messages.length - 1; i >= 0; i--) {
            if (s.messages[i].role === 'user') return s.messages[i];
        }
        return undefined;
    });
    const gaiaMood = usePlayingMood();
    return useMemo(() => {
        if (gaiaMood) return gaiaMood;
        const mood = asked ? messageMood(asked.content) : null;
        return asked && mood ? { mood, key: `${asked.created_at}|${asked.content}` } : null;
    }, [asked, gaiaMood]);
}

/** The eyes at work on `phase`, reacting first. */
export function LiveEyes({ phase }: { phase: EyesPhase }) {
    return <GaiaEyes phase={phase} reaction={useEyesReaction()} />;
}

const isGlyph = (value: unknown): value is EyesGlyph =>
    typeof value === 'string' && (EYES_GLYPHS as readonly string[]).includes(value);

/** An icon GAIA's eyes became, named in words for a tooltip and screen readers. */
function Glyph({ glyph, still }: { glyph: EyesGlyph; still: boolean }) {
    const t = useT();
    // The brain is every thought's icon, so it says nothing of its own.
    if (glyph === 'brain') return <GaiaEyes phase="brain" still={still} />;
    const label = t(`eyes.glyph.${glyph}` as MessageKey);
    return (
        <span className="final-glyph" role="img" aria-label={label} title={label} data-glyph={glyph}>
            <GaiaEyes phase={glyph} still={still} />
        </span>
    );
}

/**
 * A step's icon slot: GAIA's eyes while the step runs, then the step's own icon.
 * A thought's icon is one GAIA's eyes turn into: the brain, or an icon for a
 * strong reaction. The slot never changes size, so the label beside it never
 * moves. A `host` step plays GAIA's mood for the reply before it settles.
 */
export function StepGlyph({ live, phase, settled, host = false }: {
    live: boolean;
    phase: EyesPhase;
    /** The finished step's icon, or the icon a thought's eyes become. */
    settled: EyesGlyph | ReactNode;
    host?: boolean;
}) {
    const mood = usePlayingMood();
    const hadEyes = useRef(false);
    const [endedAt, setEndedAt] = useState(0);
    const wasLive = useRef(live);
    useEffect(() => {
        if (wasLive.current && !live) setEndedAt(Date.now());
        wasLive.current = live;
    }, [live]);
    const inGrace = useUntil(host && endedAt ? endedAt + MOOD_GRACE_MS : 0);
    // An icon that changes while in view (the brain becoming a question mark) plays its change.
    const firstGlyph = useRef(isGlyph(settled) ? settled : null);
    const changedGlyph = isGlyph(settled) && firstGlyph.current !== null && settled !== firstGlyph.current;

    let content: ReactNode;
    let eyes = true;
    if (live) {
        content = <LiveEyes phase={phase} />;
    } else if (host && mood) {
        content = <GaiaEyes phase="think" reaction={mood} holdReaction />;
    } else if (inGrace) {
        content = <GaiaEyes phase="think" />;
    } else {
        eyes = false;
        content = isGlyph(settled) ? <Glyph glyph={settled} still={!hadEyes.current && !changedGlyph} /> : settled;
    }
    const settling = !eyes && hadEyes.current;
    if (eyes) hadEyes.current = true;

    return (
        // The eyes and a step's own icon are decoration; an icon GAIA's eyes became says something.
        <span className={`step-glyph${eyes ? ' is-live' : ''}${settling ? ' is-settling' : ''}`} aria-hidden={!eyes && isGlyph(settled) && settled !== 'brain' ? undefined : true}>
            {content}
        </span>
    );
}

/**
 * GAIA's mood on its own line, where no step plays it: a reply with no steps,
 * or one whose last step is out of view. When the mood ends, the eyes become
 * `glyph` and stay; with no icon to keep (the brain is the steps' own), the
 * line folds away.
 */
export function MoodEyes({ glyph = 'brain', playMood = true }: {
    glyph?: EyesGlyph;
    /** This reply is the one GAIA's current mood belongs to. */
    playMood?: boolean;
}) {
    const hosted = useContext(MoodHostContext);
    const current = usePlayingMood();
    const playing = playMood ? current : null;
    const [shown, setShown] = useState<EyesReaction | null>(null);
    const keeps = glyph !== 'brain';
    useEffect(() => {
        if (playing) {
            setShown(playing);
            return;
        }
        const timer = setTimeout(() => setShown(null), LEAVE_MS);
        return () => clearTimeout(timer);
    }, [playing]);
    const reaction = playing ?? shown;
    if (hosted || (!reaction && !keeps)) return null;
    return (
        <div className={`mood-line${playing || keeps ? '' : ' is-leaving'}`}>
            <div>
                <span className="thinking-indicator">
                    <StepGlyph live={false} phase="think" settled={glyph} host={!!playing} />
                </span>
            </div>
        </div>
    );
}
