package cards

import (
	"encoding/json"
	"fmt"
	"strings"
)

// The email agent's attention-view envelope, mirroring
// hub/agents/email/npm/src/types.ts (EmailAttentionResult) field for field.
//
// Rendered on open, without a user prompt — this is not a `/query` tool_result
// card, so it is not wired into Render()'s dispatch table; the TUI decodes and
// renders it directly from a GET /v1/email/attention response (#2582).

type attentionItem struct {
	Kind      string `json:"kind"`
	MessageID string `json:"message_id"`
	ThreadID  string `json:"thread_id"`
	Sender    string `json:"sender"`
	Subject   string `json:"subject"`
	Why       string `json:"why"`
	DueHint   string `json:"due_hint"`
	Mailbox   string `json:"mailbox"`
}

type attentionCoverage struct {
	Scanned       int            `json:"scanned"`
	TotalUnread   *int           `json:"total_unread"`
	ScanTruncated bool           `json:"scan_truncated"`
	Degraded      bool           `json:"degraded"`
	MailboxErrors []mailboxError `json:"mailbox_errors"`
}

type emailAttention struct {
	Kind            string            `json:"kind"`
	Items           []attentionItem   `json:"items"`
	Coverage        attentionCoverage `json:"coverage"`
	GeneratedAt     string            `json:"generated_at"`
	CacheAgeSeconds float64           `json:"cache_age_seconds"`
	Stale           bool              `json:"stale"`
}

// maxAttentionSectionRows bounds how many rows one signal section renders
// before collapsing the rest into a "+N more" line — this view has no
// per-message cap on the server side (unlike the pre-scan card's urgent/
// actionable buckets), so without a client-side bound a busy inbox could
// render a card far longer than the terminal.
const maxAttentionSectionRows = 8

// isAttentionEnvelope reports whether the payload actually claims to be an
// attention view, rather than merely failing to contradict one — the same
// guard emailprescan.go uses so `null`/`{}` cannot silently render as the
// honest empty state.
func isAttentionEnvelope(data json.RawMessage) bool {
	var probe map[string]json.RawMessage
	if err := json.Unmarshal(data, &probe); err != nil || probe == nil {
		return false
	}
	for _, field := range []string{"kind", "items", "coverage", "generated_at"} {
		if _, ok := probe[field]; ok {
			return true
		}
	}
	return false
}

// RenderEmailAttention draws the "what needs you" attention view at the given
// outer width. Exported (unlike the /query card renderers) because it is
// invoked directly by the chat model on open, not through Render()'s
// tool_result dispatch.
//
// seen is the set of message_ids a card rendered earlier in the same turn
// already showed (pass nil, or use Render, for a standalone render with no
// dedup). An item whose id is already in seen is skipped; every non-empty id
// this call renders is returned so a later card can be threaded against it
// too. An item with no message_id is never treated as a duplicate.
func RenderEmailAttention(data json.RawMessage, width int, seen map[string]bool) (string, []string) {
	var a emailAttention
	if err := json.Unmarshal(data, &a); err != nil {
		return renderInvalid("email_attention", err.Error(), data, width), nil
	}
	if k := strings.TrimSpace(a.Kind); k != "" && k != "email_attention" {
		return renderInvalid("email_attention", "kind is "+k+", expected email_attention", data, width), nil
	}
	if !isAttentionEnvelope(data) {
		return renderInvalid("email_attention", "payload carries no attention-view fields", data, width), nil
	}

	hadItems := len(a.Items) > 0
	var ids []string
	a.Items, ids = dedupByMessageID(a.Items, func(it attentionItem) string { return it.MessageID }, seen)
	if hadItems && len(a.Items) == 0 {
		// Every item was already shown by an earlier card this turn -- this
		// card would add nothing, so (unlike a genuinely empty scan) it does
		// not render at all rather than claiming "Nothing needs you".
		return "", nil
	}

	b := newBox(a.title(), width)

	a.renderStaleness(b)
	renderMailboxErrorBanner(b, a.Coverage.MailboxErrors)

	if len(a.Items) == 0 {
		a.renderEmpty(b)
		return b.render(), ids
	}

	sections := []struct {
		label string
		kind  string
	}{
		{"MEETING PROPOSALS", "meeting_request"},
		{"WAITING ON YOU", "waiting_on_you"},
		{"NEEDS REVIEW", "needs_review"},
		{"ACTION ITEMS", "action_item"},
	}

	shownAny := false
	row := 0
	for _, sec := range sections {
		items := a.itemsOfKind(sec.kind)
		if len(items) == 0 {
			continue
		}
		if shownAny {
			b.blank()
		}
		row = a.section(b, sec.label, items, row)
		shownAny = true
	}
	// An item whose kind the server introduced after this client was built
	// still gets shown — under a generic header — rather than silently
	// dropped from the card. It inherits the running counter, not a reset.
	if other := a.itemsOfUnknownKind(sections); len(other) > 0 {
		if shownAny {
			b.blank()
		}
		// Result deliberately dropped: this is the last section, so the running
		// counter has no further reader. Assigning it tripped ineffassign.
		a.section(b, "OTHER", other, row)
	}

	if footer := a.coverageFooterLine(); footer != "" {
		b.blank()
		b.addWrapped("  ", footer)
	}
	return b.render(), ids
}

// title must be identifiable on its own — the pre-scan card can appear in the same transcript.
func (a emailAttention) title() string {
	return fmt.Sprintf("Needs you · %d inbox messages scanned", a.Coverage.Scanned)
}

func (a emailAttention) itemsOfKind(kind string) []attentionItem {
	var out []attentionItem
	for _, it := range a.Items {
		if it.Kind == kind {
			out = append(out, it)
		}
	}
	return out
}

func (a emailAttention) itemsOfUnknownKind(known []struct {
	label string
	kind  string
}) []attentionItem {
	knownKinds := map[string]bool{}
	for _, k := range known {
		knownKinds[k.kind] = true
	}
	var out []attentionItem
	for _, it := range a.Items {
		if !knownKinds[it.Kind] {
			out = append(out, it)
		}
	}
	return out
}

// renderStaleness labels a cached result with its age — cache_age_seconds is
// nonzero whenever this call was served from cache rather than freshly
// computed. A cache the server itself marked `stale` (a failed refresh
// attempt past its freshness window) gets the stronger warning; a cache
// still within the freshness window gets a plain age note, since serving
// from cache is the normal, expected behaviour here (#2582 — compute on
// open, then cache) and must never be presented as if it were current.
func (a emailAttention) renderStaleness(b *box) {
	age := int(a.CacheAgeSeconds + 0.5)
	if age <= 0 {
		return
	}
	if a.Stale {
		b.addWrapped("  ", fmt.Sprintf(
			"[!] Showing data from %s ago — the last refresh attempt failed.",
			formatAttentionAge(age),
		))
	} else {
		b.addWrapped("  ", fmt.Sprintf("(as of %s ago)", formatAttentionAge(age)))
	}
	b.blank()
}

func formatAttentionAge(seconds int) string {
	if seconds < 60 {
		return itoa(seconds) + "s"
	}
	minutes := seconds / 60
	if minutes < 60 {
		return itoa(minutes) + "m"
	}
	hours := minutes / 60
	return itoa(hours) + "h"
}

// renderEmpty is the honest empty state (#2582's core acceptance criterion):
// "nothing needs you" may only appear stating what was actually covered —
// count, unread denominator when known, and whether the scan hit its
// ceiling — never as an unqualified whole-mailbox claim. This is the exact
// defect #2584 fixed one layer down for the pre-scan card; the same rule
// applies here.
func (a emailAttention) renderEmpty(b *box) {
	b.addWrapped("  ", "Nothing needs you.")
	b.addWrapped("  ", a.coverageLine())
}

// coverageLine is the one honest sentence stating what this view actually
// looked at — used both by the empty state and, when items ARE present, as
// the closing footer so a populated card cannot be read as "this is
// everything" either.
//
// "unread" must modify TotalUnread only — Scanned counts read mail too.
func (a emailAttention) coverageLine() string {
	c := a.Coverage
	line := itoa(c.Scanned) + " inbox messages scanned"
	if c.TotalUnread != nil {
		line += " · " + itoa(*c.TotalUnread) + " unread"
	}
	if c.ScanTruncated {
		line += " (of the " + itoa(c.Scanned) + " most recent — older mail may exist)"
	}
	return line
}

// coverageFooterLine is shown under a POPULATED card so a full item list is
// never mistaken for whole-mailbox coverage either.
func (a emailAttention) coverageFooterLine() string {
	return a.coverageLine() + "."
}

// section draws one bucket and returns the running row number — numbers must stay unique across the whole card, not just within one section.
func (a emailAttention) section(b *box, label string, items []attentionItem, start int) int {
	show := len(items)
	if show > maxAttentionSectionRows {
		show = maxAttentionSectionRows
	}
	count := itoa(show)
	if len(items) > show {
		count = itoa(show) + " of " + itoa(len(items))
	}
	b.sectionHeader(label, count)

	n := start
	for _, it := range items[:show] {
		n++
		sender := displaySender(it.Sender)
		if it.Kind == "action_item" && strings.TrimSpace(it.Sender) == "" {
			sender = "(action item)"
		}
		if it.Mailbox != "" {
			sender = mailboxLabel(it.Mailbox) + " · " + sender
		}
		b.row(n, sender, it.Subject)
		detail := it.Why
		if it.DueHint != "" {
			detail = strings.TrimSuffix(detail, ".") + " — due " + it.DueHint
		}
		if strings.TrimSpace(detail) != "" {
			b.addWrapped(rationaleIndent, detail)
		}
	}
	if extra := len(items) - show; extra > 0 {
		b.add(rationaleIndent + "+" + itoa(extra) + " more")
	}
	return n
}
