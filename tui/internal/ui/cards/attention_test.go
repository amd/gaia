package cards

import (
	"os"
	"strings"
	"testing"
)

const populatedAttention = `{
  "kind": "email_attention",
  "items": [
    {"kind": "meeting_request", "message_id": "m1", "thread_id": "t1", "sender": "Sarah Chen <sarah@corp.example>", "subject": "Team sync", "why": "looks like it's proposing a meeting or a time to talk"},
    {"kind": "waiting_on_you", "message_id": "m2", "thread_id": "t2", "sender": "alice@example.com", "subject": "Re: budget", "why": "waiting 3d on your reply"},
    {"kind": "needs_review", "message_id": "m3", "thread_id": "t3", "sender": "bob@example.com", "subject": "Random note", "why": "the heuristic was not confident about this message's category"},
    {"kind": "action_item", "message_id": "m4", "sender": "", "subject": "Send the Q3 report", "why": "open action item from a previous triage", "due_hint": "Friday"}
  ],
  "coverage": {"scanned": 42, "total_unread": 100, "scan_truncated": false, "degraded": false, "mailbox_errors": null},
  "generated_at": "2026-07-28T12:00:00+00:00",
  "cache_age_seconds": 0.0,
  "stale": false
}`

const emptyHonestAttention = `{
  "kind": "email_attention",
  "items": [],
  "coverage": {"scanned": 200, "total_unread": 512, "scan_truncated": true, "degraded": false, "mailbox_errors": null},
  "generated_at": "2026-07-28T12:00:00+00:00",
  "cache_age_seconds": 0.0,
  "stale": false
}`

const staleAttention = `{
  "kind": "email_attention",
  "items": [
    {"kind": "meeting_request", "message_id": "m1", "sender": "Sarah Chen <sarah@corp.example>", "subject": "Team sync", "why": "looks like it's proposing a meeting or a time to talk"}
  ],
  "coverage": {"scanned": 10, "total_unread": 20, "scan_truncated": false, "degraded": false, "mailbox_errors": null},
  "generated_at": "2026-07-28T10:00:00+00:00",
  "cache_age_seconds": 612.0,
  "stale": true
}`

const freshCacheAttention = `{
  "kind": "email_attention",
  "items": [],
  "coverage": {"scanned": 5, "total_unread": 5, "scan_truncated": false, "degraded": false, "mailbox_errors": null},
  "generated_at": "2026-07-28T10:00:00+00:00",
  "cache_age_seconds": 45.0,
  "stale": false
}`

const partialFailureAttention = `{
  "kind": "email_attention",
  "items": [
    {"kind": "meeting_request", "message_id": "m1", "sender": "Sarah Chen <sarah@corp.example>", "subject": "Team sync", "why": "looks like it's proposing a meeting or a time to talk", "mailbox": "google"}
  ],
  "coverage": {"scanned": 12, "total_unread": null, "scan_truncated": false, "degraded": true, "mailbox_errors": [{"mailbox": "microsoft", "error": "token expired — reconnect Outlook"}]},
  "generated_at": "2026-07-28T12:00:00+00:00",
  "cache_age_seconds": 0.0,
  "stale": false
}`

func TestAttentionPopulated(t *testing.T) {
	out := Render("email_attention", raw(t, populatedAttention), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out,
		"Attention · 42 scanned",
		"MEETING PROPOSALS", "WAITING ON YOU", "NEEDS REVIEW", "ACTION ITEMS",
		"Sarah Chen", "Team sync",
		"looks like it's proposing a meeting or a time to talk",
		"waiting 3d on your reply",
		"Send the Q3 report",
		"due Friday",
		// Coverage footer under a populated card -- never presented as
		// whole-mailbox complete.
		"42 of 100 unread scanned",
	)
}

func TestAttentionEmptyStateIsHonest(t *testing.T) {
	out := Render("email_attention", raw(t, emptyHonestAttention), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out,
		"Nothing needs you.",
		// Must state what was covered, including the truncation admission --
		// never an unqualified whole-mailbox claim (#2584 lesson, one layer up).
		"200 of 512 unread scanned",
		"of the 200 most recent",
		"older mail may",
	)
	assertNotContains(t, out, "MEETING PROPOSALS", "WAITING ON YOU", "NEEDS REVIEW", "ACTION ITEMS")
}

func TestAttentionStaleIsLabelled(t *testing.T) {
	out := Render("email_attention", raw(t, staleAttention), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out,
		"10m ago",
		"last refresh attempt failed",
		"Team sync",
	)
}

func TestAttentionFreshCacheGetsPlainAgeNote(t *testing.T) {
	// Within the freshness window (stale=false) but still served from cache
	// (cache_age_seconds > 0) -- must still show its age, just without the
	// stronger failed-refresh warning.
	out := Render("email_attention", raw(t, freshCacheAttention), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out, "45s ago")
	assertNotContains(t, out, "last refresh attempt failed")
}

func TestAttentionZeroAgeShowsNoStalenessNote(t *testing.T) {
	out := Render("email_attention", raw(t, populatedAttention), width80)
	assertNotContains(t, plain(out), "ago)")
}

func TestAttentionPartialFailureSurfacesConnectorError(t *testing.T) {
	out := Render("email_attention", raw(t, partialFailureAttention), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out,
		"Outlook wasn't scanned",
		"token expired",
		"Results below are unaffected",
		// The surviving mailbox's item is still shown, tagged with its source.
		"Gmail", "Team sync",
	)
}

func TestAttentionMalformedPayloadDegradesVisibly(t *testing.T) {
	out := Render("email_attention", raw(t, `{"kind": "email_attention", "items": "not a list"}`), width80)
	t.Logf("\n%s", plain(out))
	assertContains(t, out, "email_attention")
}

func TestAttentionNullPayloadIsNotRenderedAsEmptySuccess(t *testing.T) {
	out := Render("email_attention", raw(t, `null`), width80)
	t.Logf("\n%s", plain(out))
	assertNotContains(t, out, "Nothing needs you.")
}

func TestAttentionWrongKindDegradesVisibly(t *testing.T) {
	out := Render("email_attention", raw(t, `{"kind": "email_pre_scan", "items": [], "coverage": {}, "generated_at": "x"}`), width80)
	t.Logf("\n%s", plain(out))
	assertContains(t, out, "email_attention")
}

func TestAttentionCapsSectionAtMaxRows(t *testing.T) {
	items := ""
	for i := 0; i < maxAttentionSectionRows+3; i++ {
		if i > 0 {
			items += ","
		}
		items += `{"kind": "needs_review", "message_id": "m` + itoa(i) + `", "sender": "a@example.com", "subject": "msg", "why": "unconfident"}`
	}
	payload := `{"kind": "email_attention", "items": [` + items + `], ` +
		`"coverage": {"scanned": 20, "total_unread": 20, "scan_truncated": false, "degraded": false}, ` +
		`"generated_at": "x", "cache_age_seconds": 0.0, "stale": false}`

	out := Render("email_attention", raw(t, payload), width80)
	t.Logf("\n%s", plain(out))
	assertContains(t, out, "+3 more")
}

func TestAttentionRendererReferencesNoMutatingCall(t *testing.T) {
	// Structural guard mirroring the waiting_on_you_tools convention
	// (test_module_references_no_send_path): the renderer only draws what
	// the server already computed -- it must never itself reference an
	// archive/send/label/draft call. The real "no mutation happened"
	// evidence is the Python aggregator's transport-call assertion
	// (hub/agents/email/python/tests/test_attention_tools.py); this guards
	// the client side from ever growing an action affordance.
	src, err := os.ReadFile("attention.go")
	if err != nil {
		t.Fatalf("could not read attention.go: %v", err)
	}
	forbidden := []string{
		"archive_message", "send_message", "send_draft", "create_draft",
		"trash_message", "label_message", "unarchive", "quarantine",
	}
	for _, word := range forbidden {
		if strings.Contains(string(src), word) {
			t.Errorf("attention.go references %q -- the attention view must stay read-only (no action affordance)", word)
		}
	}
}
