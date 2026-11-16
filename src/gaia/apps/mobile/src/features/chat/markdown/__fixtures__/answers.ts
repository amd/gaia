// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Sample answers for the Markdown gallery and its test; fixture content, not UI copy.

import type { LibraryDoc } from '@webui/utils/citations';

export const LIBRARY: LibraryDoc[] = [
    { id: 'doc-1', filename: 'quarterly-report.pdf' },
    { id: 'doc-2', filename: 'notes.md' },
];

export const ENGLISH = `# Setting up the project

Here is a **short plan** with some *emphasis*, ~~a dropped idea~~ and \`inline_code()\`.

## Steps

1. Install the dependencies
2. Run the tests
   - unit first
   - then integration
3. Ship it

- [x] Write the plan
- [ ] Review it

> Measure twice, cut once.

---

See [the docs](https://amd-gaia.ai/docs) or [do not click](javascript:alert(1)). The logs are in C:\\Users\\me\\.gaia\\logs\\app.log.`;

export const ARABIC = `# إعداد المشروع

هذه **خطة قصيرة** مع *تأكيد* و\`inline_code()\`.

1. ثبّت الاعتمادات
2. شغّل الاختبارات
   - اختبارات الوحدة أولاً
   - ثم التكامل

> قِس مرتين واقطع مرة واحدة.

| الاسم | الحالة |
| --- | --- |
| الاختبارات | ناجحة |
| البناء | قيد التنفيذ |`;

export const MIXED = `هذه الإجابة بالعربية، لكنها تذكر الأمر \`npm run build\` والملف C:\\Projects\\gaia\\package.json داخل النص.

This paragraph is English and sits inside an Arabic app: ثم كلمة عربية في المنتصف.

- عنصر عربي
- An English item
- عنصر آخر`;

const LONG_CODE = Array.from({ length: 40 }, (_, i) => `    const value${i} = compute(${i}, 'a fairly long argument string to force sideways scrolling in the block');`).join('\n');

export const CODE_TABLE_CITATIONS = `According to quarterly-report.pdf, pages 12 and 14, revenue grew. The notes in \`notes.md\`، page 3 say more.

\`\`\`ts
function compute(n: number, label: string) {
  return n * label.length;
}
${LONG_CODE}
\`\`\`

| Region | Q1 | Q2 | Comment |
| --- | ---: | ---: | --- |
| North | 120 | 135 | Steady growth across every product line in the region |
| South | 98 | 101 | Flat |
| West | 143 | 160 | Strongest quarter on record |`;

export const STREAMING = `Here is the start of an answer that is still being written. The first sentence is done, and the second one is`;
