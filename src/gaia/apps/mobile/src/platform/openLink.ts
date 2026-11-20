// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import * as WebBrowser from 'expo-web-browser';
import { Alert, Linking, Platform } from 'react-native';

import { deviceLocaleCode, translator } from '@/i18n/useT';
import { useLocaleStore } from '@/state/locale';

import { checkLink } from './linkPolicy';

export interface OpenLinkOptions {
    /** Opens a web link in the in-app browser instead of the phone's own. */
    inApp?: boolean;
}

/** Opens a link from content (a reply, a source, the server) only when the policy allows it; anything else shows the blocked notice and opens nothing. Resolves false when blocked. */
export async function openLink(raw: string, options: OpenLinkOptions = {}): Promise<boolean> {
    const verdict = checkLink(raw);
    if (!verdict.allowed) {
        console.error(`Refused to open a link (${verdict.scheme ?? 'no scheme'}): ${raw.slice(0, 300)}`);
        const t = translator(useLocaleStore.getState().locale ?? deviceLocaleCode());
        const title = t('mobileApp.link.blockedTitle');
        const message = t('mobileApp.link.blocked', { url: raw.slice(0, 300), scheme: verdict.scheme ?? t('mobileApp.link.noScheme') });
        // react-native-web's Alert shows nothing.
        if (Platform.OS === 'web') globalThis.alert(`${title}\n\n${message}`);
        else Alert.alert(title, message);
        return false;
    }
    if (options.inApp && !/^mailto:/i.test(verdict.url)) await WebBrowser.openBrowserAsync(verdict.url);
    else await Linking.openURL(verdict.url);
    return true;
}

/** `openLink` for a press handler: a failure to open is logged, as the call sites did before. */
export function openLinkFromPress(raw: string, options?: OpenLinkOptions): void {
    openLink(raw, options).catch((error: unknown) => console.error('Opening a link failed', error));
}
