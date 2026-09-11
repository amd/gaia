// Package gaiaslack asks the `gaia` CLI about the Slack bridge and records the
// user's answer to the setup offer.
//
// Everything here shells out rather than re-deriving anything in Go, for the
// same reason gaiainit does: src/gaia/messaging/slack/ owns what "installed",
// "configured" and "should we offer" mean, and a Go copy of those rules goes
// stale the first time one of them changes. `gaia slack status --json` exists
// precisely so this package can be a thin reader of it.
//
// Nothing is cached. Whether Slack is installed is read fresh on every call —
// the whole point of the offer is to notice a Slack that was not there before.
package gaiaslack

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// StatusTimeout bounds the read-only probe. It spawns a Python interpreter and
// walks the installed-application inventory, so a couple of seconds is normal;
// this only stops a wedged call from hanging the UI forever.
const StatusTimeout = 30 * time.Second

// ErrUnanswered wraps every failure to ASK the question, as opposed to a clean
// answer of "not set up". Callers must not render it as "Slack is not
// connected": against a gaia too old to know the subcommand, argparse exits 2,
// and treating that as a clean negative would offer setup on a machine that
// cannot run it.
var ErrUnanswered = errors.New("Slack readiness could not be determined")

// Binary resolves the `gaia` CLI on PATH. A package var, not a bare
// exec.LookPath, so tests can substitute a stub — same injection point as
// gaiainit.Binary.
var Binary = func() (string, error) {
	bin, err := exec.LookPath("gaia")
	if err != nil {
		return "", fmt.Errorf(
			"the `gaia` CLI is not on PATH, so Slack setup cannot run. " +
				"Install GAIA with `curl -fsSL https://amd-gaia.ai/install.sh | sh` " +
				"(on Windows: `irm https://amd-gaia.ai/install.ps1 | iex`), or " +
				"`pip install amd-gaia` into the Python environment on your PATH")
	}
	return bin, nil
}

// Status is `gaia slack status --json`, one field per question a screen asks.
type Status struct {
	// SlackInstalled is whether the Slack desktop app is on this machine.
	SlackInstalled bool `json:"slack_installed"`
	// Configured is whether tokens are stored and the bridge could start.
	Configured bool `json:"configured"`
	// OnboardingState is one of unset / connected / skipped / never.
	OnboardingState string `json:"onboarding_state"`
	// TeamName names the workspace the stored tokens belong to.
	TeamName string `json:"team_name"`
	// ShouldOfferSetup is the ONLY field a caller should gate the offer on. The
	// rule behind it — offer once, then again only if Slack appears on a machine
	// that lacked it — lives in Python and must not be re-implemented here.
	ShouldOfferSetup bool `json:"should_offer_setup"`
	// Running is whether a backgrounded bridge is alive.
	Running bool `json:"running"`
}

// Summary is the one-line form for a status row.
func (s Status) Summary() string {
	switch {
	case s.Running:
		return "connected and running" + s.workspace()
	case s.Configured:
		return "connected" + s.workspace() + " — not running"
	case s.SlackInstalled:
		return "Slack is installed here, but GAIA is not connected to it"
	default:
		return "no Slack app found on this machine"
	}
}

func (s Status) workspace() string {
	if s.TeamName == "" {
		return ""
	}
	return " to " + s.TeamName
}

// Query asks the CLI for the current Slack status.
func Query(ctx context.Context) (Status, error) {
	bin, err := Binary()
	if err != nil {
		return Status{}, fmt.Errorf("%w: %w", ErrUnanswered, err)
	}
	ctx, cancel := context.WithTimeout(ctx, StatusTimeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, bin, "slack", "status", "--json")
	var out, errBuf bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &errBuf
	if runErr := cmd.Run(); runErr != nil {
		return Status{}, fmt.Errorf("%w (%w). GAIA said: %s",
			ErrUnanswered, runErr, lastMeaningfulLine(errBuf.String()+out.String()))
	}

	// The CLI's logger writes to stderr, but a stray banner on stdout would
	// still break a naive unmarshal — so decode from the first '{' rather than
	// from byte zero.
	raw := out.Bytes()
	if i := bytes.IndexByte(raw, '{'); i > 0 {
		raw = raw[i:]
	}
	var status Status
	if err := json.Unmarshal(raw, &status); err != nil {
		return Status{}, fmt.Errorf("%w: `gaia slack status --json` returned "+
			"something that is not JSON (%w): %s",
			ErrUnanswered, err, lastMeaningfulLine(out.String()))
	}
	return status, nil
}

// Decline records that the user does not want Slack set up. `never` is the
// difference between "not now" — reconsidered once if Slack appears later — and
// "stop asking", which is honoured forever.
func Decline(ctx context.Context, never bool) error {
	bin, err := Binary()
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, StatusTimeout)
	defer cancel()

	args := []string{"slack", "decline"}
	if never {
		args = append(args, "--never")
	}
	cmd := exec.CommandContext(ctx, bin, args...)
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	if runErr := cmd.Run(); runErr != nil {
		return fmt.Errorf("could not record the Slack decision (%w). GAIA said: %s",
			runErr, lastMeaningfulLine(out.String()))
	}
	return nil
}

// SetupCommand is the command that runs setup, for a caller that suspends the
// TUI and hands the terminal over. Setup is interactive — it opens a browser
// and reads two pasted tokens — so it cannot run as a captured child.
func SetupCommand() (string, []string, error) {
	bin, err := Binary()
	if err != nil {
		return "", nil, err
	}
	return bin, []string{"slack", "setup"}, nil
}

// TypedCommand is what a user should type to do this themselves.
const TypedCommand = "gaia slack setup"

// lastMeaningfulLine returns the last non-blank line, for quoting a failure.
// The whole buffer would bury the reason under a traceback.
func lastMeaningfulLine(s string) string {
	lines := strings.Split(strings.TrimSpace(s), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		if line := strings.TrimSpace(lines[i]); line != "" {
			return line
		}
	}
	return "(no output)"
}
