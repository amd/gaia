// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/gaiainit"
)

// First-boot setup for a chat opened WITHOUT the readiness gate in front of it.
//
// The flagship agent is a local subprocess talking straight to Lemonade, so a
// clean machine's first launch used to die wherever the missing piece was first
// touched — Lemonade absent, or the chat/embedding model not downloaded — with
// whatever error that code path happened to produce, instead of the one command
// that fixes all of it.
//
// The launch path that DOES run the gate (root's local preflight) proves the
// same facts before chat opens, and tells the constructor so: arming this as
// well would spawn a fresh Python interpreter twice on every cold launch, each
// up to gaiainit.CheckTimeout. See NewChatModelForFlagship.
//
// `/setup` is unconditional either way — typing it IS the user asking for the
// real thing to run.

// setupAgentID is the one catalog agent this gate applies to.
const setupAgentID = "gaia"

// setupCheckResultMsg is delivered once the read-only readiness probe returns.
type setupCheckResultMsg struct {
	ready bool
	// err is non-nil only when the check itself could not run (gaia missing,
	// the probe timed out) -- never set merely because the profile isn't
	// ready, which is the ordinary "ready: false" case.
	err error
}

// setupStreamMsg carries one event, tagged with the channel it came from so a
// message from an abandoned run (cancelled, then /setup again) cannot be
// mistaken for the current one -- same pattern as eventMsg/m.events.
type setupStreamMsg struct {
	ch  <-chan gaiainit.Event
	evt gaiainit.Event
}

// checkSetupCmd asks whether the flagship profile is ready, without installing,
// starting, or downloading anything.
func checkSetupCmd(claudeMode bool) tea.Cmd {
	return func() tea.Msg {
		ready, err := gaiainit.Check(context.Background(), claudeMode)
		if err != nil {
			return setupCheckResultMsg{err: err}
		}
		return setupCheckResultMsg{ready: ready}
	}
}

// waitForSetupEvent reads the next event off a setup run's channel.
func waitForSetupEvent(ch <-chan gaiainit.Event) tea.Cmd {
	return func() tea.Msg {
		evt, ok := <-ch
		if !ok {
			return setupStreamMsg{ch: ch, evt: gaiainit.Event{Done: true}}
		}
		return setupStreamMsg{ch: ch, evt: evt}
	}
}

// applyFirstBootGate arms the first-boot check for the flagship agent. It does
// not run anything -- Init() fires the actual probe once Bubble Tea's event
// loop is running; this only makes the composer hold Enter (and Init hold the
// initial --query, if any) until that probe answers.
//
// A Claude session is never gated: `gaia init` starts LemonadeServer.exe
// unconditionally (_auto_start_server in src/gaia/installer/init_command.py),
// which is the one thing --use-claude exists to avoid. --skip-chat-model skips
// the model DOWNLOAD only; nothing skipped the server.
//
// Skipped, not hidden -- setupSkippedForClaudeNotice says so and what it costs.
func (m ChatModel) applyFirstBootGate() ChatModel {
	if m.agentID != setupAgentID || m.setupVerified {
		return m
	}
	if m.claudeMode {
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: setupSkippedForClaudeNotice,
		})
		return m
	}
	m.setupChecking = true
	return m
}

// setupSkippedForClaudeNotice says what was skipped and names the fix.
//
// Conditional on purpose. This is appended at construction, before anything
// knows the local server's state, and most machines running this already have
// Lemonade installed and up — asserting that document search is broken would
// be wrong for them. So it states what did not run and leaves whether that
// matters to what the user observes. Embeddings have no Anthropic equivalent,
// so those features do need Lemonade either way (see
// hub/agents/gaia/python/gaia_agent/stdio.py).
var setupSkippedForClaudeNotice = "Local setup skipped — `gaia init` was not run " +
	"and the Lemonade server was not started for this session. If document search, " +
	"memory or the code index turn out not to work, that is why: run `" +
	gaiainit.RunCommand(true) + "` in a terminal, or type /setup here."

// supersededSetup reports whether an event belongs to a setup run that is no
// longer the current one.
func (m ChatModel) supersededSetup(ch <-chan gaiainit.Event) bool {
	return ch != m.setupCh
}

// releaseAfterSetupGate fires whatever Init() deferred behind the first-boot
// gate now that it has resolved -- ready, or a setup run finished, failed,
// or was cancelled. Today that is only the one-shot --query launch; nothing
// queues a chat turn before the user has seen the composer at least once.
func (m *ChatModel) releaseAfterSetupGate() tea.Cmd {
	if m.initialQuery == "" {
		return nil
	}
	query := m.initialQuery
	m.initialQuery = ""
	return func() tea.Msg { return sendQueryMsg{query: query} }
}

// handleSetupCheckResult reacts to the first-boot readiness probe. A ready
// profile or a check that could not run both release the gate immediately --
// the latter fails loudly (an error message naming what to do) rather than
// silently blocking a machine that might in fact be fine.
func (m ChatModel) handleSetupCheckResult(msg setupCheckResultMsg) (tea.Model, tea.Cmd) {
	m.setupChecking = false

	if msg.err != nil {
		m.messages = append(m.messages, Message{
			Role: RoleError,
			Content: fmt.Sprintf(
				"Could not check whether %s is set up: %v\nType /setup to try running it directly, "+
					"or run `%s` in a terminal.",
				m.agentName, msg.err, gaiainit.RunCommand(m.claudeMode)),
		})
		m.updateViewport()
		return m, m.releaseAfterSetupGate()
	}

	if msg.ready {
		return m, m.releaseAfterSetupGate()
	}

	return m.startSetupRun(true /* firstBoot */)
}

// startSetupRun launches (or re-launches) `gaia init` for the flagship
// profile. firstBoot only changes the announcement's wording -- the
// first-boot trigger and /setup share every other line of code, so a user
// who reconfigures later gets exactly what a fresh machine gets.
func (m ChatModel) startSetupRun(firstBoot bool) (tea.Model, tea.Cmd) {
	ch, cancel, err := gaiainit.Start(m.claudeMode)
	if err != nil {
		m.messages = append(m.messages, Message{
			Role: RoleError,
			Content: fmt.Sprintf(
				"Could not start setup: %v\nRun `%s` in a terminal instead.",
				err, gaiainit.RunCommand(m.claudeMode)),
		})
		m.updateViewport()
		return m, m.releaseAfterSetupGate()
	}

	intro := "Setting up " + m.agentName + " -- running `gaia init --profile " + gaiainit.Profile + "`"
	if m.claudeMode {
		intro += " (skipping the local chat model: this session runs on Claude)"
	}
	intro += ". This can take a few minutes on a slow connection. Press Esc to cancel."
	if firstBoot {
		intro = "First run: " + intro
	}
	m.messages = append(m.messages, Message{Role: RoleStatus, Content: intro})
	m.upsertSetupProgress("starting…")

	m.setupRunning = true
	m.setupCancelRequested = false
	m.setupCancel = cancel
	m.setupCh = ch
	m.updateViewport()
	return m, waitForSetupEvent(ch)
}

// handleSetupEvent applies one line of `gaia init` output, or -- once Done
// -- the run's terminal result.
func (m ChatModel) handleSetupEvent(evt gaiainit.Event) (tea.Model, tea.Cmd) {
	if !evt.Done {
		m.upsertSetupProgress(evt.Line)
		m.updateViewport()
		return m, waitForSetupEvent(m.setupCh)
	}

	m.setupRunning = false
	m.setupCancel = nil
	m.setupCh = nil
	// `gaia init` installs and STARTS the local server, so whatever the last
	// ping said about it is now stale — drop it rather than let a `/model`
	// switch be refused against a server this run just brought up.
	m.lemonadeKnown = false
	m.lemonadeDownRefused = false

	switch {
	case m.setupCancelRequested:
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: "Setup cancelled. Type /setup to try again.",
		})
	case evt.Err != nil:
		m.messages = append(m.messages, Message{
			Role: RoleError,
			Content: fmt.Sprintf(
				"Setup failed: %v\nRun `%s` in a terminal to see the full log, then /setup to retry.",
				evt.Err, gaiainit.RunCommand(m.claudeMode)),
		})
	default:
		m.messages = append(m.messages, Message{Role: RoleStatus, Content: "[✓] Setup complete."})
	}
	m.setupCancelRequested = false
	m.updateViewport()
	return m, m.releaseAfterSetupGate()
}

// cancelSetup asks the in-flight `gaia init` child to stop. The confirmed
// "cancelled" message is left to handleSetupEvent's Done branch -- this only
// announces the request, mirroring requestCancel's "cancelling…" / settled
// two-step so Esc always produces an immediate, honest response.
func (m ChatModel) cancelSetup() (tea.Model, tea.Cmd) {
	m.setupCancelRequested = true
	if m.setupCancel != nil {
		m.setupCancel()
	}
	m.upsertSetupProgress("cancelling…")
	m.updateViewport()
	return m, nil
}

// setupProgressIdentity marks the one "what gaia init is doing right now"
// message so a run producing dozens of lines over several minutes updates a
// single spot in the transcript instead of scrolling everything else off
// screen -- the same in-place-update trick as upsertCard, for RoleStatus.
const setupProgressIdentity = "setup_progress"

func (m *ChatModel) upsertSetupProgress(line string) {
	for i := range m.messages {
		if m.messages[i].Role == RoleStatus && m.messages[i].Identity == setupProgressIdentity {
			m.messages[i].Content = "  " + line
			return
		}
	}
	m.messages = append(m.messages, Message{
		Role:     RoleStatus,
		Identity: setupProgressIdentity,
		Content:  "  " + line,
	})
}

// statusNote appends one status line and re-renders -- a small, generic
// counterpart to bypassNote for callers (like /setup) that have nothing
// bypass-specific about them.
func (m ChatModel) statusNote(text string) ChatModel {
	m.messages = append(m.messages, Message{Role: RoleStatus, Content: text})
	m.updateViewport()
	return m
}

// isSetupCommand reports whether a composed line is the /setup form, so the
// composer never sends it to the agent as a question.
func isSetupCommand(query string) bool {
	return strings.TrimSpace(query) == "/setup"
}
