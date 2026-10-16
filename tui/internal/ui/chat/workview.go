// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/components"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// A turn reads as one bullet per meaningful event:
//
//	● I'll read each contract first, then build the register.   (narration)
//	● Read 8 files · 41s                                        (actions, folded)
//	  └ msa_acme.docx, nda_beta.docx, … 6 more
//	● Install formulas library to verify workbook · 12s         (the agent's goal)
//	  └ python -m pip install formulas --quiet
//	  └ ✗ failed: No module named pip — carried on
//
// A green dot is done, a grey one is neutral, a red one a failure that ended
// the work. The words carry the state too, so nothing rests on colour alone.
// What is running RIGHT NOW is not a bullet: it is the status line under the
// transcript (statusLine), the one row that moves.

const (
	glyphDot    = "●"
	glyphDetail = "└"
	glyphOK     = "✓"
	glyphFail   = "✗"
)

// Where an action's headline came from, best first.
const (
	headDescription = iota
	headGoal
	headDerived
)

// foldAfter is how many entries a finished turn shows before its middle folds.
const foldAfter = 8

// foldKeepHead and foldKeepTail are what a folded turn keeps either side.
const (
	foldKeepHead = 2
	foldKeepTail = 5
)

// detailRowsFolded caps the `└` lines under one bullet; diffDiffRows* cap a diff.
const (
	detailRowsFolded = 2
	diffRowsFolded   = 4
	diffRowsExpanded = 20
	expandedCmdRows  = 6
)

var (
	dotDoneStyle   = lipgloss.NewStyle().Foreground(theme.Success)
	dotLiveStyle   = lipgloss.NewStyle().Foreground(theme.Dim)
	dotFailStyle   = lipgloss.NewStyle().Foreground(theme.Danger)
	dotSayStyle    = lipgloss.NewStyle().Foreground(theme.Text)
	headStyle      = lipgloss.NewStyle().Foreground(theme.Text).Bold(true)
	sayStyle       = lipgloss.NewStyle().Foreground(theme.Text)
	detailStyle    = lipgloss.NewStyle().Foreground(theme.Dim)
	stageStyle     = lipgloss.NewStyle().Foreground(theme.Dim).Italic(true)
	lineNoStyle    = lipgloss.NewStyle().Foreground(theme.Faint)
	addSignStyle   = lipgloss.NewStyle().Foreground(theme.Success)
	delSignStyle   = lipgloss.NewStyle().Foreground(theme.Danger)
	foldLineStyle  = lipgloss.NewStyle().Foreground(theme.Faint).Italic(true)
	recapStyleBase = theme.Dim
)

// workEntry is one bullet.
type workEntry struct {
	kind  string // "say", "act", "stage", "fold"
	text  string
	items []ActivityItem
	src   int
}

// headline is what an action leads with, and where that came from.
func headline(it ActivityItem) (string, int) {
	if d := it.Call.Description; d != "" {
		return d, headDescription
	}
	if g := clean(it.Goal); goalLike(g) {
		return truncateRunes(g, descriptionMax), headGoal
	}
	if d := it.Call.Derived; d != "" {
		return d, headDerived
	}
	return imperative(clean(it.Content)), headDerived
}

func categoryOfItem(it ActivityItem) string {
	if it.Call.Category != "" {
		return it.Call.Category
	}
	return categoryOf(it.Tool)
}

// goalLike reports whether an announced status reads as a goal the next call
// serves — "Install formulas library to verify workbook" — rather than a stage
// ("Working out how to answer"), the model musing ("I need to check…"), or a
// template placeholder. Only a goal can head an action.
func goalLike(s string) bool {
	if s == "" || isTemplateGoal(s) || displayWidth(s) > 2*descriptionMax {
		return false
	}
	first, _, _ := strings.Cut(s, " ")
	if strings.HasSuffix(strings.ToLower(first), "ing") {
		return false
	}
	return !musing(s)
}

// musing reports whether a status is the model thinking aloud ("Hmm, should I
// fix it too?", "Let me do it.") — prose for Ctrl+O, never a headline or the
// status line.
func musing(s string) bool {
	if strings.HasSuffix(strings.TrimSpace(s), "?") {
		return true
	}
	first, _, _ := strings.Cut(strings.TrimSpace(s), " ")
	switch strings.ToLower(strings.TrimRight(first, ",.:!")) {
	case "i", "i'll", "i'm", "i've", "i'd", "let", "let's", "we", "we'll", "we're",
		"now", "ok", "okay", "hmm", "so", "the", "this", "it", "well", "alright", "first", "next":
		return true
	}
	return false
}

// approvalClass is what an approval says beyond "it ran": a plain yes is not
// worth a word on every row, a no or a timeout is.
func approvalClass(a string) string {
	if a == "" || a == "approved" {
		return ""
	}
	return a
}

func failedItem(it ActivityItem) bool {
	if it.Success != nil && !*it.Success {
		return true
	}
	return strings.HasPrefix(strings.ToLower(clean(it.Detail)), "failed")
}

// isOpen reports whether an item is the action running right now.
func isOpen(it ActivityItem) bool {
	return isStep(it) && !it.Done && !strings.HasPrefix(it.Approval, "denied")
}

// buildEntries turns the activity log into bullets. While the turn runs, the
// open action is left out: the status line carries it. Once it is over
// (finished), every step is a bullet — including one that never got a result,
// which is often the one that explains how the turn ended.
func buildEntries(items []ActivityItem, expanded, finished bool) []workEntry {
	live := -1
	for i := len(items) - 1; i >= 0 && !finished; i-- {
		if isStep(items[i]) {
			if isOpen(items[i]) && items[i].Approval != approvalWaiting {
				live = i
			}
			break
		}
	}
	var out []workEntry
	for i, it := range items {
		switch it.Kind {
		case "note":
			text := clean(it.Content)
			if text == "" || (len(out) > 0 && out[len(out)-1].kind == "say" && out[len(out)-1].text == text) {
				continue
			}
			out = append(out, workEntry{kind: "say", text: text})
		case "status", "thinking":
			if !expanded {
				continue
			}
			text := clean(it.Content)
			if text == "" || isTemplateGoal(text) || (len(out) > 0 && out[len(out)-1].kind == "stage" && out[len(out)-1].text == text) {
				continue
			}
			out = append(out, workEntry{kind: "stage", text: text})
		case "tool", "confirm":
			if i == live {
				continue
			}
			head, src := headline(it)
			if n := len(out); n > 0 && canJoin(out[n-1], it, head, src) {
				out[n-1].items = append(out[n-1].items, it)
				continue
			}
			// Expanded, the goal's own stage line sits right above the action
			// it now heads; saying it twice in a row is one too many.
			if src == headGoal {
				for k := len(out) - 1; k >= 0 && k >= len(out)-2; k-- {
					if out[k].kind == "stage" && out[k].text == head {
						out = append(out[:k], out[k+1:]...)
						break
					}
				}
			}
			out = append(out, workEntry{kind: "act", text: head, items: []ActivityItem{it}, src: src})
		}
	}
	return out
}

// canJoin says whether it continues the bullet e. A failure always closes its
// bullet: the retry after it is a new event, and folding it in would hide that
// anything went wrong.
func canJoin(e workEntry, it ActivityItem, head string, src int) bool {
	if e.kind != "act" || e.src != src {
		return false
	}
	last := e.items[len(e.items)-1]
	if failedItem(last) || last.Approval == approvalWaiting {
		return false
	}
	if approvalClass(last.Approval) != approvalClass(it.Approval) {
		return false
	}
	if src != headDerived {
		return e.text == head
	}
	cat := categoryOfItem(it)
	if cat != categoryOfItem(last) {
		return false
	}
	// Different tools only share a bullet when the bullet can count them
	// ("Read 3 files"); otherwise one would be named for the other.
	if groupSummary(cat, 2, nil) == "" {
		return last.Tool == it.Tool
	}
	return true
}

// entryText is the bullet's headline as shown: a run of derived calls of one
// kind is counted ("Read 3 files"), and a described one repeated says so.
func entryText(e workEntry) string {
	n := len(e.items)
	if n > 1 {
		text := e.text
		if e.src == headDerived {
			if s := groupSummary(categoryOfItem(e.items[0]), n, e.items); s != "" {
				return s
			}
			// The same tool on different targets: name every target, so
			// "x3" never claims the first one was touched three times.
			for _, it := range e.items[1:] {
				next, _ := headline(it)
				text = mergeTargets(text, next)
			}
		}
		return fmt.Sprintf("%s x%d", text, n)
	}
	return e.text
}

// entryElapsed is wall time from the first call's start to the last one's end.
func entryElapsed(e workEntry) time.Duration {
	if len(e.items) == 0 {
		return 0
	}
	start, end := e.items[0].At, e.items[len(e.items)-1].End
	if start.IsZero() || end.IsZero() || end.Before(start) {
		return 0
	}
	return end.Sub(start)
}

// entryState is how a bullet ended: "ok", "fail" (a failure that mattered),
// "calm" (failed, but the agent carried on), or "live".
func entryState(e workEntry, recovered bool) string {
	last := e.items[len(e.items)-1]
	switch {
	case last.Approval == approvalWaiting:
		return "live"
	case strings.HasPrefix(last.Approval, "denied"):
		return "calm"
	case !last.Done:
		// No result yet: still running, or — once the turn is over — cut short.
		return "open"
	case failedItem(last) && recovered:
		return "calm"
	case failedItem(last):
		return "fail"
	}
	return "ok"
}

// renderEntries lays bullets out to width. finished says the turn is over, so a
// failure nothing after it recovered from is a real one.
func (m ChatModel) renderEntries(entries []workEntry, finished bool) []string {
	width := m.logWidth()
	var lines []string
	for i, e := range entries {
		switch e.kind {
		case "fold":
			lines = append(lines, "    "+foldLineStyle.Render(truncateRunes(e.text, width)))
		case "say":
			rows := m.sayRows()
			wrapped := wrapLog(e.text, "", width-2, rows)
			lines = append(lines, "  "+dotSayStyle.Render(glyphDot)+" "+sayStyle.Render(wrapped[0]))
			for _, r := range wrapped[1:] {
				lines = append(lines, "    "+sayStyle.Render(r))
			}
		case "stage":
			lines = append(lines, "    "+stageStyle.Render(truncateRunes(e.text, width)))
		case "act":
			lines = append(lines, m.renderAct(e, finished, laterSuccess(entries[i+1:]), width)...)
		}
	}
	return lines
}

func (m ChatModel) sayRows() int {
	if m.expandWork {
		return 6
	}
	return 2
}

// laterSuccess reports whether anything after a failure went through — the
// sign the agent carried on rather than stopping on it.
func laterSuccess(rest []workEntry) bool {
	for _, e := range rest {
		if e.kind != "act" {
			continue
		}
		for _, it := range e.items {
			if it.Done && !failedItem(it) {
				return true
			}
		}
	}
	return false
}

// renderAct draws one action bullet. A failure is calm (dim, never red) while
// the turn runs, because the agent may yet recover; once the turn is over it is
// red only if nothing after it went through.
func (m ChatModel) renderAct(e workEntry, finished, recovered bool, width int) []string {
	state := entryState(e, recovered || !finished)
	last := e.items[len(e.items)-1]

	suffix := ""
	if a := approvalClass(last.Approval); a != "" {
		suffix += " · " + clean(a)
	}
	if d := entryElapsed(e); d >= time.Second && state != "live" {
		suffix += " · " + humanDuration(d)
	}

	if state == "open" && finished && approvalClass(last.Approval) == "" {
		suffix += " · did not finish"
	}
	dot := dotDoneStyle
	switch state {
	case "fail":
		dot = dotFailStyle
	case "calm", "live", "open":
		dot = dotLiveStyle
	}
	head := wrapLog(entryText(e), "", width-2-displayWidth(suffix), 1)[0]
	lines := []string{"  " + dot.Render(glyphDot) + " " + headStyle.Render(head) + detailStyle.Render(suffix)}

	if m.expandWork {
		return append(lines, m.renderActExpanded(e, recovered || !finished, width)...)
	}

	var details []string
	if state == "fail" || state == "calm" {
		if f := failureLine(last, state == "calm" && finished); f != "" {
			details = append(details, f)
		}
	}
	if what := whatLine(e); what != "" {
		details = append([]string{what}, details...)
	}
	if state == "ok" {
		if out := conciseDetail(last.Detail); out != "" && !strings.EqualFold(out, entryText(e)) && !rawDump(out) {
			details = append(details, out)
		}
	}
	if len(details) > detailRowsFolded {
		details = details[len(details)-detailRowsFolded:]
	}
	for _, d := range details {
		style := detailStyle
		if state == "fail" && strings.HasPrefix(d, glyphFail) {
			style = failStyle
		}
		rows := 1
		if strings.HasPrefix(d, glyphFail) && (state == "fail" || !finished) {
			// A failure that ended the work, or one the agent has not moved
			// past yet, keeps its tail: that is the remedy.
			rows = logDetailRows
		}
		wrapped := wrapLog(d, "", width-4, rows)
		lines = append(lines, "    "+style.Render(glyphDetail+" "+wrapped[0]))
		for _, r := range wrapped[1:] {
			lines = append(lines, "      "+style.Render(r))
		}
	}
	lines = append(lines, m.renderChanges(e, diffRowsFolded, width)...)
	return lines
}

// whatLine is the one dim line of evidence under a bullet, when it adds to the
// headline: the command a shell call ran, or the files a run of reads touched.
func whatLine(e workEntry) string {
	last := e.items[len(e.items)-1]
	switch categoryOfItem(last) {
	case catShell:
		cmd := firstLine(last.Call.Command)
		if cmd == "" {
			return ""
		}
		return cmd + timing(last, len(e.items) > 1)
	case catPython:
		if e.src != headDerived && last.Call.Derived != "" {
			return last.Call.Derived + timing(last, len(e.items) > 1)
		}
	case catRead, catDocs:
		// Under a goal, the files are the evidence; under a derived headline
		// they are already named, unless there were too many to name.
		names := namesList(e.items)
		if e.src != headDerived || strings.Contains(entryText(e), "files") {
			return names
		}
	}
	return ""
}

// timing is " (12s · 10 lines)" for a call that took long enough to mention.
// withTime is false when the bullet's own headline already carries the time.
func timing(it ActivityItem, withTime bool) string {
	var parts []string
	if withTime && !it.At.IsZero() && !it.End.IsZero() {
		if d := it.End.Sub(it.At); d >= time.Second {
			parts = append(parts, humanDuration(d))
		}
	}
	if it.OutLines > 0 {
		parts = append(parts, countNoun(it.OutLines, "line"))
	}
	if len(parts) == 0 {
		return ""
	}
	return " (" + strings.Join(parts, " · ") + ")"
}

// namesList names the files a run of calls touched: "a.docx, b.docx, … 6 more".
func namesList(items []ActivityItem) string {
	var names []string
	seen := map[string]bool{}
	for _, it := range items {
		for _, p := range it.Call.Reads {
			if n := baseName(p); n != "" && !seen[n] {
				seen[n] = true
				names = append(names, n)
			}
		}
	}
	if len(names) == 0 {
		return ""
	}
	const show = 3
	if len(names) > show {
		return strings.Join(names[:show], ", ") + fmt.Sprintf(", … %d more", len(names)-show)
	}
	return strings.Join(names, ", ")
}

// failureLine is "✗ failed: <reason>", and for a failure the agent moved past,
// says so — that is what makes it calm rather than alarming.
func failureLine(it ActivityItem, calm bool) string {
	if strings.HasPrefix(it.Approval, "denied") {
		return ""
	}
	reason := clean(it.Detail)
	if reason == "" {
		reason = "failed"
	}
	if !strings.HasPrefix(strings.ToLower(reason), "failed") {
		reason = "failed: " + reason
	}
	if calm {
		return glyphFail + " " + reason + " — carried on"
	}
	return glyphFail + " " + reason
}

// renderActExpanded is Ctrl+O's view of one bullet: every call in it, what
// each was asked to run, its timing and its full outcome.
func (m ChatModel) renderActExpanded(e workEntry, recovered bool, width int) []string {
	var lines []string
	for _, it := range e.items {
		label := it.Call.Derived
		if label == "" {
			label = clean(it.Content)
		}
		meta := timing(it, true)
		if a := clean(it.Approval); a != "" {
			meta += " · " + a
		}
		// A single call is already named by its bullet; say only what is new.
		if len(e.items) == 1 && label == e.text {
			label = ""
			meta = strings.TrimPrefix(strings.TrimSpace(strings.TrimPrefix(meta, " ·")), "(")
			meta = strings.TrimSuffix(meta, ")")
		}
		if row := strings.TrimSpace(label + meta); row != "" {
			lines = append(lines, "    "+detailStyle.Render(glyphDetail+" "+truncateRunes(row, width-4)))
		}
		if cmd := it.Call.Command; cmd != "" {
			// Wrapped, not cut: Ctrl+O is where the whole command is read.
			var rows []string
			for _, l := range splitLines(cmd) {
				rows = append(rows, wrapLog(l, "", max(width-8, 8), logHeadRows)...)
			}
			for i, r := range rows {
				if i == expandedCmdRows {
					lines = append(lines, "        "+foldLineStyle.Render(fmt.Sprintf("… %d more lines", len(rows)-expandedCmdRows)))
					break
				}
				lines = append(lines, "        "+devPayloadStyle.Render(r))
			}
		}
		if d := expandedDetail(it.Detail); d != "" {
			style := detailStyle
			if failedItem(it) && !recovered {
				style = failStyle
			}
			for _, r := range wrapLog(d, "", width-6, logDetailRows) {
				lines = append(lines, "      "+style.Render(r))
			}
		}
	}
	return append(lines, m.renderChanges(e, diffRowsExpanded, width)...)
}

// renderChanges draws the first write or edit in a bullet as a compact diff,
// and counts the rest.
func (m ChatModel) renderChanges(e workEntry, maxRows, width int) []string {
	var changes []*fileChange
	for _, it := range e.items {
		if c := it.Call.Change; c != nil && !failedItem(it) {
			changes = append(changes, c)
		}
	}
	if len(changes) == 0 {
		return nil
	}
	lines := diffLines(changes[0], maxRows, width)
	if extra := len(changes) - 1; extra > 0 {
		lines = append(lines, "      "+foldLineStyle.Render(fmt.Sprintf("… %d more %s changed", extra, pluralize("files", extra))))
	}
	return lines
}

// diffLines renders one change: a summary line, then numbered rows with +/-,
// syntax colour and a tinted band, cut with "… N more lines".
func diffLines(c *fileChange, maxRows, width int) []string {
	var summary []string
	if c.NewTotal > 0 {
		summary = append(summary, "Added "+countNoun(c.NewTotal, "line"))
	}
	if c.OldTotal > 0 {
		summary = append(summary, "removed "+countNoun(c.OldTotal, "line"))
	}
	if len(summary) == 0 {
		return nil
	}
	head := strings.Join(summary, ", ")
	head = strings.ToUpper(head[:1]) + head[1:]
	lines := []string{"    " + detailStyle.Render(glyphDetail+" "+head)}

	type row struct {
		no   int
		side components.DiffSide
		text string
	}
	var rows []row
	start := c.StartLine
	for i, l := range c.Old {
		no := 0
		if start > 0 {
			no = start + i
		}
		rows = append(rows, row{no, components.DiffRemoved, l})
	}
	for i, l := range c.New {
		no := 0
		if start > 0 {
			no = start + i
		}
		rows = append(rows, row{no, components.DiffAdded, l})
	}
	total := c.OldTotal + c.NewTotal

	numW := 0
	for _, r := range rows {
		if w := len(fmt.Sprint(r.no)); r.no > 0 && w > numW {
			numW = w
		}
	}
	shown := 0
	for _, r := range rows {
		if shown == maxRows {
			break
		}
		gutter := "      "
		if numW > 0 {
			num := ""
			if r.no > 0 {
				num = fmt.Sprint(r.no)
			}
			gutter += lineNoStyle.Render(fmt.Sprintf("%*s", numW, num)) + " "
		}
		sign := addSignStyle.Render("+")
		if r.side == components.DiffRemoved {
			sign = delSignStyle.Render("-")
		}
		codeW := width - 6 - numW - 3
		if codeW < 4 {
			codeW = 4
		}
		lines = append(lines, gutter+sign+" "+components.SyntaxLine(c.Path, scrubLine(r.text), r.side, codeW))
		shown++
	}
	if rest := total - shown; rest > 0 {
		lines = append(lines, "      "+foldLineStyle.Render(fmt.Sprintf("… %d more %s", rest, pluralize("lines", rest))))
	}
	return lines
}

// foldEntries keeps a long finished turn readable: the first and last few
// bullets, and one line saying how many were folded between them.
func foldEntries(entries []workEntry) []workEntry {
	if len(entries) <= foldAfter {
		return entries
	}
	hidden := len(entries) - foldKeepHead - foldKeepTail
	out := append([]workEntry(nil), entries[:foldKeepHead]...)
	out = append(out, workEntry{kind: "fold", text: fmt.Sprintf("… %d more %s · Ctrl+O to expand", hidden, pluralize("actions", hidden))})
	return append(out, entries[len(entries)-foldKeepTail:]...)
}

// --- finished turn ---------------------------------------------------------

// renderWork draws a finished turn's record.
func (m ChatModel) renderWork(msg *Message) string {
	entries := buildEntries(msg.Work, m.expandWork, true)
	if !m.expandWork {
		entries = foldEntries(entries)
	}
	return strings.Join(m.renderEntries(entries, true), "\n")
}

// --- live turn -------------------------------------------------------------

// renderLiveRegion draws the running turn's finished bullets, newest last,
// within the height budget. What is running now is the status line's.
func (m ChatModel) renderLiveRegion() string {
	entries := buildEntries(m.activity, m.expandWork, false)
	groups := make([][]string, len(entries))
	for i := range entries {
		groups[i] = m.renderEntries(entries[i:i+1], false)
	}
	plan := m.renderPlan(m.logWidth() + 4)
	budget := max(m.logRows()-len(plan), 1)
	total := 0
	for _, g := range groups {
		total += len(g)
	}
	kept := len(groups)
	if total > budget {
		// Something is dropped, so one row says so; the newest bullets keep
		// the rest. A single bullet taller than that is cut, never skipped:
		// an empty region reads as a hang.
		avail, rows := max(budget-1, 1), 0
		kept = 0
		for i := len(groups) - 1; i >= 0 && rows+len(groups[i]) <= avail; i-- {
			rows += len(groups[i])
			kept++
		}
		if kept == 0 && len(groups) > 0 {
			kept = 1
			newest := groups[len(groups)-1]
			groups[len(groups)-1] = newest[:min(len(newest), avail)]
		}
	}
	var lines []string
	if hidden := len(groups) - kept; hidden > 0 {
		lines = append(lines, "    "+foldLineStyle.Render(fmt.Sprintf("… %d earlier %s · Ctrl+O", hidden, pluralize("actions", hidden))))
	}
	for _, g := range groups[len(groups)-kept:] {
		lines = append(lines, g...)
	}
	// Last, so it sits right above the running step on the status line.
	lines = append(lines, plan...)
	return strings.Join(lines, "\n")
}

// --- status line -----------------------------------------------------------

// statusRows is how many rows the status area takes while a turn runs: the
// status line and the row under it (a tip, or air before the composer). Fixed,
// so a tip coming and going never changes the transcript's height mid-turn.
const statusRows = 2

// liveItem is the action running right now, if any.
func (m ChatModel) liveItem() (ActivityItem, bool) {
	for i := len(m.activity) - 1; i >= 0; i-- {
		it := m.activity[i]
		if isStep(it) {
			if isOpen(it) {
				return it, true
			}
			return ActivityItem{}, false
		}
	}
	return ActivityItem{}, false
}

// statusPhrase says what the agent is doing right now, in plain words.
func (m ChatModel) statusPhrase() string {
	switch {
	case m.cancelPending:
		return "Stopping at the next step"
	case m.awaitingUser():
		return "Waiting for your answer"
	}
	if it, ok := m.liveItem(); ok {
		head, _ := headline(it)
		return head
	}
	if m.buffer != "" {
		return "Writing your answer"
	}
	if n := len(m.activity); n > 0 && m.activity[n-1].Kind == "stage" {
		return clean(m.activity[n-1].Content)
	}
	if p := m.phasePhrase(); p != "" {
		return p
	}
	if g := m.standingGoal(); g != "" && !musing(g) && !isTemplateGoal(g) {
		return g
	}
	if !anyCompleted(m.activity) {
		return "Waiting for the model"
	}
	return "Thinking about the next step"
}

// standingGoal is the goal the agent announced since its last call, if any.
func (m ChatModel) standingGoal() string {
	if !m.goalFresh {
		return ""
	}
	for i := len(m.activity) - 1; i >= 0; i-- {
		if m.activity[i].Kind == "status" {
			return clean(m.activity[i].Content)
		}
	}
	return ""
}

// takeGoal hands the standing goal to the call it introduced, once.
func (m *ChatModel) takeGoal() string {
	g := m.standingGoal()
	m.goalFresh = false
	return g
}

// statusLine is the one live row: spinner, what is happening, and the turn's
// clock — "⣟ Reading contract_A.docx… (1m 41s)". The words shimmer; nothing
// else on screen moves.
func (m ChatModel) statusLine(now time.Time) string {
	var elapsed time.Duration
	if !m.queryStart.IsZero() {
		elapsed = m.turnElapsed(now)
	}
	clock := " (" + humanDuration(elapsed) + ")"
	phrase := strings.TrimRight(m.statusPhrase(), ".…")
	room := m.width - 4 - displayWidth(clock) - 1
	if room < 4 {
		room = 4
	}
	phrase = truncateRunes(phrase, room-1)
	if !strings.HasSuffix(phrase, "…") {
		phrase += "…"
	}
	text := m.motion.shimmer(phrase, theme.Success, theme.Text, elapsed, shimmerPeriod, false)
	return "  " + m.spinner.View() + " " + text + activityStyle.Render(clock)
}

// statusTip is the row under the status line: a tip when one helps, else air.
func (m ChatModel) statusTip(now time.Time) string {
	if m.queryStart.IsZero() || m.awaitingUser() || anyCompleted(m.activity) || m.turnElapsed(now) < stillWorkingAfter {
		return ""
	}
	tip := " still working — a local model usually takes 60-90s"
	if m.modelRemote {
		tip = " still working — a long request takes a while to read"
	}
	return "    " + activityStyle.Render(truncateRunes(glyphDetail+tip, max(m.width-5, 1)))
}

// --- recap -------------------------------------------------------------------

// recapLine is the finished turn's one line: what it did, how long, how many
// tokens, and what it cost when that is known — "✓ Read 8 files · ran 2 Python
// scripts · 39s · ↓ 9.8k tokens · $0.04".
func (m ChatModel) recapLine(msg *Message) string {
	if msg.Duration <= 0 {
		return ""
	}
	tail := []string{humanDuration(msg.Duration)}
	if msg.Tokens > 0 {
		tail = append(tail, "↓ "+thousands(msg.Tokens)+" tokens")
	}
	if msg.CostUSD > 0 {
		tail = append(tail, usdText(msg.CostUSD))
	}
	// The files a turn produced are named once, in the Created block above.
	var lead []string
	for _, part := range strings.Split(msg.Recap, " · ") {
		if part != "" {
			lead = append(lead, part)
		}
	}
	// What the turn did gives way before how long it took: the time and the
	// tokens are the part a glance is for.
	fit := m.width - 6
	for len(lead) > 0 && m.width > 0 && displayWidth(strings.Join(append(append([]string{}, lead...), tail...), " · ")) > fit {
		lead = lead[:len(lead)-1]
	}
	line := strings.Join(append(lead, tail...), " · ")
	if len(lead) > 0 {
		line = glyphOK + " " + line
	}
	return line
}

func usdText(v float64) string {
	if v < 0.001 {
		return "<$0.001"
	}
	if v < 1 {
		return fmt.Sprintf("$%.3f", v)
	}
	return fmt.Sprintf("$%.2f", v)
}

// renderRecap draws the recap, with its one sweep when the turn just landed.
func (m ChatModel) renderRecap(msg *Message, now time.Time) string {
	line := m.recapLine(msg)
	if line == "" {
		return ""
	}
	line = truncateRunes(line, max(m.width-4, 8))
	if !msg.FinishedAt.IsZero() && m.motion.finishAt.Equal(msg.FinishedAt) {
		return "  " + m.motion.sweepOnce(line, recapStyleBase, theme.Success, now.Sub(msg.FinishedAt), finishSweep)
	}
	return "  " + activityStyle.Render(line)
}

// workRecap summarises a turn's calls by kind: "read 8 files · ran 3 commands".
func workRecap(items []ActivityItem) string {
	counts := map[string]int{}
	reads := map[string]bool{}
	for _, it := range items {
		if !isStep(it) || !it.Done || failedItem(it) {
			continue
		}
		cat := categoryOfItem(it)
		if cat == catWrite && len(it.Call.Writes) > 0 && deliverableExts[extOf(it.Call.Writes[0])] {
			continue
		}
		if cat == catRead {
			for _, p := range it.Call.Reads {
				reads[baseName(p)] = true
			}
			if len(it.Call.Reads) == 0 {
				counts[cat]++
			}
			continue
		}
		counts[cat]++
	}
	counts[catRead] += len(reads)
	var parts []string
	add := func(cat, s string) {
		if counts[cat] > 0 {
			parts = append(parts, s)
		}
	}
	add(catRead, "read "+countNoun(counts[catRead], "file"))
	add(catWeb, fmt.Sprintf("searched the web %s", timesText(counts[catWeb])))
	add(catFetch, "read "+countNoun(counts[catFetch], "web page"))
	add(catDocs, fmt.Sprintf("searched your documents %s", timesText(counts[catDocs])))
	add(catShell, "ran "+countNoun(counts[catShell], "command"))
	add(catPython, "ran "+countNoun(counts[catPython], "Python script"))
	add(catEdit, "edited "+countNoun(counts[catEdit], "file"))
	// Writes of results are named by the Created block; this counts the rest
	// (source, scratch), so nothing is said twice.
	add(catWrite, "wrote "+countNoun(counts[catWrite], "file"))
	if len(parts) == 0 {
		return ""
	}
	s := strings.Join(parts, " · ")
	return strings.ToUpper(s[:1]) + s[1:]
}

func timesText(n int) string {
	if n == 1 {
		return "once"
	}
	return fmt.Sprintf("%d times", n)
}

// --- helpers -----------------------------------------------------------------

// humanDuration is "41s", "1m 41s", "1h 20m 3s" — under ten seconds it keeps a
// decimal, because 2.1s and 2.9s are different answers to "was that fast?".
func humanDuration(d time.Duration) string {
	if d < 0 {
		d = 0
	}
	if d < 10*time.Second {
		return fmt.Sprintf("%.1fs", d.Seconds())
	}
	total := int(d.Round(time.Second).Seconds())
	h, mi, s := total/3600, (total%3600)/60, total%60
	switch {
	case h > 0:
		return fmt.Sprintf("%dh %dm %ds", h, mi, s)
	case mi > 0:
		return fmt.Sprintf("%dm %ds", mi, s)
	}
	return fmt.Sprintf("%ds", s)
}

func (m ChatModel) now() time.Time {
	if m.clock != nil {
		return m.clock()
	}
	return time.Now()
}

// outputLines is how many lines of output a result carried, 0 when it carried
// none it would admit to.
func outputLines(data json.RawMessage) int {
	obj := decodeObject(data)
	if obj == nil {
		return 0
	}
	for _, k := range []string{"stdout", "output"} {
		if s, ok := obj[k].(string); ok && strings.TrimSpace(s) != "" {
			return len(splitLines(s))
		}
	}
	if co, ok := obj["command_output"].(map[string]interface{}); ok {
		if s, ok := co["stdout"].(string); ok && strings.TrimSpace(s) != "" {
			return len(splitLines(s))
		}
	}
	return 0
}

// locateEditMaxBytes bounds the read locateEdit does.
const locateEditMaxBytes = 4 << 20

// locateEdit finds the line an edit landed on, so its diff can be numbered.
// 0 when the file cannot be read or the new text is not in it.
func locateEdit(c *fileChange) int {
	if c == nil || c.Path == "" {
		return 0
	}
	info, err := os.Stat(c.Path)
	if err != nil || info.IsDir() || info.Size() > locateEditMaxBytes {
		return 0
	}
	raw, err := os.ReadFile(c.Path) // #nosec G304 -- a path the agent just edited, read to number the diff
	if err != nil {
		return 0
	}
	raw = bytes.ReplaceAll(raw, []byte("\r\n"), []byte("\n"))
	anchor := c.New
	if len(anchor) == 0 {
		return 0
	}
	needle := []byte(strings.Join(anchor, "\n"))
	at := bytes.Index(raw, needle)
	if at < 0 {
		return 0
	}
	return bytes.Count(raw[:at], []byte("\n")) + 1
}

// markFinish starts the recap's one sweep on the answer that just landed.
func (m *ChatModel) markFinish() {
	if n := len(m.messages); n > 0 && !m.motion.still {
		m.motion.finishAt = m.messages[n-1].FinishedAt
		m.motion.sweepUnpainted = true
	}
}

// turnCostUSD is what a turn cost, for a per-token provider with a known
// price. 0 otherwise — never a guessed rate.
func (m ChatModel) turnCostUSD(in, cached, out int) float64 {
	if !m.isMeteredModel() || in+out == 0 {
		return 0
	}
	price := lookupPrice(m.modelID)
	if price == nil {
		return 0
	}
	return price.totalUSD(in, cached, out)
}

// answerTrailer is everything drawn under an answer: the files it produced,
// the recap line, and — with Ctrl+O — the full stats and the turn's metrics.
func (m ChatModel) answerTrailer(msg *Message) string {
	var rows []string
	if files := m.renderDeliverables(msg.Deliverables); len(files) > 0 {
		// Air between the answer and the files, so they read as the result.
		rows = append(append(rows, ""), files...)
	}
	if recap := m.renderRecap(msg, m.now()); recap != "" {
		rows = append(rows, recap)
	}
	if stats := m.answerStats(msg); stats != "" {
		rows = append(rows, activityStyle.Render("  "+stats))
	}
	if block := m.turnMetricsBlock(msg); block != "" {
		rows = append(rows, block)
	}
	return strings.Join(rows, "\n")
}

// showStatusLine reports whether the live status rows are drawn. The warm-up
// stage has its own spinner and checklist in their place.
//
// A pending prompt takes the rows instead: it is the question the turn is
// parked on, the status bar already says so, and on a short terminal those two
// rows are the difference between the command being on screen or clipped.
func (m ChatModel) showStatusLine() bool {
	return m.streaming && !(m.warming && !m.warmHidden) && m.width > 0 &&
		!(m.confirmation != nil && m.confirmation.Pending())
}

// expandedDetail is an outcome in full for Ctrl+O, minus the harness's own
// "success": latency and size stay, a bare status word goes.
func expandedDetail(detail string) string {
	var keep []string
	for _, part := range strings.Split(clean(detail), " · ") {
		if p := strings.TrimSpace(part); p != "" && !isBareStatusWord(p) {
			keep = append(keep, p)
		}
	}
	return strings.Join(keep, " · ")
}

// rawDump reports an outcome that is the tool's raw output squeezed onto one
// row — a directory tree, a table border — which says nothing folded. Ctrl+O
// still shows it.
func rawDump(s string) bool {
	for _, mark := range []string{"|--", "`--", "├", "└──", "+--", "─┼─"} {
		if strings.Contains(s, mark) {
			return true
		}
	}
	return false
}
