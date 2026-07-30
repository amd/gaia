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
		// Title carries the pre-cap scan size: 2 + 5 + 6 + 2.
		"Inbox · 15 scanned",
		// Sections are words, not colours (R2 — no colour-only signals).
		"URGENT", "NEEDS A REPLY", "SUGGESTED ARCHIVE",
		// Display name is extracted from the raw From header.
		"Sarah Chen", "Prod incident follow-up",
		// A bare address has no display name and stays as-is.
		"billing@vendorco.com",
		// Every urgent/actionable row carries its rationale.
		"asked for a reply by Friday", "payment date has passed",
		"waiting on your sign-off",
		// Rows are numbered continuously across sections.
		" 1  ", " 2  ", " 3  ", " 7  ",
		"6 informational, not listed.",
		"Using your priority senders: Sarah Chen, Priya N.",
	)
}

func TestPreScanEmptyState(t *testing.T) {
	out := Render("email_pre_scan", raw(t, emptyPreScan), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out,
		"Nothing needs you.",
		"19 messages scanned · 0 urgent · 0 waiting on a reply",
		"19 informational, not listed.",
	)
	// No empty section frames for buckets with nothing in them.
	assertNotContains(t, out, "URGENT", "NEEDS A REPLY", "SUGGESTED ARCHIVE")
}

func TestPreScanCapsHitShowsNofM(t *testing.T) {
	out := Render("email_pre_scan", raw(t, capsHitPreScan), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)

	// `totals` is pre-cap; every list here is short of it. A bare count would
	// imply the list is everything, which is exactly what `totals` exists to
	// prevent — so each header must read "N of M" against the real total, and
	// the trailing "+K more" must agree with it.
	wantTotals := map[string]int{"URGENT": 9, "NEEDS A REPLY": 17, "SUGGESTED ARCHIVE": 31}
	headers := sectionCounts(t, plain(out))
	if len(headers) != len(wantTotals) {
		t.Fatalf("got %d section headers, want %d: %v", len(headers), len(wantTotals), headers)
	}
	for label, total := range wantTotals {
		h, ok := headers[label]
		if !ok {
			t.Fatalf("section %q missing from card:\n%s", label, plain(out))
		}
		if h.total != total {
			t.Errorf("section %q header reads %q; want a total of %d", label, h.text, total)
		}
		if h.shown >= h.total {
			t.Errorf("section %q shows %d of %d — the fixture caps every bucket, so this must be a strict subset", label, h.shown, h.total)
		}
		want := "+" + itoa(h.total-h.shown) + " more"
		if !strings.Contains(plain(out), want) {
			t.Errorf("section %q header says %q but the card never says %q", label, h.text, want)
		}
	}
}

type sectionHeaderCount struct {
	text  string
	shown int
	total int
}

// sectionCounts reads the "N" / "N of M" count off each section header line.
func sectionCounts(t *testing.T, rendered string) map[string]sectionHeaderCount {
	t.Helper()
	out := map[string]sectionHeaderCount{}
	for _, label := range []string{"URGENT", "NEEDS A REPLY", "SUGGESTED ARCHIVE"} {
		for _, line := range strings.Split(rendered, "\n") {
			body := strings.TrimSpace(strings.Trim(line, "│"))
			if !strings.HasPrefix(body, label) {
				continue
			}
			count := strings.TrimSpace(strings.TrimPrefix(body, label))
			h := sectionHeaderCount{text: count}
			if shown, total, ok := strings.Cut(count, " of "); ok {
				h.shown, h.total = atoi(t, shown), atoi(t, total)
			} else {
				h.shown = atoi(t, count)
				h.total = h.shown
			}
			out[label] = h
			break
		}
	}
	return out
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

func TestPreScanUncappedShowsBareCount(t *testing.T) {
	// When totals match the delivered lists there is nothing hidden, so the
	// header must NOT read "2 of 2" — that reads as a truncation that isn't one.
	out := Render("email_pre_scan", raw(t, populatedPreScan), width80)
	if strings.Contains(plain(out), "2 of 2") {
		t.Errorf("uncapped section rendered as %q; want a bare count\n%s", "2 of 2", plain(out))
	}
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
		"URGENT", "Sarah Chen", "Prod incident",
		// Rows are tagged with their account, because more than one is in play.
		"Gmail · asked for a reply by Friday",
		"Outlook · needs sign-off today",
	)
}

func TestPreScanSingleMailboxOmitsTag(t *testing.T) {
	// One account: the mailbox tag is noise, so it is not drawn.
	out := Render("email_pre_scan", raw(t, populatedPreScan), width80)
	assertNotContains(t, out, "Gmail ·", "Outlook ·")
}

func TestPreScanMissingTotalsFallsBackToListLengths(t *testing.T) {
	var envelope map[string]any
	if err := json.Unmarshal([]byte(populatedPreScan), &envelope); err != nil {
		t.Fatal(err)
	}
	delete(envelope, "totals")
	data, err := json.Marshal(envelope)
	if err != nil {
		t.Fatal(err)
	}

	out := Render("email_pre_scan", data, width80)
	assertWidth(t, out, width80)
	// Derived totals equal the visible counts, so no section claims a hidden tail.
	assertNotContains(t, out, " of ", "+")
	assertContains(t, out, "URGENT", "Sarah Chen")
}

func TestPreScanInvalidPayload(t *testing.T) {
	// `urgent` is an object where the schema says array — a schema-invalid
	// payload must say so and dump the data, per contract §7.
	bad := raw(t, `{"kind":"email_pre_scan","urgent":{"nope":1}}`)
	out := Render("email_pre_scan", bad, width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out, "Invalid card", "Invalid email_pre_scan payload", "raw data:", "nope")
}

func TestPreScanWrongKindIsInvalid(t *testing.T) {
	out := Render("email_pre_scan", raw(t, `{"kind":"something_else","urgent":[]}`), width80)
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
		if !strings.Contains(plain(out), "URGENT") {
			t.Errorf("width %d dropped the URGENT section:\n%s", w, plain(out))
		}
	}
}

// ---------------------------------------------------------------------------
// needs_review (#2584) -- the pre-scan coverage-honesty bucket. These fixtures
// are defined inline (not in testdata_test.go) so the Go-side changes for
// this issue stay confined to this one file until emailprescan.go grows a
// NeedsReview field and its own `scanned()`/`isEmpty()` handling.
// ---------------------------------------------------------------------------

const needsReviewPopulatedPreScan = `{
  "kind": "email_pre_scan",
  "urgent": [],
  "actionable": [
    {"message_id":"a1","thread_id":"ta1","sender":"boss@example.com",
     "subject":"Q3 numbers","why":"direct question"}
  ],
  "informational_count": 2,
  "suggested_archives": [],
  "suggested_drafts": [],
  "needs_review": [
    {"message_id":"nr1","thread_id":"tnr1","sender":"colleague@example.com",
     "subject":"Any chance to meet this Thursday at 9am?","why":"heuristic unconfident (no match)"}
  ],
  "preferences_applied": null,
  "totals": {"urgent": 0, "actionable": 1, "informational": 2, "suggested_archives": 0, "needs_review": 1}
}`

// needsReviewOnlyPreScan: every OTHER bucket is empty -- only needs_review
// has an item. isEmpty() today only looks at Urgent/Actionable/
// SuggestedArchives, so this fixture must NOT render as "Nothing needs you"
// once needs_review is a real section.
const needsReviewOnlyPreScan = `{
  "kind": "email_pre_scan",
  "urgent": [],
  "actionable": [],
  "informational_count": 0,
  "suggested_archives": [],
  "suggested_drafts": [],
  "needs_review": [
    {"message_id":"nr1","thread_id":"tnr1","sender":"colleague@example.com",
     "subject":"Any chance to meet this Thursday at 9am?","why":"heuristic unconfident"}
  ],
  "preferences_applied": null,
  "totals": {"urgent": 0, "actionable": 0, "informational": 0, "suggested_archives": 0, "needs_review": 1}
}`

func TestPreScanNeedsReviewRendersAndCountsTowardScanned(t *testing.T) {
	out := Render("email_pre_scan", raw(t, needsReviewPopulatedPreScan), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	// Today's scanned() sums Urgent+Actionable+Informational+SuggestedArchives
	// = 0+1+2+0 = 3, so the title reads "3 scanned". Once needs_review (1)
	// counts toward scanned(), it must read "4 scanned".
	assertContains(t, out, "Inbox · 4 scanned")
	// A needs-review row's sender/subject/why must appear somewhere in the
	// rendered card -- today the field is unknown to the Go struct and is
	// silently dropped, so none of this text renders.
	assertContains(t, out,
		"colleague@example.com",
		"Any chance to meet this Thursday at 9am?",
		"heuristic unconfident (no match)",
	)
}

func TestPreScanNeedsReviewOnlyIsNotEmptyState(t *testing.T) {
	out := Render("email_pre_scan", raw(t, needsReviewOnlyPreScan), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	// A needs_review-only pre-scan still needs the user's attention -- it
	// must never render as "Nothing needs you", which is exactly what
	// isEmpty() (Urgent/Actionable/SuggestedArchives only) produces today.
	assertNotContains(t, out, "Nothing needs you.")
}

// ---------------------------------------------------------------------------
// #2631 -- RenderDeduped's seen threading. The chat model only ever calls
// this with the pre-scan card rendering first (seen still empty), but the
// primitive is shared with the attention card and must behave correctly
// when a pre-scan section is the one losing an item, not just the other way
// round -- exercised directly here rather than relying on ordering that
// happens to hold true today.
// ---------------------------------------------------------------------------

const preScanTwoSectionsOneItemEach = `{
  "kind": "email_pre_scan",
  "urgent": [
    {"message_id":"u1","sender":"a@x.com","subject":"UrgentDup","why":"r1"}
  ],
  "actionable": [
    {"message_id":"a1","sender":"b@x.com","subject":"ActionableUnique","why":"r2"}
  ],
  "suggested_archives": [],
  "needs_review": [],
  "totals": {"urgent": 1, "actionable": 1, "informational": 0, "suggested_archives": 0, "needs_review": 0}
}`

func TestPreScanRenderDedupedDropsSeenItemAndEmptiedSection(t *testing.T) {
	seen := map[string]bool{"u1": true}
	out, ids := RenderDeduped("email_pre_scan", raw(t, preScanTwoSectionsOneItemEach), width80, seen)
	t.Logf("\n%s", plain(out))

	assertNotContains(t, out, "URGENT", "UrgentDup")
	assertContains(t, out, "NEEDS A REPLY", "ActionableUnique")

	if len(ids) != 1 || ids[0] != "a1" {
		t.Errorf(`returned ids = %v, want exactly ["a1"] -- u1 was already seen and must not be re-added`, ids)
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
