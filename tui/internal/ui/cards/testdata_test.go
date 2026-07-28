package cards

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"
)

// The fixtures below are built from pre_scan_inbox_impl's documented envelope
// (hub/agents/email/python/gaia_agent_email/tools/read_tools.py) and its TS
// mirror (hub/agents/email/npm/src/types.ts, EmailPreScanResult): kind, urgent,
// actionable, informational_count, suggested_archives, suggested_drafts (always
// empty, reserved), preferences_applied, totals — plus the two SSE-only
// supersets from merge_pre_scan_backends: per-item `mailbox` and top-level
// `mailbox_errors`.

const populatedPreScan = `{
  "kind": "email_pre_scan",
  "urgent": [
    {"message_id":"m1","thread_id":"t1","sender":"\"Sarah Chen\" <sarah.chen@example.com>",
     "subject":"Prod incident follow-up","why":"asked for a reply by Friday"},
    {"message_id":"m2","thread_id":"t2","sender":"billing@vendorco.com",
     "subject":"Invoice 4471 past due","why":"payment date has passed"}
  ],
  "actionable": [
    {"message_id":"m3","thread_id":"t3","sender":"Marcus Webb <marcus@example.org>",
     "subject":"Re: Q3 roadmap review","why":"direct question to you"},
    {"message_id":"m4","thread_id":"t4","sender":"recruiting@acme.io",
     "subject":"Interview times for Thursday","why":"asked you to pick a slot"},
    {"message_id":"m5","thread_id":"t5","sender":"\"Priya N.\" <priya@example.net>",
     "subject":"Re: contract redlines","why":"waiting on your sign-off"}
  ],
  "informational_count": 6,
  "suggested_archives": [
    {"message_id":"m6","thread_id":"t6","sender":"news@substack.com",
     "subject":"Weekly digest #212","reason":"newsletter"},
    {"message_id":"m7","thread_id":"t7","sender":"offers@retailer.com",
     "subject":"48-hour sale","reason":"promotional"}
  ],
  "suggested_drafts": [],
  "preferences_applied": {
    "priority_senders": ["Sarah Chen", "Priya N."],
    "low_priority_senders": [],
    "category_defaults": {}
  },
  "totals": {"urgent": 2, "actionable": 5, "informational": 6, "suggested_archives": 2}
}`

// capsHitPreScan: every list is at its agent-side cap while `totals` reports the
// real pre-cap counts, so each header must read "N of M".
const capsHitPreScan = `{
  "kind": "email_pre_scan",
  "urgent": [
    {"message_id":"u1","sender":"a@x.com","subject":"one","why":"r1"},
    {"message_id":"u2","sender":"b@x.com","subject":"two","why":"r2"},
    {"message_id":"u3","sender":"c@x.com","subject":"three","why":"r3"},
    {"message_id":"u4","sender":"d@x.com","subject":"four","why":"r4"},
    {"message_id":"u5","sender":"e@x.com","subject":"five","why":"r5"}
  ],
  "actionable": [
    {"message_id":"a1","sender":"f@x.com","subject":"six","why":"r6"},
    {"message_id":"a2","sender":"g@x.com","subject":"seven","why":"r7"},
    {"message_id":"a3","sender":"h@x.com","subject":"eight","why":"r8"},
    {"message_id":"a4","sender":"i@x.com","subject":"nine","why":"r9"},
    {"message_id":"a5","sender":"j@x.com","subject":"ten","why":"r10"}
  ],
  "informational_count": 4,
  "suggested_archives": [
    {"message_id":"s1","sender":"k@x.com","subject":"promo","reason":"promotional"}
  ],
  "suggested_drafts": [],
  "preferences_applied": null,
  "totals": {"urgent": 9, "actionable": 17, "informational": 4, "suggested_archives": 31}
}`

// emptyPreScan: the "nothing needs you" state — only informational_count is set.
const emptyPreScan = `{
  "kind": "email_pre_scan",
  "urgent": [],
  "actionable": [],
  "informational_count": 19,
  "suggested_archives": [],
  "suggested_drafts": [],
  "preferences_applied": {"priority_senders": [], "low_priority_senders": [], "category_defaults": {}},
  "totals": {"urgent": 0, "actionable": 0, "informational": 19, "suggested_archives": 0}
}`

// mailboxErrorsPreScan: multi-account scan where one grant is broken. Items carry
// the SSE-only `mailbox` tag and the envelope carries `mailbox_errors`.
const mailboxErrorsPreScan = `{
  "kind": "email_pre_scan",
  "urgent": [
    {"message_id":"m1","sender":"Sarah Chen <sarah@example.com>","subject":"Prod incident",
     "why":"asked for a reply by Friday","mailbox":"google"},
    {"message_id":"m2","sender":"ops@corp.example","subject":"Change window tonight",
     "why":"needs sign-off today","mailbox":"microsoft"}
  ],
  "actionable": [],
  "informational_count": 3,
  "suggested_archives": [],
  "suggested_drafts": [],
  "preferences_applied": null,
  "totals": {"urgent": 2, "actionable": 0, "informational": 3, "suggested_archives": 0},
  "mailbox_errors": [{"mailbox":"microsoft","error":"token expired — reconnect Outlook"}]
}`

// plain strips styling so assertions compare the text a user reads, not escapes.
func plain(s string) string { return ansi.Strip(s) }

// assertWidth fails unless every rendered line occupies exactly want columns.
// A card that overflows by one column shears every border below it.
func assertWidth(t *testing.T, rendered string, want int) {
	t.Helper()
	for i, line := range strings.Split(plain(rendered), "\n") {
		if got := ansi.StringWidth(line); got != want {
			t.Errorf("line %d width = %d, want %d: %q", i, got, want, line)
		}
	}
}

func assertContains(t *testing.T, rendered string, wants ...string) {
	t.Helper()
	got := plain(rendered)
	for _, w := range wants {
		if !strings.Contains(got, w) {
			t.Errorf("rendered output missing %q\n---\n%s\n---", w, got)
		}
	}
}

func assertNotContains(t *testing.T, rendered string, unwanted ...string) {
	t.Helper()
	got := plain(rendered)
	for _, u := range unwanted {
		if strings.Contains(got, u) {
			t.Errorf("rendered output unexpectedly contains %q\n---\n%s\n---", u, got)
		}
	}
}

func raw(t *testing.T, s string) json.RawMessage {
	t.Helper()
	if !json.Valid([]byte(s)) {
		t.Fatalf("fixture is not valid JSON: %s", s)
	}
	return json.RawMessage(s)
}
