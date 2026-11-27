// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"slices"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/event"
)

// pythonChat is the flagship on the Python agent, its skills already listed.
func pythonChat(t *testing.T) ChatModel {
	t.Helper()
	m := NewChatModelForCatalogAgent(&nullClient{}, setupAgentID, "GAIA")
	m.width, m.height = 120, 30
	m.gpuApplies = true
	m.skillCommands = []event.SkillCommand{{Name: "inbox-triage", Description: "Sort the inbox."}}
	return m
}

// gaiacppChat is the flagship on gaiacpp, which reports an empty skill list.
func gaiacppChat(t *testing.T) ChatModel {
	t.Helper()
	m := nativeChat(t)
	m.gpuApplies = true
	m.skillCommands = []event.SkillCommand{}
	return m
}

// sentToModel reports whether a submit started a turn carrying the line.
func sentToModel(m ChatModel) bool {
	return m.streaming && len(m.messages) > 0 && m.messages[len(m.messages)-1].Role == RoleUser
}

func gateError(m ChatModel) string {
	if len(m.messages) == 0 || m.messages[len(m.messages)-1].Role != RoleError {
		return ""
	}
	return m.messages[len(m.messages)-1].Content
}

// worksOnGaiacpp is the decision for every command the palette offers. A new
// command fails TestEveryCommandIsDecidedForBothAgents until it has a row.
var worksOnGaiacpp = map[string]bool{
	"/help": true, "/clear": true, "/memory": true, "/full-access": true, "/setup": true,
	"/connectors": true, "/models": true, "/gpu": true, "/slack": true, "/discord": true,
	"/automations": true, "/model": true, "/provider": true, "/gateway": true, "/cost": true,
	"/copy": true, "/copy all": true, "/read-aloud": true, "/voice": true, "/agent": true,
	"/agents": true, "/update": true, "/quit": true,
	"/skills": false, "/research": false, "/context": false, "/image": false,
	"/sessions": false, "/sessions all": false,
}

func TestEveryCommandIsDecidedForBothAgents(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	t.Setenv("GAIA_HOME", t.TempDir())
	for _, c := range paletteCommands {
		works, decided := worksOnGaiacpp[c.Name]
		if !decided {
			t.Errorf("%s has no row in worksOnGaiacpp: decide whether gaiacpp serves it", c.Name)
			continue
		}

		py := pythonChat(t)
		if why := py.nativeUnavailable(c.Name); why != "" {
			t.Errorf("%s is refused on the Python agent: %s", c.Name, why)
		}
		if !py.availableCommandSet()[c.Name] {
			t.Errorf("%s is missing from the Python agent's palette and /help", c.Name)
		}

		cpp := gaiacppChat(t)
		offered := cpp.availableCommandSet()[c.Name]
		if works {
			if why := cpp.nativeUnavailable(c.Name); why != "" {
				t.Errorf("%s is refused on gaiacpp: %s", c.Name, why)
			}
			if !offered {
				t.Errorf("%s works on gaiacpp but its palette and /help leave it out", c.Name)
			}
			continue
		}
		if offered {
			t.Errorf("%s does not work on gaiacpp but its palette and /help offer it", c.Name)
		}
		updated, _ := cpp.submit(c.Name)
		next := updated.(ChatModel)
		if next.streaming {
			t.Errorf("%s was sent to gaiacpp, which reads it as chat text", c.Name)
		}
		word, _, _ := strings.Cut(c.Name, " ")
		got := gateError(next)
		for _, want := range []string{word + " needs Full-featured (the Python agent): gaiacpp", "--agent-program gaiapy"} {
			if !strings.Contains(got, want) {
				t.Errorf("%s on gaiacpp: the refusal does not say %q: %q", c.Name, want, got)
			}
		}
	}
	for name := range worksOnGaiacpp {
		if !slices.ContainsFunc(paletteCommands, func(c paletteCommand) bool { return c.Name == name }) {
			t.Errorf("worksOnGaiacpp has a row for %s, which the palette no longer offers", name)
		}
	}
}

func TestTheNativeRefusalNamesTheWayToSwitch(t *testing.T) {
	m := gaiacppChat(t)
	if got := m.nativeUnavailable("/sessions"); got != "/sessions needs Full-featured (the Python agent): gaiacpp, the Fast and light agent "+
		"this session runs, doesn't keep sessions yet. Start gaia-cli with --agent-program gaiapy to use it." {
		t.Errorf("refusal = %q", got)
	}
	if got := m.WithAgentPrograms().nativeUnavailable("/context"); !strings.HasSuffix(got, "Type /agent gaiapy to switch.") {
		t.Errorf("a chat that can switch does not say how: %q", got)
	}
	for _, line := range []string{"/research tides", "/research-report tides", "/skills add x", "/image open", "/Sessions all"} {
		updated, _ := m.submit(line)
		next := updated.(ChatModel)
		if next.streaming || !strings.Contains(gateError(next), "(the Python agent)") {
			t.Errorf("%q on gaiacpp was not refused: streaming=%v, %q", line, next.streaming, gateError(next))
		}
	}
}

// The Python agent still gets what gaiacpp is refused.
func TestThePythonAgentStillRunsWhatGaiacppLacks(t *testing.T) {
	for _, line := range []string{"/skills", "/research tides"} {
		updated, _ := pythonChat(t).submit(line)
		if next := updated.(ChatModel); !sentToModel(next) {
			t.Errorf("%q was not sent to the Python agent: %q", line, gateError(next))
		}
	}
	updated, _ := pythonChat(t).submit("/context")
	if next := updated.(ChatModel); gateError(next) != "" || next.streaming {
		t.Errorf("/context on the Python agent: streaming=%v, %q", next.streaming, gateError(next))
	}
	_, cmd := pythonChat(t).submit("/sessions")
	if _, ok := cmd().(OpenSessionsMsg); !ok {
		t.Error("/sessions on the Python agent did not open the session list")
	}
}

func TestCommandShapedLinesNeverReachTheModel(t *testing.T) {
	const (
		text     = "sent"
		unknown  = "Unknown command "
		badForm  = " doesn't take "
		quits    = "quit"
		opensHlp = "help"
	)
	cases := []struct{ line, want string }{
		{"/tmp", unknown},
		{"/foo bar", unknown},
		{"/", unknown},
		{"/ foo", unknown},
		{" /foo", unknown},
		{"/resume", unknown},
		{"/history", unknown},
		{"/3d", unknown},
		{"/foo\nbar", unknown},
		{"/copy foo", badForm},
		{"/help me write a letter", badForm},
		{"/usr/bin/x", text},
		{"/Users/kalin/file.txt please summarise", text},
		{"/etc/hosts", text},
		{"/tmp/", text},
		{"//", text},
		{"/foo.txt", text},
		{"hello /foo", text},
		{"what is /tmp", text},
		{"/Help", opensHlp},
		{"/help ", opensHlp},
		{"/quit", quits},
		{"/exit", quits},
		{"/QUIT", quits},
	}
	agents := map[string]func(*testing.T) ChatModel{"gaiapy": pythonChat, "gaiacpp": gaiacppChat}
	for agent, build := range agents {
		for _, tc := range cases {
			updated, cmd := build(t).submit(tc.line)
			next := updated.(ChatModel)
			switch tc.want {
			case text:
				if !sentToModel(next) {
					t.Errorf("%s: %q was not sent as a message: %q", agent, tc.line, gateError(next))
				}
			case quits:
				if cmd == nil {
					t.Errorf("%s: %q did nothing", agent, tc.line)
				} else if _, ok := cmd().(tea.QuitMsg); !ok || next.streaming {
					t.Errorf("%s: %q did not quit", agent, tc.line)
				}
			case opensHlp:
				if cmd == nil {
					t.Errorf("%s: %q did nothing", agent, tc.line)
				} else if _, ok := cmd().(ToggleHelpMsg); !ok || next.streaming {
					t.Errorf("%s: %q did not open /help", agent, tc.line)
				}
			default:
				if next.streaming {
					t.Errorf("%s: %q was sent to the model", agent, tc.line)
				}
				if got := gateError(next); !strings.Contains(got, tc.want) || !strings.Contains(got, "/help lists commands") {
					t.Errorf("%s: %q answered %q, want it to say %q and point at /help", agent, tc.line, got, tc.want)
				}
			}
		}
	}
}

func TestTheUnknownCommandLineSaysHowToSendItAsText(t *testing.T) {
	updated, _ := pythonChat(t).submit("/tmp")
	want := `Unknown command /tmp; /help lists commands. To send this as a message, start it with something other than "/".`
	if got := gateError(updated.(ChatModel)); got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestAnInstalledSkillStillRunsByName(t *testing.T) {
	for _, line := range []string{"/inbox-triage", "/inbox-triage now"} {
		updated, _ := pythonChat(t).submit(line)
		if next := updated.(ChatModel); !sentToModel(next) {
			t.Errorf("%q did not reach the agent: %q", line, gateError(next))
		}
	}
	updated, _ := gaiacppChat(t).submit("/inbox-triage")
	if next := updated.(ChatModel); next.streaming || !strings.Contains(gateError(next), "Unknown command /inbox-triage") {
		t.Errorf("gaiacpp lists no skills, yet /inbox-triage was not refused: %q", gateError(next))
	}
}

// Before the agent lists its skills, /<name> can't be told from a typo.
func TestACommandBeforeTheSkillListIsHeldBack(t *testing.T) {
	m := pythonChat(t)
	m.skillCommands = nil
	updated, _ := m.submit("/inbox-triage")
	next := updated.(ChatModel)
	if next.streaming || !strings.Contains(gateError(next), "hasn't listed its skills yet") {
		t.Errorf("streaming=%v, %q", next.streaming, gateError(next))
	}
}

// An agent that is not GAIA's own may read slash commands itself.
func TestAnotherAgentKeepsItsOwnSlashCommands(t *testing.T) {
	m, _ := newTestModel(t)
	updated, _ := m.submit("/foo")
	if next := updated.(ChatModel); !sentToModel(next) {
		t.Errorf("/foo was not passed to a third-party agent: %q", gateError(next))
	}
	updated, _ = m.submit("/copy foo")
	if next := updated.(ChatModel); next.streaming || !strings.Contains(gateError(next), "/copy doesn't take") {
		t.Errorf("a bad form of the TUI's own /copy was not refused: %q", gateError(next))
	}
}

func TestThePaletteOffersOnlyWhatGaiacppRuns(t *testing.T) {
	m := gaiacppChat(t)
	var names []string
	for _, c := range filterPaletteCommands("/", m.availableCommandSet()) {
		names = append(names, c.Name)
	}
	for _, hidden := range []string{"/sessions", "/sessions all", "/context", "/skills", "/research", "/image"} {
		if slices.Contains(names, hidden) {
			t.Errorf("gaiacpp's palette offers %s", hidden)
		}
		if slices.Contains(m.AvailableCommandNames(), hidden) {
			t.Errorf("gaiacpp's /help lists %s", hidden)
		}
	}
	for _, shown := range []string{"/help", "/model", "/memory", "/quit", "/agent"} {
		if !slices.Contains(names, shown) {
			t.Errorf("gaiacpp's palette is missing %s", shown)
		}
	}
}
