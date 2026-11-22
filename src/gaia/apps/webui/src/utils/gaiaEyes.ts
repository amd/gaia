// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** What GAIA's eyes show: the work in progress, and a short reaction to how a message sounds. */

import { EYES_ANIMATIONS, EYES_VARIANTS, type EyesState } from '../components/gaiaEyesSheet';

export type EyesPhase = 'think' | 'read' | 'tool' | 'wait';
/** What the eyes become once GAIA has answered: the brain, or an icon for a strong reaction. */
export const EYES_GLYPHS = [
    'brain', 'heart', 'question', 'exclaim', 'star', 'sun', 'note', 'tear', 'bolt', 'cloud', 'sprout', 'moon', 'check',
] as const satisfies readonly EyesState[];
export type EyesGlyph = (typeof EYES_GLYPHS)[number];
export type EyesMood = Exclude<EyesState, EyesPhase | EyesGlyph>;

const NOT_MOODS: readonly string[] = ['think', 'read', 'tool', 'wait', ...EYES_GLYPHS];

/**
 * The icon each strong reaction settles on. Reactions not listed settle on the
 * brain, unless GAIA ended by asking the user something.
 */
const GLYPH_FOR: Partial<Record<EyesMood, EyesGlyph>> = {
    love: 'heart', grateful: 'heart',
    confused: 'question',
    surprised: 'exclaim', scared: 'exclaim',
    impressed: 'star', proud: 'star', excited: 'star',
    happy: 'sun', greet: 'sun', laugh: 'sun',
    playful: 'note', wink: 'note',
    sad: 'tear', disappointed: 'tear', apologetic: 'tear',
    angry: 'bolt',
    worried: 'cloud', disgusted: 'cloud', embarrassed: 'cloud',
    hopeful: 'sprout',
    calm: 'moon', sleepy: 'moon', goodbye: 'moon',
    relieved: 'check',
};

const ENDS_ASKING = /[?؟]["'”’)\]*_\s]*$/;

/**
 * True when a reply leaves the user something to answer: its last paragraph
 * asks a question, the one before ends on one (an aside may follow), or it
 * lists questions, one per line. Code, links and headings ask nothing.
 */
export function asksUser(answer: string): boolean {
    const text = answer
        .replace(/```[\s\S]*?(```|$)/g, '')
        .replace(/`[^`\n]*`/g, '')
        .replace(/\bhttps?:\/\/\S+/g, '')
        .replace(/^\s{0,3}#{1,6}\s.*$/gm, '')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
    const paragraphs = text.split(/\n\s*\n/);
    if (/[?؟]/.test(paragraphs[paragraphs.length - 1] ?? '')) return true;
    if (paragraphs.length > 1 && ENDS_ASKING.test(paragraphs[paragraphs.length - 2].trim())) return true;
    return text.split('\n').filter((line) => ENDS_ASKING.test(line.trim())).length >= 2;
}

/** Reactions too strong for a closing question to replace: what she felt is the message. */
const INTENSE: ReadonlySet<EyesMood> = new Set<EyesMood>([
    'love', 'grateful', 'sad', 'disappointed', 'apologetic', 'angry', 'scared', 'surprised',
    'excited', 'impressed', 'proud', 'worried', 'disgusted',
]);

/**
 * What GAIA's eyes settle on for a finished reply. An intense reaction keeps its
 * own icon; otherwise a reply that asks the user something shows a question
 * mark, so they see it is their turn; then a milder reaction's icon; else the
 * brain.
 */
export function finalGlyph(mood: string | null | undefined, answer: string): EyesGlyph {
    const known = asEyesMood(mood);
    const icon = known ? GLYPH_FOR[known] : undefined;
    if (icon && INTENSE.has(known!)) return icon;
    if (asksUser(answer)) return 'question';
    return icon ?? 'brain';
}

/** Every mood the eyes can draw. GAIA names one of these for her own reply (`MOODS` in agents/base/mood.py). */
export const EYES_MOODS = (Object.keys(EYES_VARIANTS) as EyesState[]).filter(
    (state): state is EyesMood => !NOT_MOODS.includes(state),
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
 * GAIA's first reaction to a message, from her side: how she responds to what was
 * said, never a mirror of the user's own state. Someone confused gets her
 * determined to explain, someone tired a calm look, thanks a small bow. Plain word
 * matching, first rule that fits. English only: a message in another language,
 * or one with nothing to react to, gets no reaction.
 */
const RULES: Array<[EyesMood, RegExp]> = [
    // Swearing at GAIA herself.
    ['angry', /\b(fuck (you|u|off|gaia)|screw you|shut up|stfu|i hate you|you suck|you('re| are)? (so |such an? |an? |fucking )*(stupid|dumb|useless|idiot|moron|worthless|trash|garbage|terrible|awful|pathetic)|(stupid|dumb|useless|worthless) (bot|ai|robot|assistant|machine|thing)|piece of (shit|crap|junk))\b/],
    // Afraid for someone in danger.
    ['scared', /\b(i('m| am) in danger|someone('s| is) (following me|breaking in|in my house)|call 911|emergency|chest pain|can('t|not) breathe|i('m| am) bleeding|help me,? please)\b/],
    ['sad', /\b(i('m| am)? (so |really |very |feeling |just |a bit |kind of )*(sad|depressed|lonely|heartbroken|down|miserable|devastated|grieving|crying|upset)|i feel (so |really |very )?(sad|alone|lonely|hopeless|empty|awful|terrible|down)|passed away|has died|just died|(my|our) [a-z' ]{1,30}(died|is dying|is dead)|i lost my|i miss (him|her|them|you)|broke up|(bad|worst|awful|terrible|rough) day)\b/],
    // Something she did went wrong for the user.
    ['apologetic', /\b(you (broke|deleted|ruined|lost|messed up) (it|my|the|all|everything)|why did you (do|delete|break|change) (that|it|this))\b/],
    ['embarrassed', /\b(that('s| is| was) (wrong|incorrect|not right|not correct|a mistake)|that (doesn't|does not) sound right|you('re| are) wrong|you made a mistake|you got (it|that) wrong|not what i asked)\b/],
    // A firm no to something harmful.
    ['shake', /(\brm -rf\b|\bformat (my|the) (drive|disk)\b|\bdelete (everything|all my)\b|\b(share|send|give) (them |him |her )?my password\b)/],
    ['disappointed', /\b((didn't|did not|doesn't|does not) work|still (broken|not working|failing)|failed again|it failed|i didn't get (it|the job|in)|got rejected|i lost (the|our) (game|match))\b/],
    ['worried', /\b(i('m| am) (so |really |very |getting )*(scared|afraid|worried|anxious|nervous|stressed|terrified|panicking|overwhelmed|angry|furious|pissed|mad|frustrated)|freaking out|panic attack|so (frustrating|annoying|stressful)|fed up|wtf|fuck(ing|ed)?|shit(ty)?|damn(it)?|ugh+)\b/],
    ['disgusted', /\b(gross|disgusting|ew+|yuck|nasty|vomit(ed|ing)?|threw up|throwing up|rotten|cockroach(es)?|maggots?)\b/],
    ['goodbye', /\b(good ?night|good ?bye|bye|see (you|ya)|talk (to you )?later|gotta go|ttyl|cya|i('m| am) off)\b/],
    ['greet', /^\W*(hi|hello|hey|hiya|howdy|yo|good (morning|afternoon|evening)|greetings)\b/],
    ['calm', /\b(i('m| am) (so |really |very )?(tired|sleepy|exhausted)|going to (bed|sleep)|time for bed|no rush|take your time|no worries|it('s| is) (ok|okay|fine)|(i'm |i am )?sorry|my bad|apologies)\b/],
    ['sleepy', /\b(are you (tired|sleepy)|so boring|zzz+|yawn)\b/],
    ['love', /\b(i love (you|this|it|that)|love (you|u)|you('re| are) (the best|amazing|awesome|wonderful|a lifesaver|incredible)|lifesaver|proud of you)\b|❤|😍/],
    ['laugh', /\b(lol|lmao|rofl|ha(ha)+|he(he)+|that('s| is| was) (so )?(funny|hilarious))\b|😂|🤣/],
    // Sarcasm earns a look; checked before thanks, since "thanks a lot" can be either.
    ['eyeroll', /\b(yeah,? right|sure,? sure|oh,? great|thanks a lot|thanks for nothing|wow,? thanks)\b/],
    ['wink', /\b(just kidding|jk|only joking|kidding)\b|;\)|😉|😜/],
    ['playful', /\b(tell me a joke|let's play|riddle|play a game)\b/],
    // Shown something the user made.
    ['impressed', /\b(look (at )?what i (made|built|did)|check (this|it) out|i made (this|a|an)|here's what i (made|built))\b/],
    // Proud of the user.
    ['proud', /\b(i('m| am) (so |really )?proud( of myself)?|i (built|finished|shipped|fixed|solved) (it|this)( on my own| myself)?)\b/],
    ['relieved', /\b(it works|finally works|works now|that (fixed|solved) it|finally fixed|phew|false alarm|crisis averted|all good now)\b/],
    // Thanks gets a small bow.
    ['grateful', /\b(thank(s| you)|thx|ty|appreciate (it|you|that)|cheers)\b/],
    ['excited', /\b(i did it|we did it|let'?s go+|so excited|can('t|not) wait|i passed|i won\b|we won\b|i got (the job|in|accepted|promoted|engaged|married)|good news|congrat)/],
    ['happy', /\b(great job|good job|well done|nice work|good bot|perfect|awesome|yay+|you('re| are) (great|smart|helpful|kind))/],
    ['hopeful', /\b(wish me luck|fingers crossed|i hope|hopefully|pray for me|here goes|let's hope)\b/],
    ['listening', /\b(can i (tell|ask) you something|i need to (talk|vent)|hear me out|listen to me)\b/],
    // Doubted: she checks carefully.
    ['focused', /\b(let's (get to work|focus)|deep dive|step by step|concentrate|carefully|debug(ging)? (this|it)|are you sure|prove it)\b/],
    // Someone confused or trying again: she is determined to help.
    ['determined', /\b(let('s| us) do (it|this)|we can do (it|this)|won't give up|not giving up|one more (try|time)|let('s| us) try again|i('m| am) (so |really )?(confused|lost)|i (don't|dont|do not) (understand|get it|get this)|makes? no sense|what do you mean)\b/],
    // A claim that is hard to believe.
    ['skeptical', /\b(trust me|i swear|believe me|100% (sure|true|guaranteed)|guaranteed|the earth is flat|too good to be true|easy money|get rich quick)\b/],
    ['surprised', /\b(wo+w|whoa+|omg|oh my god|no way|unbelievable|holy (cow|crap|moly)|seriously\?|you won't believe|plot twist)|!{3,}/],
    ['curious', /\b(i wonder|i('m| am) curious|guess what|did you know|fun fact|what if)\b/],
    ['shrug', /\b(who knows|whatever|meh|idk)\b/],
    ['nod', /^\W*(yes|yep|yeah|yup|exactly|right|correct|agreed|sounds good|ok(ay)?|sure|go ahead|do it)\b/],
    // A message that is only question marks.
    ['confused', /^\s*\?+\s*$/],
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
