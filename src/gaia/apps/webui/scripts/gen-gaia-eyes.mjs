#!/usr/bin/env node
// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Draws GAIA's pixel eyes: every frame of every animation, as one 1-bit PNG
// sprite sheet (src/assets/gaia-eyes.png) plus the table that says where each
// animation sits on it (src/components/gaiaEyesSheet.ts). To change a pose, or
// add another variation of a state, edit the frames below and run:
//
//   node scripts/gen-gaia-eyes.mjs          # rewrite both files
//   node scripts/gen-gaia-eyes.mjs --check  # exit 1 if either file is stale

import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { deflateSync, inflateSync } from 'node:zlib';

export const SHEET_PATH = fileURLToPath(new URL('../src/assets/gaia-eyes.png', import.meta.url));
export const META_PATH = fileURLToPath(new URL('../src/components/gaiaEyesSheet.ts', import.meta.url));
export const REGENERATE_HINT = 'cd src/gaia/apps/webui && npm run gen:gaia-eyes';

/** One frame is a CELL x CELL grid; the eyes rest centred on these two points. */
export const CELL = 16;
const HOME = [[4, 8], [11, 8]];

const SHAPES = {
    happy: ['.###.', '##.##', '#...#'],
    happyw: ['.#####.', '##...##'],
    smug: ['#...#', '##.##', '.###.'],
    gt: ['##..', '.##.', '..##', '.##.', '##..'],
    lt: ['..##', '.##.', '##..', '.##.', '..##'],
    q: ['.##.', '#..#', '..#.', '.#..', '....', '.#..'],
    heart: ['.#.#.', '#####', '#####', '.###.', '..#..'],
    heartB: ['.##.##.', '#######', '#######', '.#####.', '..###..', '...#...'],
    angL: ['#...', '##..', '###.', '####', '####'],
    angsL: ['#...', '###.', '####'],
    sadL: ['...#', '..##', '.###', '####', '####'],
    worL: ['..#', '.##', '###', '###', '###', '###'],
    disL: ['###.', '..##'],
    star: ['..#..', '.###.', '#####', '.###.', '#.#.#'],
    starS: ['.#.', '###', '.#.'],
};
for (const name of ['ang', 'angs', 'sad', 'wor', 'dis']) {
    SHAPES[`${name}R`] = SHAPES[`${name}L`].map((row) => [...row].reverse().join(''));
}

/** A rectangular eye, `w` by `h`, moved `dx`,`dy` from its resting place. */
const R = (w = 3, h = 5, dx = 0, dy = 0) => ({ rows: Array(h).fill('#'.repeat(w)), dx, dy, rect: [w, h] });
/** A shaped eye from SHAPES. */
const S = (name, dx = 0, dy = 0) => ({ rows: SHAPES[name], dx, dy, shape: name });
/** A frame: the left eye, the right eye (the same when omitted), and loose pixels such as a tear. */
const F = (left, right = left, px = []) => ({ eyes: [left, right ?? left], px });
/** Both eyes the same shape. */
const B = (name, dx = 0, dy = 0, px = []) => F(S(name, dx, dy), undefined, px);
/** A mirrored pair, `<name>L` and `<name>R`. */
const P = (name, dx = 0, dy = 0, px = []) => F(S(`${name}L`, dx, dy), S(`${name}R`, dx, dy), px);
const hold = (frame, n) => Array(n).fill(frame);
const cycle = (frames, n) => Array(n).fill(frames).flat();

function render(frame) {
    const grid = Array.from({ length: CELL }, () => new Uint8Array(CELL));
    const dot = (x, y) => { if (x >= 0 && x < CELL && y >= 0 && y < CELL) grid[y][x] = 1; };
    frame.eyes.forEach(({ rows, dx, dy }, side) => {
        const [cx, cy] = HOME[side];
        const x0 = cx - Math.floor((rows[0].length - 1) / 2) + dx;
        const y0 = cy - Math.floor(rows.length / 2) + dy;
        rows.forEach((row, y) => [...row].forEach((ch, x) => { if (ch === '#') dot(x0 + x, y0 + y); }));
    });
    for (const [x, y] of frame.px) dot(x, y);
    return grid;
}

const SWEEP = [-3, -2, -1, 0, 1, 2, 3];
const REST = F(R());
const BLINK = F(R(5, 1));
const SQUEEZE = [F(S('gt'), S('lt')), F(S('gt', 1), S('lt', -1))];
// Looking up and to one side: the eye on that side narrows, like a turned head.
const UP_LEFT = F(R(2, 5, -3, -3), R(3, 5, -2, -3));
const UP_RIGHT = F(R(3, 5, 2, -3), R(2, 5, 2, -3));
const WIDE = F(R(4, 6, 0, -1));
const SAD = P('sad', 0, 1);
const LOW = P('sad', 0, 2);
const BEAT = F(S('heartB', -1, 0), S('heartB', 1, 0));
const WINK = F(S('happy'), R(3, 5));
const DOUBT = F(R(4, 2, 0, 1), R(3, 6, 0, -1));
/** A tear falling from under the left (x = 3) or right (x = 12) eye. */
const tears = (dy, ys, xs = [3]) => ys.map((y) => P('sad', 0, dy, xs.flatMap((x) => [[x, y], [x, y + 1]])));
const BLUSH = [[2, 13], [4, 13], [6, 13], [9, 13], [11, 13], [13, 13]];
const shy = (dx, dy) => F(R(3, 3, dx, dy), undefined, BLUSH);
/** A sleep bubble. Not a "z": the sheet is mirrored in right-to-left layouts, so nothing on it may be a letter. */
const bubble = (x, y) => [[x, y], [x + 1, y], [x, y + 1], [x + 1, y + 1]];
const ASLEEP = R(5, 1, 0, 3);
const drop = (y) => [[15, y], [15, y + 1]];
/** A small four-pointed twinkle centred on x, y; the same mirrored, so it survives right-to-left. */
const twinkle = (x, y) => [[x, y], [x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]];
const SCARED = F(R(2, 6, 0, -1));
const DISGUST = P('dis', 0, 1);
const HALF = F(R(3, 3, 0, 1));
const UPWARD = F(R(3, 5, 0, -2));
const FLAT = F(R(4, 2, 0, 3));

/** What the eyes become once GAIA has thought: a brain, two halves and their folds. */
const BRAIN = [
    '................',
    '.....##.###.....',
    '...####.#####...',
    '..##..#.#..###..',
    '.###.##.##.#.##.',
    '.#.###..#.##..#.',
    '##..#.#.##..####',
    '#.###.#.#.###..#',
    '##..###.##..#.##',
    '.###..#.#.###.#.',
    '.#.###..#.#..##.',
    '..##..#.####.#..',
    '...######.###...',
    '........##......',
    '.........##.....',
    '................',
];
/** A frame with no eyes, only loose pixels. */
const G = (px) => ({ eyes: [], px });
/** `icon`'s pixels within `r` of either eye, moved `dy`: it grows out of the eyes. */
const growFrom = (icon, r, dy = 0) => ({
    ...G(icon.flatMap((row, y) => [...row].flatMap((ch, x) =>
        ch === '#' && HOME.some(([cx, cy]) => Math.hypot(x - cx, y - cy) <= r) ? [[x, y + dy]] : []))),
    grow: { icon, r, dy },
});
/** The eyes widen, close, and `icon` grows out of them, with a small settle. */
const becomes = (icon) => ({
    fps: 16, loop: false, pose: -1,
    variants: [[
        REST, WIDE, F(R(4, 3)), F(R(3, 1)),
        F(R(2, 1), undefined, growFrom(icon, 2).px), F(R(1, 1), undefined, growFrom(icon, 3.5).px),
        growFrom(icon, 5), growFrom(icon, 6.5), growFrom(icon, 8), growFrom(icon, 99, -1), growFrom(icon, 99),
    ]],
});

/**
 * What GAIA's eyes become once she has answered. The brain is the default; the
 * others mark a strong reaction or something she is telling the user. Every one
 * survives the right-to-left mirror: the question mark becomes the Arabic one,
 * and nothing else is a letter.
 */
const ICONS = {
    brain: BRAIN,
    heart: [
        '................',
        '................',
        '...###....###...',
        '..#####..#####..',
        '.##############.',
        '.##############.',
        '.##############.',
        '..############..',
        '...##########...',
        '....########....',
        '.....######.....',
        '......####......',
        '.......##.......',
        '................',
        '................',
        '................',
    ],
    question: [
        '................',
        '.....######.....',
        '....########....',
        '...###....###...',
        '...##......##...',
        '..........###...',
        '.........###....',
        '........###.....',
        '.......###......',
        '.......##.......',
        '.......##.......',
        '................',
        '.......##.......',
        '.......##.......',
        '................',
        '................',
    ],
    exclaim: [
        '................',
        '.......##.......',
        '......####......',
        '......####......',
        '......####......',
        '......####......',
        '.......##.......',
        '.......##.......',
        '.......##.......',
        '................',
        '................',
        '.......##.......',
        '......####......',
        '.......##.......',
        '................',
        '................',
    ],
    star: [
        '................',
        '.......##.......',
        '.......##.......',
        '......####......',
        '......####......',
        '.##############.',
        '..############..',
        '....########....',
        '.....######.....',
        '....########....',
        '....###..###....',
        '...###....###...',
        '...##......##...',
        '................',
        '................',
        '................',
    ],
    sun: [
        '................',
        '.......##.......',
        '..##...##...##..',
        '...##......##...',
        '.....######.....',
        '....########....',
        '....########....',
        '###.########.###',
        '###.########.###',
        '....########....',
        '....########....',
        '.....######.....',
        '...##......##...',
        '..##...##...##..',
        '.......##.......',
        '................',
    ],
    note: [
        '................',
        '......########..',
        '......########..',
        '......##....##..',
        '......##....##..',
        '......##....##..',
        '......##....##..',
        '......##....##..',
        '......##....##..',
        '...####...####..',
        '..#####..#####..',
        '..#####..#####..',
        '...###....###...',
        '................',
        '................',
        '................',
    ],
    tear: [
        '................',
        '.......##.......',
        '.......##.......',
        '......####......',
        '......####......',
        '.....######.....',
        '....########....',
        '...##########...',
        '...#######.##...',
        '...#######.##...',
        '...######.###...',
        '....#######.....',
        '.....######.....',
        '................',
        '................',
        '................',
    ],
    bolt: [
        '................',
        '.........####...',
        '........####....',
        '.......####.....',
        '......####......',
        '.....##########.',
        '....##########..',
        '.........####...',
        '........####....',
        '.......####.....',
        '......###.......',
        '.....##.........',
        '....#...........',
        '................',
        '................',
        '................',
    ],
    cloud: [
        '................',
        '................',
        '......####......',
        '....########....',
        '...##########...',
        '..############..',
        '.##############.',
        '################',
        '################',
        '.##############.',
        '................',
        '...#...#...#....',
        '..#...#...#.....',
        '................',
        '................',
        '................',
    ],
    sprout: [
        '................',
        '.####......####.',
        '######....######',
        '#######..#######',
        '.######..######.',
        '..#####..#####..',
        '....###..###....',
        '.......##.......',
        '.......##.......',
        '.......##.......',
        '....########....',
        '...##########...',
        '...##########...',
        '................',
        '................',
        '................',
    ],
    moon: [
        '................',
        '.....######.....',
        '...#####........',
        '..####..........',
        '.####...........',
        '.####.......#...',
        '####.......###..',
        '####........#...',
        '####............',
        '####...........#',
        '.####.........##',
        '.#####.......###',
        '..############..',
        '....########....',
        '................',
        '................',
    ],
    check: [
        '................',
        '................',
        '..............##',
        '.............###',
        '............###.',
        '...........###..',
        '##........###...',
        '###......###....',
        '.###....###.....',
        '..###..###......',
        '...######.......',
        '....####........',
        '.....##.........',
        '................',
        '................',
        '................',
    ],
};

/**
 * state -> { fps, loop, pose, variants }. Each variant is one way to play the
 * state; the component picks among them at random so a state rarely repeats
 * itself. `pose` is the frame of the first variant shown under reduced motion.
 * Working states loop while GAIA works; `wait`, `brain` and the moods play once.
 *
 * Frames are drawn for left-to-right reading. In a right-to-left layout the
 * whole sheet is mirrored, so the eyes read, glance and write the other way and
 * the question mark turns into the Arabic one. Draw only shapes that survive
 * that flip: no letters, digits or one-directional symbols.
 */
export const STATES = {
    think: {
        fps: 10, loop: true, pose: 6,
        variants: [
            [
                ...hold(REST, 2), F(R(5, 3, 0, 1)), F(R(3, 7, -1, -2)), F(R(3, 4, -3, -4)), ...hold(UP_LEFT, 5),
                F(R(5, 3, 0, -3)), ...hold(F(R(3, 5, 0, -3)), 3), ...hold(F(R(3, 3, 0, -3)), 3),
                F(R(6, 2, 1, -3)), F(R(3, 4, 3, -4)), ...hold(UP_RIGHT, 6),
                F(R(3, 4, 3, -1)), F(R(3, 4, 2, 1)), F(R(3, 4, 0, 2)), F(R(3, 4, -2, 1)), F(R(3, 4, -3, -1)), ...hold(UP_LEFT, 3),
                F(R(4, 1, -2, -2)), ...hold(UP_LEFT, 2), F(R(3, 7, -1, -1)), F(R(5, 3, 0, 2)), F(R(3, 6, 0, -1)), ...hold(REST, 3),
                ...hold(WIDE, 2), REST,
            ],
            // Starts to the right, drifts slowly across the top, narrows on the far side.
            [
                ...hold(REST, 2), F(R(5, 3, 0, 1)), F(R(3, 7, 1, -2)), F(R(3, 4, 3, -4)), ...hold(UP_RIGHT, 6), F(R(4, 1, 2, -2)),
                ...hold(UP_RIGHT, 3), F(R(3, 5, 1, -3)), ...hold(F(R(3, 5, 0, -3)), 2), F(R(3, 5, -1, -3)), ...hold(UP_LEFT, 5),
                ...hold(F(R(2, 3, -3, -3), R(3, 3, -2, -3)), 3), ...hold(UP_LEFT, 2), F(R(3, 7, -1, -1)), F(R(5, 3, 0, 2)),
                F(R(3, 6, 0, -1)), ...hold(REST, 4),
            ],
            // Looks straight up and bobs as if counting, glances down, then snaps up to the right.
            [
                ...hold(REST, 2), F(R(5, 3, 0, 1)), F(R(3, 7, 0, -3)), ...hold(F(R(3, 5, 0, -3)), 3),
                ...cycle([F(R(3, 4, 0, -2)), F(R(3, 5, 0, -4)), F(R(3, 5, 0, -3))], 3), F(R(3, 6, -1, 0)),
                ...hold(F(R(3, 4, -2, 2)), 4), F(R(5, 1, -2, 3)), ...hold(F(R(3, 4, -2, 2)), 2), F(R(3, 7, 1, -1)),
                ...hold(UP_RIGHT, 5), F(R(3, 7, 1, -1)), F(R(5, 3, 0, 2)), ...hold(REST, 3),
            ],
        ],
    },
    read: {
        fps: 10, loop: true, pose: 3,
        variants: [
            [
                ...SWEEP.map((d) => F(R(3, 3, d, -1))), F(R(7, 1, 0, 0)),
                ...[-3, -2, -1, 0].map((d) => F(R(3, 3, d, 1))), ...hold(F(R(3, 4, 0, 1)), 2), F(R(3, 3, -1, 1)),
                ...[0, 1, 2, 3].map((d) => F(R(3, 3, d, 1))), F(R(5, 1, 3, 1)), F(R(7, 1, 0, 0)),
            ],
            // Three short lines, skimmed, then a jump up to the next page.
            [
                ...[-2, 0, 2].flatMap((dy) => [...[-3, -1, 1, 3].map((d) => F(R(3, 3, d, dy))), F(R(7, 1, 0, dy))]),
                F(R(5, 1, 0, 2)), F(R(3, 7, 0, -1)), ...hold(F(R(3, 3, -3, -2)), 2),
            ],
        ],
    },
    tool: {
        fps: 10, loop: true, pose: 5,
        variants: [
            [
                F(R(5, 3, 0, 1)), ...cycle(SQUEEZE, 2),
                ...hold(F(R(4, 3, -2, 0)), 2), F(R(6, 1, 0, 0)), ...hold(F(R(4, 3, 2, 0)), 2), F(R(6, 1, 0, 0)),
                F(R(4, 3, -1, 1)), F(R(4, 3, 1, 1)), ...SQUEEZE, ...hold(F(R(4, 7)), 2), F(R(5, 3, 0, 1)), REST,
            ],
            // Narrowed eyes check all four corners, then the effort and the "got it".
            [
                F(R(5, 3, 0, 1)), ...hold(F(R(4, 3, -2, -2)), 2), F(R(6, 1, 0, -2)), ...hold(F(R(4, 3, 2, -2)), 2), F(R(3, 6, 2, 0)),
                ...hold(F(R(4, 3, 2, 2)), 2), F(R(6, 1, 0, 2)), ...hold(F(R(4, 3, -2, 2)), 2), F(R(3, 6, -2, 0)),
                ...cycle(SQUEEZE, 2), ...hold(F(R(4, 7)), 2), F(R(5, 3, 0, 1)), REST,
            ],
        ],
    },
    wait: {
        fps: 8, loop: false, pose: 0,
        variants: [[
            ...hold(WIDE, 2), F(R(5, 4, 0, 1)), F(R(3, 7, 0, -3)), F(R(4, 6, 0, -4)), F(R(4, 6, 0, -3)), F(R(5, 4, 0, 1)),
            ...hold(WIDE, 2), ...hold(F(R(4, 6, -2, -1)), 2), ...hold(F(R(4, 6, 2, -1)), 2),
            WIDE, BLINK, WIDE, BLINK, ...hold(WIDE, 2), ...hold(F(R(5, 7, 0, -1), R(3, 4, 0, -2)), 4), WIDE,
        ]],
    },
    happy: {
        fps: 10, loop: false, pose: -1,
        variants: [
            [
                F(R(5, 3, 0, 2)), F(R(3, 7, 0, -2)), B('happy', 0, -4), B('happy', 0, -5), B('happy', 0, -3), B('happy', 0, 1), B('happyw', 0, 2),
                B('happy', 0, -2), B('happy', 0, -3), B('happy', 0, -1), B('happyw', 0, 1), B('happy', 0, -1), ...hold(B('happy'), 3),
                WINK, F(S('happy'), R(5, 1)), WINK, ...hold(B('happy'), 3),
            ],
            // A hop, then a contented sway from side to side.
            [
                F(R(5, 3, 0, 2)), F(R(3, 7, 0, -2)), B('happy', 0, -3),
                ...cycle([B('happy', -1, -1), B('happy', -2, 0), B('happy', -1, -1), B('happy', 0, -2), B('happy', 1, -1), B('happy', 2, 0), B('happy', 1, -1), B('happy', 0, -2)], 2),
                ...hold(B('happy'), 4),
            ],
        ],
    },
    excited: {
        fps: 10, loop: false, pose: -1,
        variants: [
            [
                ...cycle([F(R(5, 3, 0, 2)), F(R(3, 7, 0, -3)), F(R(4, 6, 0, -4))], 2), F(R(5, 3, 0, 2)),
                B('happy', 0, -4), B('happy', 0, -2), B('happyw', 0, 2), B('happy', 0, -3), B('happy', 0, -1), B('happyw', 0, 1), ...hold(B('happy'), 4),
            ],
            // Can't keep still: a fast shake, then one big jump.
            [
                ...cycle([F(R(4, 6, -1, -1)), F(R(4, 6, 1, -1))], 4), F(R(5, 3, 0, 2)), F(R(3, 8, 0, -3)), B('happy', 0, -5), B('happy', 0, -2),
                B('happyw', 0, 2), B('happy', 0, -1), ...hold(B('happy'), 4),
            ],
        ],
    },
    love: {
        fps: 10, loop: false, pose: -1,
        variants: [
            [
                REST, F(R(5, 3, 0, 1)), B('heart', 0, -3), B('heart', 0, -1),
                ...cycle([BEAT, B('heart'), BEAT, B('heart'), B('heart'), B('heart')], 2), BEAT, B('heart'), B('heart'),
            ],
            // The hearts beat in turn, then float up and settle.
            [
                REST, F(R(5, 3, 0, 1)), B('heart'),
                ...cycle([F(S('heartB', -1, 0), S('heart')), B('heart'), F(S('heart'), S('heartB', 1, 0)), B('heart')], 2),
                B('heart', 0, -1), B('heart', 0, -2), B('heart', 0, -3), B('heart', 0, -2), B('heart', 0, -1), ...hold(B('heart'), 3),
            ],
        ],
    },
    laugh: {
        fps: 10, loop: false, pose: 0,
        variants: [
            [
                ...cycle([F(S('gt', 0, -2), S('lt', 0, -2)), F(S('gt', 0, 1), S('lt', 0, 1))], 6),
                B('happy', 0, -2), B('happy', 0, 1), B('happy', 0, -1), ...hold(B('happy'), 3),
            ],
            // Shoulders shaking: the smile rocks side to side before it doubles over.
            [
                ...cycle([B('happy', -1, -1), B('happy', 1, 0)], 5),
                ...cycle([F(S('gt', 0, -1), S('lt', 0, -1)), F(S('gt', 0, 2), S('lt', 0, 2))], 3), B('happy', 0, -2), ...hold(B('happy'), 3),
            ],
        ],
    },
    playful: {
        fps: 10, loop: false, pose: 3,
        variants: [[
            REST, F(R(5, 3, 0, 1)), F(S('happy', 0, -1), R(3, 5, 0, -1)), ...hold(WINK, 3),
            F(S('happy', 1), R(3, 5, 1)), F(S('happy', 2), R(3, 5, 2)), F(S('happy', 1), R(3, 5, 1)), WINK,
            F(S('happy', -1), R(3, 5, -1)), WINK, REST, ...hold(F(R(3, 5), S('happy')), 4), ...hold(B('happy'), 3),
        ]],
    },
    proud: {
        fps: 10, loop: false, pose: -1,
        variants: [[
            REST, BLINK, ...hold(B('smug'), 2), B('smug', 0, -1), ...hold(B('smug', 0, -2), 4),
            ...[1, 2, 1, 0, -1, -2, -1].map((d) => B('smug', d, -2)), ...hold(B('smug', 0, -2), 4),
        ]],
    },
    curious: {
        fps: 10, loop: false, pose: 4,
        variants: [[
            REST, F(R(5, 3, 0, 1)), F(R(3, 5, 2, -1)), ...hold(F(R(3, 5, 2, -1), R(4, 7, 2, -1)), 5), F(R(3, 1, 2, -1), R(4, 1, 2, -1)),
            ...hold(F(R(3, 5, 2, -1), R(4, 7, 2, -1)), 2), F(R(6, 2, 0, -1)), ...hold(F(R(4, 7, -2, -1), R(3, 5, -2, -1)), 5),
            F(R(3, 6, 0, -1)), ...hold(WIDE, 3),
        ]],
    },
    surprised: {
        fps: 10, loop: false, pose: 3,
        variants: [
            [
                REST, F(R(5, 2, 0, 2)), F(R(3, 8, 0, -3)), ...hold(F(R(5, 7, 0, -2)), 2), WIDE, ...hold(F(R(2, 2, 0, -1)), 5),
                BLINK, ...hold(F(R(2, 2, 0, -1)), 2), ...hold(WIDE, 3), REST,
            ],
            // A double take: looking away, then snapping back.
            [
                REST, ...hold(F(R(3, 5, -3, 0)), 3), F(R(6, 2, 0, 0)), ...hold(F(R(5, 7, 0, -2)), 2), ...hold(F(R(2, 2, 0, -1)), 4),
                BLINK, ...hold(F(R(2, 2, 0, -1)), 2), BLINK, ...hold(WIDE, 3), REST,
            ],
        ],
    },
    confused: {
        fps: 10, loop: false, pose: 0,
        variants: [
            [
                ...hold(F(R(4, 6, 0, -1), R(3, 2, 0, 1)), 4), ...hold(F(R(3, 2, 0, 1), R(4, 6, 0, -1)), 4),
                ...cycle([F(R(3, 5, -1, 0), S('q', 0, -1)), F(R(3, 5, -1, 0), S('q', 0, -2))], 3), BLINK,
                ...hold(F(R(4, 6, 0, -1), R(3, 2, 0, 1)), 3),
            ],
            // Goes cross-eyed, uncrosses, and the question mark hops from one eye to the other.
            [
                ...hold(F(R(3, 5, 2, 0), R(3, 5, -1, 0)), 4), ...hold(F(R(3, 5, -2, 0), R(3, 5, 2, 0)), 3), ...hold(F(R(3, 5, 2, 0), R(3, 5, -1, 0)), 3),
                ...cycle([F(R(3, 5, -1, 0), S('q', 0, -1)), F(R(3, 5, -1, 0), S('q', 0, -2))], 2), ...hold(F(S('q', 0, -1), R(3, 5, 1, 0)), 3), BLINK, REST,
            ],
        ],
    },
    skeptical: {
        fps: 10, loop: false, pose: 1,
        variants: [[
            REST, ...hold(DOUBT, 5), ...hold(F(R(4, 2, -2, 1), R(3, 6, -2, -1)), 3), ...hold(F(R(4, 2, 2, 1), R(3, 6, 2, -1)), 3), ...hold(DOUBT, 2),
            ...hold(F(R(3, 6, 0, -1), R(4, 2, 0, 1)), 4), BLINK, REST,
        ]],
    },
    worried: {
        fps: 10, loop: false, pose: 10,
        variants: [
            [
                ...hold(WIDE, 2), ...cycle([P('wor', 1), P('wor', -1)], 3), ...hold(P('wor'), 2), ...hold(P('wor', -3), 3),
                ...hold(P('wor', 3), 3), ...cycle([P('wor', 1), P('wor', -1)], 2), ...hold(P('wor'), 3),
            ],
            // A bead of sweat runs down beside the eyes.
            [
                ...hold(WIDE, 2), ...hold(P('wor', 0, -1), 2),
                ...[3, 5, 7, 9].map((y, i) => P('wor', i % 2 ? 1 : -1, -1, drop(y))), ...hold(P('wor'), 2), ...hold(P('wor', -3), 2),
                ...hold(P('wor', 2), 2), ...[3, 5, 7, 9].map((y, i) => P('wor', i % 2 ? 1 : -1, 0, drop(y))), ...hold(P('wor'), 3),
            ],
        ],
    },
    sad: {
        fps: 10, loop: false, pose: 4,
        variants: [
            [
                REST, F(R(3, 5, 0, 1)), ...hold(SAD, 3), ...tears(1, [12, 13, 14, 15]), P('sad'), SAD,
                ...hold(F(R(5, 1, 0, 3)), 2), ...hold(LOW, 2), ...tears(2, [13, 14, 15]), ...hold(LOW, 4),
            ],
            // A slump, a look away, a tear from the other eye, then one from each.
            [
                REST, F(R(5, 3, 0, 2)), ...hold(LOW, 3), ...hold(P('sad', -1, 2), 2), ...tears(2, [13, 14, 15], [12]), SAD,
                ...hold(LOW, 3), ...tears(2, [13, 14, 15], [3, 12]), ...hold(LOW, 3),
            ],
        ],
    },
    embarrassed: {
        fps: 10, loop: false, pose: 4,
        variants: [[
            REST, ...hold(WIDE, 2), ...hold(shy(-2, 2), 4), ...hold(shy(0, 0), 2), ...hold(shy(2, 2), 4),
            F(R(5, 1, 2, 3), undefined, BLUSH), ...hold(shy(0, 2), 3),
        ]],
    },
    angry: {
        fps: 10, loop: false, pose: 3,
        variants: [
            [
                REST, F(R(5, 3, 0, 1)), P('ang', 0, -2), P('ang'), ...cycle([P('ang', 1), P('ang', -1)], 4),
                ...hold(P('ang'), 4), ...hold(P('angs', 0, 1), 5), ...hold(P('ang'), 2),
            ],
            // Two stomps, then the glare.
            [
                REST, ...cycle([P('ang', 0, -3), P('ang', 0, 3), P('angs', 0, 3)], 2), ...hold(P('ang'), 3),
                ...cycle([P('ang', 1), P('ang', -1)], 3), ...hold(P('angs', 0, 1), 4), ...hold(P('ang'), 2),
            ],
        ],
    },
    determined: {
        fps: 10, loop: false, pose: 3,
        variants: [[
            REST, F(R(5, 3, 0, 1)), P('angs', 0, -1), ...hold(P('angs'), 3), ...cycle([P('angs', 0, 2), P('angs')], 2),
            ...hold(P('angs'), 3), WIDE, ...hold(P('angs'), 3),
        ]],
    },
    sleepy: {
        fps: 10, loop: false, pose: 3,
        variants: [[
            REST, F(R(3, 4, 0, 1)), F(R(3, 3, 0, 1)), ...hold(F(R(4, 2, 0, 2)), 3), F(ASLEEP), ...hold(F(R(5, 1, 0, 4)), 2),
            F(R(4, 6, 0, -2)), REST, F(R(3, 4, 0, 1)), F(R(3, 3, 0, 1)), F(R(4, 2, 0, 2)),
            ...hold(F(ASLEEP, undefined, [[11, 7]]), 3), ...hold(F(ASLEEP, undefined, [...bubble(12, 4), [10, 7]]), 3),
            ...hold(F(ASLEEP, undefined, [...bubble(13, 1), ...bubble(11, 4), [9, 7]]), 3),
        ]],
    },
    scared: {
        fps: 12, loop: false, pose: 6,
        variants: [
            [
                REST, WIDE, ...hold(SCARED, 2), ...cycle([F(R(2, 6, 1, -1)), F(R(2, 6, -1, -1))], 5), ...hold(SCARED, 2),
                ...[3, 5, 7, 9].map((y, i) => F(R(2, 6, i % 2 ? 1 : -1, -1), undefined, drop(y))), ...hold(SCARED, 4),
            ],
            // Tiny pupils darting, then a shiver.
            [
                REST, ...hold(WIDE, 2), ...hold(F(R(2, 2, -3, 0)), 3), ...hold(F(R(2, 2, 3, 0)), 3), ...hold(F(R(2, 2, -3, 0)), 2),
                ...hold(F(R(2, 2)), 2), ...cycle([F(R(2, 6, 1, -1)), F(R(2, 6, -1, -1))], 4), ...hold(SCARED, 4),
            ],
        ],
    },
    disgusted: {
        fps: 10, loop: false, pose: 6,
        variants: [[
            REST, F(R(4, 3, 0, 1)), ...hold(DISGUST, 2), ...hold(P('dis', 0, 2), 2), ...cycle([P('dis', -2, 1), P('dis', 2, 1)], 3),
            ...hold(DISGUST, 3), F(R(5, 1, 0, 2)), ...hold(DISGUST, 4),
        ]],
    },
    relieved: {
        fps: 10, loop: false, pose: 16,
        variants: [[
            ...hold(WIDE, 3), F(R(4, 3)), BLINK, ...hold(B('happyw', 0, 1), 3), ...hold(B('happyw', 0, 2), 4),
            ...hold(B('happyw', 0, 3), 3), ...hold(B('happyw', 0, 1), 4),
        ]],
    },
    calm: {
        fps: 8, loop: false, pose: 14,
        variants: [[
            ...hold(REST, 3), F(R(3, 4, 0, 1)), ...hold(HALF, 3), ...hold(F(R(5, 1, 0, 1)), 2), ...hold(HALF, 3),
            ...hold(B('happyw', 0, 1), 6),
        ]],
    },
    hopeful: {
        fps: 10, loop: false, pose: 12,
        variants: [[
            REST, F(R(3, 5, 0, -1)), ...hold(UPWARD, 2), ...cycle([
                F(R(3, 5, 0, -2), undefined, [...twinkle(1, 2), [14, 1]]),
                F(R(3, 5, 0, -2), undefined, [[1, 2], ...twinkle(14, 1)]),
            ], 3), ...hold(F(R(3, 6, 0, -3), undefined, [...twinkle(1, 2), ...twinkle(14, 1)]), 4), BLINK,
            ...hold(F(R(3, 5, 0, -2), undefined, twinkle(14, 1)), 3),
        ]],
    },
    disappointed: {
        fps: 10, loop: false, pose: 18,
        variants: [[
            REST, F(R(3, 5, 0, 1)), F(R(3, 3, 0, 2)), ...hold(FLAT, 3), ...hold(F(R(4, 2, -2, 3)), 3), F(R(5, 1, -2, 4)),
            ...hold(F(R(4, 2, -2, 3)), 2), ...hold(FLAT, 2), F(R(5, 1, 0, 4)), ...hold(FLAT, 4),
        ]],
    },
    // Communication: what the eyes do while GAIA talks, not only how she feels.
    greet: {
        fps: 10, loop: false, pose: 6,
        variants: [
            [
                REST, WIDE, ...hold(B('happyw', 0, -1), 2), ...cycle([B('happyw', 0, -2), B('happyw')], 2),
                ...hold(B('happyw'), 2), ...hold(F(S('happy'), R(3, 5)), 3), ...hold(B('happyw'), 3),
            ],
            // Brows up, a double blink, a smile.
            [
                REST, ...hold(F(R(3, 6, 0, -2)), 3), BLINK, F(R(3, 6, 0, -2)), BLINK, ...hold(F(R(3, 6, 0, -2)), 2),
                ...hold(B('happy'), 4), ...hold(B('happyw'), 3),
            ],
        ],
    },
    goodbye: {
        fps: 10, loop: false, pose: 6,
        variants: [[
            REST, ...hold(F(S('happy'), R(3, 5)), 3), BLINK, ...hold(B('happyw'), 3), ...hold(B('happyw', 0, 1), 2),
            F(R(5, 1, 0, 1)), ...hold(B('happyw', 0, 1), 2), ...hold(F(R(5, 1, 0, 2)), 4),
        ]],
    },
    nod: {
        fps: 12, loop: false, pose: 0,
        variants: [[
            REST, ...cycle([F(R(3, 5, 0, 1)), F(R(3, 4, 0, 3)), F(R(3, 5, 0, 1)), REST, F(R(3, 5, 0, -1)), REST], 2),
            ...hold(B('happy'), 4), REST,
        ]],
    },
    shake: {
        fps: 12, loop: false, pose: 1,
        variants: [[
            REST, F(R(3, 4)), ...cycle([F(R(3, 4, -2)), F(R(3, 4)), F(R(3, 4, 2)), F(R(3, 4))], 3),
            ...hold(F(R(4, 2, 0, 1)), 4), REST,
        ]],
    },
    shrug: {
        fps: 10, loop: false, pose: 9,
        variants: [[
            REST, ...hold(UP_LEFT, 3), ...hold(UP_RIGHT, 3), F(R(3, 5, 0, -2)), ...hold(F(R(4, 2, 0, -1)), 3),
            ...hold(F(R(4, 2, 0, 1)), 2), BLINK, ...hold(F(R(4, 2, 0, 1)), 3),
        ]],
    },
    grateful: {
        // A small bow: "my pleasure".
        fps: 10, loop: false, pose: 3,
        variants: [[
            REST, ...hold(B('happyw'), 2), ...hold(B('happyw', 0, 2), 2), ...hold(B('happyw', 0, 4), 4),
            ...hold(B('happyw', 0, 2), 2), ...hold(B('happyw'), 4), ...hold(B('happy'), 3),
        ]],
    },
    apologetic: {
        fps: 10, loop: false, pose: 6,
        variants: [[
            REST, F(R(3, 4, 0, 1)), ...hold(P('sad', 0, 2), 3), ...hold(P('sad', 0, 2, BLUSH), 3),
            ...hold(P('sad', -2, 2, BLUSH), 3), ...hold(P('sad', 0, 2, BLUSH), 2), F(R(5, 1, 0, 3), undefined, BLUSH),
            ...hold(P('sad', 0, 2, BLUSH), 3),
        ]],
    },
    listening: {
        fps: 8, loop: false, pose: 4,
        variants: [[
            REST, F(R(3, 6, 0, -1)), ...hold(F(R(3, 6, 0, -2), R(3, 6, 0, 0)), 4), ...hold(F(R(3, 6, 0, -1)), 2),
            F(R(3, 4, 0, 1)), ...hold(F(R(3, 6, 0, -1)), 3), F(R(5, 1)), ...hold(F(R(3, 6, 0, -2), R(3, 6, 0, 0)), 4),
        ]],
    },
    focused: {
        fps: 8, loop: false, pose: 3,
        variants: [[
            REST, F(R(4, 3)), ...hold(F(R(4, 2)), 4), ...hold(F(R(4, 2, 1)), 3), ...hold(F(R(4, 2, -1)), 3),
            ...hold(F(R(4, 2)), 3), F(R(5, 1)), ...hold(F(R(4, 2)), 3),
        ]],
    },
    impressed: {
        fps: 10, loop: false, pose: 3,
        variants: [[
            REST, WIDE, ...hold(B('star'), 2), ...cycle([B('starS'), B('star')], 3), ...hold(B('star'), 4),
            ...hold(B('happyw'), 3),
        ]],
    },
    wink: {
        fps: 10, loop: false, pose: 2,
        variants: [[
            REST, F(R(3, 5), R(3, 3, 0, 1)), ...hold(F(R(3, 5), R(4, 1, 0, 1)), 4), F(R(3, 5), R(3, 3, 0, 1)),
            ...hold(F(R(3, 5), S('happy')), 3), REST,
        ]],
    },
    eyeroll: {
        fps: 10, loop: false, pose: 6,
        variants: [[
            REST, F(R(3, 3, -2, -2)), F(R(3, 3, -1, -4)), F(R(3, 3, 1, -4)), F(R(3, 3, 2, -2)), ...hold(F(R(3, 3, 2, -2)), 2),
            ...hold(F(R(4, 2, 0, 1)), 3), BLINK, ...hold(F(R(4, 2, 0, 1)), 4),
        ]],
    },
    // What the eyes become once GAIA has answered: the brain, or an icon for a strong reaction.
    ...Object.fromEntries(Object.entries(ICONS).map(([name, icon]) => [name, becomes(icon)])),
};

/** Frames per row of the sheet. Animations run on from one row into the next, so no cell is wasted. */
export const COLUMNS = 48;

const mid = (a, b) => Math.round((a + b) / 2);

/** An eye halfway between `a` and `b`: a rectangle's size and place, or a shape's place. Null when they don't blend. */
function tweenEye(a, b) {
    if (a.rect && b.rect) return R(mid(a.rect[0], b.rect[0]), mid(a.rect[1], b.rect[1]), mid(a.dx, b.dx), mid(a.dy, b.dy));
    if (a.shape && a.shape === b.shape) return S(a.shape, mid(a.dx, b.dx), mid(a.dy, b.dy));
    return null;
}

/** The frame halfway between `a` and `b`, or `a` again when they don't blend (a new shape, a falling tear). */
function tween(a, b) {
    if (a.grow && b.grow && a.grow.icon === b.grow.icon) {
        return growFrom(a.grow.icon, (a.grow.r + b.grow.r) / 2, mid(a.grow.dy, b.grow.dy));
    }
    if (a.eyes.length !== 2 || b.eyes.length !== 2 || JSON.stringify(a.px) !== JSON.stringify(b.px)) return a;
    const eyes = a.eyes.map((eye, i) => tweenEye(eye, b.eyes[i]));
    return eyes.every(Boolean) ? F(eyes[0], eyes[1], a.px) : a;
}

/**
 * Twice the frames at twice the rate: an in-between after every frame, so moves
 * and blinks glide instead of jumping. Same length in time; a loop also blends
 * its last frame back into its first, and a one-shot ends on its own last frame.
 */
export function smooth(frames, loop) {
    const out = frames.flatMap((frame, i) => [frame, tween(frame, frames[i + 1] ?? frames[0])]);
    return loop ? out : out.slice(0, -1);
}

/** The sheet as one bit per pixel (1 = eye), every animation's frames in a run of cells, plus where each starts. */
export function buildSheet() {
    const runs = Object.entries(STATES).flatMap(([state, { variants, fps, loop, pose }]) =>
        variants.map((frames, i) => ({
            name: i ? `${state}${i + 1}` : state, state, frames: smooth(frames, loop), fps: fps * 2, loop,
            // The still is the same frame as before smoothing; -1 is the last.
            pose: 2 * ((pose + frames.length) % frames.length),
        })));
    const total = runs.reduce((sum, run) => sum + run.frames.length, 0);
    const width = COLUMNS * CELL;
    const height = Math.ceil(total / COLUMNS) * CELL;
    const pixels = new Uint8Array(width * height);
    const meta = {};
    const variants = {};
    let start = 0;
    for (const { name, state, frames, fps, loop, pose } of runs) {
        frames.forEach((frame, i) => {
            const cell = start + i;
            const [cx, cy] = [(cell % COLUMNS) * CELL, Math.floor(cell / COLUMNS) * CELL];
            render(frame).forEach((line, y) => line.forEach((on, x) => {
                if (on) pixels[(cy + y) * width + cx + x] = 1;
            }));
        });
        meta[name] = { start, frames: frames.length, fps, loop, pose };
        (variants[state] ??= []).push(name);
        start += frames.length;
    }
    return { width, height, pixels, meta, variants };
}

const CRC_TABLE = Uint32Array.from({ length: 256 }, (_, n) => {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    return c >>> 0;
});

function chunk(type, data) {
    const body = Buffer.concat([Buffer.from(type, 'latin1'), data]);
    let crc = 0xffffffff;
    for (const byte of body) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
    const out = Buffer.alloc(body.length + 8);
    out.writeUInt32BE(data.length, 0);
    body.copy(out, 4);
    out.writeUInt32BE((crc ^ 0xffffffff) >>> 0, body.length + 4);
    return out;
}

/** A 1-bit palette PNG: index 0 transparent, index 1 opaque white. The page colours it through a mask. */
export function encodePng({ width, height, pixels }) {
    const stride = Math.ceil(width / 8) + 1;
    const raw = Buffer.alloc(stride * height);
    for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
            if (pixels[y * width + x]) raw[y * stride + 1 + (x >> 3)] |= 0x80 >> (x & 7);
        }
    }
    const header = Buffer.alloc(13);
    header.writeUInt32BE(width, 0);
    header.writeUInt32BE(height, 4);
    header.set([1, 3, 0, 0, 0], 8);
    return Buffer.concat([
        Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
        chunk('IHDR', header),
        chunk('PLTE', Buffer.from([0, 0, 0, 255, 255, 255])),
        chunk('tRNS', Buffer.from([0, 255])),
        chunk('IDAT', deflateSync(raw, { level: 9 })),
        chunk('IEND', Buffer.alloc(0)),
    ]);
}

/** Reads back a PNG written by encodePng; throws on any other kind, so a hand-edited sheet is caught. */
export function decodePng(png) {
    const width = png.readUInt32BE(16);
    const height = png.readUInt32BE(20);
    if (png[24] !== 1 || png[25] !== 3) {
        throw new Error(`gaia-eyes.png is not the 1-bit palette PNG this script writes. Regenerate it: ${REGENERATE_HINT}`);
    }
    const data = [];
    for (let at = 8; at < png.length;) {
        const length = png.readUInt32BE(at);
        if (png.toString('latin1', at + 4, at + 8) === 'IDAT') data.push(png.subarray(at + 8, at + 8 + length));
        at += length + 12;
    }
    const raw = inflateSync(Buffer.concat(data));
    const stride = Math.ceil(width / 8) + 1;
    const pixels = new Uint8Array(width * height);
    for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
            if (raw[y * stride + 1 + (x >> 3)] & (0x80 >> (x & 7))) pixels[y * width + x] = 1;
        }
    }
    return { width, height, pixels };
}

/** The TypeScript table the component reads. */
export function metaSource({ width, height, meta, variants }) {
    const rows = Object.entries(meta).map(([name, m]) =>
        `    ${name}: { start: ${m.start}, frames: ${m.frames}, fps: ${m.fps}, loop: ${m.loop}, pose: ${m.pose} },`);
    const sets = Object.entries(variants).map(([state, names]) => `    ${state}: [${names.map((n) => `'${n}'`).join(', ')}],`);
    return `// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// AUTO-GENERATED by scripts/gen-gaia-eyes.mjs — do not edit.
// Regenerate from the repo root: ${REGENERATE_HINT}

/** Pixels per frame, frames per row, and the size of src/assets/gaia-eyes.png. */
export const EYES_CELL = ${CELL};
export const EYES_COLUMNS = ${COLUMNS};
export const EYES_SHEET_WIDTH = ${width};
export const EYES_SHEET_HEIGHT = ${height};

/** Where each animation sits on the sheet: its first cell (counted along the rows), frame count, speed, and the still shown under reduced motion. */
export const EYES_ANIMATIONS = {
${rows.join('\n')}
} as const;

export type EyesAnimation = keyof typeof EYES_ANIMATIONS;

/** Every way each state can be played; the first is the one shown under reduced motion. */
export const EYES_VARIANTS = {
${sets.join('\n')}
} as const satisfies Record<string, readonly EyesAnimation[]>;

export type EyesState = keyof typeof EYES_VARIANTS;
`;
}

async function main() {
    const sheet = buildSheet();
    const source = metaSource(sheet);
    if (process.argv.includes('--check')) {
        const committed = decodePng(await readFile(SHEET_PATH));
        const samePixels = committed.width === sheet.width && committed.height === sheet.height
            && Buffer.compare(Buffer.from(committed.pixels), Buffer.from(sheet.pixels)) === 0;
        const sameMeta = (await readFile(META_PATH, 'utf8')).replace(/\r\n/g, '\n') === source;
        if (!samePixels || !sameMeta) {
            console.error(`GAIA's eye sprite is stale. Regenerate it: ${REGENERATE_HINT}`);
            process.exit(1);
        }
        return;
    }
    const png = encodePng(sheet);
    await writeFile(SHEET_PATH, png);
    await writeFile(META_PATH, source);
    console.log(`Wrote ${Object.keys(sheet.meta).length} animations, ${sheet.width}x${sheet.height}, ${png.length} bytes.`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
    await main();
}
