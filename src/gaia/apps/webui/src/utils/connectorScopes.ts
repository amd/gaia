// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { MessageKey } from '../i18n/registry';
import type { Translate } from '../i18n/translator';
import type { ConnectorRow } from '../types';

// Human-readable labels for well-known OAuth scope URIs.
// Unrecognised scopes fall back to the last path segment of the URI.
const SCOPE_LABELS: Record<string, MessageKey> = {
    'https://www.googleapis.com/auth/gmail.readonly': 'settings.connectors.scope.gmailRead',
    'https://www.googleapis.com/auth/gmail.modify': 'settings.connectors.scope.gmailModify',
    'https://www.googleapis.com/auth/gmail.send': 'settings.connectors.scope.gmailSend',
    'https://www.googleapis.com/auth/gmail.compose': 'settings.connectors.scope.gmailCompose',
    'https://www.googleapis.com/auth/calendar.readonly': 'settings.connectors.scope.calendarRead',
    'https://www.googleapis.com/auth/calendar.events': 'settings.connectors.scope.calendarEvents',
    'https://www.googleapis.com/auth/drive.readonly': 'settings.connectors.scope.driveRead',
    'https://www.googleapis.com/auth/drive.file': 'settings.connectors.scope.driveFile',
    'https://www.googleapis.com/auth/spreadsheets.readonly': 'settings.connectors.scope.sheetsRead',
    'https://www.googleapis.com/auth/spreadsheets': 'settings.connectors.scope.sheets',
    'openid': 'settings.connectors.scope.openid',
    'https://www.googleapis.com/auth/userinfo.email': 'settings.connectors.scope.email',
    'email': 'settings.connectors.scope.email',
    'profile': 'settings.connectors.scope.profile',
    'https://graph.microsoft.com/Mail.Read': 'settings.connectors.scope.gmailRead',
    'https://graph.microsoft.com/Mail.ReadWrite': 'settings.connectors.scope.outlookMail',
    'https://graph.microsoft.com/Mail.Send': 'settings.connectors.scope.outlookSend',
    'https://graph.microsoft.com/Calendars.ReadWrite': 'settings.connectors.scope.outlookCalendar',
    'https://graph.microsoft.com/Calendars.Read': 'settings.connectors.scope.outlookCalendarRead',
    'https://graph.microsoft.com/User.Read': 'settings.connectors.scope.microsoftProfile',
};

/**
 * What Connect asks for. With agents to grant, nothing: the backend asks for
 * what they declare, not the connector's whole list (Drive, mail.modify).
 */
export function connectScopes(
    connector: Pick<ConnectorRow, 'available_scopes' | 'default_scopes'>,
    grantAgents: string[],
): string[] {
    if (grantAgents.length > 0) return [];
    return connector.available_scopes?.length ? connector.available_scopes : connector.default_scopes;
}

export function scopeLabel(scope: string, t: Translate): string {
    const key = SCOPE_LABELS[scope];
    return key ? t(key) : scope.split(/[/.:]/).pop() ?? scope;
}
