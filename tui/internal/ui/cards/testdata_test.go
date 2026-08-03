package cards

import (
	"encoding/json"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"
)

// The fixtures below are built from pre_scan_inbox_impl's documented envelope
// (hub/agents/email/python/gaia_agent_email/tools/read_tools.py) and its TS
// mirror (hub/agents/email/npm/src/types.ts, EmailPreScanResult): kind,
// scanned, needs_you, needs_you_total, bulk, preferences_applied — plus the
// two SSE-only supersets from merge_pre_scan_backends: per-item `mailbox`
// and top-level `mailbox_errors`. #2743 replaced the four raw buckets
// (urgent/actionable/needs_review/suggested_archives) with needs_you, a
// server-built VIEW over them; these fixtures only populate needs_you, the
// one thing the client renders from.

const populatedPreScan = `{
  "kind": "email_pre_scan",
  "urgent": [], "actionable": [], "informational_count": 6,
  "suggested_archives": [], "suggested_drafts": [], "needs_review": [],
  "scanned": 15,
  "needs_you": [
    {"ref":1,"kind":"urgent","message_id":"m1","thread_id":"t1","sender":"\"Sarah Chen\" <sarah.chen@example.com>",
     "subject":"Prod incident follow-up","why":"asked for a reply by Friday"},
    {"ref":2,"kind":"urgent","message_id":"m2","thread_id":"t2","sender":"billing@vendorco.com",
     "subject":"Invoice 4471 past due","why":"payment date has passed"},
    {"ref":3,"kind":"needs_response","message_id":"m3","thread_id":"t3","sender":"Marcus Webb <marcus@example.org>",
     "subject":"Re: Q3 roadmap review","why":"direct question to you"},
    {"ref":4,"kind":"needs_response","message_id":"m4","thread_id":"t4","sender":"recruiting@acme.io",
     "subject":"Interview times for Thursday","why":"asked you to pick a slot"},
    {"ref":5,"kind":"needs_response","message_id":"m5","thread_id":"t5","sender":"\"Priya N.\" <priya@example.net>",
     "subject":"Re: contract redlines","why":"waiting on your sign-off"}
  ],
  "needs_you_total": 5,
  "bulk": {"count": 4, "filter_tests": ["no_direct_question"]},
  "preferences_applied": {
    "priority_senders": ["Sarah Chen", "Priya N."],
    "low_priority_senders": [],
    "category_defaults": {}
  }
}`

// capsHitPreScan: needs_you is at its server-side cap of 5 while
// needs_you_total reports the real pre-cap count, so the header must read
// "5 of 40".
const capsHitPreScan = `{
  "kind": "email_pre_scan",
  "urgent": [], "actionable": [], "informational_count": 4,
  "suggested_archives": [], "suggested_drafts": [], "needs_review": [],
  "scanned": 40,
  "needs_you": [
    {"ref":1,"kind":"urgent","message_id":"u1","sender":"a@x.com","subject":"one","why":"r1"},
    {"ref":2,"kind":"urgent","message_id":"u2","sender":"b@x.com","subject":"two","why":"r2"},
    {"ref":3,"kind":"urgent","message_id":"u3","sender":"c@x.com","subject":"three","why":"r3"},
    {"ref":4,"kind":"urgent","message_id":"u4","sender":"d@x.com","subject":"four","why":"r4"},
    {"ref":5,"kind":"urgent","message_id":"u5","sender":"e@x.com","subject":"five","why":"r5"}
  ],
  "needs_you_total": 40,
  "bulk": {"count": 1, "filter_tests": ["no_direct_question"]},
  "preferences_applied": null
}`

// emptyPreScan: the "nothing needs you" state — only bulk.count is set.
const emptyPreScan = `{
  "kind": "email_pre_scan",
  "urgent": [], "actionable": [], "informational_count": 19,
  "suggested_archives": [], "suggested_drafts": [], "needs_review": [],
  "scanned": 19,
  "needs_you": [],
  "needs_you_total": 0,
  "bulk": {"count": 19, "filter_tests": ["no_deadline_signal"]},
  "preferences_applied": {"priority_senders": [], "low_priority_senders": [], "category_defaults": {}}
}`

// mailboxErrorsPreScan: multi-account scan where one grant is broken. Items
// carry the SSE-only `mailbox` tag and the envelope carries `mailbox_errors`.
const mailboxErrorsPreScan = `{
  "kind": "email_pre_scan",
  "urgent": [], "actionable": [], "informational_count": 3,
  "suggested_archives": [], "suggested_drafts": [], "needs_review": [],
  "scanned": 5,
  "needs_you": [
    {"ref":1,"kind":"urgent","message_id":"m1","sender":"Sarah Chen <sarah@example.com>","subject":"Prod incident",
     "why":"asked for a reply by Friday","mailbox":"google"},
    {"ref":2,"kind":"needs_response","message_id":"m2","sender":"ops@corp.example","subject":"Change window tonight",
     "why":"needs sign-off today","mailbox":"microsoft"}
  ],
  "needs_you_total": 2,
  "bulk": {"count": 3, "filter_tests": []},
  "preferences_applied": null,
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

func atoi(t *testing.T, s string) int {
	t.Helper()
	n := 0
	for _, r := range strings.TrimSpace(s) {
		if r < '0' || r > '9' {
			t.Fatalf("not a number: %q", s)
		}
		n = n*10 + int(r-'0')
	}
	return n
}

// rowNumberPattern matches a rendered card's numbered-row gutter once a
// line's border and leading whitespace are trimmed away: digits, then AT
// LEAST TWO spaces, then the sender column (box.row: " NN  sender  subject").
// The coverage/footer line (coverageLine's "N inbox messages scanned...")
// also starts with digits, but has exactly ONE space after them, so it does
// not match -- this is what makes the pattern usable to tell a real row from
// the footer, instead of a substring check that cannot (#2631 reflection C4).
var rowNumberPattern = regexp.MustCompile(`^(\d+)\s{2,}\S`)

// rowNumbers scrapes every numbered row's index out of a rendered card, in
// render order, so a test can assert on the real sequence (e.g. with
// reflect.DeepEqual) instead of a substring check that cannot tell
// [1,2,3,4] apart from [1,1,2,4] (#2631 reflection C4).
func rowNumbers(t *testing.T, rendered string) []int {
	t.Helper()
	var got []int
	for _, line := range strings.Split(plain(rendered), "\n") {
		body := strings.TrimSpace(strings.Trim(line, "│"))
		m := rowNumberPattern.FindStringSubmatch(body)
		if m == nil {
			continue
		}
		n, err := strconv.Atoi(m[1])
		if err != nil {
			t.Fatalf("row number %q did not parse: %v", m[1], err)
		}
		got = append(got, n)
	}
	return got
}
