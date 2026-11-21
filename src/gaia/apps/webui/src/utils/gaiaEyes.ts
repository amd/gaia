// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** What GAIA's eyes show: the work in progress, and a short reaction to how a message sounds. */

import { EYES_ANIMATIONS, EYES_VARIANTS, type EyesState } from '../components/gaiaEyesSheet';

export type EyesPhase = 'think' | 'read' | 'tool' | 'wait';
export type EyesMood = Exclude<EyesState, EyesPhase>;

const PHASES: readonly string[] = ['think', 'read', 'tool', 'wait'] satisfies EyesPhase[];

/** Every mood the eyes can draw. GAIA names one of these for her own reply (`MOODS` in agents/base/mood.py). */
export const EYES_MOODS = (Object.keys(EYES_VARIANTS) as EyesState[]).filter(
    (state): state is EyesMood => !PHASES.includes(state),
);

/** `word` as a mood the eyes can draw, or null. */
export function asEyesMood(word: string | null | undefined): EyesMood | null {
    return EYES_MOODS.find((mood) => mood === word) ?? null;
}

/** How long the longest mood takes to play. */
export const LONGEST_MOOD_MS = Math.max(
    ...EYES_MOODS.flatMap((mood) => EYES_VARIANTS[mood].map((name) => (EYES_ANIMATIONS[name].frames / EYES_ANIMATIONS[name].fps) * 1000)),
);

/** Live status lines that mean GAIA is thinking; any other line is a tool or the model server at work. */
const THINKING = /^(Thinking\.\.\.|Reasoning\b|Waiting for GAIA to start this answer…)/;

/** The eyes' working state for the live status line the backend sent (in English, before translation). */
export function eyesPhase(label: string | null | undefined, awaitingUser: boolean): EyesPhase {
    if (awaitingUser) return 'wait';
    if (!label || THINKING.test(label)) return 'think';
    if (label.startsWith('Reading ')) return 'read';
    return 'tool';
}

/**
 * Plain word matching on the user's message, first rule that fits. English only:
 * a message in another language, or one with no clear feeling, gets no reaction.
 * Swearing at GAIA is anger; swearing at anything else is the user's own
 * frustration, which gets concern.
 */
const RULES: Array<[EyesMood, RegExp]> = [
    ['angry', /\b(fuck (you|u|off|gaia)|screw you|shut up|stfu|i hate you|you suck|you('re| are)? (so |such an? |an? |fucking )*(stupid|dumb|useless|idiot|moron|worthless|trash|garbage|terrible|awful|pathetic)|(stupid|dumb|useless|worthless) (bot|ai|robot|assistant|machine|thing)|piece of (shit|crap|junk))\b/],
    ['embarrassed', /\b(that('s| is| was) (wrong|incorrect|not right|not correct|a mistake)|you('re| are) wrong|you made a mistake|you got (it|that) wrong|not what i asked|you messed up)\b/],
    ['sad', /\b(i('m| am)? (so |really |very |feeling |just |a bit |kind of )*(sad|depressed|lonely|heartbroken|down|miserable|devastated|grieving|crying|upset)|i feel (so |really |very )?(sad|alone|lonely|hopeless|empty|awful|terrible|down)|passed away|has died|just died|i lost my|i miss (him|her|them|you)|broke up|(bad|worst|awful|terrible|rough) day)\b/],
    ['worried', /\b(i('m| am) (so |really |very |getting )*(scared|afraid|worried|anxious|nervous|stressed|terrified|panicking|overwhelmed|angry|furious|pissed|mad|frustrated)|freaking out|panic attack|so (frustrating|annoying|stressful)|fed up|wtf|fuck(ing|ed)?|shit(ty)?|damn(it)?|ugh+)\b/],
    ['sleepy', /\b(good ?night|i('m| am) (so |really |very )?(tired|sleepy|exhausted)|going to (bed|sleep)|time for bed)\b/],
    ['love', /\b(i love (you|this|it|that)|love (you|u)|you('re| are) (the best|amazing|awesome|wonderful|a lifesaver|incredible)|lifesaver)\b|❤|😍/],
    ['laugh', /\b(lol|lmao|rofl|ha(ha)+|he(he)+|that('s| is| was) (so )?(funny|hilarious))\b|😂|🤣/],
    ['playful', /\b(just kidding|jk|only joking|tell me a joke)\b|;\)|😉|😜/],
    ['excited', /\b(i did it|we did it|let'?s go+|so excited|can('t|not) wait|i passed|i won|we won)\b/],
    ['proud', /\b(i('m| am) (so |really )?proud|proud of (you|myself|this)|i (built|made|finished|shipped|fixed) (it|this))\b/],
    ['happy', /\b(thank(s| you)|thx|great job|good job|well done|nice work|good bot|perfect|awesome|yay+|it works|finally works|good news|i got the job|congrat)/],
    ['skeptical', /\b(are you sure|i doubt (it|that)|that (doesn't|does not) sound right|prove it|really\?)/],
    ['surprised', /\b(wo+w|whoa+|omg|oh my god|no way|unbelievable|holy (cow|crap|moly)|seriously\?)|!{3,}/],
    ['curious', /\b(i wonder|i('m| am) curious|guess what|did you know|fun fact)\b/],
    ['determined', /\b(let('s| us) do (it|this)|we can do (it|this)|i won't give up|not giving up|one more (try|time)|let('s| us) try again)\b/],
    ['confused', /\b(i('m| am) (so |really )?(confused|lost)|i (don't|dont|do not) (understand|get it|get this)|makes? no sense|what do you mean|huh)\b|\?{2,}/],
];

/** How a message sounds, or null when nothing in it calls for a reaction. */
export function messageMood(text: string): EyesMood | null {
    const lower = text.toLowerCase();
    for (const [mood, pattern] of RULES) {
        if (pattern.test(lower)) return mood;
    }
    return null;
}

/** Every mood the word matching can reach, in rule order. */
export const MATCHED_MOODS: readonly EyesMood[] = RULES.map(([mood]) => mood);
