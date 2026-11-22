// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useLayoutEffect, useRef, useState } from 'react';
import { prefersReducedMotion } from '../utils/motion';
import type { EyesGlyph, EyesMood, EyesPhase } from '../utils/gaiaEyes';
import { EYES_ANIMATIONS, EYES_CELL, EYES_SHEET_HEIGHT, EYES_SHEET_WIDTH, EYES_VARIANTS, type EyesAnimation, type EyesState } from './gaiaEyesSheet';
import './GaiaEyes.css';

/** A reaction to one message; `key` names that message, so a remount never replays it. */
export interface EyesReaction {
    mood: EyesMood;
    key: string;
    /** When it began (ms since epoch), if before these eyes first saw it. */
    at?: number;
}

let lastReactionKey: string | null = null;
/** When the latest reaction began, so eyes that mount partway through carry on from there. */
let reactionStarted = 0;
/** The variation the latest reaction plays, so every pair of eyes showing it agrees. */
let reactionVariant: { key: string; name: EyesAnimation } | null = null;
const lastPlayed = new Map<EyesState, EyesAnimation>();

/** One way to play `state`, never the one it played last, so the eyes rarely repeat themselves. */
function pickVariant(state: EyesState): EyesAnimation {
    const all: readonly EyesAnimation[] = EYES_VARIANTS[state];
    const fresh = all.filter((name) => name !== lastPlayed.get(state));
    const options = fresh.length > 0 ? fresh : all;
    const picked = options[Math.floor(Math.random() * options.length)];
    lastPlayed.set(state, picked);
    return picked;
}

const lengthMs = (name: EyesAnimation) => (EYES_ANIMATIONS[name].frames / EYES_ANIMATIONS[name].fps) * 1000;

/**
 * GAIA's two pixel eyes, in the icon slot of whatever step is live. They act out
 * the work in progress, after first playing a short reaction to how the user's
 * message sounded, and become a brain once GAIA has thought. Frames step from
 * script at the sheet's own rate; under reduced motion each state is one still.
 */
export function GaiaEyes({ phase, reaction = null, holdReaction = false, still = false }: {
    phase: EyesPhase | EyesGlyph;
    reaction?: EyesReaction | null;
    /** Show only the reaction, resting on its last frame, instead of going back to `phase`. */
    holdReaction?: boolean;
    /** Show the state's resting frame without playing it: a step that finished before these eyes mounted. */
    still?: boolean;
}) {
    const ref = useRef<HTMLSpanElement>(null);
    const shownState = useRef<EyesState | null>(null);
    const [reactingTo, setReactingTo] = useState<string | null>(null);
    const mood = reaction?.mood;
    const reactionKey = reaction?.key;
    const reactionAt = reaction?.at;

    useLayoutEffect(() => {
        if (!reactionKey) return;
        if (reactionKey !== lastReactionKey) {
            lastReactionKey = reactionKey;
            reactionStarted = reactionAt ?? Date.now();
            reactionVariant = null;
        } else if (!reactionVariant || Date.now() - reactionStarted >= lengthMs(reactionVariant.name)) {
            // Already played out: a remount never replays it.
            return;
        }
        // New, or the status line was rebuilt partway through: play on from where it has got to.
        setReactingTo(reactionKey);
    }, [reactionKey, reactionAt]);

    const reacting = !!mood && (holdReaction || reactingTo === reactionKey);
    const state: EyesState = reacting ? mood : phase;

    useLayoutEffect(() => {
        const el = ref.current;
        if (!el) return;
        let anim: (typeof EYES_ANIMATIONS)[EyesAnimation];
        const play = (name: EyesAnimation) => {
            anim = EYES_ANIMATIONS[name];
            el.dataset.variant = name;
        };
        const show = (frame: number) => {
            const position = `${-frame * EYES_CELL}px ${-anim.row * EYES_CELL}px`;
            el.style.setProperty('-webkit-mask-position', position);
            el.style.setProperty('mask-position', position);
        };
        const finish = () => { if (reacting && !holdReaction) setReactingTo(null); };

        // A change of expression blinks; the first one shown just appears.
        const changed = shownState.current !== null && shownState.current !== state;
        shownState.current = state;
        if (changed && !still) {
            el.classList.remove('is-swapping');
            void el.offsetWidth; // restart the blink when states change quickly
            el.classList.add('is-swapping');
        }

        if (still) {
            play(EYES_VARIANTS[state][0]);
            show(anim!.pose);
            return;
        }

        if (prefersReducedMotion()) {
            play(EYES_VARIANTS[state][0]);
            show(anim!.pose);
            if (!reacting || holdReaction) return;
            const held = window.setTimeout(finish, (anim!.frames / anim!.fps) * 1000);
            return () => window.clearTimeout(held);
        }

        let frame = 0;
        if (reacting && reactionKey) {
            play(reactionVariant?.key === reactionKey ? reactionVariant.name : pickVariant(state));
            reactionVariant = { key: reactionKey, name: el.dataset.variant as EyesAnimation };
            frame = Math.max(0, Math.min(anim!.frames - 1, Math.floor(((Date.now() - reactionStarted) * anim!.fps) / 1000)));
        } else {
            play(pickVariant(state));
        }
        show(frame);
        const timer = window.setInterval(() => {
            frame += 1;
            if (frame >= anim.frames) {
                if (!anim.loop) {
                    // A one-shot rests on its last frame.
                    window.clearInterval(timer);
                    finish();
                    return;
                }
                // Each time round, a different variation.
                play(pickVariant(state));
                frame = 0;
            }
            show(frame);
        }, 1000 / anim!.fps);
        return () => window.clearInterval(timer);
    }, [state, reacting, holdReaction, reactionKey, still]);

    const size = `${EYES_SHEET_WIDTH}px ${EYES_SHEET_HEIGHT}px`;
    return (
        <span
            ref={ref}
            className="gaia-eyes"
            data-eyes={state}
            aria-hidden="true"
            style={{ WebkitMaskSize: size, maskSize: size }}
            onAnimationEnd={(e) => { if (e.animationName === 'eyesSwap') e.currentTarget.classList.remove('is-swapping'); }}
        />
    );
}
