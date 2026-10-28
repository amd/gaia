// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"fmt"
	"path"
	"path/filepath"
	"strings"
	"unicode"

	tea "github.com/charmbracelet/bubbletea"
)

// A turn is read a step at a time, so the work log shows one short row per
// step: what ran, and a failure's reason when one failed. Everything else the
// agent says on the way — its narration, stage lines, the text it streamed
// before calling a tool — is detail, folded away until Ctrl+O asks for it.
//
// The log outlives the turn as a RoleWork message (commitWork), which is also
// the durable record of every failure and every confirmation answer; the
// transcript carries no separate line for either.

// approvalWaiting marks a step parked on the confirmation modal.
const approvalWaiting = "waiting for you"

// isNarration reports whether an item is the agent talking rather than acting.
func isNarration(item ActivityItem) bool {
	switch item.Kind {
	case "status", "thinking", "note":
		return true
	}
	return false
}

// isStep reports whether an item is work the user would want a record of.
func isStep(item ActivityItem) bool {
	return item.Kind == "tool" || item.Kind == "confirm"
}

// stashNarration moves text streamed so far into the log as a narration note.
//
// Called when a tool call (or a confirmation) arrives: whatever the model wrote
// before it was the preamble to that step, not the answer. Left in the buffer,
// each step's preamble ran straight into the next — "first.Reproduced: …Now
// the fix." — and the real answer arrived glued to the end of them.
func (m *ChatModel) stashNarration() {
	text := clean(m.buffer)
	if text == "" {
		m.buffer = ""
		return
	}
	m.stashed, m.buffer = m.buffer, ""
	content := truncateRunes(shortenPaths(text), narrationMax)
	if m.lastNote() == content {
		return
	}
	m.activity = append(m.activity, ActivityItem{Kind: "note", Content: content})
}

// lastNote is the most recent narration line in the work log, or "".
func (m *ChatModel) lastNote() string {
	for i := len(m.activity) - 1; i >= 0; i-- {
		if m.activity[i].Kind == "note" {
			return m.activity[i].Content
		}
	}
	return ""
}

// awaitApproval marks the step a confirmation gates. The tool_call for a gated
// tool arrives just ahead of its confirmation, so that step is the open one;
// when there is none, the confirmation gets a row of its own.
func (m *ChatModel) awaitApproval(action string) {
	for i := len(m.activity) - 1; i >= 0; i-- {
		item := &m.activity[i]
		if item.Kind != "tool" || item.Done {
			continue
		}
		if item.Tool == action || item.Tool == "" {
			item.Approval = approvalWaiting
			m.approvalAt = i
			return
		}
		break
	}
	m.activity = append(m.activity, ActivityItem{
		Kind:     "confirm",
		Tool:     action,
		Content:  toolNarration(action, nil, ""),
		Approval: approvalWaiting,
	})
	m.approvalAt = len(m.activity) - 1
}

// recordApproval writes the user's answer onto the step that asked for it.
// A step that is still running keeps running — its own result closes it — but
// a confirmation with no step of its own is finished by the answer.
func (m *ChatModel) recordApproval(action, outcome string, approved bool) {
	i := m.approvalAt
	m.approvalAt = -1
	if i < 0 || i >= len(m.activity) || m.activity[i].Approval != approvalWaiting {
		// The log was cleared under it (a cancel); the answer still gets a row.
		m.activity = append(m.activity, ActivityItem{
			Kind: "confirm", Tool: action, Content: toolNarration(action, nil, ""),
		})
		i = len(m.activity) - 1
	}
	item := &m.activity[i]
	item.Approval = outcome
	if item.Kind == "confirm" {
		item.Done = true
		item.Success = &approved
	}
}

// commitWork keeps the finished turn's steps in the transcript and clears the
// live log. A turn that only talked leaves no record — there is nothing in it
// the answer does not already say.
func (m *ChatModel) commitWork() {
	work := m.activity
	m.activity = nil
	m.approvalAt = -1
	var steps []ActivityItem
	for _, item := range work {
		if isStep(item) {
			steps = append(steps, item)
		}
	}
	var copyText []string
	for _, item := range collapseActivity(settleFailures(steps, true)) {
		copyText = append(copyText, stepText(item))
	}
	if len(copyText) == 0 {
		return
	}
	kept := append([]ActivityItem(nil), work...)
	for i := range kept {
		if kept[i].Approval == approvalWaiting {
			kept[i].Approval = "not answered"
		}
	}
	m.messages = append(m.messages, Message{
		Role: RoleWork,
		// Plain text, so double-click copies the steps rather than nothing.
		Content: strings.Join(copyText, "\n"),
		Work:    kept,
	})
}

// commitAnsweredWork commits a turn that ends in an answer. A closing status
// ("Done.") only leads into that answer, so it is not kept beside it.
func (m *ChatModel) commitAnsweredWork() {
	last := len(m.activity)
	for last > 0 && m.activity[last-1].Kind == "status" {
		last--
	}
	m.activity = m.activity[:last]
	m.commitWork()
}

// stepText is one step as plain text.
func stepText(item ActivityItem) string {
	line := clean(item.Content)
	if item.Repeat > 0 {
		line += fmt.Sprintf(" x%d", item.Repeat+1)
	}
	if item.Approval != "" {
		line += " · " + clean(item.Approval)
	}
	d := clean(item.Detail)
	if item.Recovery != "" && stepFailed(item) {
		// Copied text says what the screen says: not "failed" for a retried step.
		d = calmDetail(d, item.Recovery)
	}
	if d != "" {
		line += " — " + d
	}
	return line
}

// visibleWork is what the log shows of items: everything when expanded (or in
// --dev, whose job is the mechanics), only the steps when folded. Narration is
// the model thinking aloud ("Hmm, should I fix it too?") — noise even as the
// live line, which then names the state instead (idlePhrase).
func (m ChatModel) visibleWork(items []ActivityItem) []ActivityItem {
	if m.expandWork || m.dev {
		return items
	}
	out := make([]ActivityItem, 0, len(items))
	for _, item := range items {
		if !isNarration(item) {
			out = append(out, item)
		}
	}
	return out
}

// renderWork draws a finished turn's work record.
func (m ChatModel) renderWork(msg *Message) string {
	var lines []string
	for _, item := range collapseActivity(m.visibleWork(settleFailures(msg.Work, true))) {
		lines = append(lines, m.renderActivityItem(item, false, 0, 0)...)
	}
	return strings.Join(lines, "\n")
}

// hasWork reports whether there is any folded detail for Ctrl+O to open.
func (m ChatModel) hasWork() bool {
	if len(m.activity) > 0 {
		return true
	}
	for i := range m.messages {
		if m.messages[i].Role == RoleWork {
			return true
		}
	}
	return false
}

// toggleWorkDetail opens or folds every work log at once — the live one and
// each finished turn's record — so the transcript reads one way throughout.
func (m ChatModel) toggleWorkDetail() (tea.Model, tea.Cmd) {
	m.expandWork = !m.expandWork
	m.updateViewport()
	return m, nil
}

// conciseDetail is a successful step's outcome with the noise taken out: the
// harness's own "success" and a bare latency say nothing the closed row does
// not. Returns "" when nothing is left worth a row.
func conciseDetail(detail string) string {
	d := clean(detail)
	if i := strings.Index(d, ": "); i >= 0 && isBareStatusWord(d[:i]) {
		d = d[i+2:]
	}
	var keep []string
	for _, part := range strings.Split(d, " · ") {
		if p := strings.TrimSpace(part); p != "" && !isBareStatusWord(p) && !isLatency(p) {
			keep = append(keep, p)
		}
	}
	return strings.Join(keep, " · ")
}

// isLatency reports whether s is nothing but a latency figure: "0ms", "1.2s".
func isLatency(s string) bool {
	num := strings.TrimSuffix(strings.TrimSuffix(s, "ms"), "s")
	if num == s || num == "" {
		return false
	}
	return strings.Trim(num, "0123456789.") == ""
}

// How a failed step reads once the rest of its turn is known. An agent that
// fixes its own script and runs it again is working normally; drawing each
// attempt as a red failure made a successful turn look broken.
const (
	// recoveryPending: the turn is still running, so the agent may yet retry.
	// Not "retrying": nothing has said it will.
	recoveryPending = "error"
	// recoveryRetried: a later call of the same kind succeeded.
	recoveryRetried = "error, retried"
)

// stepFailed reports whether a step's outcome is a failure. The word counts as
// well as the flag: a sidecar's preview can report a failure its data did not.
func stepFailed(item ActivityItem) bool {
	return (item.Success != nil && !*item.Success) ||
		strings.HasPrefix(strings.ToLower(clean(item.Detail)), "failed")
}

// settleFailures returns items with each failed tool step's Recovery set.
//
// Decided from what has already happened, never from a guess at what will: a
// failure followed by a success of the same kind was recovered from; one with
// no such success yet is only provisional while the turn runs (final false),
// and stands as a failure once the turn is over. A user's own refusal is never
// softened — a denied step failed because they said so.
func settleFailures(items []ActivityItem, final bool) []ActivityItem {
	out := append([]ActivityItem(nil), items...)
	for i := range out {
		item := &out[i]
		item.Recovery = ""
		if item.Kind != "tool" || !item.Done || !stepFailed(*item) {
			continue
		}
		if item.Approval != "" && !strings.HasPrefix(item.Approval, "approved") {
			continue
		}
		// A call with no file or command to match on (a fetch, a query) is
		// never matched: another URL or SQL succeeding says nothing about it.
		if key := item.RetryKey; key != "" {
			for _, later := range out[i+1:] {
				if later.Kind == "tool" && later.Done && !stepFailed(later) &&
					later.RetryKey == key {
					item.Recovery = recoveryRetried
					break
				}
			}
		}
		if item.Recovery == "" && !final {
			item.Recovery = recoveryPending
		}
	}
	return out
}

// retryKey is what a later success must match to settle a failed call: the
// tool plus the job a shell command does (see commandKey), or the tool plus a
// file tool's file. "" means nothing to match on, so the failure is never
// marked retried. A failed `npm run build` is not recovered by `npm run lint`,
// `cd build && make` by `cd build && ls`, or clean.py by report.py.
func retryKey(tool string, args json.RawMessage) string {
	var fields struct {
		Command  string `json:"command"`
		FilePath string `json:"file_path"`
		Path     string `json:"path"`
	}
	if json.Unmarshal(args, &fields) != nil {
		return ""
	}
	if strings.TrimSpace(fields.Command) != "" {
		if key := commandKey(fields.Command, 0); key != "" {
			return tool + " " + key
		}
		return ""
	}
	file := fields.FilePath
	if file == "" {
		file = fields.Path
	}
	if file = strings.TrimSpace(file); file != "" {
		return tool + " " + path.Clean(slashed(file))
	}
	return ""
}

// commandKey names the job a command line does. Every command joined by &&,
// || or ; counts, so `make || echo done` and `ls || echo done` differ; a cd
// among them becomes the folder the job runs in. Every stage of a pipe counts
// except a trailing output filter: `npm test | tail -5` is the same job as
// `npm test | tail -50`, but `cat a | python p.py` is not `cat a | head`.
func commandKey(cmd string, depth int) string {
	var dir string
	var jobs []string
	for _, stages := range shellSegments(cmd) {
		words := stages[0]
		program := programName(words[0])
		switch program {
		case "cd", "pushd", "chdir", "set-location", "sl":
			if d := positional(words[1:]); len(d) > 0 {
				dir = path.Clean(slashed(d[0]))
			}
			continue
		}
		for len(stages) > 1 && outputFilter(programName(stages[len(stages)-1][0])) {
			stages = stages[:len(stages)-1]
		}
		parts := []string{jobKey(program, words[1:], depth)}
		for _, stage := range stages[1:] {
			parts = append(parts, jobKey(programName(stage[0]), stage[1:], depth))
		}
		jobs = append(jobs, strings.Join(parts, " | "))
	}
	if len(jobs) == 0 {
		if dir == "" {
			return ""
		}
		jobs = []string{"cd"}
	}
	key := strings.Join(jobs, " ; ")
	if dir != "" {
		key = "in " + dir + ": " + key
	}
	return key
}

// outputFilter reports a program that only trims what a pipe shows.
func outputFilter(program string) bool {
	switch program {
	case "head", "tail", "more", "less", "select-object":
		return true
	}
	return false
}

// jobKey is a program and its positional words. A shell wrapper (cmd /c,
// powershell -Command, bash -c) is keyed by the command it runs.
func jobKey(program string, args []string, depth int) string {
	if depth < 3 {
		for i, w := range args {
			if wrapperFlag(program, w) && i+1 < len(args) {
				return program + " (" + commandKey(strings.Join(args[i+1:], " "), depth+1) + ")"
			}
		}
	}
	return strings.Join(append([]string{program}, positional(args)...), " ")
}

// positional drops the words that only modify a command. A bare flag (-m,
// --user, /c) goes; its value, if it takes one, stays as a positional word,
// so `--target out pkgA` and `--target out pkgB` differ. A `--flag=value`
// stays whole for the same reason. `run` goes: `npm run build` is `npm build`.
func positional(args []string) []string {
	var out []string
	for _, w := range args {
		if w == "" || strings.EqualFold(w, "run") || (commandFlag(w) && !strings.Contains(w, "=")) {
			continue
		}
		out = append(out, w)
	}
	return out
}

func wrapperFlag(program, w string) bool {
	w = strings.ToLower(w)
	switch program {
	case "cmd":
		return w == "/c" || w == "/k"
	case "powershell", "pwsh":
		return w == "-command" || w == "-c"
	case "bash", "sh", "zsh", "dash":
		return w == "-c" || (len(w) > 2 && w[0] == '-' && w[1] != '-' && strings.HasSuffix(w, "c"))
	}
	return false
}

func programName(w string) string {
	return strings.TrimSuffix(strings.ToLower(filepath.Base(slashed(w))), ".exe")
}

// shellSegments splits a command line into its commands, each as the stages
// of its pipe, each stage as words with quotes removed. Outside quotes, &&,
// ||, ;, & and newlines end a command and a | ends a stage. A quoted
// separator is just text, and an & in a redirect (2>&1) is not a separator.
// Empty stages and commands are dropped.
func shellSegments(cmd string) [][][]string {
	var segs [][][]string
	var stages [][]string
	var words []string
	var cur strings.Builder
	var quote rune
	quoted := false
	flush := func() {
		if cur.Len() > 0 || quoted {
			words = append(words, cur.String())
		}
		cur.Reset()
		quoted = false
	}
	pipe := func() {
		flush()
		if len(words) > 0 {
			stages = append(stages, words)
		}
		words = nil
	}
	end := func() {
		pipe()
		if len(stages) > 0 {
			segs = append(segs, stages)
		}
		stages = nil
	}
	rs := []rune(cmd)
	for i := 0; i < len(rs); i++ {
		r := rs[i]
		switch {
		case quote != 0:
			if r == quote {
				quote = 0
			} else {
				cur.WriteRune(r)
			}
		case r == '"' || r == '\'':
			quote, quoted = r, true
		case r == '&' && ((i > 0 && (rs[i-1] == '>' || rs[i-1] == '<')) ||
			(i+1 < len(rs) && rs[i+1] == '>')):
			cur.WriteRune(r)
		case r == ';' || r == '\n' || r == '&':
			if r == '&' && i+1 < len(rs) && rs[i+1] == '&' {
				i++
			}
			end()
		case r == '|':
			if i+1 < len(rs) && rs[i+1] == '|' {
				i++
				end()
			} else {
				pipe()
			}
		case unicode.IsSpace(r):
			flush()
		default:
			cur.WriteRune(r)
		}
	}
	end()
	return segs
}

// commandFlag reports a word that modifies a command rather than naming what
// it does: -m, --user, -Command, and cmd's /c. A longer word starting with /
// is a path (/tmp/a.py), not a switch.
func commandFlag(w string) bool {
	if strings.HasPrefix(w, "-") {
		return true
	}
	return strings.HasPrefix(w, "/") && len(w) <= 3 && !strings.Contains(w[1:], "/")
}

func slashed(p string) string { return strings.ReplaceAll(p, `\`, "/") }

// calmDetail rewords a recovered step's failure line: the state leads instead
// of "failed", and the tool's own one-line error follows it.
func calmDetail(detail, recovery string) string {
	rest := clean(detail)
	if strings.HasPrefix(strings.ToLower(rest), "failed") {
		rest = rest[len("failed"):]
	}
	rest = strings.TrimLeft(rest, " —:")
	if strings.HasPrefix(strings.ToLower(rest), "error:") {
		rest = strings.TrimSpace(rest[len("error:"):])
	}
	if rest == "" {
		return recovery
	}
	return recovery + ": " + rest
}
