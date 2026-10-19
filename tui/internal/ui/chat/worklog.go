// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

// A turn is read a step at a time: one bullet per meaningful event, drawn by
// workview.go. The agent's stage lines are detail, folded away until Ctrl+O
// asks for them; its narration is shown as plain prose.
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
	text := clean(withoutThinking(m.buffer))
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
	for _, item := range collapseActivity(steps) {
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

// withoutThinking drops a model's raw reasoning from streamed text: every
// <think>…</think> span, and an unclosed one still streaming. The status line
// already says the model is reasoning; the reasoning itself is not the answer.
func withoutThinking(s string) string {
	const openTag, closeTag = "<think>", "</think>"
	for {
		open := strings.Index(s, openTag)
		closeAt := strings.Index(s, closeTag)
		switch {
		case closeAt >= 0 && (open < 0 || closeAt < open):
			// Reasoning that began before this text did: drop up to the close.
			s = s[closeAt+len(closeTag):]
		case open >= 0 && closeAt > open:
			s = s[:open] + s[closeAt+len(closeTag):]
		case open >= 0:
			return strings.TrimSpace(s[:open])
		default:
			return strings.TrimSpace(s)
		}
	}
}
