// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package phone

import (
	"errors"
	"strings"
)

// Rules in the order the panel lists them.
var Rules = []string{"schedule_failed", "approval_waiting", "gaia_down", "agent"}

// Steps in the order a rule cycles through them.
var Steps = []string{"notify", "notify_text", "notify_text_call"}

// RuleLabel names a rule for a person.
func RuleLabel(rule string) string {
	switch rule {
	case "schedule_failed":
		return "A scheduled job fails"
	case "approval_waiting":
		return "A task waits for approval"
	case "gaia_down":
		return "GAIA can't run"
	case "agent":
		return "Agents may alert me"
	}
	return rule
}

// StepsLabel says what a rule does.
func StepsLabel(steps string) string {
	switch steps {
	case "notify":
		return "notify"
	case "notify_text":
		return "notify, then text"
	case "notify_text_call":
		return "notify, text, then call"
	}
	return steps
}

// ProviderLabel names the provider.
func ProviderLabel(provider string) string {
	if provider == "twilio" {
		return "Twilio"
	}
	return "Simulated phone"
}

// ErrorText is what a failed phone call shows: the service's own sentence
// (it is written for people), or a fixed one by code when it sent none.
func ErrorText(err error) string {
	var pe *Error
	if errors.As(err, &pe) {
		if pe.Message != "" {
			return pe.Message
		}
		return CodeText(pe.Code)
	}
	if errors.Is(err, ErrUnavailable) {
		return "Phone alerts aren't in the GAIA background service on this PC yet. They arrive when GAIA updates."
	}
	return err.Error()
}

// CodeText is the fixed sentence for an error code.
func CodeText(code string) string {
	switch code {
	case "bad_number":
		return "That number can't be used."
	case "not_in_phone_book":
		return "That number isn't in the phone book."
	case "not_configured":
		return "Choose a provider and add your number first."
	case "phone_off":
		return "Phone is off."
	case "bad_credentials":
		return "Twilio rejected the Account SID or Auth Token. Copy both again from the Twilio console."
	case "provider_error":
		return "The phone provider refused the last call or text."
	case "provider_unreachable":
		return "Couldn't reach the phone provider."
	case "trial_unverified":
		return "Verify this number in the Twilio console too; the account is a trial."
	case "verification_failed":
		return "That code isn't right."
	case "verification_expired":
		return "The code expired. Start again."
	case "rate_limited":
		return "Too many code requests. Try again in an hour."
	case "limit_reached":
		return "The daily limit is reached."
	case "last_number":
		return "Turn phone off before removing the last number."
	case "duplicate_number":
		return "That number is already in the phone book."
	case "not_found":
		return "That isn't there any more."
	}
	return "Phone alerts hit a problem (" + strings.ReplaceAll(code, "_", " ") + ")."
}

// HeldText says why an alert is waiting.
func HeldText(held string) string {
	switch held {
	case "":
		return ""
	case "quiet_hours":
		return "Held for quiet hours"
	case "text_limit":
		return "Not texted: daily limit reached"
	case "call_limit":
		return "Not called: daily limit reached"
	case "agent_call_limit":
		return "Not called: agents' daily limit reached"
	case "no_answer":
		return "Nobody answered"
	}
	return "Held: " + strings.ReplaceAll(held, "_", " ")
}

// TalkStatus says whether a call can talk with GAIA, or what is missing.
func TalkStatus(s Settings) (ready bool, text string) {
	switch {
	case !s.TalkOnCalls:
		return false, "Talking on calls is off"
	case !s.PinSet:
		return false, "Set a PIN"
	case !s.TalkAvailable:
		return false, "Turn on mobile access so calls can reach this PC"
	}
	return true, "Ready: calls can talk"
}

// OutcomeText says how a call or text ended, in words.
func OutcomeText(outcome string, reached bool) string {
	switch {
	case reached || outcome == "human":
		return "answered"
	case outcome == "machine_start" || strings.HasPrefix(outcome, "machine"):
		return "voicemail"
	case outcome == "no-answer":
		return "no answer"
	}
	return strings.NewReplacer("_", " ", "-", " ").Replace(outcome)
}

// AwakeText says whether the PC stays awake for calls.
func AwakeText(a *Awake) string {
	if a == nil {
		return "off"
	}
	if a.Held {
		return "keeping this PC awake"
	}
	switch a.Reason {
	case "battery":
		return "not on battery: calls only while this PC is awake"
	case "unsupported":
		return "can't keep this PC awake: calls only while it is"
	}
	return "off"
}

// Mask keeps the last four digits of a number.
func Mask(number string) string {
	digits := 0
	for _, r := range number {
		if r >= '0' && r <= '9' {
			digits++
		}
	}
	if digits <= 4 {
		return number
	}
	return "•••" + number[len(number)-4:]
}
