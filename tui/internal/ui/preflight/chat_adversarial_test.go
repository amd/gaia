// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"errors"
	"fmt"
	"math/rand"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/lemonade"
)

// ── Tiny terminals ───────────────────────────────────────────────────────

func TestTheChoiceSurvivesTinyTerminals(t *testing.T) {
	for _, size := range [][2]int{{20, 6}, {30, 8}, {12, 4}, {1, 1}, {0, 0}} {
		t.Run(fmt.Sprintf("%dx%d", size[0], size[1]), func(t *testing.T) {
			m, _, _ := choosingGate(t, size[0], size[1])
			if !m.Choosing() {
				t.Fatal("a tiny terminal skipped the question")
			}
			for _, k := range []string{"down", "down", "up", "2"} {
				m, _ = press(m, k)
				screen := plain(m)
				if size[0] > 0 && size[1] > 0 {
					assertFits(t, splitLines(screen), size[0], size[1])
				}
			}
			if m.choice != 1 {
				t.Errorf("choice = %d after pressing 2", m.choice+1)
			}
		})
	}
}

// Shrinking the window while the question is up, then growing it back.
func TestResizingWhileChoosingKeepsTheChoice(t *testing.T) {
	m, _, _ := choosingGate(t, 100, 24)
	m, _ = press(m, "3")
	for _, size := range [][2]int{{20, 6}, {1, 1}, {200, 60}, {44, 14}} {
		updated, _ := m.Update(tea.WindowSizeMsg{Width: size[0], Height: size[1]})
		m = updated.(Model)
		_ = m.View()
	}
	if !m.Choosing() || m.choice != 2 {
		t.Errorf("resizing lost the choice: choosing %v, choice %d", m.Choosing(), m.choice+1)
	}
	if !strings.Contains(oneLine(plain(m)), "> 3. AMD LLM Gateway") {
		t.Errorf("the selected option is not marked after resizing:\n%s", plain(m))
	}
}

// ── Nothing to measure, nothing reachable ────────────────────────────────

func TestLocalOptionWhenThePCCannotBeMeasured(t *testing.T) {
	for name, capErr := range map[string]error{
		"capacity read fails": errors.New("system-info timed out"),
	} {
		t.Run(name, func(t *testing.T) {
			lemonadeUp(t, lemonade.Capacity{}, capErr)
			rep := Report{Rows: []Row{{Key: KeyLemonade, State: StateOK}}}
			got := localRunner{}.localFit(t.Context(), rep, nil)
			if got != "Setup picks the model that fits this PC's memory." {
				t.Errorf("unmeasurable PC: %q", got)
			}
		})
	}
	rep := Report{Rows: []Row{{Key: KeyLemonade, State: StateFailed}}}
	if got := (localRunner{}).localFit(t.Context(), rep, nil); !strings.Contains(got, "Setup picks") {
		t.Errorf("Lemonade down: %q", got)
	}
}

func TestEveryOptionIsStillOfferedWhenSizesAreUnknown(t *testing.T) {
	options := chatOptions("", "")
	if len(options) != len(lemonade.ChatProviders) || len(options) == 0 {
		t.Fatalf("options = %+v", options)
	}
	for _, o := range options {
		if o.Label == "" || o.TradeOff == "" || o.Needs == "" {
			t.Errorf("option %q is missing text: %+v", o.Provider, o)
		}
		if strings.Contains(o.Needs, "Downloads  ") || strings.HasSuffix(o.Needs, " ") {
			t.Errorf("option %q has a gap where a size should be: %q", o.Provider, o.Needs)
		}
	}
}

func TestAnUnknownProviderStillReadsAsAnOption(t *testing.T) {
	o := chatOption("acme-cloud", "", "")
	if o.TradeOff == "" || o.Needs == "" || !strings.Contains(o.TradeOff, o.Label) {
		t.Errorf("unknown provider option: %+v", o)
	}
}

// ── A re-check landing while the question is up ──────────────────────────

func TestAReportArrivingMidChoiceKeepsTheScreenUsable(t *testing.T) {
	m, chosen, _ := choosingGate(t, 100, 24)
	m, _ = press(m, "2")

	broken := firstRunReport()
	broken.Rows[1].Line = "not reachable"
	broken.Rows[1].Detail = "Lemonade stopped answering"
	updated, _ := m.Update(reportMsg{rep: broken})
	m = updated.(Model)
	_ = plain(m)

	noChatRow := chooseFirstRunReport()
	noChatRow.ChatOptions = nil
	var rows []Row
	for _, r := range noChatRow.Rows {
		if r.Key != KeyChat {
			rows = append(rows, r)
		}
	}
	noChatRow.Rows = rows
	updated, _ = m.Update(reportMsg{rep: noChatRow})
	m = updated.(Model)
	screen := plain(m)

	if m.Choosing() {
		if m.choice < 0 || m.choice >= len(m.chatOptions()) {
			t.Fatalf("choice %d is out of range", m.choice)
		}
		m, _ = press(m, "enter")
		if *chosen == "" {
			t.Errorf("enter after the re-check chose nothing:\n%s", screen)
		}
	}
}

func TestFewerOptionsAfterARecheckClampTheCursor(t *testing.T) {
	m, _, _ := choosingGate(t, 100, 24)
	m, _ = press(m, "3")
	rep := chooseFirstRunReport()
	rep.ChatOptions = rep.ChatOptions[:1]
	updated, _ := m.Update(reportMsg{rep: rep})
	m = updated.(Model)
	defer func() {
		if r := recover(); r != nil {
			t.Fatalf("the cursor ran past a shorter option list: %v", r)
		}
	}()
	_ = plain(m)
	if m.Choosing() {
		m, _ = press(m, "enter")
	}
	_ = m
}

// ── Key mashing ──────────────────────────────────────────────────────────

func TestRapidKeyMashingNeverPanicsOrEscapesTheOptions(t *testing.T) {
	keysPool := []string{"up", "down", "1", "2", "3", "4", "9", "0", "j", "k", "tab",
		"r", "p", "x", " ", "esc", "enter", "q", "d", "?"}
	rng := rand.New(rand.NewSource(5109))
	for run := 0; run < 20; run++ {
		m, _, _ := choosingGate(t, 60, 20)
		for i := 0; i < 300; i++ {
			k := keysPool[rng.Intn(len(keysPool))]
			if k == "esc" && !m.Choosing() {
				continue // a second esc leaves the screen; not what this measures
			}
			var cmd tea.Cmd
			m, cmd = press(m, k)
			if m.choice < 0 || m.choice >= len(m.chatOptions()) {
				t.Fatalf("run %d key %d (%q): choice %d out of range", run, i, k, m.choice)
			}
			_ = cmd
			if i%25 == 0 {
				assertFits(t, splitLines(plain(m)), 60, 20)
			}
		}
	}
}

func TestEnterMashedAfterAChoiceRecordsItOnce(t *testing.T) {
	m, chosen, _ := choosingGate(t, 100, 24)
	m, _ = press(m, "2")
	var told int
	for i := 0; i < 10; i++ {
		var cmd tea.Cmd
		m, cmd = press(m, "enter")
		for _, msg := range collect(cmd) {
			if _, ok := msg.(ChatChosenMsg); ok {
				told++
			}
		}
	}
	if *chosen != "fireworks" || told != 1 {
		t.Errorf("chosen %q, ChatChosenMsg sent %d times; want fireworks once", *chosen, told)
	}
}
