// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"

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
	if d := clean(item.Detail); d != "" {
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
		key := stepRetryKey(*item)
		for _, later := range out[i+1:] {
			if later.Kind == "tool" && later.Done && !stepFailed(later) &&
				stepRetryKey(later) == key {
				item.Recovery = recoveryRetried
				break
			}
		}
		if item.Recovery == "" && !final {
			item.Recovery = recoveryPending
		}
	}
	return out
}

func stepRetryKey(item ActivityItem) string {
	if item.RetryKey != "" {
		return item.RetryKey
	}
	return activityKey(item)
}

// retryKey is the tool, plus — for a shell command — its program and first
// argument. A failed `pip install` is not recovered by a later `dir`, or by
// `python -c` succeeding after `python -m pip` failed.
func retryKey(tool string, args json.RawMessage) string {
	var fields struct {
		Command string `json:"command"`
	}
	if json.Unmarshal(args, &fields) != nil {
		return tool
	}
	words := strings.Fields(fields.Command)
	if len(words) == 0 {
		return tool
	}
	program := strings.ToLower(filepath.Base(strings.ReplaceAll(words[0], "\\", "/")))
	program = strings.TrimSuffix(program, ".exe")
	key := tool + " " + program
	if len(words) > 1 {
		key += " " + words[1]
	}
	return key
}

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
