// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The writing systems the headline scramble passes through, oldest first: a
// short history of people writing the world down, ending in the code we write
// for machines. Characters render in public/fonts/gaia-scripts.woff2, a subset
// of the Noto fonts holding exactly these characters (see global.css); the Ice
// Age signs predate writing and are drawn as strokes.

export interface ScriptGlyph {
  /** A character, or for the Ice Age signs an SVG drawn on a 48x48 grid. */
  char?: string;
  svg?: string;
  means: string;
}

export interface WritingSystem {
  id: string;
  /** Sort key: year, negative for BCE. */
  year: number;
  when: string;
  who: string;
  meaning: string;
  glyphs: ScriptGlyph[];
}

const line = (x1: number, y1: number, x2: number, y2: number) => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
const path = (d: string) => `<path d="${d}"/>`;
const dot = (cx: number, cy: number) => `<circle cx="${cx}" cy="${cy}" r="2.4" fill="currentColor" stroke="none"/>`;
const sign = (inner: string) =>
  `<svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;

export const WRITING_SYSTEMS: WritingSystem[] = [
  {
    id: 'ice',
    year: -38000,
    when: '~40,000–10,000 BCE',
    who: 'Ice Age signs, European caves',
    meaning: 'The same few geometric signs recur in cave after cave for 30,000 years. They may be the roots of symbolic communication, before writing.',
    glyphs: [
      { svg: sign(path('M6 30 L14 18 L22 30 L30 18 L38 30 L42 24')), means: 'zigzag' },
      { svg: sign(line(24, 6, 24, 42) + [12, 20, 28, 36].map((y) => line(24, y, 16, y - 6) + line(24, y, 32, y - 6)).join('')), means: 'penniform (feather)' },
      { svg: sign(line(24, 8, 24, 40) + line(10, 20, 38, 20)), means: 'cruciform' },
      { svg: sign(line(24, 8, 24, 40) + line(10, 16, 38, 32) + line(10, 32, 38, 16)), means: 'asterisk' },
      { svg: sign(path('M8 34 L24 12 L40 34') + line(8, 34, 40, 34) + line(24, 12, 24, 34) + line(16, 23, 32, 23)), means: 'tectiform (roof)' },
      { svg: sign(line(24, 26, 24, 42) + line(24, 26, 12, 10) + line(24, 26, 36, 10)), means: 'Y-shape' },
      { svg: sign([10, 18, 26, 34].map((x) => dot(x + 2, 24)).join('')), means: 'line of dots' },
      { svg: sign(path('M10 36 L24 12 L38 36')), means: 'open angle' },
    ],
  },
  {
    id: 'cuneiform',
    year: -3200,
    when: '~3200 BCE',
    who: 'Sumerian cuneiform',
    meaning: 'The first full writing system, pressed into clay with a reed.',
    glyphs: [
      { char: '𒀭', means: 'AN / DINGIR: god, heaven' },
      { char: '𒆠', means: 'KI: earth' },
      { char: '𒂗', means: 'EN: lord' },
      { char: '𒈗', means: 'LUGAL: king' },
      { char: '𒁾', means: 'DUB: clay tablet' },
      { char: '𒈨', means: 'ME: the divine powers of civilization' },
      { char: '𒍣', means: 'ZI: life, breath' },
      { char: '𒌓', means: 'UD: sun, day' },
    ],
  },
  {
    id: 'egyptian',
    year: -3150,
    when: '~3200 BCE',
    who: 'Egyptian hieroglyphs',
    meaning: '“The god’s words”: writing as a sacred act.',
    glyphs: [
      { char: '𓊹', means: 'netjer: god' },
      { char: '𓋹', means: 'ankh: life' },
      { char: '𓂀', means: 'wedjat: Eye of Horus, wholeness' },
      { char: '𓆣', means: 'kheper: scarab, to become' },
      { char: '𓆄', means: 'feather of Ma’at: truth' },
      { char: '𓊽', means: 'djed: stability' },
      { char: '𓇳', means: 'ra: sun' },
      { char: '𓏞', means: 'scribe’s palette: writing' },
      { char: '𓀭', means: 'seated god' },
      { char: '𓅓', means: 'owl: the sound m' },
      { char: '𓂋', means: 'mouth: the sound r' },
      { char: '𓈖', means: 'water: the sound n' },
    ],
  },
  {
    id: 'lineara',
    year: -1800,
    when: '~1800 BCE',
    who: 'Linear A, Minoan Crete',
    meaning: 'Still undeciphered: a human script nobody alive can read.',
    glyphs: [
      { char: '𐘀', means: 'sign AB001' },
      { char: '𐘚', means: 'sign AB028' },
      { char: '𐙁', means: 'sign AB080' },
      { char: '𐙕', means: 'sign A301' },
      { char: '𐘋', means: 'sign AB013' },
    ],
  },
  {
    id: 'linearb',
    year: -1450,
    when: '~1450 BCE',
    who: 'Linear B, Mycenaean Greece',
    meaning: 'The oldest written Greek, deciphered in 1952.',
    glyphs: [
      { char: '𐀀', means: 'syllable a' },
      { char: '𐀁', means: 'syllable e' },
      { char: '𐀂', means: 'syllable i' },
      { char: '𐀃', means: 'syllable o' },
      { char: '𐂀', means: 'ideogram: man' },
    ],
  },
  {
    id: 'chinese',
    year: -1250,
    when: '~1250 BCE',
    who: 'Chinese, from the oracle bones',
    meaning: 'Unbroken in use for over 3,000 years (modern forms shown).',
    glyphs: [
      { char: '天', means: 'tiān: heaven' },
      { char: '神', means: 'shén: spirit, god' },
      { char: '智', means: 'zhì: wisdom' },
      { char: '心', means: 'xīn: heart-mind' },
      { char: '文', means: 'wén: writing, culture' },
      { char: '人', means: 'rén: person' },
    ],
  },
  {
    id: 'phoenician',
    year: -1050,
    when: '~1050 BCE',
    who: 'Phoenician',
    meaning: 'The first alphabet: the ancestor of Greek, Latin, Hebrew and Arabic letters.',
    glyphs: [
      { char: '𐤀', means: 'alef: ox' },
      { char: '𐤄', means: 'he' },
      { char: '𐤅', means: 'waw: hook' },
      { char: '𐤏', means: 'ayin: eye' },
      { char: '𐤔', means: 'shin: tooth' },
      { char: '𐤕', means: 'taw: mark' },
      { char: '𐤒', means: 'qof' },
    ],
  },
  {
    id: 'greek',
    year: -800,
    when: '~800 BCE',
    who: 'Greek',
    meaning: 'The alphabet of philosophy, logic and science.',
    glyphs: [
      { char: 'Ψ', means: 'psi: psyche, mind' },
      { char: 'Θ', means: 'theta: theos, god' },
      { char: 'Λ', means: 'lambda: logos, reason' },
      { char: 'Ω', means: 'omega' },
      { char: 'Σ', means: 'sigma' },
      { char: 'Φ', means: 'phi: the golden ratio' },
      { char: 'Ξ', means: 'xi' },
    ],
  },
  {
    id: 'oldpersian',
    year: -520,
    when: '~520 BCE',
    who: 'Old Persian cuneiform',
    meaning: 'Darius’s inscriptions, written in a new cuneiform alphabet.',
    glyphs: [
      { char: '𐏈', means: 'Ahuramazda: “Lord Wisdom”' },
      { char: '𐏎', means: 'baga: god' },
      { char: '𐏋', means: 'xšāyaθiya: king' },
      { char: '𐎠', means: 'a' },
      { char: '𐏌', means: 'dahyāuš: land' },
    ],
  },
  {
    id: 'aramaic',
    year: -500,
    when: '~500 BCE',
    who: 'Imperial Aramaic',
    meaning: 'The language of empire, ancestor of Hebrew square script and Arabic.',
    glyphs: [
      { char: '𐡀', means: 'aleph' },
      { char: '𐡔', means: 'shin' },
      { char: '𐡕', means: 'taw' },
    ],
  },
  {
    id: 'tifinagh',
    year: -300,
    when: '~300 BCE',
    who: 'Libyco-Berber / Tifinagh',
    meaning: 'An ancient North African script, still written today.',
    glyphs: [
      { char: 'ⵣ', means: 'yaz: the free person' },
      { char: 'ⴰ', means: 'ya' },
      { char: 'ⵙ', means: 'yas' },
      { char: 'ⵔ', means: 'yar' },
      { char: 'ⵜ', means: 'yat' },
    ],
  },
  {
    id: 'brahmi',
    year: -260,
    when: '~260 BCE',
    who: 'Brahmi, Ashoka’s India',
    meaning: 'Mother of most scripts of South and Southeast Asia.',
    glyphs: [
      { char: '𑀅', means: 'a' },
      { char: '𑀓', means: 'ka' },
      { char: '𑀫', means: 'ma' },
      { char: '𑀬', means: 'ya' },
    ],
  },
  {
    id: 'runes',
    year: 150,
    when: '~150 CE',
    who: 'Elder Futhark runes',
    meaning: 'Germanic letters carved in wood and stone.',
    glyphs: [
      { char: 'ᚨ', means: 'ansuz: a god, Odin' },
      { char: 'ᛗ', means: 'mannaz: human' },
      { char: 'ᛞ', means: 'dagaz: dawn' },
      { char: 'ᛉ', means: 'algiz: protection' },
      { char: 'ᛟ', means: 'othala: heritage' },
      { char: 'ᛜ', means: 'ingwaz' },
      { char: 'ᚠ', means: 'fehu: wealth' },
    ],
  },
  {
    id: 'geez',
    year: 330,
    when: '~330 CE',
    who: 'Ge’ez, Aksum (Ethiopia)',
    meaning: 'An African script in continuous use for 1,700 years.',
    glyphs: [
      { char: 'ሀ', means: 'hä' },
      { char: 'ለ', means: 'lä' },
      { char: 'ቀ', means: 'qä' },
      { char: 'አ', means: 'ʾä' },
    ],
  },
  {
    id: 'ogham',
    year: 400,
    when: '~400 CE',
    who: 'Ogham, Ireland',
    meaning: 'Strokes cut along the edge of a standing stone; letters named after trees.',
    glyphs: [
      { char: 'ᚁ', means: 'beith: birch' },
      { char: 'ᚇ', means: 'dair: oak' },
      { char: 'ᚋ', means: 'muin: vine' },
      { char: 'ᚐ', means: 'ailm: pine' },
      { char: 'ᚎ', means: 'straif' },
    ],
  },
  {
    id: 'arabic',
    year: 600,
    when: '~600 CE',
    who: 'Arabic (later Ottoman Turkish)',
    meaning: 'The script of the Islamic Golden Age, and of the Ottoman court.',
    glyphs: [
      { char: 'ع', means: 'ʿayn: eye, source' },
      { char: 'ق', means: 'qāf' },
      { char: 'ن', means: 'nūn' },
      { char: 'ح', means: 'ḥāʾ' },
      { char: 'ك', means: 'kāf' },
    ],
  },
  {
    id: 'oldturkic',
    year: 720,
    when: '~720 CE',
    who: 'Old Turkic (Orkhon)',
    meaning: 'Carved on the Orkhon steles of the Göktürks.',
    glyphs: [
      { char: '𐰀', means: 'a' },
      { char: '𐰉', means: 'ab' },
      { char: '𐰢', means: 'em' },
      { char: '𐰸', means: 'oq' },
      { char: '𐱁', means: 'esh' },
    ],
  },
  {
    id: 'glagolitic',
    year: 862,
    when: '~862 CE',
    who: 'Glagolitic, Byzantine mission to the Slavs',
    meaning: 'Invented by Saints Cyril and Methodius; the letters have names.',
    glyphs: [
      { char: 'Ⰰ', means: 'azъ: I' },
      { char: 'Ⱄ', means: 'slovo: word' },
      { char: 'Ⰴ', means: 'dobro: good' },
      { char: 'Ⰶ', means: 'živěte: live' },
      { char: 'Ⰿ', means: 'myslite: think' },
      { char: 'Ⱋ', means: 'šta' },
    ],
  },
  {
    id: 'code',
    year: 1950,
    when: 'Today',
    who: 'Code: the languages we write for machines',
    meaning: 'From the first programs to AI. The original scramble’s own characters.',
    glyphs: [
      { char: '{', means: 'brace' },
      { char: '}', means: 'brace' },
      { char: '<', means: 'angle' },
      { char: '/', means: 'slash' },
      { char: '=', means: 'equals' },
      { char: '#', means: 'hash' },
      { char: '0', means: 'zero' },
      { char: '1', means: 'one' },
      { char: 'λ', means: 'lambda: functions' },
      { char: '∑', means: 'sum' },
      { char: '∇', means: 'gradient: how AI learns' },
    ],
  },
];
