// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Message, StreamEvent } from '@/api/client.types';

let nextId = 1;
const at = (minutes: number) => new Date(Date.UTC(2026, 10, 13, 14, minutes, 9)).toISOString();
const msg = (role: 'user' | 'assistant', content: string, minutes: number): Message => ({
    id: nextId++,
    session_id: 'dev',
    role,
    content,
    created_at: at(minutes),
    rag_sources: null,
});

const LONG_USER = Array.from(
    { length: 40 },
    (_, i) => `Log line ${i + 1}: the nightly job copied 12 files and skipped 3 that were already there.`,
).join('\n');

const ANSWER = 'Here is what I found.\n\n- The job ran at **02:00**\n- It copied 12 files\n- 3 were skipped because they already existed';
const ARABIC_ANSWER = 'هذا ما وجدته.\n\n- بدأت المهمة في **02:00**\n- نسخت 12 ملفاً\n- تم تخطي 3 لأنها موجودة';

const SHORT = [
    msg('user', 'What did the nightly job do?', 0),
    msg('assistant', ANSWER, 1),
    msg('user', 'ما الذي فعلته المهمة الليلية؟', 2),
    msg('assistant', ARABIC_ANSWER, 3),
];

const MANY = Array.from({ length: 12 }, (_, i) => [
    msg('user', `Question ${i + 1}: how many files did run ${i + 1} copy?`, i * 2),
    msg('assistant', `Run ${i + 1} copied ${i + 4} files.`, i * 2 + 1),
]).flat();

const withCards = (m: Message, cards: NonNullable<Message['cards']>): Message => ({ ...m, cards });

// A card whose payload throws when read, to show the error boundary.
const THROWING = {
    columns: ['Name'],
    get rows(): never {
        throw new Error('fixture: card payload threw');
    },
};

const CARDS = [
    msg('user', 'Summarize last week\'s runs as cards.', 0),
    withCards(msg('assistant', 'Cards first, then the answer text. Run 3 was the slowest.', 1), [
        {
            render: 'table',
            data: {
                title: 'Nightly runs',
                columns: ['Run', 'Started', 'Files copied', 'Skipped', 'Duration (s)', 'Status'],
                rows: [
                    ['Run 1', '2026-11-08 02:00', 12, 3, 41.5, 'ok'],
                    ['Run 2', '2026-11-09 02:00', 15, 0, 38.2, 'ok'],
                    ['Run 3', '2026-11-10 02:00', 1204, 17, 912, 'slow'],
                ],
            },
        },
        {
            render: 'key_value',
            data: {
                title: 'Run 3',
                items: [
                    { key: 'Source', value: 'C:\\Users\\me\\Documents\\Projects\\Quarterly reports 2026\\drafts' },
                    { key: 'Files copied', value: 1204 },
                    { key: 'Notes', value: 'Copied everything again because the index was missing, so the job took far longer than the others.' },
                    { key: 'Verified', value: true },
                    { key: 'Error', value: null },
                ],
            },
        },
        { render: 'list', data: { title: 'Next steps', ordered: true, items: ['Rebuild the index', 'Re-run the job', 'Check the disk'] } },
        { render: 'list', data: { title: 'خطوات تالية', items: ['إعادة بناء الفهرس', 'إعادة تشغيل المهمة'] } },
    ]),
];

const BAD_CARDS = [
    msg('user', 'Show me the broken cards.', 0),
    withCards(msg('assistant', 'The answer text still shows below the cards.', 1), [
        { render: 'table', data: { title: 'Missing its columns', rows: [[1, 2]] } },
        { render: 'chart', data: { series: [1, 2, 3] } },
        { render: 'table', data: THROWING },
        { render: 'list', data: { items: ['Still fine'] } },
    ]),
];

export const GUIDE_WORKOUT = {
    kind: 'guide',
    guideType: 'workout',
    title: '15-minute home workout',
    description: 'No equipment needed.',
    servings: null,
    items: ['A mat or towel', 'Water bottle'],
    steps: [
        'Warm up: Jog in place for 2 minutes, then circle your arms.',
        'Squats: 3 sets of 12, rest 45 seconds between sets.',
        'Plank: 3 sets of 1, hold 30 seconds, rest 20 seconds.',
        'Cool down: Stretch your legs and shoulders for 3 minutes.',
    ],
    notes: 'Stop if anything hurts.',
};

const GUIDE = [
    msg('user', 'Banana bread, a workout, a packing list and two calculators, please.', 0),
    withCards(msg('assistant', 'Everything is a card you can change.', 1), [
        {
            render: 'guide',
            data: {
                kind: 'guide',
                guideType: 'recipe',
                title: 'Banana bread',
                description: 'Moist, simple, and good for ripe bananas.',
                servings: 4,
                items: ['1 1/2 cups all-purpose flour', '2 tbsp butter, melted', '3 ripe bananas', '1/2 cup sugar', '2 large eggs', 'A pinch of salt'],
                steps: [
                    'Heat the oven to 350°F and grease a loaf pan.',
                    'Mash the bananas, then stir in the butter, sugar and eggs.',
                    'Fold in the flour and salt, pour into the pan and bake for 55 minutes.',
                    'Cool in the pan for 10 minutes before slicing.',
                ],
                notes: 'Keeps for three days, wrapped.',
            },
        },
        { render: 'guide', data: GUIDE_WORKOUT },
        {
            render: 'guide',
            data: {
                kind: 'guide',
                guideType: 'checklist',
                title: 'Beach weekend',
                servings: null,
                items: ['Sunscreen', 'Towels', 'Swimsuit', 'Sandals', 'Phone charger'],
                steps: [],
            },
        },
        { render: 'calculator', data: { kind: 'calculator', calculator: 'tip', title: 'Dinner for four', currency: 'USD', inputs: { bill: 140, tipPercent: 20, people: 4 } } },
        { render: 'calculator', data: { kind: 'calculator', calculator: 'loan', currency: 'USD', inputs: { amount: 300000, annualRatePercent: 6, years: 30, downPayment: 60000 } } },
    ]),
];

const GRADIENT_PNG = 'data:image/png;base64,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';

const LONG_DIFF_LINE = 'const summary = buildRunSummary(runs, { includeFailures: true, groupBy: "day", timezone: "UTC", locale: "en-US", maxRows: 500 });';

const MEDIA = [
    msg('user', 'Show the chart image and what changed in the script.', 0),
    withCards(msg('assistant', 'Here is the image and the edit.', 1), [
        { render: 'image', data: { src: GRADIENT_PNG, alt: 'A blue-green gradient', caption: 'Weekly run summary' } },
        { render: 'image', data: { src: 'https://example.com/chart.png', alt: 'Remote chart' } },
        {
            render: 'diff',
            data: {
                badge: 'Edited',
                path: 'scripts/report.py',
                unified: ['--- a/scripts/report.py', '+++ b/scripts/report.py', '@@ -1,4 +1,4 @@', ' def report(runs):', '-    return len(runs)', '+    return sum(r.copied for r in runs)', ' ', '@@ -20,2 +20,2 @@', ' print(report(runs))', '-print("done")', '+print("finished")'].join('\n'),
            },
        },
        {
            render: 'diff',
            data: { title: 'summary.ts', path: 'src/summary.ts', unified: ['@@ -1,2 +1,2 @@', '-const summary = buildRunSummary(runs);', `+${LONG_DIFF_LINE}`].join('\n') },
        },
        { render: 'diff', data: { badge: 'Created', path: 'assets/logo.png', note: 'Binary file, no text diff to show.', unified: '' } },
    ]),
];

export type MessagesFixture = 'badCards' | 'cards' | 'guide' | 'loading' | 'long' | 'many' | 'media' | 'recording' | 'short' | 'streaming';

export const FIXTURES: Record<MessagesFixture, { messages: Message[]; live?: Message; events?: StreamEvent[] }> = {
    badCards: { messages: BAD_CARDS },
    cards: { messages: CARDS },
    guide: { messages: GUIDE },
    loading: { messages: [] },
    long: { messages: [...SHORT.slice(0, 2), msg('user', LONG_USER, 4), msg('assistant', ANSWER, 5)] },
    many: { messages: MANY },
    media: { messages: MEDIA },
    recording: {
        messages: [msg('user', 'Summarize the team meeting recording.', 0)],
        events: [
            { type: 'status', phase: 'recording', stage: 1, state: 1 },
            { type: 'status', phase: 'recording', stage: 2, state: 0, percent: 62, eta_seconds: 540 },
            {
                type: 'status', phase: 'recording_found', section: 1, sections: 4, start: '00:00', end: '07:30',
                summary: 'Planning for the November release.',
                items: [{ title: 'Ship the phone app by Friday', start: '02:10', seconds: 130 }],
            },
        ],
    },
    short: { messages: SHORT },
    streaming: {
        messages: SHORT,
        live: { ...msg('assistant', 'Counting the files in', 6), id: -1 },
    },
};
