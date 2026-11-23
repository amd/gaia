// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package root

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/gaiaprogram"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// programFixture is a machine with chosen agent programs installed and its own
// config.json.
type programFixture struct {
	dir        string
	configPath string
}

// installPrograms puts stand-in files for the named programs in a temp dir and
// points the saved settings at a throwaway file.
func installPrograms(t *testing.T, programs ...string) programFixture {
	t.Helper()
	f := programFixture{dir: t.TempDir(), configPath: filepath.Join(t.TempDir(), "config.json")}
	for _, p := range programs {
		if err := os.WriteFile(f.path(p), []byte("#!/bin/sh\n"), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("GAIA_CONFIG_FILE", f.configPath)
	return f
}

func (f programFixture) path(program string) string {
	if runtime.GOOS == "windows" {
		program += ".exe"
	}
	return filepath.Join(f.dir, program)
}

// resolve stands in for catalog.ForFlagship: the real selection, against this
// fixture's programs instead of the machine's.
func (f programFixture) resolve(program string) (*catalog.Catalog, string, error) {
	path := f.path(program)
	if _, err := os.Stat(path); err != nil {
		return nil, "", &catalog.MissingProgramError{Binary: program,
			Lookup: catalog.Lookup{Looked: []string{"your PATH", f.dir}}}
	}
	cat := catalog.NewCatalog()
	notice, err := cat.SelectFlagshipAgent(path)
	return cat, notice, err
}

func (f programFixture) saved(t *testing.T) string {
	t.Helper()
	raw, err := os.ReadFile(f.configPath)
	if os.IsNotExist(err) {
		return ""
	}
	if err != nil {
		t.Fatal(err)
	}
	doc := map[string]any{}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("config.json is not valid JSON: %v", err)
	}
	s, _ := doc[preflight.FlagshipAgentKey].(string)
	return s
}

// programSession is a live flagship chat on choice, on fixture f.
func programSession(t *testing.T, f programFixture, choice string) (FlagshipModel, *nullClient) {
	t.Helper()
	cat, _, err := f.resolve(preflight.FlagshipAgentProgram(choice))
	if err != nil {
		t.Fatalf("test setup: %v", err)
	}
	agent := cat.Get(catalog.FlagshipID)
	c := &nullClient{}
	chatModel := chat.NewChatModelForFlagship(c, agent.ID, agent.Name, true).WithAgentPrograms()
	m := NewFlagshipModel(*agent, false).WithCatalog(cat).WithSessionsDir(t.TempDir()).
		WithLocalPreflight(preflight.LocalOptions{Binary: agent.BinaryPath})
	m.programs = f.resolve
	m.chatClient.set(c)
	m.chat = &chatModel
	m.activeView = viewChat
	m.width, m.height = 120, 40
	return m, c
}

func lastLine(t *testing.T, m FlagshipModel) string {
	t.Helper()
	msgs := m.chat.Messages()
	if len(msgs) == 0 {
		t.Fatal("the transcript is empty")
	}
	return msgs[len(msgs)-1].Content
}

// passGate commits the switch the gate on screen is guarding, as ProceedMsg does.
func passGate(t *testing.T, m FlagshipModel) FlagshipModel {
	t.Helper()
	if m.activeView != viewPreflight || m.pending == nil {
		t.Fatalf("no readiness gate is up: view=%v pending=%v; last line: %s", m.activeView, m.pending, lastLine(t, m))
	}
	agent := *m.pending
	m.closeGate()
	updated, _ := m.launchAgent(agent, true)
	return updated.(FlagshipModel)
}

func header(t *testing.T, m FlagshipModel) string {
	t.Helper()
	updated, _ := m.Update(tea.WindowSizeMsg{Width: 120, Height: 40})
	lines := strings.Split(updated.(FlagshipModel).View(), "\n")
	if len(lines) > 3 {
		lines = lines[:3]
	}
	return strings.Join(lines, "\n")
}

func TestAgentProgramSwitchesToNativeAndBack(t *testing.T) {
	f := installPrograms(t, catalog.PythonAgentBinary, catalog.NativeAgentBinary)
	m, oldClient := programSession(t, f, preflight.FlagshipAgentPython)
	m.chat.AppendStatus("earlier line")
	if strings.Contains(header(t, m), "(gaiacpp · Beta)") {
		t.Fatal("test setup: the Python session's header already says gaiacpp")
	}

	updated, _ := m.Update(chat.AgentProgramMsg{Arg: "gaiacpp"})
	m = updated.(FlagshipModel)
	if m.activeView != viewPreflight || m.pending == nil || !m.pending.Native || m.pending.ID != catalog.FlagshipID {
		t.Fatalf("the switch did not open the readiness gate for gaiacpp: view=%v pending=%+v; %s",
			m.activeView, m.pending, lastLine(t, m))
	}
	if got := f.saved(t); got != "" {
		t.Fatalf("the choice was saved before gaiacpp was running: %q", got)
	}
	if oldClient.closed {
		t.Fatal("the outgoing agent was closed before the gate passed")
	}
	if got := m.localOptions(*m.pending).Binary; got != f.path(catalog.NativeAgentBinary) {
		t.Fatalf("the gate would probe %s, not the program being switched to", got)
	}

	m = passGate(t, m)
	m.chatClient.retiring.Wait()
	if !m.agent.Native || m.agent.BinaryPath != f.path(catalog.NativeAgentBinary) || m.activeView != viewChat {
		t.Fatalf("the session is not on gaiacpp: %+v view=%v", m.agent, m.activeView)
	}
	if !oldClient.closed {
		t.Fatal("the Python agent outlived the switch")
	}
	if got := f.saved(t); got != preflight.FlagshipAgentNative {
		t.Fatalf("the choice was not saved once gaiacpp was running: %q", got)
	}
	if m.programSwitch != nil || !m.catalog.Get(catalog.FlagshipID).Native {
		t.Fatal("the switch was not committed to the session's catalog")
	}
	if got := m.localOptions(m.agent).Binary; got != f.path(catalog.NativeAgentBinary) {
		t.Fatalf("a later gate would probe %s", got)
	}
	var texts []string
	for _, msg := range m.chat.Messages() {
		texts = append(texts, msg.Content)
	}
	all := strings.Join(texts, "\n")
	want := "Switched the agent program from gaiapy to gaiacpp. gaiacpp now answers, and it does not see the conversation above this line."
	if !strings.Contains(all, "earlier line") || !strings.Contains(all, want) {
		t.Fatalf("the transcript or its divider did not survive the switch:\n%s", all)
	}
	if !strings.Contains(header(t, m), "(gaiacpp · Beta)") {
		t.Fatalf("the header does not mark gaiacpp after the switch:\n%s", header(t, m))
	}

	updated, _ = m.Update(chat.AgentProgramMsg{Arg: "gaiapy"})
	m = passGate(t, updated.(FlagshipModel))
	if m.agent.Native || m.agent.BinaryPath != f.path(catalog.PythonAgentBinary) || len(m.agent.BinaryArgs) != 0 {
		t.Fatalf("the session is not back on the Python agent: %+v", m.agent)
	}
	if got := f.saved(t); got != preflight.FlagshipAgentPython {
		t.Fatalf("the saved choice is %q after switching back", got)
	}
	if strings.Contains(header(t, m), "(gaiacpp · Beta)") {
		t.Fatalf("the header still marks gaiacpp after switching back:\n%s", header(t, m))
	}
	texts = nil
	for _, msg := range m.chat.Messages() {
		texts = append(texts, msg.Content)
	}
	if !strings.Contains(strings.Join(texts, "\n"), "from gaiacpp to gaiapy") {
		t.Fatalf("no divider for the switch back:\n%s", strings.Join(texts, "\n"))
	}
}

func TestAgentProgramMissingFailsLoudlyAndChangesNothing(t *testing.T) {
	f := installPrograms(t, catalog.PythonAgentBinary)
	m, c := programSession(t, f, preflight.FlagshipAgentPython)

	// A release with no gaiacpp for this PC: the missing program stays missing.
	m.checkProgram = func(context.Context) (gaiaprogram.Status, error) {
		return gaiaprogram.Status{Reason: "this GAIA release has no gaiacpp to download."}, nil
	}
	updated, cmd := m.agentProgram("gaiacpp")
	m = updated.(FlagshipModel)
	if cmd == nil {
		t.Fatal("a missing gaiacpp did not ask the release for one")
	}
	updated, cmd = m.Update(cmd())
	m = updated.(FlagshipModel)
	if cmd != nil || m.activeView != viewChat || c.closed || m.agent.Native || m.programSwitch != nil || m.programFetch != nil {
		t.Fatal("a missing program must leave the live session alone")
	}
	got := lastLine(t, m)
	for _, want := range []string{"gaiacpp can't be downloaded here", "this GAIA release has no gaiacpp", "still on gaiapy"} {
		if !strings.Contains(got, want) {
			t.Errorf("the refusal does not say %q:\n%s", want, got)
		}
	}
	if saved := f.saved(t); saved != "" {
		t.Fatalf("a program that cannot run was saved as the choice: %q", saved)
	}
}

func TestAgentProgramPinnedSessionSavesButDoesNotSwitch(t *testing.T) {
	for _, pin := range []string{"--agent-program", catalog.AgentEnv} {
		f := installPrograms(t, catalog.PythonAgentBinary, catalog.NativeAgentBinary)
		m, c := programSession(t, f, preflight.FlagshipAgentPython)
		m = m.WithAgentPin(pin)

		updated, cmd := m.agentProgram("gaiacpp")
		m = updated.(FlagshipModel)
		if cmd != nil || m.activeView != viewChat || c.closed || m.agent.Native {
			t.Fatalf("%s: a pinned session was switched", pin)
		}
		if saved := f.saved(t); saved != preflight.FlagshipAgentNative {
			t.Fatalf("%s: the choice was not saved: %q", pin, saved)
		}
		got := lastLine(t, m)
		for _, want := range []string{"Saved gaiacpp", f.configPath, "pinned to gaiapy by " + pin, "without " + pin} {
			if !strings.Contains(got, want) {
				t.Errorf("%s: the note does not say %q:\n%s", pin, want, got)
			}
		}
	}
}

func TestAgentProgramRefusesNativeInClaudeModeAndReopenedSessions(t *testing.T) {
	cases := map[string]func(FlagshipModel) FlagshipModel{
		"--use-claude": func(m FlagshipModel) FlagshipModel { return m.WithClaude(true, "") },
		"--resume":     func(m FlagshipModel) FlagshipModel { m.reopened = true; return m },
	}
	for want, arrange := range cases {
		f := installPrograms(t, catalog.PythonAgentBinary, catalog.NativeAgentBinary)
		m, c := programSession(t, f, preflight.FlagshipAgentPython)
		m = arrange(m)

		updated, cmd := m.agentProgram("gaiacpp")
		m = updated.(FlagshipModel)
		if cmd != nil || m.activeView != viewChat || c.closed || m.agent.Native {
			t.Fatalf("%s: the session was switched to gaiacpp", want)
		}
		got := lastLine(t, m)
		if !strings.Contains(got, "Cannot switch to gaiacpp") || !strings.Contains(got, want) ||
			!strings.Contains(got, "Nothing was changed") {
			t.Errorf("%s: the refusal does not give the reason:\n%s", want, got)
		}
		if saved := f.saved(t); saved != "" {
			t.Errorf("%s: a refused switch still saved %q", want, saved)
		}
	}
}

// A launch that reopened a session marks it, and a fresh launch clears it.
func TestLaunchRecordsWhetherTheSessionWasReopened(t *testing.T) {
	f := installPrograms(t, catalog.PythonAgentBinary)
	m, _ := programSession(t, f, preflight.FlagshipAgentPython)

	m.resumeID = "20260101-000000-abcdef"
	updated, _ := m.launchAgent(m.agent, true)
	m = updated.(FlagshipModel)
	if !m.reopened {
		t.Fatal("a launch that reopened a session was not marked reopened")
	}
	updated, _ = m.launchAgent(m.agent, true)
	if updated.(FlagshipModel).reopened {
		t.Fatal("a launch of a new session is still marked reopened")
	}
}

func TestAgentProgramAlreadyRunningSavesAndStays(t *testing.T) {
	f := installPrograms(t, catalog.PythonAgentBinary, catalog.NativeAgentBinary)
	m, c := programSession(t, f, preflight.FlagshipAgentNative)

	updated, cmd := m.agentProgram(" GaiaCPP ")
	m = updated.(FlagshipModel)
	if cmd != nil || m.activeView != viewChat || c.closed {
		t.Fatal("asking for the running program must not relaunch it")
	}
	if !strings.Contains(lastLine(t, m), "Already running gaiacpp") || f.saved(t) != preflight.FlagshipAgentNative {
		t.Fatalf("got %q, saved %q", lastLine(t, m), f.saved(t))
	}
}

func TestAgentProgramUnknownNameNamesBothChoices(t *testing.T) {
	f := installPrograms(t, catalog.PythonAgentBinary)
	m, _ := programSession(t, f, preflight.FlagshipAgentPython)
	updated, cmd := m.agentProgram("rust")
	m = updated.(FlagshipModel)
	got := lastLine(t, m)
	if cmd != nil || !strings.Contains(got, `"rust"`) || !strings.Contains(got, "/agent gaiapy") || !strings.Contains(got, "/agent gaiacpp") {
		t.Fatalf("got %q", got)
	}
	if f.saved(t) != "" {
		t.Fatal("an unknown name was saved")
	}
}

func TestAgentProgramCancelledAtTheGateStaysAndSavesNothing(t *testing.T) {
	f := installPrograms(t, catalog.PythonAgentBinary, catalog.NativeAgentBinary)
	m, c := programSession(t, f, preflight.FlagshipAgentPython)
	updated, _ := m.agentProgram("gaiacpp")
	m = updated.(FlagshipModel)

	updated, cmd := m.cancelFromGate()
	m = updated.(FlagshipModel)
	if cmd != nil || m.activeView != viewChat || c.closed || m.agent.Native || m.programSwitch != nil {
		t.Fatal("backing out of the gate must return to the live Python session")
	}
	if m.catalog.Get(catalog.FlagshipID).Native {
		t.Fatal("a cancelled switch still changed the session's catalog")
	}
	got := lastLine(t, m)
	for _, want := range []string{"Switch to gaiacpp cancelled", "staying on gaiapy", "saved choice was not changed"} {
		if !strings.Contains(got, want) {
			t.Errorf("the note does not say %q:\n%s", want, got)
		}
	}
	if saved := f.saved(t); saved != "" {
		t.Fatalf("a cancelled switch saved %q", saved)
	}
}

// A program that passes the gate and then cannot be started is not saved.
func TestAgentProgramFailedLaunchStaysAndSavesNothing(t *testing.T) {
	f := installPrograms(t, catalog.PythonAgentBinary, catalog.NativeAgentBinary)
	m, c := programSession(t, f, preflight.FlagshipAgentPython)
	updated, _ := m.agentProgram("gaiacpp")
	m = updated.(FlagshipModel)
	if err := os.Remove(f.path(catalog.NativeAgentBinary)); err != nil {
		t.Fatal(err)
	}

	m = passGate(t, m)
	if m.activeView != viewChat || c.closed || m.agent.Native || m.programSwitch != nil {
		t.Fatal("a failed launch must leave the live Python session in place")
	}
	got := lastLine(t, m)
	for _, want := range []string{"Could not switch to gaiacpp", "Staying on gaiapy", "saved choice was not changed"} {
		if !strings.Contains(got, want) {
			t.Errorf("the note does not say %q:\n%s", want, got)
		}
	}
	if saved := f.saved(t); saved != "" {
		t.Fatalf("a program that failed to start was saved: %q", saved)
	}
}

// A switch that worked but could not be saved says so in the new chat.
func TestAgentProgramSwitchReportsAFailedSave(t *testing.T) {
	f := installPrograms(t, catalog.PythonAgentBinary, catalog.NativeAgentBinary)
	m, _ := programSession(t, f, preflight.FlagshipAgentPython)
	updated, _ := m.agentProgram("gaiacpp")
	m = updated.(FlagshipModel)
	if err := os.WriteFile(f.configPath, []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}

	m = passGate(t, m)
	if !m.agent.Native {
		t.Fatal("the switch itself did not happen")
	}
	got := lastLine(t, m)
	if !strings.Contains(got, "now runs gaiacpp") || !strings.Contains(got, "could not be saved") || !strings.Contains(got, f.configPath) {
		t.Fatalf("the failed save was not reported:\n%s", got)
	}
}

func TestAgentProgramListingNamesBothMarksBetaCurrentAndUnavailable(t *testing.T) {
	f := installPrograms(t, catalog.PythonAgentBinary)
	m, _ := programSession(t, f, preflight.FlagshipAgentPython)

	updated, cmd := m.Update(chat.AgentProgramMsg{})
	m = updated.(FlagshipModel)
	if cmd != nil || m.activeView != viewChat {
		t.Fatal("the listing must not start anything")
	}
	got := lastLine(t, m)
	var py, cpp string
	for _, l := range strings.Split(got, "\n") {
		switch {
		case strings.Contains(l, " gaiapy — "):
			py = l
		case strings.Contains(l, " gaiacpp — "):
			cpp = l
		}
	}
	if !strings.Contains(py, "running now") || strings.Contains(py, "Beta") || !strings.HasPrefix(py, "●") {
		t.Errorf("the Python row is wrong: %q", py)
	}
	if !strings.Contains(cpp, "Beta") || strings.Contains(cpp, "running now") || !strings.HasPrefix(cpp, "○") {
		t.Errorf("the native row is wrong: %q", cpp)
	}
	for _, want := range []string{
		"Mature and well tested", "Heavier", "Fast and light", "Fewer tools",
		"Not on this PC yet: /agent gaiacpp offers to download it.",
		"No saved choice yet", "/agent gaiapy", "/agent gaiacpp",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("the listing does not say %q:\n%s", want, got)
		}
	}

	// Installed, saved and pinned all show up once they are true.
	f2 := installPrograms(t, catalog.PythonAgentBinary, catalog.NativeAgentBinary)
	if _, err := preflight.WriteFlagshipAgent(preflight.FlagshipAgentNative); err != nil {
		t.Fatal(err)
	}
	m, _ = programSession(t, f2, preflight.FlagshipAgentNative)
	m = m.WithAgentPin(catalog.AgentEnv)
	updated, _ = m.agentProgram("")
	got = lastLine(t, updated.(FlagshipModel))
	if strings.Contains(got, "Unavailable") || !strings.Contains(got, "Beta · running now · your saved choice") ||
		!strings.Contains(got, "pinned to gaiacpp by "+catalog.AgentEnv) {
		t.Errorf("the listing for an installed, saved, pinned gaiacpp is wrong:\n%s", got)
	}

	// A saved value GAIA does not know is reported, not hidden.
	if err := os.WriteFile(f2.configPath, []byte(`{"flagship_agent": "rust"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	updated, _ = m.agentProgram("")
	if got = lastLine(t, updated.(FlagshipModel)); !strings.Contains(got, "can't run here") || !strings.Contains(got, f2.configPath) {
		t.Errorf("an unknown saved value was not reported:\n%s", got)
	}

	// A malformed one still says it could not be read.
	if err := os.WriteFile(f2.configPath, []byte(`{"flagship_agent": 2}`), 0o600); err != nil {
		t.Fatal(err)
	}
	updated, _ = m.agentProgram("")
	if got = lastLine(t, updated.(FlagshipModel)); !strings.Contains(got, "could not be read") || !strings.Contains(got, f2.configPath) {
		t.Errorf("a malformed saved value was not reported:\n%s", got)
	}
}
