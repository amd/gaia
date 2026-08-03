package cards

import (
	"encoding/json"
	"strings"
	"testing"
)

// The width a card gets inside an 80-column terminal: chat passes m.width-4.
const width80 = 76

func TestPreScanPopulated(t *testing.T) {
	out := Render("email_pre_scan", raw(t, populatedPreScan), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out,
		// Title carries the top-level `scanned` field.
		"Inbox · 15 scanned",
		// Section is one worklist, not four buckets (#2743).
		"NEEDS YOU",
		// Verb labels, mapped from kind -- REPLY covers urgent/waiting_on_you/needs_response.
		"REPLY",
		// Display name is extracted from the raw From header.
		"Sarah Chen", "Prod incident follow-up",
		// A bare address has no display name and stays as-is.
		"billing@vendorco.com",
		// Every row carries its rationale.
		"asked for a reply by Friday", "payment date has passed",
		"waiting on your sign-off",
		// Rows are numbered by the SERVER's own ref, never recomputed.
		" 1  ", " 2  ", " 3  ", " 5  ",
		"4 filtered", "promotional",
		"Using your priority senders: Sarah Chen, Priya N.",
	)
}

func TestPreScanEmptyState(t *testing.T) {
	out := Render("email_pre_scan", raw(t, emptyPreScan), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out,
		"Nothing needs you.",
		"19 inbox messages scanned",
		"19 filtered",
		"FYI",
	)
	// No worklist header for a genuinely empty scan.
	assertNotContains(t, out, "NEEDS YOU")
}

func TestPreScanCapsHitShowsNofM(t *testing.T) {
	out := Render("email_pre_scan", raw(t, capsHitPreScan), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	// needs_you is capped at 5 server-side while needs_you_total (40)
	// reports the true pre-cap count -- the header must read "5 of 40"
	// rather than a bare count that implies the list is everything.
	assertContains(t, out, "NEEDS YOU", "5 of 40")
}

func TestPreScanUncappedShowsBareCount(t *testing.T) {
	// needs_you_total (5) matches len(needs_you) (5) -- nothing hidden, so
	// the header must NOT read "5 of 5", which reads as a truncation that
	// isn't one.
	out := Render("email_pre_scan", raw(t, populatedPreScan), width80)
	assertNotContains(t, out, "5 of 5")
	assertNotContains(t, out, "+0 more")
}

func TestPreScanMailboxErrorsBanner(t *testing.T) {
	out := Render("email_pre_scan", raw(t, mailboxErrorsPreScan), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out,
		// The broken grant is a warning banner, not a failed card.
		"[!] Outlook wasn't scanned: token expired",
		"Results below are unaffected.",
		// Results that DID arrive are still shown.
		"NEEDS YOU", "Sarah Chen", "Prod incident",
		// Rows are tagged with their account, because more than one is in play.
		"Gmail · Sarah Chen", "Outlook ·",
	)
}

func TestPreScanSingleMailboxOmitsTag(t *testing.T) {
	// One account: the mailbox tag is noise, so it is not drawn.
	out := Render("email_pre_scan", raw(t, populatedPreScan), width80)
	assertNotContains(t, out, "Gmail ·", "Outlook ·")
}

func TestPreScanMissingNeedsYouTotalFallsBackToListLength(t *testing.T) {
	var envelope map[string]any
	if err := json.Unmarshal([]byte(populatedPreScan), &envelope); err != nil {
		t.Fatal(err)
	}
	delete(envelope, "needs_you_total")
	data, err := json.Marshal(envelope)
	if err != nil {
		t.Fatal(err)
	}

	out := Render("email_pre_scan", data, width80)
	assertWidth(t, out, width80)
	// A missing needs_you_total decodes as its zero value (0), which is not
	// greater than the 5 shown -- so the header falls back to a bare count
	// rather than claiming a hidden tail that was never reported.
	assertNotContains(t, out, " of ")
	assertContains(t, out, "NEEDS YOU", "Sarah Chen")
}

func TestPreScanInvalidPayload(t *testing.T) {
	// `needs_you` is an object where the schema says array — a
	// schema-invalid payload must say so and dump the data, per contract §7.
	bad := raw(t, `{"kind":"email_pre_scan","needs_you":{"nope":1}}`)
	out := Render("email_pre_scan", bad, width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out, "Invalid card", "Invalid email_pre_scan payload", "raw data:", "nope")
}

func TestPreScanWrongKindIsInvalid(t *testing.T) {
	out := Render("email_pre_scan", raw(t, `{"kind":"something_else","needs_you":[]}`), width80)
	assertContains(t, out, "Invalid email_pre_scan payload", "kind is something_else")
}

func TestPreScanAt80x24(t *testing.T) {
	// The whole point of the bound: an 80x24 terminal has 24 rows total, of
	// which the header, dividers, input and status bar take 6. A card that
	// cannot fit the remainder is a scroll trap, not a card.
	out := Render("email_pre_scan", raw(t, populatedPreScan), width80)
	assertWidth(t, out, width80)

	lines := strings.Split(plain(out), "\n")
	if len(lines) > 24 {
		t.Errorf("card is %d lines; must stay within a 24-row terminal", len(lines))
	}
}

func TestPreScanDegradesAtNarrowWidth(t *testing.T) {
	// Narrow terminals truncate; they never break the frame.
	for _, w := range []int{20, 24, 32, 40, 60, 76, 120} {
		out := Render("email_pre_scan", raw(t, populatedPreScan), w)
		assertWidth(t, out, w)
		if !strings.Contains(plain(out), "NEEDS YOU") {
			t.Errorf("width %d dropped the NEEDS YOU section:\n%s", w, plain(out))
		}
	}
}

// ---------------------------------------------------------------------------
// needs_review (#2584) -- folded into needs_you as its own kind (#2743).
// These fixtures are defined inline (not in testdata_test.go) so they stay
// scoped to this one file.
// ---------------------------------------------------------------------------

const needsReviewPopulatedPreScan = `{
  "kind": "email_pre_scan",
  "urgent": [], "actionable": [], "informational_count": 2,
  "suggested_archives": [], "suggested_drafts": [], "needs_review": [],
  "scanned": 4,
  "needs_you": [
    {"ref":1,"kind":"needs_response","message_id":"a1","thread_id":"ta1","sender":"boss@example.com",
     "subject":"Q3 numbers","why":"direct question"},
    {"ref":2,"kind":"needs_review","message_id":"nr1","thread_id":"tnr1","sender":"colleague@example.com",
     "subject":"Any chance to meet this Thursday at 9am?","why":"heuristic unconfident (no match)"}
  ],
  "needs_you_total": 2,
  "bulk": {"count": 0, "filter_tests": []},
  "preferences_applied": null
}`

// needsReviewOnlyPreScan: needs_you holds only a needs_review-kind item --
// must NOT render as "Nothing needs you" just because there's no urgent/
// actionable signal.
const needsReviewOnlyPreScan = `{
  "kind": "email_pre_scan",
  "urgent": [], "actionable": [], "informational_count": 0,
  "suggested_archives": [], "suggested_drafts": [], "needs_review": [],
  "scanned": 1,
  "needs_you": [
    {"ref":1,"kind":"needs_review","message_id":"nr1","thread_id":"tnr1","sender":"colleague@example.com",
     "subject":"Any chance to meet this Thursday at 9am?","why":"heuristic unconfident"}
  ],
  "needs_you_total": 1,
  "bulk": {"count": 0, "filter_tests": []},
  "preferences_applied": null
}`

func TestPreScanNeedsReviewRendersWithCheckVerb(t *testing.T) {
	out := Render("email_pre_scan", raw(t, needsReviewPopulatedPreScan), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out, "Inbox · 4 scanned")
	// A needs_review row renders under the CHECK verb, with its own
	// sender/subject/why -- distinct provenance from a category bucket.
	// The subject is truncated at this width by the sender/subject column
	// split (box.rowWithPrefix); check a prefix that survives it.
	assertContains(t, out,
		"CHECK",
		"colleague@example.com",
		"Any chance to meet this",
		"heuristic unconfident (no match)",
	)
}

func TestPreScanNeedsReviewOnlyIsNotEmptyState(t *testing.T) {
	out := Render("email_pre_scan", raw(t, needsReviewOnlyPreScan), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	// A needs_review-only pre-scan still needs the user's attention -- it
	// must never render as "Nothing needs you".
	assertNotContains(t, out, "Nothing needs you.")
}

// ---------------------------------------------------------------------------
// #2631 -- RenderDeduped's seen threading, now over the single needs_you
// list rather than four separate buckets.
// ---------------------------------------------------------------------------

const preScanTwoItemsForDedup = `{
  "kind": "email_pre_scan",
  "urgent": [], "actionable": [], "informational_count": 0,
  "suggested_archives": [], "suggested_drafts": [], "needs_review": [],
  "scanned": 2,
  "needs_you": [
    {"ref":1,"kind":"urgent","message_id":"u1","sender":"a@x.com","subject":"UrgentDup","why":"r1"},
    {"ref":2,"kind":"needs_response","message_id":"a1","sender":"b@x.com","subject":"ActionableUnique","why":"r2"}
  ],
  "needs_you_total": 2,
  "bulk": {"count": 0, "filter_tests": []},
  "preferences_applied": null
}`

func TestPreScanRenderDedupedDropsSeenItem(t *testing.T) {
	seen := map[string]bool{"u1": true}
	out, ids := RenderDeduped("email_pre_scan", raw(t, preScanTwoItemsForDedup), width80, seen)
	t.Logf("\n%s", plain(out))

	assertNotContains(t, out, "UrgentDup")
	assertContains(t, out, "ActionableUnique")

	if len(ids) != 1 || ids[0] != "a1" {
		t.Errorf(`returned ids = %v, want exactly ["a1"] -- u1 was already seen and must not be re-added`, ids)
	}
}

func TestPreScanRenderDedupedSuppressesWholeCardWhenEverythingIsSeen(t *testing.T) {
	seen := map[string]bool{"u1": true, "a1": true}
	out, ids := RenderDeduped("email_pre_scan", raw(t, preScanTwoItemsForDedup), width80, seen)
	if out != "" {
		t.Errorf("card rendered even though every item was already seen:\n%s", plain(out))
	}
	if len(ids) != 0 {
		t.Errorf("ids = %v, want none", ids)
	}
}

func TestDisplaySender(t *testing.T) {
	for _, tc := range []struct{ in, want string }{
		{`"Sarah Chen" <sarah@example.com>`, "Sarah Chen"},
		{`Marcus Webb <marcus@example.org>`, "Marcus Webb"},
		{`<solo@example.com>`, "solo@example.com"},
		{`billing@vendorco.com`, "billing@vendorco.com"},
		{``, "(unknown sender)"},
		{`   `, "(unknown sender)"},
	} {
		if got := displaySender(tc.in); got != tc.want {
			t.Errorf("displaySender(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestVerbForKind(t *testing.T) {
	for _, tc := range []struct{ kind, want string }{
		{"urgent", "REPLY"},
		{"waiting_on_you", "REPLY"},
		{"needs_response", "REPLY"},
		{"meeting_request", "DECIDE"},
		{"needs_review", "CHECK"},
		{"action_item", "DO"},
		{"some_future_kind", "REVIEW"},
	} {
		if got := verbForKind(tc.kind); got != tc.want {
			t.Errorf("verbForKind(%q) = %q, want %q", tc.kind, got, tc.want)
		}
	}
}

func TestFilterTestLabelDegradesVisiblyWhenUnmapped(t *testing.T) {
	if got := filterTestLabel("category_fyi"); got != "FYI" {
		t.Errorf("filterTestLabel(category_fyi) = %q, want FYI", got)
	}
	// An id this client predates must still show something, not vanish.
	if got := filterTestLabel("some_future_filter_test"); got != "some_future_filter_test" {
		t.Errorf("filterTestLabel(unmapped) = %q, want the raw id back", got)
	}
}
