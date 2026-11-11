// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package providers

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/lemonade"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

var offline = &lemonade.UnreachableError{Host: "llm-api.amd.com", Err: errors.New("no such host")}

// stubProbe answers every gateway check with err and records the URL asked about.
func stubProbe(t *testing.T, err error) *[]string {
	t.Helper()
	var asked []string
	old := probeGateway
	probeGateway = func(_ context.Context, _, base string) error { asked = append(asked, base); return err }
	t.Cleanup(func() { probeGateway = old })
	return &asked
}

// probed delivers what a provider read scheduled, the way the program does.
func probed(t *testing.T, m Model, cmd tea.Cmd) Model {
	t.Helper()
	if cmd == nil {
		t.Fatal("the provider read scheduled no gateway check")
	}
	msg := cmd()
	if batch, ok := msg.(tea.BatchMsg); ok {
		for _, c := range batch {
			if c == nil {
				continue
			}
			if got, ok := c().(gatewayProbedMsg); ok {
				msg = got
			}
		}
	}
	if _, ok := msg.(gatewayProbedMsg); !ok {
		t.Fatalf("the provider read scheduled %T, not a gateway check", msg)
	}
	next, _ := m.Update(msg)
	return next.(Model)
}

func amdRow(t *testing.T, m Model) string {
	t.Helper()
	lines := strings.Split(ansi.Strip(m.View()), "\n")
	for i, l := range lines {
		if strings.Contains(l, "AMD LLM Gateway") && i+1 < len(lines) {
			return strings.TrimSpace(lines[i+1])
		}
	}
	t.Fatalf("no AMD row:\n%s", ansi.Strip(m.View()))
	return ""
}

func TestTheGatewayRowSaysItIsCheckingUntilTheProbeAnswers(t *testing.T) {
	stubProbe(t, nil)
	m := readProviders(New("", 100, 30), lemonade.Provider{Name: "amd", RuntimeKey: true})
	if row := amdRow(t, m); !strings.Contains(row, "checking the network") {
		t.Fatalf("AMD row before the check = %q", row)
	}
}

func TestAReachableGatewaySaysTheNetworkIsAvailable(t *testing.T) {
	asked := stubProbe(t, nil)
	m := New("", 100, 30)
	next, cmd := m.Update(loadedMsg{source: m.client, providers: []lemonade.Provider{
		{Name: "amd", BaseURL: "https://gw.example.test/v1", RuntimeKey: true}}})
	m = probed(t, next.(Model), cmd)
	if len(*asked) != 1 || (*asked)[0] != "https://gw.example.test/v1" {
		t.Fatalf("probed %q, want the configured gateway URL", *asked)
	}
	row := amdRow(t, m)
	if !strings.Contains(row, "network available") || !strings.Contains(row, "key configured") {
		t.Fatalf("AMD row = %q", row)
	}
}

// The reported failure: off AMD's network the row blamed the key.
func TestAnUnreachableGatewayIsGreyedOutAndSaysNoNetwork(t *testing.T) {
	stubProbe(t, offline)
	none := 0
	m := New("", 100, 30)
	next, cmd := m.Update(loadedMsg{source: m.client, providers: []lemonade.Provider{
		{Name: "amd", RuntimeKey: true, ModelsDiscovered: &none}}})
	m = probed(t, next.(Model), cmd)
	row := amdRow(t, m)
	if !strings.Contains(row, "No network available") || !strings.Contains(row, "llm-api.amd.com") {
		t.Fatalf("AMD row = %q", row)
	}
	if strings.Contains(strings.ToLower(row), "check the key") {
		t.Fatalf("an unreachable gateway still blames the key: %q", row)
	}
	if !m.gatewayDisabled() {
		t.Fatal("an unreachable gateway is not greyed out")
	}
}

func TestAnUnreachableGatewayCannotBeChosen(t *testing.T) {
	stubProbe(t, offline)
	m := New("", 100, 30)
	next, cmd := m.Update(loadedMsg{source: m.client, providers: []lemonade.Provider{{Name: "amd"}}})
	m = probed(t, next.(Model), cmd)
	m.selected = 2
	m = key(m, tea.KeyEnter)
	if m.stage != "providers" {
		t.Fatalf("enter on an unreachable gateway opened %q", m.stage)
	}
	if !strings.Contains(m.note, "No network available") || !strings.Contains(m.note, "press r") {
		t.Fatalf("note = %q", m.note)
	}
}

// Opening the panel straight on the gateway's key form waits for the check.
func TestConnectingTheGatewayWaitsForTheNetworkCheck(t *testing.T) {
	stubProbe(t, offline)
	m := New("", 100, 30).WithConnect("amd")
	next, cmd := m.Update(loadedMsg{source: m.client, providers: []lemonade.Provider{{Name: "amd"}}})
	m = next.(Model)
	if m.stage != "providers" {
		t.Fatalf("the key form opened before the network check: %q", m.stage)
	}
	m = probed(t, m, cmd)
	if m.stage != "providers" || !strings.Contains(m.note, "No network available") {
		t.Fatalf("stage %q note %q", m.stage, m.note)
	}

	stubProbe(t, nil)
	m = New("", 100, 30).WithConnect("amd")
	next, cmd = m.Update(loadedMsg{source: m.client, providers: []lemonade.Provider{{Name: "amd"}}})
	if m = probed(t, next.(Model), cmd); m.stage != "setup" {
		t.Fatalf("a reachable gateway did not open its key form: %q", m.stage)
	}
}

// A check that answers after the network came back must be able to re-enable it.
func TestRecheckingReenablesAGatewayThatCameBack(t *testing.T) {
	stubProbe(t, offline)
	m := New("", 100, 30)
	next, cmd := m.Update(loadedMsg{source: m.client, providers: []lemonade.Provider{{Name: "amd"}}})
	m = probed(t, next.(Model), cmd)
	stubProbe(t, nil)
	next, cmd = m.Update(loadedMsg{source: m.client, providers: []lemonade.Provider{{Name: "amd"}}})
	if m = probed(t, next.(Model), cmd); m.gatewayDisabled() {
		t.Fatal("the gateway stayed greyed out after the network came back")
	}
}

// When the check can't be made from here, the gateway stays choosable.
func TestAGatewayThatCannotBeCheckedFromHereStaysChoosable(t *testing.T) {
	stubProbe(t, lemonade.ErrCannotCheck)
	m := New("", 100, 30)
	next, cmd := m.Update(loadedMsg{source: m.client, providers: []lemonade.Provider{{Name: "amd", RuntimeKey: true}}})
	m = probed(t, next.(Model), cmd)
	if m.gatewayDisabled() {
		t.Fatal("an unchecked gateway was greyed out")
	}
	if row := amdRow(t, m); row != "Via Lemonade · key configured" {
		t.Fatalf("AMD row = %q", row)
	}
	m.selected = 2
	if m = key(m, tea.KeyEnter); m.stage != "setup" {
		t.Fatalf("enter on an unchecked gateway opened %q", m.stage)
	}
}

// A read that failed keeps its note and never opens the key form.
func TestConnectingTheGatewayWaitsForAGoodRead(t *testing.T) {
	stubProbe(t, nil)
	m := New("", 100, 30).WithSetupStep(2).WithConnect("amd")
	next, cmd := m.Update(loadedMsg{source: m.client, err: lemonade.ErrUnreachable})
	m = probed(t, next.(Model), cmd)
	if m.stage != "providers" || !strings.Contains(m.note, "isn't installed yet") {
		t.Fatalf("stage %q note %q", m.stage, m.note)
	}
	next, cmd = m.Update(loadedMsg{source: m.client, providers: []lemonade.Provider{{Name: "amd"}}})
	if m = probed(t, next.(Model), cmd); m.stage != "setup" {
		t.Fatalf("the good read did not open the key form: %q", m.stage)
	}
}

// An answer to an older check must not overwrite a newer one.
func TestAnOlderGatewayCheckIsIgnored(t *testing.T) {
	stubProbe(t, offline)
	m := New("", 100, 30)
	next, stale := m.Update(loadedMsg{source: m.client, providers: []lemonade.Provider{{Name: "amd"}}})
	m = next.(Model)
	stubProbe(t, nil)
	next, fresh := m.Update(loadedMsg{source: m.client, providers: []lemonade.Provider{{Name: "amd"}}})
	m = probed(t, next.(Model), fresh)
	next, _ = m.Update(stale())
	if m = next.(Model); m.gatewayDisabled() {
		t.Fatal("a late answer to an older check greyed out a reachable gateway")
	}
}
