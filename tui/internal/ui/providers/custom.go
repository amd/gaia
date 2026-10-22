// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package providers

import (
	"fmt"
	"strings"

	"github.com/amd/gaia/tui/internal/gaiamodel"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/theme"
	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// Seams for tests; the real calls shell out to `gaia model` and read config.json.
var (
	checkLink        = gaiamodel.CheckLink
	addLink          = gaiamodel.Add
	removeModel      = gaiamodel.Remove
	readCustomModels = lemonade.ReadCustomModels
)

type customCheckedMsg struct {
	report gaiamodel.Report
	err    error
	adding bool
}

type customRemovedMsg struct {
	removed gaiamodel.Removed
	err     error
}

// isCustom reports whether e is a model the user added from a link.
func isCustom(e lemonade.Entry) bool { return e.Recommended != nil && e.Recommended.Custom }

// openAdd is ctrl+a on the local model list: paste a Hugging Face link.
func (m Model) openAdd() (Model, tea.Cmd) {
	f := textinput.New()
	f.Prompt = ""
	f.CharLimit = 2048
	f.Placeholder = "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF"
	f.Focus()
	m.addInput = f
	m.addReport = nil
	m.addQuant = ""
	m.stage = "add"
	m.note = ""
	return m, textinput.Blink
}

// runLink checks the pasted link, or adds it when adding is set.
func (m Model) runLink(quant string, adding bool) (Model, tea.Cmd) {
	url := strings.TrimSpace(m.addInput.Value())
	if url == "" {
		m.note = "Paste a Hugging Face link first."
		return m, nil
	}
	m.busy = true
	m.note = ""
	m.activity = "Checking the link"
	if adding {
		m.activity = "Adding the model"
	}
	ctx := m.ctx
	return m, tea.Batch(m.spin.Tick, func() tea.Msg {
		if adding {
			r, err := addLink(ctx, url, quant)
			return customCheckedMsg{report: r, err: err, adding: true}
		}
		r, err := checkLink(ctx, url, quant)
		return customCheckedMsg{report: r, err: err}
	})
}

func (m Model) updateAdd(v tea.KeyMsg) (Model, tea.Cmd) {
	switch v.String() {
	case "esc":
		m.stage = "models"
		m.addReport = nil
		m.note = ""
		return m, nil
	case "enter":
		passed := m.addReport != nil && m.addReport.OK
		return m.runLink(m.addQuant, passed)
	case "tab":
		if r := m.addReport; r != nil && len(r.Variants) > 1 {
			next := 0
			for i, variant := range r.Variants {
				if variant.File == m.addQuant {
					next = (i + 1) % len(r.Variants)
				}
			}
			return m.runLink(r.Variants[next].File, false)
		}
		return m, nil
	}
	before := m.addInput.Value()
	var cmd tea.Cmd
	m.addInput, cmd = m.addInput.Update(v)
	if m.addInput.Value() != before {
		// A different link: what was checked no longer applies.
		m.addReport = nil
		m.addQuant = ""
	}
	return m, cmd
}

func (m Model) onChecked(v customCheckedMsg) (Model, tea.Cmd) {
	m.busy = false
	if v.err != nil {
		m.note = v.err.Error()
		return m, nil
	}
	report := v.report
	if v.adding && report.OK {
		m.stage = "models"
		m.addReport = nil
		m.focusID = report.ModelID
		m.afterRefresh = "Added " + report.DisplayName + ". Press enter on it to download it and switch to it."
		return m.refresh()
	}
	m.addReport = &report
	m.addQuant = report.File
	return m, nil
}

// openRemove asks before removing the focused model, if the user added it.
func (m Model) openRemove(e lemonade.Entry) Model {
	if !isCustom(e) {
		m.note = "Only models you added from a link can be removed here."
		return m
	}
	if lemonade.SameLocalID(e.Model.ID, m.currentModel) || lemonade.SameLocalID(e.Recommended.ID, m.currentModel) {
		m.note = entryName(e, m.chosen()) + " is the model this chat runs on. Switch to another model first, then remove it."
		return m
	}
	m.removing = &e
	m.stage = "remove"
	m.note = ""
	return m
}

func (m Model) updateRemove(v tea.KeyMsg) (Model, tea.Cmd) {
	switch v.String() {
	case "esc", "n":
		m.stage = "models"
		m.removing = nil
		return m, nil
	case "enter", "y":
		id, ctx := m.removing.Recommended.ID, m.ctx
		m.busy = true
		m.activity = "Removing the model"
		return m, tea.Batch(m.spin.Tick, func() tea.Msg {
			r, err := removeModel(ctx, id)
			return customRemovedMsg{removed: r, err: err}
		})
	}
	return m, nil
}

func (m Model) onRemoved(v customRemovedMsg) (Model, tea.Cmd) {
	m.busy = false
	m.stage = "models"
	m.removing = nil
	if v.err != nil {
		m.note = v.err.Error()
		return m, nil
	}
	m.afterRefresh = "Removed " + v.removed.DisplayName + " and its download."
	for _, key := range v.removed.Cleared {
		switch key {
		case "last_model":
			m.afterRefresh += " It was your last model, so the next launch uses the default."
		case "default_model":
			m.afterRefresh += " It was your default model, so GAIA's own default takes its place."
		}
	}
	return m.refresh()
}

func (m Model) refresh() (Model, tea.Cmd) {
	m.busy = true
	m.activity = "Refreshing models"
	return m, tea.Batch(m.spin.Tick, m.fetchModels())
}

func (m Model) addView(w int) []string {
	title := lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright)
	dim := lipgloss.NewStyle().Foreground(theme.Dim)
	ok := lipgloss.NewStyle().Foreground(theme.Success)
	warn := lipgloss.NewStyle().Foreground(theme.Warning)
	f := m.addInput
	f.Width = max(10, w-4)
	lines := []string{
		title.Render("Add a model from Hugging Face"),
		"Paste the link to a GGUF model. GAIA checks that it is a chat model your Lemonade can run and that it fits this PC.",
		"",
		"› Link",
		"  " + f.View(),
		"",
	}
	r := m.addReport
	if r == nil {
		return lines
	}
	for _, c := range r.Checks {
		if c.OK {
			lines = append(lines, ok.Render("✓ ")+c.Label+dim.Render(" — "+c.Detail))
		} else {
			lines = append(lines, warn.Render("✗ "+c.Label+" — "+c.Detail))
		}
	}
	for _, w := range r.Warnings {
		lines = append(lines, warn.Render("! "+w))
	}
	if len(r.Variants) > 1 && r.Quant != "" {
		lines = append(lines, dim.Render(fmt.Sprintf("Quantization %s · %d available · tab for another", r.Quant, len(r.Variants))))
	}
	if r.OK {
		lines = append(lines, "", fmt.Sprintf("Enter adds %s (%.1f GB download when you first pick it).", r.DisplayName, r.SizeGB))
	}
	return lines
}

func (m Model) removeView() []string {
	title := lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright)
	e := m.removing
	if e == nil {
		return nil
	}
	name := entryName(*e, m.chosen())
	what := "It was never downloaded, so nothing is deleted from disk."
	if e.Model.Downloaded {
		what = fmt.Sprintf("Its download (%.1f GB) is deleted too.", e.SizeGB())
	}
	return []string{title.Render("Remove " + name + "?"), what, "", "From " + e.Recommended.SourceURL}
}
