/*
 * Real star charts — J2000 positions of the bright stars in eleven classical
 * constellations, plus the line figures that join them.
 *
 * Coordinates are right ascension in HOURS (0–24) and declination in DEGREES,
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
    name: 'Cygnus',
    stars: [
      [20.6905, 45.28, 1.25, 'Deneb'],
      [20.3705, 40.257, 2.23, 'Sadr'],
      [20.7702, 33.97, 2.48, 'Gienah'],
      [19.7498, 45.131, 2.87, 'Delta Cyg'],
      [19.512, 27.96, 3.18, 'Albireo'],
    ],
    lines: [[0, 1], [1, 4], [1, 2], [1, 3]],
  },
  {
    name: 'Lyra',
    stars: [
      [18.6156, 38.784, 0.03, 'Vega'],
      [18.7461, 37.605, 4.36, 'Zeta Lyr'],
      [18.9089, 36.899, 4.3, 'Delta Lyr'],
      [18.9824, 32.69, 3.25, 'Sulafat'],
      [18.8347, 33.363, 3.52, 'Sheliak'],
    ],
    lines: [[0, 1], [1, 2], [2, 3], [3, 4], [4, 1]],
  },
  {
    name: 'Scorpius',
    stars: [
      [16.0906, -19.805, 2.62, 'Graffias'],
      [16.0055, -22.622, 2.29, 'Dschubba'],
      [15.9811, -26.114, 2.89, 'Pi Sco'],
      [16.3533, -25.593, 2.89, 'Sigma Sco'],
      [16.4901, -26.432, 1.06, 'Antares'],
      [16.5983, -28.216, 2.82, 'Tau Sco'],
      [16.8361, -34.293, 2.29, 'Epsilon Sco'],
      [17.622, -42.998, 1.86, 'Sargas'],
      [17.5601, -37.104, 1.62, 'Shaula'],
    ],
    lines: [
      [0, 1], [1, 2], [1, 3], [3, 4], [4, 5],
      [5, 6], [6, 7], [7, 8],
    ],
  },
  {
    name: 'Crux',
    stars: [
      [12.4433, -63.099, 0.77, 'Acrux'],
      [12.5194, -57.113, 1.63, 'Gacrux'],
      [12.7953, -59.689, 1.25, 'Mimosa'],
      [12.2525, -58.749, 2.79, 'Delta Cru'],
    ],
    lines: [[0, 1], [2, 3]],
  },
  {
    name: 'Leo',
    stars: [
      [10.1395, 11.967, 1.36, 'Regulus'],
      [10.1222, 16.763, 3.51, 'Eta Leo'],
      [10.3329, 19.841, 2.08, 'Algieba'],
      [9.8797, 26.007, 3.88, 'Mu Leo'],
      [9.7643, 23.774, 2.98, 'Epsilon Leo'],
      [11.2351, 20.524, 2.56, 'Zosma'],
      [11.8177, 14.572, 2.14, 'Denebola'],
      [11.2372, 15.43, 3.33, 'Chertan'],
    ],
    lines: [
      [0, 1], [1, 2], [2, 3], [3, 4], [2, 5],
      [5, 6], [6, 7], [7, 0],
    ],
  },
  {
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
    name: 'Taurus',
    stars: [
      [5.6274, 21.143, 3.0, 'Zeta Tau'],
      [4.5987, 16.509, 0.85, 'Aldebaran'],
      [4.4767, 15.871, 3.4, 'Theta Tau'],
      [4.3299, 15.628, 3.65, 'Gamma Tau'],
      [4.382, 17.542, 3.76, 'Delta Tau'],
      [4.4776, 19.18, 3.53, 'Epsilon Tau'],
      [5.4382, 28.608, 1.65, 'Elnath'],
    ],
    lines: [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [5, 6]],
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
