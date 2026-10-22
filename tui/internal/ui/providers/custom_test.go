// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package providers

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/gaiamodel"
	"github.com/amd/gaia/tui/internal/lemonade"
	tea "github.com/charmbracelet/bubbletea"
)

const addedID = "user.Qwen3-0.6B-Q4_K_M-GGUF"

var passedReport = gaiamodel.Report{
	OK:  true,
	URL: "https://huggingface.co/unsloth/Qwen3-0.6B-GGUF",
	Checks: []gaiamodel.Check{
		{ID: "link", Label: "Hugging Face link", OK: true, Detail: "unsloth/Qwen3-0.6B-GGUF"},
		{ID: "architecture", Label: "Supported architecture", OK: true, Detail: "qwen3 runs on llama.cpp b10825"},
	},
	Variants: []gaiamodel.Variant{
		{Quant: "Q4_K_M", File: "Qwen3-0.6B-Q4_K_M.gguf", SizeGB: 0.4, Fits: true},
		{Quant: "Q8_0", File: "Qwen3-0.6B-Q8_0.gguf", SizeGB: 0.6, Fits: true},
	},
	ModelID:     addedID,
	DisplayName: "Qwen3-0.6B Q4_K_M",
	Quant:       "Q4_K_M",
	File:        "Qwen3-0.6B-Q4_K_M.gguf",
	SizeGB:      0.4,
}

// localModels opens the picker on the local list with one built-in and one added model.
func localModels(t *testing.T) Model {
	t.Helper()
	m := New("", 110, 40)
	m.stage = "models"
	added := &lemonade.Recommended{ID: addedID, Provider: "local", Label: "Qwen3-0.6B Q4_K_M",
		RegisterAs: addedID, Custom: true, SourceURL: passedReport.URL, Note: "added by you"}
	m.entries = []lemonade.Entry{
		{Model: lemonade.Model{ID: "Gemma-4-E4B-it-GGUF", Downloaded: true}, Listed: true, Fits: true},
		{Model: lemonade.Model{ID: "Qwen3-0.6B-Q4_K_M-GGUF", Downloaded: true, Size: 0.4}, Recommended: added, Listed: true, Fits: true},
	}
	return m
}

func pressKey(m Model, k string) (Model, tea.Cmd) {
	var msg tea.KeyMsg
	switch k {
	case "enter":
		msg = tea.KeyMsg{Type: tea.KeyEnter}
	case "esc":
		msg = tea.KeyMsg{Type: tea.KeyEsc}
	case "tab":
		msg = tea.KeyMsg{Type: tea.KeyTab}
	case "ctrl+a":
		msg = tea.KeyMsg{Type: tea.KeyCtrlA}
	case "ctrl+x":
		msg = tea.KeyMsg{Type: tea.KeyCtrlX}
	default:
		msg = tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(k), Paste: true}
	}
	next, cmd := m.Update(msg)
	return next.(Model), cmd
}

// feed runs cmd and any batch it returns, and feeds back the first message of type T.
func feed[T tea.Msg](t *testing.T, m Model, cmd tea.Cmd) (Model, T) {
	t.Helper()
	var zero T
	var walk func(tea.Cmd) (T, bool)
	walk = func(c tea.Cmd) (T, bool) {
		if c == nil {
			return zero, false
		}
		switch v := c().(type) {
		case tea.BatchMsg:
			for _, inner := range v {
				if got, ok := walk(inner); ok {
					return got, true
				}
			}
		case T:
			return v, true
		}
		return zero, false
	}
	got, ok := walk(cmd)
	if !ok {
		t.Fatalf("no %T came back", zero)
	}
	next, _ := m.Update(got)
	return next.(Model), got
}

func stubLinks(t *testing.T) *[]string {
	t.Helper()
	calls := []string{}
	oc, oa, or := checkLink, addLink, removeModel
	checkLink = func(_ context.Context, url, quant string) (gaiamodel.Report, error) {
		calls = append(calls, "check "+url+" "+quant)
		r := passedReport
		if quant != "" {
			r.File = quant
		}
		return r, nil
	}
	addLink = func(_ context.Context, url, quant string) (gaiamodel.Report, error) {
		calls = append(calls, "add "+url+" "+quant)
		return passedReport, nil
	}
	removeModel = func(_ context.Context, id string) (gaiamodel.Removed, error) {
		calls = append(calls, "remove "+id)
		return gaiamodel.Removed{ID: id, DisplayName: "Qwen3-0.6B Q4_K_M"}, nil
	}
	t.Cleanup(func() { checkLink, addLink, removeModel = oc, oa, or })
	return &calls
}

func TestAddedModelsHaveTheirOwnGroupAndNoStar(t *testing.T) {
	view := localModels(t).View()
	if !strings.Contains(view, "Added by you") || !strings.Contains(view, "Qwen3-0.6B Q4_K_M") {
		t.Fatalf("added group missing:\n%s", view)
	}
	if strings.Contains(view, "★ Qwen3-0.6B") {
		t.Error("an added model is not a recommendation")
	}
	if !strings.Contains(view, "ctrl+a add") {
		t.Errorf("the local list must offer adding from a link:\n%s", view)
	}
}

func TestPasteCheckThenAddALink(t *testing.T) {
	calls := stubLinks(t)
	m, _ := pressKey(localModels(t), "ctrl+a")
	if m.stage != "add" {
		t.Fatalf("ctrl+a opened %q", m.stage)
	}
	m, _ = pressKey(m, passedReport.URL+"?_sm_nck=1")
	m, cmd := pressKey(m, "enter")
	m, _ = feed[customCheckedMsg](t, m, cmd)
	view := m.View()
	for _, want := range []string{"✓ Supported architecture", "qwen3 runs on llama.cpp b10825", "Enter adds Qwen3-0.6B Q4_K_M", "tab quantization"} {
		if !strings.Contains(view, want) {
			t.Errorf("view lacks %q:\n%s", want, view)
		}
	}
	// tab picks the next quantization and checks again.
	m, cmd = pressKey(m, "tab")
	m, _ = feed[customCheckedMsg](t, m, cmd)
	if m.addQuant != "Qwen3-0.6B-Q8_0.gguf" {
		t.Errorf("variant = %q after tab, want the Q8_0 file", m.addQuant)
	}
	m, cmd = pressKey(m, "enter")
	m, _ = feed[customCheckedMsg](t, m, cmd)
	if m.stage != "models" || m.focusID != addedID || !m.busy {
		t.Errorf("after adding: stage %q focus %q busy %v; want the list refreshing onto it", m.stage, m.focusID, m.busy)
	}
	want := []string{
		"check " + passedReport.URL + "?_sm_nck=1 ",
		"check " + passedReport.URL + "?_sm_nck=1 Qwen3-0.6B-Q8_0.gguf",
		"add " + passedReport.URL + "?_sm_nck=1 Qwen3-0.6B-Q8_0.gguf",
	}
	if strings.Join(*calls, "|") != strings.Join(want, "|") {
		t.Errorf("calls = %q, want %q", *calls, want)
	}
}

func TestARefusedLinkShowsWhyAndCannotBeAdded(t *testing.T) {
	calls := stubLinks(t)
	checkLink = func(_ context.Context, url, quant string) (gaiamodel.Report, error) {
		*calls = append(*calls, "check")
		return gaiamodel.Report{OK: false, Error: "'flint' is not supported by llama.cpp b10825",
			Checks: []gaiamodel.Check{{ID: "architecture", Label: "Supported architecture", OK: false,
				Detail: "'flint' is not supported by llama.cpp b10825"}}}, nil
	}
	m, _ := pressKey(localModels(t), "ctrl+a")
	m, _ = pressKey(m, "https://huggingface.co/unsloth/flint-GGUF")
	m, cmd := pressKey(m, "enter")
	m, _ = feed[customCheckedMsg](t, m, cmd)
	if !strings.Contains(m.View(), "✗ Supported architecture — 'flint' is not supported") {
		t.Fatalf("refusal not shown:\n%s", m.View())
	}
	// enter checks again rather than adding.
	m, cmd = pressKey(m, "enter")
	_, _ = feed[customCheckedMsg](t, m, cmd)
	if strings.Join(*calls, ",") != "check,check" {
		t.Errorf("calls = %q; a refused link must never be added", *calls)
	}
}

func TestEditingTheLinkDropsTheOldCheck(t *testing.T) {
	stubLinks(t)
	m, _ := pressKey(localModels(t), "ctrl+a")
	m, _ = pressKey(m, passedReport.URL)
	m, cmd := pressKey(m, "enter")
	m, _ = feed[customCheckedMsg](t, m, cmd)
	m, _ = pressKey(m, "x")
	if m.addReport != nil {
		t.Fatal("a check of another link must not carry over")
	}
}

func TestACheckThatCannotRunIsANote(t *testing.T) {
	stubLinks(t)
	checkLink = func(context.Context, string, string) (gaiamodel.Report, error) {
		return gaiamodel.Report{}, errors.New("the `gaia` CLI is not on PATH")
	}
	m, _ := pressKey(localModels(t), "ctrl+a")
	m, _ = pressKey(m, passedReport.URL)
	m, cmd := pressKey(m, "enter")
	m, _ = feed[customCheckedMsg](t, m, cmd)
	if m.busy || !strings.Contains(m.note, "not on PATH") || m.stage != "add" {
		t.Fatalf("want the reason as a note, still on the add step: %+v", m.note)
	}
}

func TestRemoveAnAddedModelAfterConfirming(t *testing.T) {
	calls := stubLinks(t)
	m := localModels(t)
	m.focus = 1
	if !strings.Contains(m.View(), "ctrl+x remove") {
		t.Errorf("an added row must offer removal:\n%s", m.View())
	}
	m, _ = pressKey(m, "ctrl+x")
	if m.stage != "remove" || !strings.Contains(m.View(), "Remove Qwen3-0.6B Q4_K_M?") ||
		!strings.Contains(m.View(), "download (0.4 GB) is deleted") {
		t.Fatalf("no confirmation:\n%s", m.View())
	}
	m, cmd := pressKey(m, "enter")
	m, _ = feed[customRemovedMsg](t, m, cmd)
	if m.stage != "models" || !m.busy || !strings.HasPrefix(m.afterRefresh, "Removed Qwen3-0.6B Q4_K_M") {
		t.Errorf("after removing: stage %q busy %v note %q", m.stage, m.busy, m.afterRefresh)
	}
	if strings.Join(*calls, ",") != "remove "+addedID {
		t.Errorf("calls = %q", *calls)
	}
}

func TestRemoveCanBeCancelledAndBuiltInsCannotBeRemoved(t *testing.T) {
	calls := stubLinks(t)
	m := localModels(t)
	m.focus = 1
	m, _ = pressKey(m, "ctrl+x")
	m, _ = pressKey(m, "esc")
	if m.stage != "models" {
		t.Fatalf("esc left stage %q", m.stage)
	}
	m.focus = 0
	m, _ = pressKey(m, "ctrl+x")
	if m.stage != "models" || !strings.Contains(m.note, "Only models you added") {
		t.Errorf("a built-in must not be removable: stage %q note %q", m.stage, m.note)
	}
	if len(*calls) != 0 {
		t.Errorf("nothing should have run: %q", *calls)
	}
}

func TestTheModelChatRunsOnCannotBeRemoved(t *testing.T) {
	calls := stubLinks(t)
	m := localModels(t).WithCurrentModel(addedID)
	m.focus = 1
	m, _ = pressKey(m, "ctrl+x")
	if m.stage != "models" || !strings.Contains(m.note, "the model this chat runs on") {
		t.Fatalf("stage %q note %q", m.stage, m.note)
	}
	if len(*calls) != 0 {
		t.Errorf("nothing should have run: %q", *calls)
	}
}

func TestTheListFocusesTheModelJustAdded(t *testing.T) {
	m := localModels(t)
	entries := m.entries
	m.focusID = addedID
	m.afterRefresh = "Added Qwen3-0.6B Q4_K_M."
	next, _ := m.Update(modelsMsg{source: m.client, entries: entries})
	m = next.(Model)
	if m.focus != 1 || m.note != "Added Qwen3-0.6B Q4_K_M." || m.focusID != "" {
		t.Errorf("focus %d note %q", m.focus, m.note)
	}
}
