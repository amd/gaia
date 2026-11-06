// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Phone wording by stable code, the Agent UI's twin of the TUI's `phone/text.go`. */

import type { MessageKey } from '../../i18n/registry';
import type { Translate } from '../../i18n/useT';
import type { PhoneDelivery, PhoneFailure, PhoneStatus } from '../../services/phoneApi';

const ALERT_KINDS = ['schedule_failed', 'approval_waiting', 'gaia_down', 'agent', 'test'];
const HELD = ['quiet_hours', 'text_limit', 'call_limit', 'agent_call_limit', 'no_answer'];

/** The translated sentence for a failure; never the raw exception. */
export function phoneErrorText(f: PhoneFailure, t: Translate): string {
    const main = t(`phone.error.${f.code}` as MessageKey);
    if (f.code === 'verification_failed') {
        if (f.triesLeft != null) return `${main} ${t.plural('phone.error.triesLeft', f.triesLeft)}`;
        if (/start again/i.test(f.message)) return `${main} ${t('phone.error.startAgain')}`;
    }
    return main;
}

/** Twilio's own words, worth showing under the translated line for these. */
export function providerDetail(f: PhoneFailure): string | null {
    return ['provider_error', 'provider_unreachable', 'trial_unverified'].includes(f.code) && f.message ? f.message : null;
}

export function alertTitle(kind: string, t: Translate): string {
    return t(ALERT_KINDS.includes(kind) ? (`phone.alert.title.${kind}` as MessageKey) : 'phone.alert.title.other');
}

export function heldText(held: string, t: Translate): string {
    if (!held) return '';
    return t(HELD.includes(held) ? (`phone.held.${held}` as MessageKey) : 'phone.held.other');
}

/** How a call or text ended: answered, voicemail, no answer, and so on. */
export function outcomeText(d: PhoneDelivery, t: Translate): string {
    const outcome = d.outcome ?? '';
    if (d.reached || outcome === 'human') return t('phone.outcome.answered');
    if (outcome.startsWith('machine')) return t('phone.outcome.voicemail');
    if (outcome === 'no-answer') return t('phone.outcome.noAnswer');
    if (outcome === 'busy') return t('phone.outcome.busy');
    if (outcome === 'delivered') return t('phone.outcome.delivered');
    if (d.state === 'failed' || outcome === 'failed' || outcome === 'undelivered') return t('phone.outcome.failed');
    if (d.state === 'placing') return t(d.channel === 'call' ? 'phone.outcome.calling' : 'phone.outcome.sending');
    return t('phone.outcome.sent');
}

export function awakeText(awake: PhoneStatus['awake'], t: Translate): string {
    if (awake?.held) return t('phone.awake.held');
    if (awake?.reason === 'battery') return t('phone.awake.battery');
    if (awake?.reason === 'unsupported') return t('phone.awake.unsupported');
    return t('phone.awake.off');
}
