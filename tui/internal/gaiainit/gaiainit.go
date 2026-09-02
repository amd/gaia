// Package gaiainit runs `gaia init` — the one command that installs Lemonade
// and downloads the models the flagship agent needs.
//
// It exists so the readiness screen and the chat view cannot disagree about
// what "set up" means. Both ask the SAME `gaia init --check`, read the SAME
// exit codes, and start the SAME `gaia init --profile chat --yes`. Re-deriving
// those checks in Go was never on the table either: src/gaia/installer/
// init_command.py is the single source of truth for what setup is, and a Go
// copy of it would go stale the first time a profile changed.
//
// Nothing here is cached. Readiness is read from the same real state `gaia
// init` itself checks — Lemonade installed and reachable, required models
// present — on every call, never from a marker file recorded after a
// successful run, which goes stale the moment a model is deleted.
package gaiainit

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os/exec"
	"strings"
	"sync"
	"time"
)

// Profile is the `gaia init` profile the flagship agent needs — see
// INIT_PROFILES["chat"] in src/gaia/installer/init_command.py: the chat model,
// the RAG/memory embedder, and the [rag] pip extras.
const Profile = "chat"

// CheckTimeout bounds the read-only readiness probe. It runs a fresh Python
// interpreter plus one Lemonade health check, so a few seconds is normal; this
// only guards against a wedged network call hanging a caller forever.
const CheckTimeout = 30 * time.Second

// notReadyExitCode is the ONLY exit code that means "not set up yet".
// Anything else — notably 2, which an installed gaia older than `--check`
// returns for "unrecognized arguments" — means the question was not answered,
// and must not be mistaken for a clean machine.
const notReadyExitCode = 1

// ErrUnanswered wraps every failure to ASK the question, as opposed to a clean
// "not ready" answer. Callers must not render it as "not set up": treating it
// that way ran a full multi-minute `gaia init` on every launch against an older
// gaia (#the --check rollout).
var ErrUnanswered = errors.New("setup readiness could not be determined")

// Binary resolves the `gaia` CLI on PATH. A package var, not a bare
// exec.LookPath call, so tests can substitute a stub without spawning the real
// CLI (mirrors daemon.Options.StartCommand's injection point).
var Binary = func() (string, error) {
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

// CheckArgs and RunArgs build `gaia init` argv for the flagship profile.
//
// claudeMode mirrors --use-claude onto --skip-chat-model: a Claude-backed
// session never calls the local chat LLM, only Lemonade's embedder for
// RAG/memory (Anthropic has no embeddings API — see
// hub/agents/gaia/python/gaia_agent/stdio.py). Downloading several GB of a chat
// model that session will never touch is the bug this avoids.
func CheckArgs(claudeMode bool) []string {
	args := []string{"init", "--check", "--profile", Profile}
	if claudeMode {
		args = append(args, "--skip-chat-model")
	}
	return args
}

func RunArgs(claudeMode bool) []string {
	// --yes: nothing here can answer an interactive prompt. The child's stdin is
	// not connected to the terminal, so a prompt gaia init tried to read would
	// hang forever with no way to answer it.
	args := []string{"init", "--profile", Profile, "--yes"}
	if claudeMode {
		args = append(args, "--skip-chat-model")
	}
	return args
}

// SkipSuffix is the flag to append to a `gaia init` command shown to the user,
// so a copy-pasted retry matches what was actually attempted.
func SkipSuffix(claudeMode bool) string {
	if claudeMode {
		return " --skip-chat-model"
	}
	return ""
}

// RunCommand is the command a user should type to do this themselves.
func RunCommand(claudeMode bool) string {
	return "gaia init --profile " + Profile + SkipSuffix(claudeMode)
}

// Check reports whether the flagship profile is ready, without installing,
// starting, or downloading anything.
//
// Three outcomes, and conflating any two of them is a bug: ready, not ready,
// and — wrapped in ErrUnanswered — the question was never answered.
func Check(ctx context.Context, claudeMode bool) (ready bool, err error) {
	bin, err := Binary()
	if err != nil {
		return false, fmt.Errorf("%w: %w", ErrUnanswered, err)
	}
	ctx, cancel := context.WithTimeout(ctx, CheckTimeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, bin, CheckArgs(claudeMode)...)
	// Captured so a failure can quote what the tool actually said. Without it
	// the only evidence is an exit code.
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out

	runErr := cmd.Run()
	if runErr == nil {
		return true, nil
	}
	var exitErr *exec.ExitError
	if errors.As(runErr, &exitErr) && exitErr.ExitCode() == notReadyExitCode {
		// Exit 1 is `gaia init --check`'s documented "not ready" answer — the
		// expected negative, not a failure to ask the question.
		return false, nil
	}
	return false, fmt.Errorf("%w (%w). GAIA said: %s",
		ErrUnanswered, runErr, LastMeaningfulLine(out.String()))
}

// Event is one line of `gaia init` output, or — once Done — the run's result.
type Event struct {
	Line string
	Done bool
	// Err is the process's own exit error (nil on success). Meaningless unless
	// Done.
	Err error
}

// Start launches `gaia init` for the flagship profile and streams its
// stdout/stderr, one Event per line, terminated by an Event{Done: true}. The
// returned CancelFunc kills the child; safe to call from any goroutine.
func Start(claudeMode bool) (<-chan Event, context.CancelFunc, error) {
	bin, err := Binary()
	if err != nil {
		return nil, nil, err
	}

	ctx, cancel := context.WithCancel(context.Background())
	cmd := exec.CommandContext(ctx, bin, RunArgs(claudeMode)...)

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

	ch := make(chan Event, 64)

	// os/exec forbids calling Wait before every Read from a pipe it created has
	// completed — stdout and stderr are two separate pipes, so both readers have
	// to finish before Wait is safe.
	var wg sync.WaitGroup
	wg.Add(2)
	stream := func(r io.Reader) {
		defer wg.Done()
		scanner := bufio.NewScanner(r)
		scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
		for scanner.Scan() {
			if line := strings.TrimSpace(scanner.Text()); line != "" {
				ch <- Event{Line: line}
			}
		}
	}
	go stream(stdout)
	go stream(stderr)

	go func() {
		wg.Wait()
		waitErr := cmd.Wait()
		ch <- Event{Done: true, Err: waitErr}
		close(ch)
	}()

	return ch, cancel, nil
}

// LastMeaningfulLine picks the line worth quoting back out of a failed child's
// output. The LAST non-empty one, because a CLI that rejects its arguments
// prints its whole usage banner first and the actual complaint at the end.
func LastMeaningfulLine(s string) string {
	lines := strings.Split(s, "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		if t := strings.TrimSpace(lines[i]); t != "" {
			return t
		}
	}
	return "(no output)"
}
