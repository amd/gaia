// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const moduleNameMapper = {
    '^@/(.*)$': '<rootDir>/src/$1',
    '^@webui/(.*)$': '<rootDir>/../webui/src/$1',
};

// The Markdown parser and its helpers ship ESM only, so Babel has to compile them.
const ESM_ONLY = [
    'unified', 'bail', 'trough', 'devlop', 'vfile', 'is-plain-obj', 'remark-', 'micromark', 'mdast-',
    'unist-', 'decode-named-character-reference', 'character-entities', 'ccount', 'escape-string-regexp',
    'markdown-table', 'zwitch', 'longest-streak',
];

const transformIgnorePatterns = [
    `/node_modules/(?!(.pnpm|react-native|@react-native|@react-native-community|expo|@expo|@expo-google-fonts|react-navigation|@react-navigation|@sentry/react-native|native-base|standard-navigation|${ESM_ONLY.join('|')}))`,
    '/node_modules/react-native-reanimated/plugin/',
    '/node_modules/@react-native/babel-preset/',
];

const project = (preset) => ({
    displayName: preset,
    preset: `jest-expo/${preset}`,
    moduleNameMapper,
    // Web UI files imported through @webui resolve their packages here, as Metro does; webui may have no node_modules of its own.
    modulePaths: ['<rootDir>/node_modules'],
    setupFiles: ['<rootDir>/jest.setup.js'],
    transformIgnorePatterns,
    testMatch: ['<rootDir>/src/**/*.test.{ts,tsx}'],
    // Sheets are native-only (no BackHandler or native gesture module on web), and the component test renderer can't mount react-native-web's DOM.
    testPathIgnorePatterns: preset === 'web' ? ['/node_modules/', '/SheetHost\\.test\\.tsx$', '/Markdown\\.test\\.tsx$', '/PermissionPrompt\\.test\\.tsx$', '/QuestionCard\\.test\\.tsx$', '/UserBubble\\.test\\.tsx$', '/useMessageActions\\.test\\.tsx$', '/ChatHeader\\.test\\.tsx$', '/ChatScreen\\.test\\.tsx$', '/ChatPrompts\\.test\\.tsx$', '/SessionPanel\\.test\\.tsx$', '/ContextSection\\.test\\.tsx$', '/MemoryScreen\\.test\\.tsx$', '/DocumentsScreen\\.test\\.tsx$', '/FilePreview\\.test\\.tsx$', '/panes/Model\\.test\\.tsx$', '/SetupFooter\\.test\\.tsx$', '/NpuNotReady\\.test\\.tsx$', '/KeyScreen\\.test\\.tsx$', '/ReadyStep\\.test\\.tsx$', '/SetupGate\\.test\\.tsx$', '/PlanChecklist\\.test\\.tsx$', '/FlagshipAgentBanner\\.test\\.tsx$'] : ['/node_modules/'],
});

module.exports = {
    projects: [project('ios'), project('android'), project('web')],
};
