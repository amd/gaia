// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Each row names the account its message came from, only when the card mixes accounts.

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { EmailPreScanCard, type PreScanItem, type PreScanPayload } from '../email/EmailPreScanCard';

const item = (id: string, account?: string): PreScanItem => ({ message_id: id, sender: 'Sam <sam@example.com>', subject: `Note ${id}`, why: 'asked you', account });
const payload = (items: PreScanItem[]): PreScanPayload => ({
    kind: 'email_pre_scan', urgent: items, actionable: [], informational_count: 0, suggested_archives: [], suggested_drafts: [],
});

describe('pre-scan rows with several accounts', () => {
    it('labels each row with its account, left to right', () => {
        render(<EmailPreScanCard payload={payload([item('1', 'jane@example.com'), item('2', 'jane.work@example.com')])} />);
        expect(screen.getByText('jane@example.com')).toHaveAttribute('dir', 'ltr');
        expect(screen.getByText('jane.work@example.com')).toBeInTheDocument();
    });

    it('adds nothing when every row is from one account', () => {
        render(<EmailPreScanCard payload={payload([item('1', 'jane@example.com'), item('2', 'jane@example.com')])} />);
        expect(screen.queryByText('jane@example.com')).not.toBeInTheDocument();
    });
});
