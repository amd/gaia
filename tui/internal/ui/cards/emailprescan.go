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

type preScanItem struct {
	MessageID string `json:"message_id"`
	ThreadID  string `json:"thread_id"`
	Sender    string `json:"sender"`
	Subject   string `json:"subject"`
	Why       string `json:"why"`
	Reason    string `json:"reason"`
	Mailbox   string `json:"mailbox"`
}

// rationale is the row's justification. Archive rows carry `reason`, urgent and
// actionable rows carry `why`; the card reads reason ?? why. A row without one
// is a claim, with one it is an argument the user can check — which is what
// makes a local 4B model's triage worth trusting.
func (it preScanItem) rationale() string {
	return firstNonEmpty(it.Reason, it.Why)
}

type preScanTotals struct {
	Urgent            int `json:"urgent"`
	Actionable        int `json:"actionable"`
	Informational     int `json:"informational"`
	SuggestedArchives int `json:"suggested_archives"`
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
	Urgent             []preScanItem       `json:"urgent"`
	Actionable         []preScanItem       `json:"actionable"`
	InformationalCount int                 `json:"informational_count"`
	SuggestedArchives  []preScanItem       `json:"suggested_archives"`
	SuggestedDrafts    []json.RawMessage   `json:"suggested_drafts"`
	PreferencesApplied *preScanPreferences `json:"preferences_applied"`
	Totals             *preScanTotals      `json:"totals"`
	MailboxErrors      []mailboxError      `json:"mailbox_errors"`
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

// isPreScanEnvelope reports whether the payload actually claims to be a
// pre-scan, rather than merely failing to contradict one.
func isPreScanEnvelope(data json.RawMessage) bool {
	var probe map[string]json.RawMessage
	if err := json.Unmarshal(data, &probe); err != nil || probe == nil {
		return false
	}
	for _, field := range []string{
		"kind", "urgent", "actionable", "informational_count",
		"suggested_archives", "suggested_drafts", "totals",
	} {
		if _, ok := probe[field]; ok {
			return true
		}
	}
	return false
}

func renderEmailPreScan(data json.RawMessage, width int) string {
	var p emailPreScan
	if err := json.Unmarshal(data, &p); err != nil {
		return renderInvalid("email_pre_scan", err.Error(), data, width)
	}
	if k := strings.TrimSpace(p.Kind); k != "" && k != "email_pre_scan" {
		return renderInvalid("email_pre_scan", "kind is "+k+", expected email_pre_scan", data, width)
	}
	// `null` and `{}` both unmarshal cleanly into the zero envelope, which would
	// render as "Nothing needs you" — a malformed payload telling the user their
	// inbox is clear. Require the envelope to actually be one.
	if !isPreScanEnvelope(data) {
		return renderInvalid("email_pre_scan", "payload carries no pre-scan fields", data, width)
	}

	totals := p.totalsOrDerived()
	b := newBox(p.title(totals), width)

	showMailbox := p.multiMailbox()
	p.renderMailboxErrors(b)

	if p.isEmpty() {
		p.renderEmpty(b, totals)
		return b.render()
	}

	itemRows := [][]int{
		p.itemRows(b, p.Urgent, showMailbox, true),
		p.itemRows(b, p.Actionable, showMailbox, true),
		p.itemRows(b, p.SuggestedArchives, showMailbox, false),
	}
	sectionTotals := []int{totals.Urgent, totals.Actionable, totals.SuggestedArchives}
	budget := maxCardRows - len(b.lines)

	// Once every bucket is down to its last row there is nothing left to trim in
	// the sections, so the footer gives up its lines instead — losing the
	// preferences note beats hiding a whole bucket of mail.
	footer := p.footerLines(totals)
	full := len(footer)
	var shown []int
	for {
		// Every other truncation on this card leaves a visible marker, so a
		// dropped footer line gets one too — and it is budgeted here rather than
		// appended afterwards, or it would push the card back over the bound.
		candidate := footer
		if len(candidate) < full {
			candidate = append(append([]string(nil), candidate...),
				"+"+itoa(full-len(candidate))+" more line(s) not shown")
		}
		rows := footerRows(b, candidate)
		s, cost := fitSections(itemRows, sectionTotals, budget-rows)
		shown = s
		if cost+rows <= budget || len(footer) == 0 {
			footer = candidate
			break
		}
		footer = footer[:len(footer)-1]
	}

	n := 0
	// Urgent and actionable rows always carry their rationale — that is what
	// turns a row from a claim into an argument. Archive rows do not: their
	// reason is nearly always the category the section header already names.
	n = p.section(b, "URGENT", p.Urgent, shown[0], totals.Urgent, n, showMailbox, true)
	n = p.section(b, "NEEDS A REPLY", p.Actionable, shown[1], totals.Actionable, n, showMailbox, true)
	_ = p.section(b, "SUGGESTED ARCHIVE", p.SuggestedArchives, shown[2], totals.SuggestedArchives, n, showMailbox, false)

	if len(footer) > 0 {
		b.blank()
		for _, line := range footer {
			b.addWrapped("  ", line)
		}
	}
	return b.render()
}

// renderMailboxErrors draws the per-account warning banner. A broken grant on
// one account is free information that today surfaces nowhere — and the results
// that did arrive are still valid, so this warns and never fails the card.
//
// The banner is capped: one long enough to bury the results it annotates defeats
// its own purpose.
func (p emailPreScan) renderMailboxErrors(b *box) {
	if len(p.MailboxErrors) == 0 {
		return
	}

	// One failure gets its full message — that is the case where the text says
	// what to do about it. Several get named but summarised: four wrapped error
	// strings would take a third of the card to annotate results the user can
	// still act on.
	tail := "Results below are unaffected."
	if len(p.MailboxErrors) == 1 {
		me := p.MailboxErrors[0]
		b.addWrapped("  ", "[!] "+mailboxLabel(me.Mailbox)+" wasn't scanned: "+strings.TrimSpace(me.Error))
	} else {
		names := make([]string, len(p.MailboxErrors))
		for i, me := range p.MailboxErrors {
			names[i] = mailboxLabel(me.Mailbox)
		}
		b.addWrapped("  ", "[!] "+itoa(len(names))+" accounts weren't scanned: "+strings.Join(names, ", "))
		tail = "Reconnect them in settings. " + tail
	}
	b.addWrapped("      ", tail)
	b.blank()
}

// itemRows is how many interior rows each item will actually render to: its own
// row, plus the lines its rationale wraps to when the section shows rationales.
func (p emailPreScan) itemRows(b *box, items []preScanItem, showMailbox, withRationale bool) []int {
	out := make([]int, len(items))
	for i, it := range items {
		out[i] = 1
		if !withRationale {
			continue
		}
		detail := it.rationale()
		if showMailbox && it.Mailbox != "" {
			detail = mailboxLabel(it.Mailbox) + " · " + detail
		}
		if strings.TrimSpace(detail) != "" {
			out[i] += len(wrap(detail, b.inner()-visualLen(rationaleIndent)))
		}
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

func (p emailPreScan) footerLines(t preScanTotals) []string {
	var out []string
	if t.Informational > 0 {
		out = append(out, itoa(t.Informational)+" informational, not listed.")
	}
	if line := p.preferencesLine(); line != "" {
		out = append(out, line)
	}
	return out
}

// section draws one bucket and returns the running row number.
//
// show is how many rows fit; total is the PRE-CAP count from `totals`. The
// header therefore reads "3 of 7" whenever anything is hidden — whether the
// agent capped it or the row budget did. A bare count would imply the list is
// everything, which is the exact failure `totals` exists to prevent.
func (p emailPreScan) section(b *box, label string, items []preScanItem, show, total, start int, showMailbox, withRationale bool) int {
	if len(items) == 0 || show <= 0 {
		return start
	}
	if show > len(items) {
		show = len(items)
	}
	if start > 0 {
		b.blank()
	}
	count := itoa(show)
	if total > show {
		count = itoa(show) + " of " + itoa(total)
	}
	b.sectionHeader(label, count)

	n := start
	for _, it := range items[:show] {
		n++
		b.row(n, displaySender(it.Sender), it.Subject)
		if !withRationale {
			continue
		}
		detail := it.rationale()
		if showMailbox && it.Mailbox != "" {
			detail = mailboxLabel(it.Mailbox) + " · " + detail
		}
		if strings.TrimSpace(detail) != "" {
			b.addWrapped(rationaleIndent, detail)
		}
	}
	if extra := total - show; extra > 0 {
		b.add(rationaleIndent + "+" + itoa(extra) + " more")
	}
	return n
}

func (p emailPreScan) renderEmpty(b *box, t preScanTotals) {
	b.addWrapped("  ", "Nothing needs you.")
	b.addWrapped("  ", itoa(p.scanned(t))+" messages scanned · 0 urgent · 0 waiting on a reply")
	if t.Informational > 0 {
		b.addWrapped("  ", itoa(t.Informational)+" informational, not listed.")
	}
	if line := p.preferencesLine(); line != "" {
		b.addWrapped("  ", line)
	}
}

func (p emailPreScan) isEmpty() bool {
	return len(p.Urgent) == 0 && len(p.Actionable) == 0 && len(p.SuggestedArchives) == 0
}

// totalsOrDerived falls back to the visible list lengths when the agent omitted
// `totals`. Derived totals equal the capped counts, so "N of M" simply does not
// appear — better than inventing a number.
func (p emailPreScan) totalsOrDerived() preScanTotals {
	if p.Totals != nil {
		return *p.Totals
	}
	return preScanTotals{
		Urgent:            len(p.Urgent),
		Actionable:        len(p.Actionable),
		Informational:     p.InformationalCount,
		SuggestedArchives: len(p.SuggestedArchives),
	}
}

func (p emailPreScan) scanned(t preScanTotals) int {
	return t.Urgent + t.Actionable + t.Informational + t.SuggestedArchives
}

func (p emailPreScan) title(t preScanTotals) string {
	return "Inbox · " + itoa(p.scanned(t)) + " scanned"
}

// multiMailbox reports whether rows came from more than one account, which is
// the only case where tagging each row with its mailbox is information rather
// than noise.
func (p emailPreScan) multiMailbox() bool {
	seen := map[string]bool{}
	for _, group := range [][]preScanItem{p.Urgent, p.Actionable, p.SuggestedArchives} {
		for _, it := range group {
			if it.Mailbox != "" {
				seen[it.Mailbox] = true
			}
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
	case "":
		return "A mailbox"
	default:
		return provider
	}
}
