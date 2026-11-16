// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/gaiaautomation"
)

// `/automations`: the reminders, summaries, alerts, triggers and heartbeat
// GAIA runs on its own. Lists them numbered; `/automations pause 2` (or
// resume, remove) changes one by that number, its id, or its exact name.
// Typing the command is the user's own action, so nothing asks again.

const automationsCommand = "/automations"

type automationsListMsg struct {
	items []gaiaautomation.Item
	// said is a change's confirmation, shown above the refreshed list.
	said string
	err  error
}

func listAutomationsCmd(said string) tea.Cmd {
	return func() tea.Msg {
		items, err := gaiaautomation.List(context.Background())
		return automationsListMsg{items: items, said: said, err: err}
	}
}

func setAutomationCmd(action, ref string) tea.Cmd {
	return func() tea.Msg {
		ctx := context.Background()
		said, err := gaiaautomation.Set(ctx, action, ref)
		if err != nil {
			return automationsListMsg{err: fmt.Errorf("could not %s it: %w", action, err)}
		}
		items, err := gaiaautomation.List(ctx)
		return automationsListMsg{items: items, said: said, err: err}
	}
}

func isAutomationsCommand(query string) bool {
	return query == automationsCommand || strings.HasPrefix(query, automationsCommand+" ")
}

// handleAutomationsCommand routes every `/automations …` line.
func (m ChatModel) handleAutomationsCommand(query string) (tea.Model, tea.Cmd) {
	rest := strings.TrimSpace(strings.TrimPrefix(query, automationsCommand))
	if rest == "" {
		return m.statusNote("Reading your automations…"), listAutomationsCmd("")
	}
	verb, ref, _ := strings.Cut(rest, " ")
	ref = strings.TrimSpace(ref)
	switch verb {
	case gaiaautomation.ActionPause, gaiaautomation.ActionResume, gaiaautomation.ActionRemove:
	default:
		return m.statusNote(automationsHelpText()), nil
	}
	if ref == "" {
		return m.statusNote(fmt.Sprintf("Say which one: %s %s 2 (its number from %s), or its name.",
			automationsCommand, verb, automationsCommand)), nil
	}
	typed := ref
	if id, ok := m.automationNames[strings.ToLower(ref)]; ok {
		// A listed name wins, so one named "2" is reachable by its name.
		ref = id
	} else if n, err := strconv.Atoi(ref); err == nil {
		if n < 1 || n > len(m.automationRefs) {
			return m.statusNote(fmt.Sprintf("There is no automation %d in the last list. "+
				"Type %s to see the numbers.", n, automationsCommand)), nil
		}
		ref = m.automationRefs[n-1]
	}
	return m.statusNote(fmt.Sprintf("Working: %s %s…", verb, typed)), setAutomationCmd(verb, ref)
}

func (m ChatModel) handleAutomationsList(msg automationsListMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		if errors.Is(msg.err, gaiaautomation.ErrUnanswered) {
			return m.statusNote(fmt.Sprintf("Could not read your automations: %v", msg.err)), nil
		}
		return m.statusNote(fmt.Sprintf("Automation change failed: %v", msg.err)), nil
	}
	m.automationRefs = make([]string, 0, len(msg.items))
	m.automationNames = make(map[string]string, len(msg.items))
	for _, it := range msg.items {
		m.automationRefs = append(m.automationRefs, it.ID)
		m.automationNames[strings.ToLower(strings.TrimSpace(it.Name))] = it.ID
	}
	text := automationsListText(msg.items)
	if msg.said != "" {
		text = msg.said + "\n\n" + text
	}
	return m.statusNote(text), nil
}

func automationKind(it gaiaautomation.Item) string {
	if it.Heartbeat() {
		return "heartbeat"
	}
	return it.Kind
}

// automationsListText is /automations's answer: one numbered entry each.
func automationsListText(items []gaiaautomation.Item) string {
	if len(items) == 0 {
		return strings.Join([]string{
			"No automations yet.",
			"Ask GAIA here to set one up, like \"remind me at 5pm to call Sam\" or",
			"\"tell me when CI on main breaks\".",
		}, "\n")
	}
	lines := []string{fmt.Sprintf("Automations (%d):", len(items))}
	for i, it := range items {
		when := "once, " + it.Next
		if it.Every != "" {
			when = "every " + it.Every + ", next " + it.Next
		}
		if it.Paused() {
			when = "paused"
			if it.Every != "" {
				when = "every " + it.Every + ", paused"
			}
		}
		lines = append(lines, fmt.Sprintf("  %d. %s — %s, %s", i+1, it.Name, automationKind(it), when))
		if detail := firstNonEmpty(it.Says, it.Prompt); detail != "" {
			lines = append(lines, "     "+truncateRunes(strings.Join(strings.Fields(detail), " "), 70))
		}
		if it.Check != "" {
			lines = append(lines, "     checks: "+truncateRunes(it.Check, 62))
		}
		if it.QuietHours != "" {
			lines = append(lines, "     quiet "+it.QuietHours)
		}
		if it.FailingChecks > 0 {
			lines = append(lines, fmt.Sprintf("     its check has failed %d time(s) in a row", it.FailingChecks))
		}
	}
	lines = append(lines, "",
		automationsCommand+" pause 1 · resume 1 · remove 1",
		"To add one or change what it does, ask GAIA here.")
	return strings.Join(lines, "\n")
}

func automationsHelpText() string {
	return strings.Join([]string{
		"Automation commands:",
		"  /automations             list them, numbered",
		"  /automations pause 2     stop one running until you resume it",
		"  /automations resume 2    start a paused one again",
		"  /automations remove 2    delete one",
		"A name or id works in place of the number.",
	}, "\n")
}

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if strings.TrimSpace(v) != "" {
			return strings.TrimSpace(v)
		}
	}
	return ""
}
