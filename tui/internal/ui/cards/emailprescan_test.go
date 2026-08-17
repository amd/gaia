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
		// Title just names the card -- the scan count is the footer's job
		// alone (#2743 checkpoint review, no duplicated coverage statement).
		"┌─ Inbox ",
		"15 inbox messages scanned",
		// Section is one worklist, not four buckets (#2743).
		"NEEDS A REPLY",
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
		// The bulk line states the test applied, not the category (#2743
		// checkpoint review) -- falsifiable, never a bare label.
		"4 filtered", "none of them asked you a question",
		"Using your priority senders: Sarah Chen, Priya N.",
	)
}

func TestPreScanEmptyState(t *testing.T) {
	out := Render("email_pre_scan", raw(t, emptyPreScan), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out,
		// emptyPreScan sets no total_inbox -- coverage is unproven, so the
		// verdict itself is scoped to what was actually scanned (#2743
		// checkpoint review), never the unqualified global claim.
		"Nothing in the 19 most recent needs a reply.",
		"19 inbox messages scanned",
		"19 filtered",
		"none of them named a deadline",
	)
	// No worklist header for a genuinely empty scan.
	assertNotContains(t, out, "NEEDS A REPLY")
	assertNotContains(t, out, "Nothing needs you.")
}

// When the scan genuinely covered the whole inbox (total_inbox == scanned),
// the unqualified "Nothing needs you." is correct and welcome -- that's the
// one case it's actually true (#2743 checkpoint review).
func TestPreScanEmptyStateUnqualifiedWhenCoverageIsComplete(t *testing.T) {
	var envelope map[string]any
	if err := json.Unmarshal([]byte(emptyPreScan), &envelope); err != nil {
		t.Fatal(err)
	}
	envelope["total_inbox"] = 19 // matches "scanned": 19 -- fully covered
	data, err := json.Marshal(envelope)
	if err != nil {
		t.Fatal(err)
	}

	out := Render("email_pre_scan", data, width80)
	t.Logf("\n%s", plain(out))
	assertContains(t, out, "Nothing needs you.")
	assertNotContains(t, out, "Nothing in the 19 most recent needs a reply.")
}

// The coverage line names "inbox messages scanned" ONCE, not twice, and
// keeps the invitation to look further back on the same clause (#2743
// checkpoint review) -- this is the line carrying the issue's central
// honesty claim, so it must read cleanly.
func TestCoverageLineNamesInboxOnceWhenMoreExists(t *testing.T) {
	var envelope map[string]any
	if err := json.Unmarshal([]byte(populatedPreScan), &envelope); err != nil {
		t.Fatal(err)
	}
	envelope["total_inbox"] = 210 // more than "scanned": 15
	data, err := json.Marshal(envelope)
	if err != nil {
		t.Fatal(err)
	}

	out := Render("email_pre_scan", data, width80)
	t.Logf("\n%s", plain(out))
	got := plain(out)
	assertContains(t, out, "15 of 210 inbox messages scanned — ask me to look further back")
	if n := strings.Count(got, "inbox messages scanned"); n != 1 {
		t.Errorf(`"inbox messages scanned" appears %d times, want exactly 1:\n%s`, n, got)
	}
}

func TestPreScanCapsHitShowsNofM(t *testing.T) {
	out := Render("email_pre_scan", raw(t, capsHitPreScan), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	// The fixture ships fewer needs_you rows (5) than needs_you_total (40)
	// reports -- the header must read "5 of 40" rather than a bare count
	// that implies the list is everything.
	assertContains(t, out, "NEEDS A REPLY", "5 of 40")
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
		"NEEDS A REPLY", "Sarah Chen", "Prod incident",
		// Rows are tagged with their account, because more than one is in play.
		"Gmail · Sarah Chen", "Outlook ·",
	)
}

func TestPreScanSingleMailboxOmitsTag(t *testing.T) {
	// One account: the mailbox tag is noise, so it is not drawn.
	out := Render("email_pre_scan", raw(t, populatedPreScan), width80)
	assertNotContains(t, out, "Gmail ·", "Outlook ·")
}

// mailboxLabel is what turns a raw connector id into the per-item provenance
// label a three-mailbox prescan actually displays. A missing case here
// renders as the raw internal id instead of a name a user recognizes.
func TestMailboxLabel(t *testing.T) {
	cases := map[string]string{
		"google":         "Gmail",
		"microsoft":      "Outlook",
		"microsoft_work": "Microsoft 365",
		"":               "A mailbox",
		"yahoo":          "yahoo",
	}
	for provider, want := range cases {
		if got := mailboxLabel(provider); got != want {
			t.Errorf("mailboxLabel(%q) = %q, want %q", provider, got, want)
		}
	}
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
	// greater than the 5 shown -- so the "NEEDS A REPLY" section header falls
	// back to a bare count rather than claiming a hidden tail that was
	// never reported. Checked against the header specifically, not a bare
	// " of " substring -- the bulk line's own falsifiable phrasing ("none
	// OF them asked...") legitimately contains that substring too.
	if headers := sectionHeaderCounts(t, plain(out), "NEEDS A REPLY"); headers != "5" {
		t.Errorf("NEEDS A REPLY header = %q, want a bare \"5\" with no hidden tail", headers)
	}
	assertContains(t, out, "NEEDS A REPLY", "Sarah Chen")
}

// sectionHeaderCounts reads the count text off a section header line (the
// "N" or "N of M" that box.sectionHeader right-aligns), stripped of the
// border/indent noise around it.
func sectionHeaderCounts(t *testing.T, rendered, label string) string {
	t.Helper()
	for _, line := range strings.Split(rendered, "\n") {
		body := strings.TrimSpace(strings.Trim(line, "│"))
		if !strings.HasPrefix(body, label) {
			continue
		}
		return strings.TrimSpace(strings.TrimPrefix(body, label))
	}
	t.Fatalf("no %q section header found in:\n%s", label, rendered)
	return ""
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
		if !strings.Contains(plain(out), "NEEDS A REPLY") {
			t.Errorf("width %d dropped the NEEDS A REPLY section:\n%s", w, plain(out))
		}
	}
}

// ---------------------------------------------------------------------------
// needs_review (#2584) -- folded into needs_you as its own kind (#2743).
// These fixtures are defined inline (not in testdata_test.go) so they stay
// scoped to this one file.
// ---------------------------------------------------------------------------

// why text below matches what the Python backend actually emits for its
// unconfident/no-heuristic-match fallback post-#2744 (triage_heuristics.py)
// and its #2743-redirect sibling fix in read_tools.py/attention_tools.py --
// a plain observable fact, never the classifier's own internal trace.
const needsReviewPopulatedPreScan = `{
  "kind": "email_pre_scan",
  "urgent": [], "actionable": [], "informational_count": 2,
  "suggested_archives": [], "suggested_drafts": [], "needs_review": [],
  "scanned": 4,
  "needs_you": [
    {"ref":1,"kind":"needs_response","message_id":"a1","thread_id":"ta1","sender":"boss@example.com",
     "subject":"Q3 numbers","why":"direct question"},
    {"ref":2,"kind":"needs_review","message_id":"nr1","thread_id":"tnr1","sender":"colleague@example.com",
     "subject":"Any chance to meet this Thursday at 9am?","why":"No clear signal from the sender or subject"}
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
     "subject":"Any chance to meet this Thursday at 9am?","why":"No clear signal from the sender or subject"}
  ],
  "needs_you_total": 1,
  "bulk": {"count": 0, "filter_tests": []},
  "preferences_applied": null
}`

func TestPreScanNeedsReviewRendersWithCheckVerb(t *testing.T) {
	out := Render("email_pre_scan", raw(t, needsReviewPopulatedPreScan), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out, "4 inbox messages scanned")
	// A needs_review row renders under the CHECK verb, with its own
	// sender/subject/why -- distinct provenance from a category bucket.
	// The subject is truncated at this width by the sender/subject column
	// split (box.rowWithPrefix); check a prefix that survives it.
	assertContains(t, out,
		"CHECK",
		"colleague@example.com",
		"Any chance to meet this",
		"No clear signal from the sender or subject",
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

func TestBulkLineStatesTheTestNotTheCategory(t *testing.T) {
	// #2743 checkpoint review: the bulk line must be falsifiable -- what
	// QUESTION was asked of the filtered mail, never a category label
	// wearing the count (that was the original "27 filtered (promotional,
	// FYI)" complaint this issue exists to fix).
	p := emailPreScan{Bulk: &bulkSummary{
		Count:       27,
		FilterTests: []string{"no_direct_question", "no_deadline_signal"},
	}}
	got := p.bulkLine()
	want := "27 filtered — none of them asked you a question or named a deadline."
	if got != want {
		t.Errorf("bulkLine() = %q, want %q", got, want)
	}
}

func TestBulkLineArchivePreferenceIsAPositiveClause(t *testing.T) {
	p := emailPreScan{Bulk: &bulkSummary{
		Count:       5,
		FilterTests: []string{"matched_your_archive_preference"},
	}}
	got := p.bulkLine()
	if !strings.Contains(got, "matched your archive preference") {
		t.Errorf("bulkLine() = %q, want it to name the archive-preference match", got)
	}
	if strings.Contains(got, "none of them") {
		t.Errorf("bulkLine() = %q, a positive match must not join the negated-tests clause", got)
	}
}

func TestBulkLineUnmappedFilterTestDegradesVisibly(t *testing.T) {
	// An id this client predates must still show something, not vanish.
	p := emailPreScan{Bulk: &bulkSummary{
		Count:       3,
		FilterTests: []string{"some_future_filter_test"},
	}}
	got := p.bulkLine()
	if !strings.Contains(got, "some_future_filter_test") {
		t.Errorf("bulkLine() = %q, want the raw unmapped id shown, not dropped", got)
	}
}

// #2743 Increment 3: extracted detail/due_hint text is wrapped in the same
// untrusted-input delimiters that cover a raw message body, since it
// re-enters the AGENT's own tool-result context. A human reading the card
// is not at risk the way an LLM context is, so the wrapper must never
// appear on screen.

func TestStripUntrustedWrapperRemovesTheDelimiters(t *testing.T) {
	wrapped := untrustedBodyOpen + "\nCan you confirm the rollback completed?\n" + untrustedBodyClose
	got := stripUntrustedWrapper(wrapped)
	want := "Can you confirm the rollback completed?"
	if got != want {
		t.Errorf("stripUntrustedWrapper(%q) = %q, want %q", wrapped, got, want)
	}
}

func TestStripUntrustedWrapperPassesThroughUnwrappedText(t *testing.T) {
	// A pre-2.11 producer, or any field this defense doesn't cover, never
	// carries the wrapper -- it must render unchanged, not get mangled by
	// a strip that assumes the markers are always present.
	plain := "waiting 3d on your reply"
	if got := stripUntrustedWrapper(plain); got != plain {
		t.Errorf("stripUntrustedWrapper(%q) = %q, want unchanged", plain, got)
	}
}

func TestPreScanDetailWrapperNeverReachesTheRenderedCard(t *testing.T) {
	payload := `{
	  "kind": "email_pre_scan",
	  "urgent": [], "actionable": [], "informational_count": 0,
	  "suggested_archives": [], "suggested_drafts": [], "needs_review": [],
	  "scanned": 1,
	  "needs_you": [
	    {"ref":1,"kind":"urgent","message_id":"m1","sender":"a@x.com","subject":"s",
	     "why":"r1","detail":["` + untrustedBodyOpen + `\nCan you confirm the rollback completed?\n` + untrustedBodyClose + `"],
	     "due_hint":"` + untrustedBodyOpen + `\nFriday EOD\n` + untrustedBodyClose + `"}
	  ],
	  "needs_you_total": 1,
	  "bulk": {"count": 0, "filter_tests": []},
	  "preferences_applied": null
	}`
	out := Render("email_pre_scan", raw(t, payload), width80)
	t.Logf("\n%s", plain(out))

	assertContains(t, out, "Can you confirm the rollback completed?", "due Friday EOD")
	assertNotContains(t, out, untrustedBodyOpen, untrustedBodyClose)
}
