package cards

import (
	"encoding/json"
	"strings"
)

// The email agent's inbox pre-scan envelope, mirroring
// hub/agents/email/npm/src/types.ts (EmailPreScanResult) field for field.
//
// The SSE payload is a SUPERSET of the REST contract: merge_pre_scan_backends
// tags each item with `mailbox` and may add a top-level `mailbox_errors`, so
// both are decoded here even though the REST models do not carry them.
//
// #2743 replaced the four-bucket rendering (urgent/actionable/needs_review/
// suggested_archives) with the ONE `needs_you` worklist view: a deterministic
// VIEW the server already built over those buckets plus the waiting-on-you
// detector and persisted action items, capped at 5 and pre-ordered by kind
// then oldest-first. This client renders exactly that ONE list, never
// recomputing an order or a `ref` of its own -- the server's `ref` is
// displayed verbatim (stable within this one render only; a rescan
// re-orders and renumbers by design).

type needsYouItem struct {
	Ref        int      `json:"ref"`
	Kind       string   `json:"kind"`
	MessageID  string   `json:"message_id"`
	ThreadID   string   `json:"thread_id"`
	Sender     string   `json:"sender"`
	Subject    string   `json:"subject"`
	AgeSeconds *int     `json:"age_seconds"`
	Why        string   `json:"why"`
	Detail     []string `json:"detail"`
	DueHint    string   `json:"due_hint"`
	Mailbox    string   `json:"mailbox"`
}

type bulkSummary struct {
	Count       int      `json:"count"`
	FilterTests []string `json:"filter_tests"`
}

type preScanPreferences struct {
	PrioritySenders    []string          `json:"priority_senders"`
	LowPrioritySenders []string          `json:"low_priority_senders"`
	CategoryDefaults   map[string]string `json:"category_defaults"`
}

type mailboxError struct {
	Mailbox string `json:"mailbox"`
	Error   string `json:"error"`
}

type emailPreScan struct {
	Kind               string              `json:"kind"`
	Scanned            int                 `json:"scanned"`
	TotalUnread        *int                `json:"total_unread"`
	TotalInbox         *int                `json:"total_inbox"`
	Degraded           bool                `json:"degraded"`
	MailboxErrors      []mailboxError      `json:"mailbox_errors"`
	PreferencesApplied *preScanPreferences `json:"preferences_applied"`
	NeedsYou           []needsYouItem      `json:"needs_you"`
	NeedsYouTotal      int                 `json:"needs_you_total"`
	Bulk               *bulkSummary        `json:"bulk"`
}

// maxCardRows bounds the card's interior. 22 interior rows plus two borders is
// 24 lines — an 80x24 terminal's whole screen. Beyond it a card stops being a
// card and becomes a scroll trap, so the overflow becomes "+N more" instead.
const maxCardRows = 22

// rationaleIndent lines a row's reason up under the sender column above it
// (" NN  " is five columns wide).
const rationaleIndent = "     "

// maxBannerRows bounds the per-account warning banner's message lines, before
// its "+N more accounts" and "unaffected" lines.
const maxBannerRows = 4

// sectionCost is the interior rows one section occupies: a header, the rows the
// first `show` items actually render to (itemRows already counts each item's own
// row plus however many lines its rationale wraps to), and a "+N more" line only
// when something really is hidden. Blanks between sections are charged by
// fitSections.
//
// Costing wrapped rationale lines rather than assuming one apiece is the whole
// point — a two-line reason on every row silently doubles a section's height.
// #2743: the caller (needsYouItemRows) is what changed to cost a multi-line
// `detail` allowance on top of the row+why cost -- this function's own shape
// is unchanged, generic over however many rows each item is reported to cost.
func sectionCost(itemRows []int, show, total int) int {
	if show == 0 {
		return 0
	}
	cost := 1
	for _, rows := range itemRows[:show] {
		cost += rows
	}
	if total > show {
		cost++
	}
	return cost
}

// fitSections trims per-section row counts until the card fits its budget, and
// returns the counts plus the rows they occupy.
//
// Rows come off whichever section shows the most (ties to the lower-priority
// one), so a long reply list cannot starve the urgent one. No non-empty section
// drops below one row. Trimmed rows are still counted: the caller derives
// "N of M" and "+N more" from the pre-cap `totals`.
//
// The returned cost can exceed budget once every section is at one row; the
// caller reclaims the difference rather than hiding a bucket.
//
// #2743: needs_you is rendered as a single section, so this is called with
// one-element slices — the trim-the-largest-section logic degrades to
// "drop items from the end, one at a time" exactly as needed, unchanged.
func fitSections(itemRows [][]int, totals []int, budget int) ([]int, int) {
	show := make([]int, len(itemRows))
	for i := range itemRows {
		show[i] = len(itemRows[i])
	}

	cost := func() int {
		sum, sections := 0, 0
		for i, n := range show {
			if n > 0 {
				sum += sectionCost(itemRows[i], n, totals[i])
				sections++
			}
		}
		if sections > 1 {
			sum += sections - 1 // one blank line between adjacent sections
		}
		return sum
	}

	for cost() > budget {
		// Scan last section first so an equal-sized tie gives up the row from the
		// lowest-priority bucket. Derived from len(show), not a fixed list, so a
		// future fourth section cannot silently skip the scan.
		victim := -1
		for i := len(show) - 1; i >= 0; i-- {
			if show[i] > 1 && (victim == -1 || show[i] > show[victim]) {
				victim = i
			}
		}
		if victim == -1 {
			break // every non-empty section is down to its last row
		}
		show[victim]--
	}
	return show, cost()
}

// The untrusted-input delimiter pair the Python side wraps extracted
// needs_you[].detail/due_hint text in before it re-enters the AGENT's own
// tool-result context (#2743 Increment 3, gaia_agent_email/tools/
// read_tools.py's wrap_untrusted_body). A human reading this card is not at
// risk of being "steered" by embedded text the way an LLM context is, so
// the wrapper is stripped here rather than shown -- it exists for the
// agent's benefit, not the viewer's.
const (
	untrustedBodyOpen  = "<<<UNTRUSTED_EMAIL_BODY_START>>>"
	untrustedBodyClose = "<<<UNTRUSTED_EMAIL_BODY_END>>>"
)

// stripUntrustedWrapper removes the untrusted-input delimiter pair if
// present, trimming the surrounding whitespace the wrapper's own newlines
// leave behind. Text that never carried the wrapper (a pre-2.11 producer,
// or any field this defense doesn't cover) passes through unchanged.
func stripUntrustedWrapper(s string) string {
	trimmed := strings.TrimSpace(s)
	if !strings.HasPrefix(trimmed, untrustedBodyOpen) || !strings.HasSuffix(trimmed, untrustedBodyClose) {
		return s
	}
	inner := strings.TrimPrefix(trimmed, untrustedBodyOpen)
	inner = strings.TrimSuffix(inner, untrustedBodyClose)
	return strings.TrimSpace(inner)
}

// isPreScanEnvelope reports whether the payload actually claims to be a
// pre-scan, rather than merely failing to contradict one.
func isPreScanEnvelope(data json.RawMessage) bool {
	var probe map[string]json.RawMessage
	if err := json.Unmarshal(data, &probe); err != nil || probe == nil {
		return false
	}
	for _, field := range []string{
		"kind", "needs_you", "needs_you_total", "bulk", "scanned",
	} {
		if _, ok := probe[field]; ok {
			return true
		}
	}
	return false
}

// renderEmailPreScan draws the inbox pre-scan card: the needs_you worklist,
// the bulk/coverage footer, and any mailbox-error banner.
//
// seen is the set of message_ids a card rendered earlier in the same turn
// already showed (nil for a standalone render with no dedup). An item whose
// id is already in seen is skipped; every non-empty id this call renders is
// returned so a later card can be threaded against it too. An item with no
// message_id is never treated as a duplicate.
func renderEmailPreScan(data json.RawMessage, width int, seen map[string]bool) (string, []string) {
	var p emailPreScan
	if err := json.Unmarshal(data, &p); err != nil {
		return renderInvalid("email_pre_scan", err.Error(), data, width), nil
	}
	if k := strings.TrimSpace(p.Kind); k != "" && k != "email_pre_scan" {
		return renderInvalid("email_pre_scan", "kind is "+k+", expected email_pre_scan", data, width), nil
	}
	// `null` and `{}` both unmarshal cleanly into the zero envelope, which would
	// render as "Nothing needs you" — a malformed payload telling the user their
	// inbox is clear. Require the envelope to actually be one.
	if !isPreScanEnvelope(data) {
		return renderInvalid("email_pre_scan", "payload carries no pre-scan fields", data, width), nil
	}

	// #2743 Increment 3: strip the untrusted-body wrapper BEFORE anything
	// else reads Detail/DueHint, so both the cost estimate (fitNeedsYou)
	// and the actual render see the same, already-human-readable text.
	for i := range p.NeedsYou {
		p.NeedsYou[i].DueHint = stripUntrustedWrapper(p.NeedsYou[i].DueHint)
		for j, d := range p.NeedsYou[i].Detail {
			p.NeedsYou[i].Detail[j] = stripUntrustedWrapper(d)
		}
	}

	hadItems := len(p.NeedsYou) > 0
	var ids []string
	p.NeedsYou, ids = dedupByMessageID(p.NeedsYou, func(it needsYouItem) string { return it.MessageID }, seen)
	if hadItems && len(p.NeedsYou) == 0 {
		// Every item was already shown by an earlier card this turn -- this
		// card would add nothing, so it does not render at all.
		return "", nil
	}

	b := newBox(p.title(), width)
	p.renderMailboxErrors(b)

	if len(p.NeedsYou) == 0 {
		p.renderEmpty(b)
		return b.render(), ids
	}

	showMailbox := p.multiMailbox()
	footer := p.footerLines()
	// Reserve the header + separator row each extra section costs beyond the
	// single header the flat render used, so grouping can never push the card
	// past maxCardRows.
	budget := maxCardRows - len(b.lines) - (countVerbGroups(p.NeedsYou)-1)*2
	show, keepDetail, shownFooter := p.fitNeedsYou(b, showMailbox, footer, budget)

	prevVerb := ""
	for i := 0; i < show; i++ {
		if verb := verbForKind(p.NeedsYou[i].Kind); verb != prevVerb {
			if prevVerb != "" {
				b.blank()
			}
			count := ""
			if prevVerb == "" {
				count = p.needsYouCountLabel(show)
			}
			b.sectionHeader(sectionLabelForVerb(verb), count)
			prevVerb = verb
		}
		p.needsYouRow(b, p.NeedsYou[i], showMailbox, keepDetail[i])
	}
	if extra := p.NeedsYouTotal - show; extra > 0 {
		b.add(rationaleIndent + "+" + itoa(extra) + " more")
	}

	if len(shownFooter) > 0 {
		b.blank()
		for _, line := range shownFooter {
			b.addWrapped("  ", line)
		}
	}
	return b.render(), ids
}

// renderMailboxErrors draws the per-account warning banner. A broken grant on
// one account is free information that today surfaces nowhere — and the results
// that did arrive are still valid, so this warns and never fails the card.
func (p emailPreScan) renderMailboxErrors(b *box) {
	renderMailboxErrorBanner(b, p.MailboxErrors)
}

// renderMailboxErrorBanner draws the per-account warning banner shared by every
// card that carries a “mailbox_errors“ list (the pre-scan card and the
// attention view, #2582). A broken grant on one account is free information
// that today surfaces nowhere — and the results that did arrive are still
// valid, so this warns and never fails the card.
//
// The banner is capped: one long enough to bury the results it annotates defeats
// its own purpose.
func renderMailboxErrorBanner(b *box, errs []mailboxError) {
	if len(errs) == 0 {
		return
	}

	// One failure gets its full message — that is the case where the text says
	// what to do about it. Several get named but summarised: four wrapped error
	// strings would take a third of the card to annotate results the user can
	// still act on.
	tail := "Results below are unaffected."
	if len(errs) == 1 {
		me := errs[0]
		b.addWrapped("  ", "[!] "+mailboxLabel(me.Mailbox)+" wasn't scanned: "+strings.TrimSpace(me.Error))
	} else {
		names := make([]string, len(errs))
		for i, me := range errs {
			names[i] = mailboxLabel(me.Mailbox)
		}
		b.addWrapped("  ", "[!] "+itoa(len(names))+" accounts weren't scanned: "+strings.Join(names, ", "))
		tail = "Reconnect them in settings. " + tail
	}
	b.addWrapped("      ", tail)
	b.blank()
}

// verbForKind maps a needs_you item's provenance (kind, pure provenance —
// see contract.py's AttentionItemKind) to the render-time verb a user acts
// on (#2743 Increment 2 step 6 / contract.py's own NeedsYouItem docstring:
// "the renderer maps kind to a verb label at render time; the wire only
// carries the source signal"). An unrecognized kind (a future server
// addition this client predates) still gets a verb, never a blank one.
// sectionLabelForVerb names the group a verb heads. The wire carries only
// provenance (kind); both the verb and this heading are render-time lookups,
// so a future server kind still lands under a heading rather than none.
func sectionLabelForVerb(verb string) string {
	switch verb {
	case "REPLY":
		return "NEEDS A REPLY"
	case "DECIDE":
		return "NEEDS A DECISION"
	case "CHECK":
		return "WORTH A LOOK"
	case "DO":
		return "YOUR TASKS"
	default:
		return "NEEDS YOU"
	}
}

// countVerbGroups counts the runs of same-verb items. Items arrive already
// ordered by kind (_NEEDS_YOU_KIND_ORDER, read_tools.py), so verbs are
// contiguous and a run count equals the number of section headers the render
// will emit -- counting runs rather than distinct verbs keeps that true even
// if a future ordering interleaves them.
func countVerbGroups(items []needsYouItem) int {
	groups, prev := 0, ""
	for _, it := range items {
		if v := verbForKind(it.Kind); v != prev {
			groups++
			prev = v
		}
	}
	return groups
}

func verbForKind(kind string) string {
	switch kind {
	case "urgent", "waiting_on_you", "needs_response":
		return "REPLY"
	case "meeting_request":
		return "DECIDE"
	case "needs_review":
		return "CHECK"
	case "action_item":
		return "DO"
	default:
		return "REVIEW"
	}
}

// negatedFilterTestClauses maps a BulkSummary.filter_tests id (contract.py:
// "ids, never prose... an unmapped id degrades visibly rather than
// rendering a stale claim") to the verb clause a renderer joins under
// "none of them ..." (#2743 checkpoint review). Named after the QUESTION
// asked of the message, never the category it landed in: "27 filtered
// (promotional, FYI)" just relabels a bare count with an unchallengeable
// tag, which is the original complaint this whole issue exists to fix.
// "none of them asked you a question or named a deadline" is falsifiable
// — it invites "what about a receipt over $500?", which is the user
// checking the agent's work. Kept in sync with
// hub/agents/email/python/gaia_agent_email/tools/read_tools.py's
// FILTER_TEST_* constants.
var negatedFilterTestClauses = map[string]string{
	"no_direct_question":  "asked you a question",
	"no_deadline_signal":  "named a deadline",
	"no_meeting_proposal": "proposed a meeting",
}

// archivePreferenceFilterTest is a POSITIVE match (a session preference was
// applied), not an absence -- it gets its own clause rather than joining
// the "none of them ..." list of negated tests.
const archivePreferenceFilterTest = "matched_your_archive_preference"

// formatItemAge renders a needs_you item's age_seconds the way a person
// reads it: seconds/minutes/hours while fresh, days once it's been sitting
// a while -- the waiting-on-you detector's own signature case.
func formatItemAge(seconds int) string {
	if seconds < 60 {
		return itoa(seconds) + "s"
	}
	minutes := seconds / 60
	if minutes < 60 {
		return itoa(minutes) + "m"
	}
	hours := minutes / 60
	if hours < 24 {
		return itoa(hours) + "h"
	}
	days := hours / 24
	return itoa(days) + "d"
}

// needsYouVerbPrefixWidth is the fixed column every verb label is padded
// to (the longest, DECIDE/REVIEW, is 6) so a row's sender column starts at
// the same offset regardless of which verb precedes it.
const needsYouVerbPrefixWidth = 6

// needsYouWhyLine is the row's why/age/due-hint line, combined into ONE
// line (not a separate age row) so the row+why cost stays the stable "2
// rows for a plain item" baseline the fit budget assumes before any
// `detail` substance is added.
//
// A meeting_request's `why` is always the same boilerplate ("looks like
// it's proposing a meeting or a time to talk") -- once at least one detail
// line survives the fit budget with the actual proposed time, repeating
// the boilerplate costs a line in a height-bounded card to say nothing the
// DECIDE verb and the detail line don't already say more specifically
// (#2743 checkpoint review), so it is suppressed in that one case. Every
// other kind's `why` is genuinely per-item information and always shows.
func (it needsYouItem) needsYouWhyLine(keepDetail int) string {
	var parts []string
	if it.AgeSeconds != nil {
		parts = append(parts, formatItemAge(*it.AgeSeconds)+" ago")
	}
	if !(it.Kind == "meeting_request" && keepDetail > 0) {
		if why := strings.TrimSpace(it.Why); why != "" {
			parts = append(parts, why)
		}
	}
	if it.DueHint != "" {
		parts = append(parts, "due "+it.DueHint)
	}
	return strings.Join(parts, " · ")
}

// needsYouRow draws one row: the server's own ref (never recomputed here),
// a fixed-width verb prefix, the sender/subject columns, then the age/why/
// due-hint line and up to keepDetail of the item's own `detail` lines
// (#2743 Increment 3 substance — empty until that lands, but rendered here
// so the card is ready for it).
func (p emailPreScan) needsYouRow(b *box, it needsYouItem, showMailbox bool, keepDetail int) {
	sender := displaySender(it.Sender)
	if it.Kind == "action_item" && strings.TrimSpace(it.Sender) == "" {
		sender = "(action item)"
	}
	if showMailbox && it.Mailbox != "" {
		sender = mailboxLabel(it.Mailbox) + " · " + sender
	}
	verb := padTo(verbForKind(it.Kind), needsYouVerbPrefixWidth)
	b.rowWithPrefix(it.Ref, verb, sender, it.Subject)

	if line := it.needsYouWhyLine(keepDetail); strings.TrimSpace(line) != "" {
		b.addWrapped(rationaleIndent, line)
	}
	for i, d := range it.Detail {
		if i >= keepDetail {
			break
		}
		if strings.TrimSpace(d) != "" {
			b.addWrapped(rationaleIndent, d)
		}
	}
}

// needsYouRowCost is how many interior rows the item at keepDetail's
// allowance will actually render to: its own row, its age/why/due-hint
// line (when non-empty), plus each KEPT detail line wrapped to width. This
// is the "cost a multi-line detail" half of the #2743 checkpoint review —
// the original per-bucket renderer assumed one wrapped rationale string; a
// row can now carry up to two additional substance lines.
func (p emailPreScan) needsYouRowCost(b *box, it needsYouItem, keepDetail int) int {
	cost := 1
	if line := it.needsYouWhyLine(keepDetail); strings.TrimSpace(line) != "" {
		cost += len(wrap(line, b.inner()-visualLen(rationaleIndent)))
	}
	for i, d := range it.Detail {
		if i >= keepDetail {
			break
		}
		if strings.TrimSpace(d) == "" {
			continue
		}
		cost += len(wrap(d, b.inner()-visualLen(rationaleIndent)))
	}
	return cost
}

func (p emailPreScan) needsYouItemRows(b *box, keepDetail []int) []int {
	out := make([]int, len(p.NeedsYou))
	for i, it := range p.NeedsYou {
		out[i] = p.needsYouRowCost(b, it, keepDetail[i])
	}
	return out
}

// footerRows is the interior rows a footer occupies once wrapped, including the
// blank line that separates it from the sections above. Counting the strings
// instead would under-budget a preferences list that wraps to three rows.
func footerRows(b *box, footer []string) int {
	if len(footer) == 0 {
		return 0
	}
	rows := 1
	for _, line := range footer {
		rows += len(wrap(line, b.inner()-2))
	}
	return rows
}

// dropOneDetailLine removes the LAST detail line of the LOWEST-priority
// item that still has one (scanning from the end of NeedsYou — the list is
// already ordered high-to-low), returning false once nothing is left to
// drop anywhere.
func dropOneDetailLine(keepDetail []int) bool {
	for i := len(keepDetail) - 1; i >= 0; i-- {
		if keepDetail[i] > 0 {
			keepDetail[i]--
			return true
		}
	}
	return false
}

// fitNeedsYou decides, under budget, how many needs_you items to show, how
// many of each shown item's own `detail` lines to keep, and how much of the
// footer survives — in this trim order (#2743 checkpoint review, INVERTED
// from the original per-bucket design where the footer was trimmed first):
//
//  1. every item's own detail lines go first, keeping every item's row and
//     why/due-hint line intact — that's what turns a row into an argument;
//  2. whole items drop from the end via the shared fitSections trimmer
//     (needs_you is already ordered high-to-low, so the lowest-priority
//     item goes first);
//  3. the footer — the coverage fact and the bulk filter sentence — is cut
//     only as the very last resort.
func (p emailPreScan) fitNeedsYou(b *box, showMailbox bool, footer []string, budget int) (show int, keepDetail []int, shownFooter []string) {
	_ = showMailbox // sender/mailbox tagging does not affect row COUNT, only its content
	n := len(p.NeedsYou)
	keepDetail = make([]int, n)
	for i, it := range p.NeedsYou {
		keepDetail[i] = len(it.Detail)
	}
	shownFooter = footer

	fits := func() bool {
		rows := footerRows(b, shownFooter)
		cost := sectionCost(p.needsYouItemRows(b, keepDetail), n, p.NeedsYouTotal)
		return cost+rows <= budget
	}

	// 1) Detail lines first.
	for !fits() {
		if !dropOneDetailLine(keepDetail) {
			break
		}
	}
	if fits() {
		return n, keepDetail, shownFooter
	}

	// 2) Whole items, via the shared generic trimmer (one section).
	rows := footerRows(b, shownFooter)
	shownCounts, cost := fitSections([][]int{p.needsYouItemRows(b, keepDetail)}, []int{p.NeedsYouTotal}, budget-rows)
	show = shownCounts[0]
	if cost+rows <= budget {
		return show, keepDetail, shownFooter
	}

	// 3) The footer is the last thing cut.
	for len(shownFooter) > 0 && cost+footerRows(b, shownFooter) > budget {
		shownFooter = shownFooter[:len(shownFooter)-1]
	}
	return show, keepDetail, shownFooter
}

func (p emailPreScan) needsYouCountLabel(show int) string {
	if p.NeedsYouTotal > show {
		return itoa(show) + " of " + itoa(p.NeedsYouTotal)
	}
	return itoa(show)
}

// bulkLine states the filtered remainder AND what QUESTION was asked of it
// (#2743 checkpoint review) — BulkSummary.filter_tests carries opaque ids
// a renderer joins into one falsifiable sentence, so a bare unauditable
// count never stands alone and a category label never poses as the test
// that produced it. An unmapped id is shown by its raw id (visible
// degradation) rather than silently disappearing.
func (p emailPreScan) bulkLine() string {
	if p.Bulk == nil || p.Bulk.Count == 0 {
		return ""
	}
	var negated []string
	var other []string
	for _, id := range p.Bulk.FilterTests {
		if id == archivePreferenceFilterTest {
			other = append(other, "matched your archive preference")
			continue
		}
		if clause, ok := negatedFilterTestClauses[id]; ok {
			negated = append(negated, clause)
			continue
		}
		other = append(other, id)
	}
	var clauses []string
	if len(negated) > 0 {
		clauses = append(clauses, "none of them "+strings.Join(negated, " or "))
	}
	clauses = append(clauses, other...)

	line := itoa(p.Bulk.Count) + " filtered"
	if len(clauses) > 0 {
		line += " — " + strings.Join(clauses, "; ")
	}
	return line + "."
}

// coverageLine is the scoped fact half of "coverage as fact-plus-invitation"
// (#2743 Increment 2 step 6): what this pre-scan actually looked at, so
// neither the empty state nor a populated card can be mistaken for
// whole-mailbox coverage. When the inbox holds more than was scanned, the
// invitation half tells the user they can ask for a deeper look rather than
// silently capping the view with no way to know more exists.
func (p emailPreScan) coverageLine() string {
	switch {
	case p.TotalInbox != nil && *p.TotalInbox > p.Scanned:
		// "inbox" named once, not twice, and the invitation stays on one
		// clause (#2743 checkpoint review: the earlier phrasing repeated
		// "inbox messages scanned" and wrapped awkwardly on this, the line
		// carrying the issue's central honesty claim).
		return itoa(p.Scanned) + " of " + itoa(*p.TotalInbox) + " inbox messages scanned — ask me to look further back"
	case p.TotalUnread != nil:
		return itoa(p.Scanned) + " inbox messages scanned · " + itoa(*p.TotalUnread) + " unread"
	default:
		return itoa(p.Scanned) + " inbox messages scanned"
	}
}

func (p emailPreScan) footerLines() []string {
	var out []string
	if line := p.bulkLine(); line != "" {
		out = append(out, line)
	}
	if line := p.preferencesLine(); line != "" {
		out = append(out, line)
	}
	out = append(out, p.coverageLine()+".")
	return out
}

// emptyVerdictLine is the SCOPED zero-state verdict itself (#2743
// checkpoint review) — not just the coverage line that follows it. "Nothing
// needs you." is an unscoped GLOBAL claim from what may be a partial scan,
// the exact shape this issue exists to kill, so it is qualified on its own
// face whenever the scan did not cover the whole inbox. When it genuinely
// did (total_inbox == scanned, or unknown), the unqualified form is correct
// and welcome — that's the one case it's true.
func (p emailPreScan) emptyVerdictLine() string {
	// Positive proof of full coverage is the ONLY case for the unqualified
	// claim -- an unknown total_inbox (Outlook cannot report one) proves
	// nothing about whether more mail exists, so it gets the scoped form
	// too, not the stronger claim by default.
	if p.TotalInbox != nil && *p.TotalInbox <= p.Scanned {
		return "Nothing needs you."
	}
	return "Nothing in the " + itoa(p.Scanned) + " most recent needs a reply."
}

// renderEmpty is the SCOPED zero state (#2743 Increment 2 step 7): the
// verdict itself is scoped (emptyVerdictLine), and it may only appear
// alongside what was actually covered — never standing alone. Mirrors the
// honesty rule #2584/#2582 already established one layer over (the
// attention view's own renderEmpty).
func (p emailPreScan) renderEmpty(b *box) {
	b.addWrapped("  ", p.emptyVerdictLine())
	b.addWrapped("  ", p.coverageLine()+".")
	if line := p.bulkLine(); line != "" {
		b.addWrapped("  ", line)
	}
	if line := p.preferencesLine(); line != "" {
		b.addWrapped("  ", line)
	}
}

// title just names the card — the scan count is the footer's job
// (coverageLine), which also carries the denominator and the invitation to
// look further; stating it twice is redundant (#2743 checkpoint review).
func (p emailPreScan) title() string {
	return "Inbox"
}

// multiMailbox reports whether needs_you rows came from more than one
// account, which is the only case where tagging each row with its mailbox
// is information rather than noise.
func (p emailPreScan) multiMailbox() bool {
	seen := map[string]bool{}
	for _, it := range p.NeedsYou {
		if it.Mailbox != "" {
			seen[it.Mailbox] = true
		}
	}
	return len(seen) > 1
}

// preferencesLine is the only visible evidence the agent is learning, and the
// hook for "stop treating X as urgent".
func (p emailPreScan) preferencesLine() string {
	if p.PreferencesApplied == nil {
		return ""
	}
	var parts []string
	if s := p.PreferencesApplied.PrioritySenders; len(s) > 0 {
		parts = append(parts, "priority senders: "+strings.Join(s, ", "))
	}
	if s := p.PreferencesApplied.LowPrioritySenders; len(s) > 0 {
		parts = append(parts, "low priority: "+strings.Join(s, ", "))
	}
	if d := p.PreferencesApplied.CategoryDefaults; len(d) > 0 {
		var rules []string
		for _, k := range sortedKeys(d) {
			rules = append(rules, k+" → "+d[k])
		}
		parts = append(parts, "defaults: "+strings.Join(rules, ", "))
	}
	if len(parts) == 0 {
		return ""
	}
	line := "Using your " + strings.Join(parts, "; ")
	if strings.HasSuffix(line, ".") {
		return line
	}
	return line + "."
}

func sortedKeys(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	for i := 1; i < len(out); i++ {
		for j := i; j > 0 && out[j] < out[j-1]; j-- {
			out[j], out[j-1] = out[j-1], out[j]
		}
	}
	return out
}

// displaySender reduces a raw From header to what a person recognises: the
// display name when there is one, otherwise the bare address.
func displaySender(raw string) string {
	s := strings.TrimSpace(raw)
	if s == "" {
		return "(unknown sender)"
	}
	if i := strings.LastIndex(s, "<"); i >= 0 {
		name := strings.TrimSpace(s[:i])
		addr := strings.TrimSpace(strings.TrimSuffix(s[i+1:], ">"))
		name = strings.Trim(name, `"'`)
		if name != "" {
			return name
		}
		if addr != "" {
			return addr
		}
	}
	return strings.Trim(s, `"'`)
}

func mailboxLabel(provider string) string {
	switch strings.ToLower(strings.TrimSpace(provider)) {
	case "google":
		return "Gmail"
	case "microsoft":
		return "Outlook"
	case "microsoft_work":
		return "Microsoft 365"
	case "":
		return "A mailbox"
	default:
		return provider
	}
}
