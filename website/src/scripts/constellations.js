/*
 * Real star charts — the WINTER sky, at J2000 positions, with the line figures
 * that join them.
 *
 * The set is what actually stands over the northern hemisphere on a winter
 * night: Orion and its belt at the centre, with Taurus (Aldebaran, the Hyades
 * and the Pleiades), Auriga, Gemini and the two dogs
 * around it, Perseus and Andromeda to the west, and the circumpolar trio —
 * the Big Dipper, the Little Dipper and Cassiopeia — turning above. Summer
 * figures (Lyra, Cygnus, Scorpius) and the southern Crux are deliberately not
 * here: the first three are the wrong season, and Crux draws a cross.
 *
 * Coordinates are right ascension in HOURS (0-24) and declination in DEGREES,
 * epoch J2000, rounded to the precision a background needs (~0.5'). Magnitudes
 * are visual. The figures are the conventional asterisms — the shapes a person
 * actually recognises — not the IAU boundary polygons.
 *
 * `lines` indexes into `stars`. Keeping the pairs as indices (rather than names)
 * is what lets the renderer treat a figure as a graph without a lookup table.
 *
 * Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
 * SPDX-License-Identifier: MIT
 */

/**
 * @typedef {{ name: string, stars: Array<[number, number, number, string]>,
 *             lines: Array<[number, number]> }} Constellation
 *   stars: [raHours, decDegrees, visualMagnitude, properName]
 */

/** @type {Constellation[]} */
export const CONSTELLATIONS = [
  {
    // The Big Dipper — the asterism, not the whole of Ursa Major.
    name: 'Ursa Major',
    stars: [
      [11.0622, 61.751, 1.79, 'Dubhe'],
      [11.0307, 56.382, 2.37, 'Merak'],
      [11.8972, 53.695, 2.44, 'Phecda'],
      [12.2571, 57.033, 3.31, 'Megrez'],
      [12.9005, 55.96, 1.77, 'Alioth'],
      [13.3988, 54.925, 2.23, 'Mizar'],
      [13.7923, 49.313, 1.86, 'Alkaid'],
    ],
    lines: [[0, 1], [1, 2], [2, 3], [3, 0], [3, 4], [4, 5], [5, 6]],
  },
  {
    name: 'Orion',
    stars: [
      [5.9195, 7.407, 0.5, 'Betelgeuse'],
      [5.4188, 6.35, 1.64, 'Bellatrix'],
      [5.5334, -0.299, 2.23, 'Mintaka'],
      [5.6036, -1.202, 1.69, 'Alnilam'],
      [5.6793, -1.943, 1.77, 'Alnitak'],
      [5.7959, -9.67, 2.06, 'Saiph'],
      [5.2423, -8.202, 0.13, 'Rigel'],
      [5.5855, 9.934, 3.39, 'Meissa'],
    ],
    lines: [
      [0, 1], [0, 4], [1, 2], [2, 3], [3, 4],
      [4, 5], [2, 6], [7, 0], [7, 1],
    ],
  },
  {
    // Aldebaran and the Hyades V, plus the Pleiades — Subaru, M45 — as the
    // cluster it is: real member positions at their real separation from the
    // rest of Taurus, and joined by no lines, so it reads as a knot of stars
    // about a degree across rather than another stick figure.
    name: 'Taurus',
    stars: [
      [5.6274, 21.143, 3.0, 'Zeta Tau'],
      [4.5987, 16.509, 0.85, 'Aldebaran'],
      [4.4767, 15.871, 3.4, 'Theta Tau'],
      [4.3299, 15.628, 3.65, 'Gamma Tau'],
      [4.382, 17.542, 3.76, 'Delta Tau'],
      [4.4776, 19.18, 3.53, 'Epsilon Tau'],
      [5.4382, 28.608, 1.65, 'Elnath'],
      [3.7914, 24.105, 2.87, 'Alcyone'],
      [3.8194, 24.053, 3.63, 'Atlas'],
      [3.7449, 24.113, 3.7, 'Electra'],
      [3.7625, 24.367, 3.87, 'Maia'],
      [3.7716, 23.948, 4.18, 'Merope'],
      [3.7554, 24.467, 4.3, 'Taygeta'],
      [3.8192, 24.136, 5.05, 'Pleione'],
      [3.7425, 24.289, 5.45, 'Celaeno'],
      [3.7607, 24.555, 5.76, 'Asterope'],
    ],
    lines: [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [5, 6]],
  },
  {
    name: 'Auriga',
    stars: [
      [5.2782, 45.998, 0.08, 'Capella'],
      [5.9921, 44.947, 1.9, 'Menkalinan'],
      [5.9953, 37.213, 2.62, 'Mahasim'],
      [5.4382, 28.608, 1.65, 'Elnath'],
      [4.9497, 33.166, 2.69, 'Hassaleh'],
      [5.0328, 43.823, 3.03, 'Almaaz'],
    ],
    lines: [[0, 1], [1, 2], [2, 3], [3, 4], [4, 0], [0, 5]],
  },
  {
    name: 'Gemini',
    stars: [
      [7.5767, 31.888, 1.58, 'Castor'],
      [7.7553, 28.026, 1.14, 'Pollux'],
      [6.7328, 25.131, 3.06, 'Mebsuta'],
      [6.3828, 22.514, 2.87, 'Tejat'],
      [7.3353, 21.982, 3.53, 'Wasat'],
      [6.6285, 16.399, 1.93, 'Alhena'],
    ],
    lines: [[0, 1], [0, 2], [2, 3], [1, 4], [4, 5]],
  },
  {
    name: 'Canis Major',
    stars: [
      [6.7525, -16.716, -1.46, 'Sirius'],
      [6.3783, -17.956, 1.98, 'Mirzam'],
      [7.05, -23.833, 3.02, 'Omicron2 CMa'],
      [7.1399, -26.393, 1.83, 'Wezen'],
      [6.977, -28.972, 1.5, 'Adhara'],
      [7.4015, -29.303, 2.45, 'Aludra'],
      [6.339, -30.063, 3.02, 'Furud'],
    ],
    lines: [[1, 0], [0, 2], [2, 3], [3, 5], [3, 4], [4, 6]],
  },
  {
    name: 'Canis Minor',
    stars: [
      [7.6551, 5.225, 0.34, 'Procyon'],
      [7.4527, 8.289, 2.89, 'Gomeisa'],
    ],
    lines: [[0, 1]],
  },
  {
    name: 'Perseus',
    stars: [
      [3.4054, 49.861, 1.79, 'Mirfak'],
      [3.1361, 40.956, 2.12, 'Algol'],
      [3.902, 31.884, 2.85, 'Zeta Per'],
      [3.9642, 40.01, 2.89, 'Epsilon Per'],
      [3.08, 53.507, 2.93, 'Gamma Per'],
      [3.7154, 47.788, 3.01, 'Delta Per'],
      [2.8451, 55.895, 3.76, 'Eta Per'],
    ],
    lines: [[6, 4], [4, 0], [0, 5], [5, 3], [3, 2], [0, 1]],
  },
  {
    name: 'Andromeda',
    stars: [
      [0.1398, 29.091, 2.06, 'Alpheratz'],
      [0.6553, 30.861, 3.27, 'Delta And'],
      [1.1622, 35.621, 2.06, 'Mirach'],
      [2.065, 42.33, 2.1, 'Almach'],
    ],
    lines: [[0, 1], [1, 2], [2, 3]],
  },
  {
    name: 'Cassiopeia',
    stars: [
      [0.1529, 59.15, 2.27, 'Caph'],
      [0.6751, 56.537, 2.24, 'Schedar'],
      [0.9451, 60.717, 2.15, 'Gamma Cas'],
      [1.4304, 60.235, 2.68, 'Ruchbah'],
      [1.9067, 63.67, 3.35, 'Segin'],
    ],
    lines: [[0, 1], [1, 2], [2, 3], [3, 4]],
  },
  {
    // The Little Dipper. Its stars straddle the pole, which is why every figure
    // is projected about its own centre — see projectFigure.
    name: 'Ursa Minor',
    stars: [
      [2.5303, 89.264, 1.98, 'Polaris'],
      [17.5369, 86.586, 4.35, 'Yildun'],
      [16.7661, 82.037, 4.21, 'Epsilon UMi'],
      [15.7345, 77.794, 4.29, 'Zeta UMi'],
      [14.8451, 74.155, 2.08, 'Kochab'],
      [15.3453, 71.834, 3.05, 'Pherkad'],
      [16.2917, 75.755, 4.95, 'Eta UMi'],
    ],
    lines: [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [5, 6], [6, 3]],
  },
];

const DEG = Math.PI / 180;

/**
 * Project one constellation to flat chart coordinates.
 *
 * Gnomonic projection about the figure's own centroid, which is what a printed
 * star chart does for a single constellation: straight great circles, and no
 * visible distortion across a field this small. Doing it per figure — rather
 * than projecting the whole sky once — is also what keeps a circumpolar figure
 * like Ursa Minor from smearing across the pole.
 *
 * X is flipped so east is left, matching the convention for a chart held up to
 * the sky; without it every figure reads mirrored to anyone who knows them.
 *
 * @param {Constellation} c
 * @returns {{ name: string, pts: Array<{x: number, y: number, mag: number, name: string}>,
 *             lines: Array<[number, number]> }} unit-ish coordinates, centred on
 *   (0,0) and scaled so the longer axis spans 1.
 */
export function projectFigure(c) {
  const vecs = c.stars.map(([ra, dec]) => {
    const a = ra * 15 * DEG;
    const d = dec * DEG;
    return [Math.cos(d) * Math.cos(a), Math.cos(d) * Math.sin(a), Math.sin(d)];
  });

  // Centroid direction of the figure.
  const sum = vecs.reduce((s, v) => [s[0] + v[0], s[1] + v[1], s[2] + v[2]], [0, 0, 0]);
  const len = Math.hypot(sum[0], sum[1], sum[2]) || 1;
  const k = [sum[0] / len, sum[1] / len, sum[2] / len];

  // Local basis: east (perpendicular to the pole), then north = k × east.
  let east = [-k[1], k[0], 0];
  let e = Math.hypot(east[0], east[1], east[2]);
  if (e < 1e-6) {
    east = [1, 0, 0];                       // looking straight at a pole
    e = 1;
  }
  east = [east[0] / e, east[1] / e, east[2] / e];
  const north = [
    k[1] * east[2] - k[2] * east[1],
    k[2] * east[0] - k[0] * east[2],
    k[0] * east[1] - k[1] * east[0],
  ];

  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const pts = vecs.map((v, i) => {
    const w = dot(v, k) || 1e-6;
    return {
      x: -dot(v, east) / w,                 // east to the left
      y: -dot(v, north) / w,                // north up (canvas y grows down)
      mag: c.stars[i][2],
      name: c.stars[i][3],
    };
  });

  const xs = pts.map((p) => p.x);
  const ys = pts.map((p) => p.y);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) || 1;

  return {
    name: c.name,
    lines: c.lines,
    pts: pts.map((p) => ({ ...p, x: (p.x - cx) / span, y: (p.y - cy) / span })),
  };
}
