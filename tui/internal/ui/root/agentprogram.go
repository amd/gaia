// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package root

import (
	"errors"
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// The flagship answers through one of two programs, and /agent shows them and
// switches between them. Both carry the agent id "gaia", so this is not an
// /agents switch: the program changes and the agent does not.

// programOption is one row of the /agent listing.
type programOption struct {
	choice   string // the saved value, and what the user types
	label    string
	beta     bool
	goodAt   string
	tradeOff string
}

var programOptions = []programOption{
	{
		choice:   preflight.FlagshipAgentPython,
		label:    "Python agent (" + catalog.PythonAgentBinary + ")",
		goodAt:   "Mature and well tested, with every tool and feature.",
		tradeOff: "Heavier: slower to start and uses more memory.",
	},
	{
		choice:   preflight.FlagshipAgentNative,
		label:    "Native C++ agent",
		beta:     true,
		goodAt:   "Fast and light: starts quickly and uses little memory.",
		tradeOff: "Fewer tools and features so far, and less tested.",
	},
}

// programSwitch is a switch of agent program waiting on the readiness gate.
type programSwitch struct {
	from, to string
	// catalog replaces the session's catalog once the launch commits.
	catalog *catalog.Catalog
}

// WithAgentPin records what fixed this session's agent program at launch: a
// flag or a variable. Empty leaves /agent free to switch it.
func (m FlagshipModel) WithAgentPin(source string) FlagshipModel {
	m.agentPin = source
	return m
}

// programOf names the saved choice agent is running as.
func programOf(agent catalog.Agent) string {
	if agent.Native {
		return preflight.FlagshipAgentNative
	}
	return preflight.FlagshipAgentPython
}

// resolveProgram finds the flagship on the program a saved choice names, from
// what is installed right now.
func (m FlagshipModel) resolveProgram(choice string) (*catalog.Catalog, string, error) {
	resolve := m.programs
	if resolve == nil {
		resolve = catalog.ForFlagship
	}
	return resolve(preflight.FlagshipAgentProgram(choice))
}

func (m FlagshipModel) readProgram() (preflight.FlagshipAgent, error) {
	if m.savedProgram != nil {
		return m.savedProgram()
	}
	return preflight.ReadFlagshipAgent()
}

func (m FlagshipModel) writeProgram(choice string) (string, error) {
	if m.saveProgram != nil {
		return m.saveProgram(choice)
	}
	return preflight.WriteFlagshipAgent(choice)
}

// agentProgram answers /agent: the listing with no argument, a switch with one.
func (m FlagshipModel) agentProgram(arg string) (tea.Model, tea.Cmd) {
	if m.chat == nil {
		return m, nil
	}
	if m.agent.ID != catalog.FlagshipID {
		m.chat.AppendStatus(fmt.Sprintf(
			"%s runs on one program. Only the GAIA agent has two to choose from.", m.agent.Name))
		return m, nil
	}
	choice := strings.ToLower(strings.TrimSpace(arg))
	if choice == "" {
		m.chat.AppendStatus(m.programListing())
		return m, nil
	}
	if preflight.FlagshipAgentProgram(choice) == "" {
		m.chat.AppendStatus(fmt.Sprintf(
			"%q is not an agent program. Use /agent %s or /agent %s; /agent alone shows both.",
			arg, preflight.FlagshipAgentPython, preflight.FlagshipAgentNative))
		return m, nil
	}
	return m.switchProgram(choice)
}

// switchProgram relaunches the flagship on choice, through the same readiness
// gate and launch an /agents switch uses, and saves the choice once it runs.
func (m FlagshipModel) switchProgram(choice string) (tea.Model, tea.Cmd) {
	current := programOf(m.agent)

	// Found before anything is saved: a choice that cannot run would stop the
	// next launch.
	cat, notice, err := m.resolveProgram(choice)
	if why, ok := downloadable(err); ok && !m.programFetched {
		return m.offerProgram(why)
	}
	if err != nil {
		m.chat.AppendStatus(fmt.Sprintf(
			"Cannot switch to %s: %v\nNothing was changed: this session is still on %s and your saved choice is as it was.",
			choice, err, current))
		return m, nil
	}
	next := cat.Get(catalog.FlagshipID)
	if next == nil {
		m.chat.AppendStatus(fmt.Sprintf(
			"Cannot switch to %s: the catalog has no %q entry. Nothing was changed. Report this with GAIA diagnostics.",
			choice, catalog.FlagshipID))
		return m, nil
	}

	if m.agentPin != "" {
		path, err := m.writeProgram(choice)
		if err != nil {
			m.chat.AppendStatus(saveFailed(choice, current, err))
			return m, nil
		}
		m.chat.AppendStatus(fmt.Sprintf(
			"Saved %s as your agent program (%s). This session is pinned to %s by %s, so it keeps running that. "+
				"Start GAIA without %s to use the saved choice.",
			choice, path, current, m.agentPin, m.agentPin))
		return m, nil
	}

	if choice == current {
		path, err := m.writeProgram(choice)
		if err != nil {
			m.chat.AppendStatus(saveFailed(choice, current, err))
			return m, nil
		}
		m.chat.AppendStatus(fmt.Sprintf("Already running %s. It is saved as your agent program (%s).", choice, path))
		return m, nil
	}

	if reason := m.nativeRefusal(next.Native); reason != "" {
		m.chat.AppendStatus(reason + fmt.Sprintf(
			" Nothing was changed: this session is still on %s and your saved choice is as it was.", current))
		return m, nil
	}
	if m.chat.IsStreaming() {
		m.chat.AppendStatus(fmt.Sprintf(
			"A turn is still running on %s. Press Esc to stop it, or wait for it, then run /agent %s again. Nothing was changed.",
			current, choice))
		return m, nil
	}

	// Nothing is saved yet: a program that fails the gate or the launch must
	// not become what the next launch demands. commitProgramSwitch saves it.
	// The outgoing client stays up until the gate passes; see switchAgent.
	transcript := append([]chat.Message(nil), m.chat.Messages()...)
	transcript = append(transcript, chat.Message{Role: chat.RoleStatus, Content: programDivider(current, choice)})
	if notice != "" {
		transcript = append(transcript, chat.Message{Role: chat.RoleStatus, Content: notice})
	}
	m.pendingTranscript = transcript
	m.programSwitch = &programSwitch{from: current, to: choice, catalog: cat}
	return m.beginPreflight(*next)
}

// nativeRefusal is why this session cannot move to gaiacpp, or "". The same
// two cases refuse a gaiacpp launch on the command line.
func (m FlagshipModel) nativeRefusal(native bool) string {
	if !native {
		return ""
	}
	if m.useClaude {
		return "Cannot switch to " + preflight.FlagshipAgentNative + ": this session was started with --use-claude. " +
			client.NativeClaudeUnavailable
	}
	if m.reopened {
		return "Cannot switch to " + preflight.FlagshipAgentNative + ": this is a reopened session (--resume or /sessions), and " +
			catalog.NativeAgentBinary + " doesn't keep sessions yet, so it could not carry this one on. " +
			"Switch from a new session instead."
	}
	return ""
}

func saveFailed(choice, current string, err error) string {
	return fmt.Sprintf("Could not save %s as your agent program: %v. Nothing was switched: this session is still on %s.",
		choice, err, current)
}

// programDivider is the line a switched transcript continues under.
func programDivider(from, to string) string {
	return fmt.Sprintf("Switched the agent program from %s to %s. %s now answers, and it does not see the conversation above this line.",
		from, to, to)
}

// commitProgramSwitch finishes a switch whose launch succeeded: the program is
// running, so now it is saved as the choice.
func (m *FlagshipModel) commitProgramSwitch(agent catalog.Agent) {
	if m.programSwitch == nil {
		return
	}
	sw := m.programSwitch
	if _, err := m.writeProgram(sw.to); err != nil {
		m.chat.AppendStatus(fmt.Sprintf(
			"This session now runs %s, but it could not be saved as your agent program: %v. "+
				"The next launch uses your earlier choice; run /agent %s again once that is fixed.",
			sw.to, err, sw.to))
	}
	m.catalog = sw.catalog
	if m.pfLocal != nil {
		// Later gates must probe the program now running, not the launch one.
		local := *m.pfLocal
		local.Binary = agent.BinaryPath
		m.pfLocal = &local
	}
	m.programSwitch = nil
}

// programListing is /agent with no argument: both programs, what each is good
// at, which is running, which is saved, and whether each is installed.
func (m FlagshipModel) programListing() string {
	current := programOf(m.agent)
	saved, savedErr := m.readProgram()

	var b strings.Builder
	b.WriteString("Agent programs: two programs can answer as GAIA.\n")
	for _, opt := range programOptions {
		marker := "○"
		if opt.choice == current {
			marker = "●"
		}
		tags := []string{opt.label}
		if opt.beta {
			tags = append(tags, "Beta")
		}
		if opt.choice == current {
			tags = append(tags, "running now")
		}
		if savedErr == nil && opt.choice == saved.Choice {
			tags = append(tags, "your saved choice")
		}
		fmt.Fprintf(&b, "\n%s %s — %s\n", marker, opt.choice, strings.Join(tags, " · "))
		fmt.Fprintf(&b, "    %s\n    %s\n", opt.goodAt, opt.tradeOff)
		if opt.choice != current {
			if why := m.programUnavailable(opt.choice); why != "" {
				fmt.Fprintf(&b, "    %s\n", why)
			}
		}
	}

	b.WriteString("\n")
	switch {
	case savedErr != nil:
		fmt.Fprintf(&b, "Your saved choice could not be read: %v\n", savedErr)
	case preflight.FlagshipAgentUnavailable(saved.Choice) != "":
		fmt.Fprintf(&b, "Your saved choice can't run here: %s (%s in %s).\n",
			preflight.FlagshipAgentUnavailable(saved.Choice), preflight.FlagshipAgentKey, saved.Path)
	case saved.Choice == "":
		fmt.Fprintf(&b, "No saved choice yet: GAIA runs %s.\n", preflight.FlagshipAgentPython)
	}
	if m.agentPin != "" {
		fmt.Fprintf(&b, "This session is pinned to %s by %s; a switch here is saved for later launches.\n",
			current, m.agentPin)
	}
	fmt.Fprintf(&b, "Switch with /agent %s or /agent %s. The choice is saved and shared with the Agent UI.",
		preflight.FlagshipAgentPython, preflight.FlagshipAgentNative)
	return b.String()
}

// programUnavailable says why choice cannot be switched to, naming where it
// was looked for, or "" when it is installed.
func (m FlagshipModel) programUnavailable(choice string) string {
	_, _, err := m.resolveProgram(choice)
	if err == nil {
		return ""
	}
	var missing *catalog.MissingProgramError
	if errors.As(err, &missing) {
		if missing.Lookup.Unverified != "" {
			return fmt.Sprintf("Unavailable: %s is there but its install is unfinished. Re-run the GAIA installer.",
				missing.Lookup.Unverified)
		}
		if missing.Binary == catalog.NativeAgentBinary {
			return "Not on this PC yet: /agent " + choice + " offers to download it."
		}
		return "Unavailable: not installed. Looked in: " + strings.Join(missing.Lookup.Looked, ", ")
	}
	return "Unavailable: " + err.Error()
}
