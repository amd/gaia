// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useChatStore } from '../stores/chatStore';
import { LONGEST_MOOD_MS, messageMood, type EyesPhase } from '../utils/gaiaEyes';
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

/**
 * A step's icon slot: GAIA's eyes while the step runs, then the step's own icon.
 * A thought's icon is a brain the eyes turn into. The slot never changes size, so
 * the label beside it never moves. A `host` step plays GAIA's mood for the reply
 * before it settles.
 */
export function StepGlyph({ live, phase, settled, host = false }: {
    live: boolean;
    phase: EyesPhase;
    /** The finished step's icon, or `'brain'` for a thought. */
    settled: 'brain' | ReactNode;
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
        content = settled === 'brain' ? <GaiaEyes phase="brain" still={!hadEyes.current} /> : settled;
    }
    const settling = !eyes && hadEyes.current;
    if (eyes) hadEyes.current = true;

    return (
        <span className={`step-glyph${eyes ? ' is-live' : ''}${settling ? ' is-settling' : ''}`} aria-hidden="true">
            {content}
        </span>
    );
}

/**
 * GAIA's mood on its own line, where no step plays it: a reply with no steps,
 * or one whose last step is out of view. The line folds away when the mood ends.
 */
export function MoodEyes() {
    const hosted = useContext(MoodHostContext);
    const playing = usePlayingMood();
    const [shown, setShown] = useState<EyesReaction | null>(null);
    useEffect(() => {
        if (playing) {
            setShown(playing);
            return;
        }
        const timer = setTimeout(() => setShown(null), LEAVE_MS);
        return () => clearTimeout(timer);
    }, [playing]);
    const reaction = playing ?? shown;
    if (hosted || !reaction) return null;
    return (
        <div className={`mood-line${playing ? '' : ' is-leaving'}`}>
            <div>
                <span className="thinking-indicator">
                    <span className="step-glyph is-live" aria-hidden="true">
                        <GaiaEyes phase="think" reaction={reaction} holdReaction />
                    </span>
                </span>
            </div>
        </div>
    );
}
