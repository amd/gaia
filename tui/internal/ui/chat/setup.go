// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os/exec"
	"strings"
	"sync"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

// First-boot setup: the flagship agent (setupAgentID) is a local subprocess
// talking directly to Lemonade, with no daemon in front of it to run the
// preflight gate other agents get (see root/preflight.go, which explicitly
// skips non-daemon transports). Without this, a clean machine's first launch
// died wherever the missing piece was first touched -- Lemonade absent, or
// the chat/embedding model not downloaded -- with whatever error that
// particular code path happened to produce, instead of the one command that
// fixes all of it.
//
// This reuses `gaia init` itself rather than re-deriving its checks in Go
// (src/gaia/installer/init_command.py is the single source of truth for what
// "set up" means): a fast, side-effect-free `gaia init --check` decides
// whether anything needs to run, and a real `gaia init --profile chat --yes`
// does the work if so, with its own stdout/stderr streamed into the
// transcript as it happens.
//
// "Not set up" is read from the SAME real state `gaia init` itself checks
// (Lemonade installed + reachable, required models present) every launch --
// never a marker file recorded after the first successful run, which goes
// stale the moment a model is deleted or Lemonade is uninstalled outside
// GAIA's knowledge.

// setupAgentID is the one catalog agent this gate applies to.
const setupAgentID = "gaia"

// setupProfile is the `gaia init` profile the flagship agent needs -- see
// INIT_PROFILES["chat"] in src/gaia/installer/init_command.py: the chat
// model, the RAG/memory embedder, and the [rag] pip extras.
const setupProfile = "chat"

// setupCheckTimeout bounds the read-only readiness probe. It runs a fresh
// Python interpreter plus one Lemonade health check, so a few seconds is
// normal; this only guards against a wedged network call hanging the gate
// forever on an otherwise-idle launch.
const setupCheckTimeout = 30 * time.Second

// gaiaBinary resolves the `gaia` CLI on PATH. A package var, not a bare
// exec.LookPath call, so tests can substitute a stub without spawning the
// real CLI (mirrors daemon.Options.StartCommand's injection point).
var gaiaBinary = func() (string, error) {
	bin, err := exec.LookPath("gaia")
	if err != nil {
		return "", fmt.Errorf(
			"the `gaia` CLI is not on PATH, so setup cannot run. " +
				"Install GAIA with `curl -fsSL https://amd-gaia.ai/install.sh | sh` " +
				"(on Windows: `irm https://amd-gaia.ai/install.ps1 | iex`), or " +
				"`pip install amd-gaia` into the Python environment on your PATH, " +
				"then retry. From a clone of the repo, `pip install -e .` works too")
	}
	return bin, nil
}

// setupCheckArgs/setupRunArgs build `gaia init` argv for the flagship
// profile. claudeMode mirrors --use-claude onto --skip-chat-model: a
// Claude-backed session never calls the local chat LLM, only Lemonade's
// embedder for RAG/memory (Anthropic has no embeddings API -- see
// hub/agents/gaia/python/gaia_agent/stdio.py's header comment). Downloading
// several GB of a chat model that session will never touch is the bug this
// avoids.
func setupCheckArgs(claudeMode bool) []string {
	args := []string{"init", "--check", "--profile", setupProfile}
	if claudeMode {
		args = append(args, "--skip-chat-model")
	}
	return args
}

func setupRunArgs(claudeMode bool) []string {
	// --yes: nothing here can answer an interactive prompt. The child's
	// stdin is not connected to the terminal (Bubble Tea owns it), so a
	// prompt gaia init tried to read would hang forever with no way to
	// answer it.
	args := []string{"init", "--profile", setupProfile, "--yes"}
	if claudeMode {
		args = append(args, "--skip-chat-model")
	}
	return args
}

// claudeSkipSuffix is the flag to append to a `gaia init` command shown to
// the user, so a copy-pasted retry matches what was actually attempted.
func claudeSkipSuffix(claudeMode bool) string {
	if claudeMode {
		return " --skip-chat-model"
	}
	return ""
}

// setupCheckResultMsg is delivered once the read-only readiness probe
// (`gaia init --check`) returns.
type setupCheckResultMsg struct {
	ready bool
	// err is non-nil only when the check itself could not run (gaia missing,
	// the probe timed out) -- never set merely because the profile isn't
	// ready, which is the ordinary "ready: false" case.
	err error
}

// setupEvent is what the setup goroutine pushes over the channel: either one
// line of `gaia init` output, or -- once Done -- the run's final result.
type setupEvent struct {
	line string
	done bool
	// err is the process's own exit error (nil on success). Meaningless
	// unless done.
	err error
}

// setupStreamMsg carries one setupEvent, tagged with the channel it came
// from so a message from an abandoned run (cancelled, then /setup again)
// cannot be mistaken for the current one -- same pattern as eventMsg/m.events.
type setupStreamMsg struct {
	ch  <-chan setupEvent
	evt setupEvent
}

// checkSetupCmd runs `gaia init --check` and reports whether the flagship
// profile is ready, without installing, starting, or downloading anything.
func checkSetupCmd(claudeMode bool) tea.Cmd {
	return func() tea.Msg {
		bin, err := gaiaBinary()
		if err != nil {
			return setupCheckResultMsg{err: err}
		}
		ctx, cancel := context.WithTimeout(context.Background(), setupCheckTimeout)
		defer cancel()
		cmd := exec.CommandContext(ctx, bin, setupCheckArgs(claudeMode)...)
		runErr := cmd.Run()
		if runErr == nil {
			return setupCheckResultMsg{ready: true}
		}
		var exitErr *exec.ExitError
		if errors.As(runErr, &exitErr) {
			// Exit 1 is `gaia init --check`'s documented "not ready" answer
			// -- the expected negative, not a failure to ask the question.
			return setupCheckResultMsg{ready: false}
		}
		return setupCheckResultMsg{err: fmt.Errorf("could not check whether setup is needed: %w", runErr)}
	}
}

// startSetup launches `gaia init` for the flagship profile and begins
// streaming its stdout/stderr, one setupEvent per line, terminated by a
// setupEvent{done: true}. The returned CancelFunc kills the child; safe to
// call from any goroutine.
func startSetup(claudeMode bool) (<-chan setupEvent, context.CancelFunc, error) {
	bin, err := gaiaBinary()
	if err != nil {
		return nil, nil, err
	}

	ctx, cancel := context.WithCancel(context.Background())
	cmd := exec.CommandContext(ctx, bin, setupRunArgs(claudeMode)...)

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		cancel()
		return nil, nil, fmt.Errorf("could not prepare `gaia init`: %w", err)
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		cancel()
		return nil, nil, fmt.Errorf("could not prepare `gaia init`: %w", err)
	}

	if err := cmd.Start(); err != nil {
		cancel()
		return nil, nil, fmt.Errorf("could not start `gaia init`: %w", err)
	}

	ch := make(chan setupEvent, 64)

	// os/exec forbids calling Wait before every Read from a pipe it created
	// has completed (see subprocess.go's procHandle for the same rule) --
	// stdout and stderr are two separate pipes here, so both readers have to
	// finish before Wait is safe.
	var wg sync.WaitGroup
	wg.Add(2)
	stream := func(r io.Reader) {
		defer wg.Done()
		scanner := bufio.NewScanner(r)
		scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
		for scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if line != "" {
				ch <- setupEvent{line: line}
			}
		}
	}
	go stream(stdout)
	go stream(stderr)

	go func() {
		wg.Wait()
		waitErr := cmd.Wait()
		ch <- setupEvent{done: true, err: waitErr}
		close(ch)
	}()

	return ch, cancel, nil
}

// waitForSetupEvent reads the next event off a setup run's channel.
func waitForSetupEvent(ch <-chan setupEvent) tea.Cmd {
	return func() tea.Msg {
		evt, ok := <-ch
		if !ok {
			return setupStreamMsg{ch: ch, evt: setupEvent{done: true}}
		}
		return setupStreamMsg{ch: ch, evt: evt}
	}
}

// applyFirstBootGate arms the first-boot check for the flagship agent. It
// does not run anything -- Init() fires the actual probe once Bubble Tea's
// event loop is running; this only makes the composer hold Enter (and Init
// hold the initial --query, if any) until that probe answers.
func (m ChatModel) applyFirstBootGate() ChatModel {
	if m.agentID == setupAgentID {
		m.setupChecking = true
	}
	return m
}

// supersededSetup reports whether an event belongs to a setup run that is no
// longer the current one.
func (m ChatModel) supersededSetup(ch <-chan setupEvent) bool {
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
					"or run `gaia init --profile %s%s` in a terminal.",
				m.agentName, msg.err, setupProfile, claudeSkipSuffix(m.claudeMode)),
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
	ch, cancel, err := startSetup(m.claudeMode)
	if err != nil {
		m.messages = append(m.messages, Message{
			Role: RoleError,
			Content: fmt.Sprintf(
				"Could not start setup: %v\nRun `gaia init --profile %s%s` in a terminal instead.",
				err, setupProfile, claudeSkipSuffix(m.claudeMode)),
		})
		m.updateViewport()
		return m, m.releaseAfterSetupGate()
	}

	intro := "Setting up " + m.agentName + " -- running `gaia init --profile " + setupProfile + "`"
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
func (m ChatModel) handleSetupEvent(evt setupEvent) (tea.Model, tea.Cmd) {
	if !evt.done {
		m.upsertSetupProgress(evt.line)
		m.updateViewport()
		return m, waitForSetupEvent(m.setupCh)
	}

	m.setupRunning = false
	m.setupCancel = nil
	m.setupCh = nil

	switch {
	case m.setupCancelRequested:
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: "Setup cancelled. Type /setup to try again.",
		})
	case evt.err != nil:
		m.messages = append(m.messages, Message{
			Role: RoleError,
			Content: fmt.Sprintf(
				"Setup failed: %v\nRun `gaia init --profile %s%s` in a terminal to see the full log, "+
					"then /setup to retry.",
				evt.err, setupProfile, claudeSkipSuffix(m.claudeMode)),
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
