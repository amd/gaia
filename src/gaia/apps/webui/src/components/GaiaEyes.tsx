// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useLayoutEffect, useRef, useState } from 'react';
import { prefersReducedMotion } from '../utils/motion';
import type { EyesMood, EyesPhase } from '../utils/gaiaEyes';
import { EYES_ANIMATIONS, EYES_CELL, EYES_SHEET_HEIGHT, EYES_SHEET_WIDTH, EYES_VARIANTS, type EyesAnimation, type EyesState } from './gaiaEyesSheet';
import './GaiaEyes.css';

/** A reaction to one message; `key` names that message, so a remount never replays it. */
export interface EyesReaction {
    mood: EyesMood;
    key: string;
}

let lastReactionKey: string | null = null;
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

/**
 * GAIA's two pixel eyes, beside the live status line. They act out the work in
 * progress, after first playing a short reaction to how the user's message sounded.
 * Frames step from script at the sheet's own rate; under reduced motion each
 * state is one still.
 */
export function GaiaEyes({ phase, reaction = null }: { phase: EyesPhase; reaction?: EyesReaction | null }) {
    const ref = useRef<HTMLSpanElement>(null);
    const [reactingTo, setReactingTo] = useState<string | null>(null);
    const mood = reaction?.mood;
    const reactionKey = reaction?.key;

    useLayoutEffect(() => {
        if (!reactionKey || reactionKey === lastReactionKey) return;
        lastReactionKey = reactionKey;
        setReactingTo(reactionKey);
    }, [reactionKey]);

    const reacting = !!mood && reactingTo === reactionKey;
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
        const finish = () => { if (reacting) setReactingTo(null); };

        if (prefersReducedMotion()) {
            play(EYES_VARIANTS[state][0]);
            show(anim!.pose);
            if (!reacting) return;
            const held = window.setTimeout(finish, (anim!.frames / anim!.fps) * 1000);
            return () => window.clearTimeout(held);
        }

        let frame = 0;
        play(pickVariant(state));
        show(0);
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
    }, [state, reacting]);

    const size = `${EYES_SHEET_WIDTH}px ${EYES_SHEET_HEIGHT}px`;
    return (
        <span
            ref={ref}
            className="gaia-eyes"
            data-eyes={state}
            aria-hidden="true"
            style={{ WebkitMaskSize: size, maskSize: size }}
        />
    );
}
