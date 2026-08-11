package cards

import (
	"os"
	"reflect"
	"regexp"
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

// #2716 -- a message-level rate-limit, distinct from a mailbox-level one:
// mailbox_errors is empty/absent, only message_errors is populated.
const messageRateLimitedAttention = `{
  "kind": "email_attention",
  "items": [
    {"kind": "meeting_request", "message_id": "m1", "sender": "Sarah Chen <sarah@corp.example>", "subject": "Team sync", "why": "looks like it's proposing a meeting or a time to talk"}
  ],
  "coverage": {"scanned": 12, "total_unread": null, "scan_truncated": false, "degraded": true, "mailbox_errors": null, "message_errors": [{"message_id": "m9", "error": "Gmail rate-limited this message after exhausting retries. Try again in a minute."}]},
  "generated_at": "2026-07-28T12:00:00+00:00",
  "cache_age_seconds": 0.0,
  "stale": false
}`

func TestAttentionPopulated(t *testing.T) {
	out := Render("email_attention", raw(t, populatedAttention), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out,
		"Needs you · 42 inbox messages scanned",
		"MEETING PROPOSALS", "WAITING ON YOU", "NEEDS REVIEW", "ACTION ITEMS",
		"Sarah Chen", "Team sync",
		"looks like it's proposing a meeting or a time to talk",
		"waiting 3d on your reply",
		"Send the Q3 report",
		"due Friday",
		// Coverage footer under a populated card -- never presented as
		// whole-mailbox complete. The unit is named (#2635): "42" is how many
		// messages were scanned, "100" is a separately-labelled unread count.
		"42 inbox messages scanned",
		"100 unread",
	)
}

func TestAttentionTitleStatesItsPurpose(t *testing.T) {
	// The bare "Attention · N scanned" title is a tautology: it already
	// contains a number and the word "scanned" before any fix, so a loose
	// substring check would pass even without the rename actually happening
	// (#2631 reflection C7). Assert the full new string, and that the old
	// prefix is gone.
	out := Render("email_attention", raw(t, populatedAttention), width80)
	assertContains(t, out, "Needs you · 42 inbox messages scanned")
	assertNotContains(t, out, "Attention ·")
}

func TestAttentionEmptyStateIsHonest(t *testing.T) {
	out := Render("email_attention", raw(t, emptyHonestAttention), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out,
		"Nothing needs you.",
		// Must state what was covered, including the truncation admission --
		// never an unqualified whole-mailbox claim (#2584 lesson, one layer up).
		// The unit is named (#2635): "200" is how many were scanned, "512" is
		// a separately-labelled unread count -- not "200 of 512 unread".
		"200 inbox messages scanned",
		"512 unread",
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

func TestAttentionMessageRateLimitSurfacesAsCountNotMailboxError(t *testing.T) {
	out := Render("email_attention", raw(t, messageRateLimitedAttention), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out,
		"1 message couldn't be fetched (rate-limited)",
		"try again in a",
		"Results below are unaffected",
		// The surviving item is still shown.
		"Team sync",
	)
	assertNotContains(t, plain(out), "wasn't scanned")
	assertNotContains(t, plain(out), "accounts weren't scanned")
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

// ---------------------------------------------------------------------------
// #2635 -- coverage units. `unread` may only qualify a genuinely-unread
// number; the numerator's unit (inbox messages scanned, which can include
// read mail) must always be named. Fixtures follow the issue's own table.
// ---------------------------------------------------------------------------

const coverage100Of557Attention = `{
  "kind": "email_attention",
  "items": [],
  "coverage": {"scanned": 100, "total_unread": 557, "scan_truncated": false, "degraded": false, "mailbox_errors": null},
  "generated_at": "x",
  "cache_age_seconds": 0.0,
  "stale": false
}`

const coverage100NoTotalUnreadAttention = `{
  "kind": "email_attention",
  "items": [],
  "coverage": {"scanned": 100, "total_unread": null, "scan_truncated": false, "degraded": false, "mailbox_errors": null},
  "generated_at": "x",
  "cache_age_seconds": 0.0,
  "stale": false
}`

const coverageZeroScannedAttention = `{
  "kind": "email_attention",
  "items": [],
  "coverage": {"scanned": 0, "total_unread": 557, "scan_truncated": false, "degraded": false, "mailbox_errors": null},
  "generated_at": "x",
  "cache_age_seconds": 0.0,
  "stale": false
}`

func TestCoverageLineNamesTheScannedUnit(t *testing.T) {
	out := Render("email_attention", raw(t, coverage100Of557Attention), width80)
	t.Logf("\n%s", plain(out))

	assertContains(t, out, "100 inbox messages scanned", "557 unread")
	assertNotContains(t, out, "100 of 557 unread scanned")
	if scannedDirectlyModifiedByUnread(t, out, 100) {
		t.Errorf("the Scanned count is directly described as \"unread\" -- it can include read mail:\n%s", plain(out))
	}
}

func TestCoverageLineOmitsUnreadWhenTotalUnreadUnknown(t *testing.T) {
	// Outlook's connector does not report a total-unread count -- rather than
	// invent one, the line must simply not mention "unread" at all.
	out := Render("email_attention", raw(t, coverage100NoTotalUnreadAttention), width80)
	t.Logf("\n%s", plain(out))

	assertContains(t, out, "100 inbox messages scanned")
	assertNotContains(t, out, "unread")
}

func TestCoverageLineZeroScannedStaysHonest(t *testing.T) {
	// Scanned: 0 goes through the empty-state branch (renderEmpty), which
	// still calls coverageLine() -- the banned phrase from the issue itself
	// ("no mail needs you") does not exist anywhere in the code (renderEmpty
	// emits "Nothing needs you."), so asserting its absence would never be
	// able to fail. Assert the concrete old defect string instead
	// (#2631 reflection C5).
	out := Render("email_attention", raw(t, coverageZeroScannedAttention), width80)
	t.Logf("\n%s", plain(out))

	assertContains(t, out, "0 inbox messages scanned", "Nothing needs you.")
	assertNotContains(t, out, "0 of 557 unread scanned")
}

// scannedDirectlyModifiedByUnread reports whether "unread" describes the
// Scanned count itself -- directly, or through "of <N> " -- rather than a
// genuinely separate unread number. A fix that merely reorders the old
// defect ("557 unread, 100 scanned") must still pass; one that keeps
// "100 of 557 unread" or introduces "100 unread" must not (#2631 reflection A3).
func scannedDirectlyModifiedByUnread(t *testing.T, rendered string, scanned int) bool {
	t.Helper()
	re := regexp.MustCompile(itoa(scanned) + `\s+(of\s+\d+\s+)?unread`)
	return re.MatchString(plain(rendered))
}

// ---------------------------------------------------------------------------
// #2631 -- row numbers must run continuously across every section, including
// the unknown-kind OTHER section, never restarting at 1 per section.
// ---------------------------------------------------------------------------

// multiSectionAttentionForNumbering has one item in each of the four known
// sections plus one of an unrecognised kind, which must still render under
// OTHER (attention.go:116-118) and inherit the running counter rather than
// restart it (#2631 reflection A1).
const multiSectionAttentionForNumbering = `{
  "kind": "email_attention",
  "items": [
    {"kind": "meeting_request", "message_id": "m1", "sender": "a@example.com", "subject": "Meeting proposal", "why": "x"},
    {"kind": "waiting_on_you", "message_id": "m2", "sender": "b@example.com", "subject": "Waiting reply", "why": "x"},
    {"kind": "needs_review", "message_id": "m3", "sender": "c@example.com", "subject": "Review this", "why": "x"},
    {"kind": "action_item", "message_id": "m4", "sender": "d@example.com", "subject": "Do this thing", "why": "x"},
    {"kind": "some_future_kind", "message_id": "m5", "sender": "e@example.com", "subject": "Unknown kind item", "why": "x"}
  ],
  "coverage": {"scanned": 5, "total_unread": 5, "scan_truncated": false, "degraded": false, "mailbox_errors": null},
  "generated_at": "x",
  "cache_age_seconds": 0.0,
  "stale": false
}`

func TestAttentionRowNumbersRunContinuouslyAcrossAllSections(t *testing.T) {
	out := Render("email_attention", raw(t, multiSectionAttentionForNumbering), width80)
	t.Logf("\n%s", plain(out))

	got := rowNumbers(t, out)
	want := []int{1, 2, 3, 4, 5}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("row numbers = %v, want %v -- numbering must run continuously across every section, including OTHER", got, want)
	}
	// The unknown kind must still render, under a generic header, rather
	// than being silently dropped.
	assertContains(t, out, "OTHER", "Unknown kind item")
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
