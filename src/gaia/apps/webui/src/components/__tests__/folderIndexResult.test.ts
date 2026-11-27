// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { describeSkippedFiles } from '../folderIndexResult';
import { isolate, stripIsolates } from '../../i18n/registry';
import { translator } from '../../i18n/useT';

const en = translator('en').bare;
const ar = translator('ar').bare;

describe('describeSkippedFiles', () => {
    it('says nothing when no files were skipped', () => {
        expect(describeSkippedFiles(en, {})).toEqual([]);
    });

    it('groups names by reason before showing the truncation note', () => {
        const parts = describeSkippedFiles(en, {
            skipped_files: [
                { file: 'program.exe', reason: 'unsupported_type' },
                { file: 'secrets.json', reason: 'credential_file' },
                { file: 'song.mp3', reason: 'unsupported_type' },
            ],
            skipped_files_truncated: true,
        }).map(stripIsolates);

        expect(parts).toEqual([
            "Files GAIA can't add: program.exe, song.mp3",
            "Credential files GAIA won't add: secrets.json",
            'Showing the first 50 skipped files.',
        ]);
    });

    it('caps a combined status and keeps the server truncation flag', () => {
        const files = Array.from({ length: 51 }, (_, i) => ({
            file: `skip-${i}.bin`,
            reason: 'unsupported_type',
        }));
        const parts = describeSkippedFiles(en, { skipped_files: files }).map(stripIsolates);

        expect(parts).toHaveLength(2);
        expect(parts[0]).toContain('skip-0.bin');
        expect(parts[0]).not.toContain('skip-50.bin');
        expect(parts[1]).toBe('Showing the first 50 skipped files.');
    });

    it('uses the translated reason labels in Arabic', () => {
        const parts = describeSkippedFiles(ar, {
            skipped_files: [
                { file: 'ملف عليها.exe', reason: 'unsupported_type' },
                { file: 'credentials.json', reason: 'credential_file' },
            ],
        }).map(stripIsolates);

        expect(parts).toEqual([
            'الملفات التي لا يستطيع GAIA إضافتها: ملف عليها.exe',
            'ملفات بيانات الدخول التي لن يضيفها GAIA: credentials.json',
        ]);
    });

    it('keeps an Arabic filename in its own island in English', () => {
        const label = describeSkippedFiles(en, {
            skipped_files: [{ file: 'ملاحظات.exe', reason: 'unsupported_type' }],
        })[0];

        expect(stripIsolates(label)).toBe("Files GAIA can't add: ملاحظات.exe");
        expect(label).toContain(isolate('ملاحظات.exe', 'ltr'));
    });
});
