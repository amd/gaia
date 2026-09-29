package gaiainit

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// VerifyTimeout bounds `gaia init --check --load`: the presence check's budget
// plus a cold load of the chat model, which on a slow disk takes a minute or two.
const VerifyTimeout = CheckTimeout + 150*time.Second

// loadFailedExitCode is `gaia init --check --load`'s answer for "everything is
// downloaded, but a model would not load" — a state re-running setup cannot fix.
const loadFailedExitCode = 3

// Stages of a not-ready Status.
const (
	// StageSetup means `gaia init` still has work to do.
	StageSetup = "setup"
	// StageServer means the model server is installed but will not answer.
	StageServer = "server"
	// StageLoad means everything is downloaded but a model will not load.
	StageLoad = "load"
)

// Model is one model the verify probe described or loaded.
type Model struct {
	ID     string   `json:"id"`
	Role   string   `json:"role"` // "chat" or "embedding"
	SizeGB *float64 `json:"size_gb"`
	Loaded bool     `json:"loaded"`
	Error  string   `json:"error"`
}

// Status is the answer to "is this profile set up, and does it load?".
type Status struct {
	Ready   bool     `json:"ready"`
	Stage   string   `json:"stage"`
	Reasons []string `json:"reasons"`
	Models  []Model  `json:"models"`
}

// Chat returns the chat model, if the probe named one.
func (s Status) Chat() (Model, bool) { return s.role("chat") }

// Embedder returns the embedding model, if the probe named one.
func (s Status) Embedder() (Model, bool) { return s.role("embedding") }

func (s Status) role(role string) (Model, bool) {
	for _, m := range s.Models {
		if m.Role == role {
			return m, true
		}
	}
	return Model{}, false
}

// VerifyArgs builds `gaia init --check --load --json` for the flagship profile.
// chatModel names a local chat model other than the profile default; it is
// ignored in claudeMode, where no local chat model is checked at all.
func VerifyArgs(claudeMode bool, chatModel string) []string {
	args := append(CheckArgs(claudeMode), "--load", "--json")
	if !claudeMode && chatModel != "" {
		args = append(args, "--chat-model", chatModel)
	}
	return args
}

// Verify asks whether the flagship profile is set up AND whether its models
// load. "Downloaded" is not "works": an embedder llama-server cannot start
// passes the presence check and fails on the first chat turn.
//
// A stopped GAIA Lemonade Server is started on the way, as Check does.
func Verify(ctx context.Context, claudeMode bool, chatModel string) (Status, error) {
	bin, err := Binary()
	if err != nil {
		return Status{}, fmt.Errorf("%w: %w", ErrUnanswered, err)
	}
	ctx, cancel := context.WithTimeout(ctx, VerifyTimeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, bin, VerifyArgs(claudeMode, chatModel)...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	runErr := cmd.Run()
	code := 0
	if runErr != nil {
		var exitErr *exec.ExitError
		if !errors.As(runErr, &exitErr) {
			return Status{}, fmt.Errorf("%w (%w)", ErrUnanswered, runErr)
		}
		code = exitErr.ExitCode()
	}
	if code != 0 && code != notReadyExitCode && code != loadFailedExitCode {
		return Status{}, fmt.Errorf("%w (exit %d). GAIA said: %s", ErrUnanswered, code,
			LastMeaningfulLine(stderr.String()+"\n"+stdout.String()))
	}
	status, err := parseStatus(stdout.String())
	if err != nil {
		return Status{}, fmt.Errorf("%w: %w", ErrUnanswered, err)
	}
	return status, nil
}

// parseStatus reads the JSON object off the LAST line that holds one: GAIA's
// logger shares stdout, so warnings can precede it.
func parseStatus(out string) (Status, error) {
	lines := strings.Split(out, "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if !strings.HasPrefix(line, "{") {
			continue
		}
		var s Status
		if err := json.Unmarshal([]byte(line), &s); err != nil {
			return Status{}, fmt.Errorf("`gaia init --check --json` printed unreadable JSON: %w", err)
		}
		if !s.Ready && s.Stage == "" {
			s.Stage = StageSetup
		}
		return s, nil
	}
	return Status{}, fmt.Errorf("`gaia init --check --json` printed no result: %s",
		LastMeaningfulLine(out))
}

// --- progress ---------------------------------------------------------------

// Phase is the part of setup a line of `gaia init` output belongs to.
type Phase int

const (
	PhaseNone Phase = iota
	// PhaseServer installs and starts the local model server.
	PhaseServer
	// PhaseModels downloads the models.
	PhaseModels
	// PhaseFinish installs Python extras and the agent, then verifies.
	PhaseFinish
)

// Progress is what one line of `gaia init` output means to a person.
type Progress struct {
	Phase Phase
	// Text is a short human description, e.g. "Downloading Gemma-4-E4B-it-GGUF".
	// Empty when the line only moves Percent.
	Text string
	// Percent is a download's completion, or -1 when the line carries none.
	Percent int
	// Failed marks a line that reports an error.
	Failed bool
}

var (
	stepRe      = regexp.MustCompile(`^Step \d+/\d+:\s*(.+?)\.*$`)
	downloadRe  = regexp.MustCompile(`^Downloading:\s*(\S+)`)
	fetchRe     = regexp.MustCompile(`^Downloading Lemonade Server v(\S+?)\.*$`)
	percentRe   = regexp.MustCompile(`\]\s*(\d{1,3})%`)
	logPrefixRe = regexp.MustCompile(`^\[\d{4}-\d\d-\d\d [\d:]+\]\s*\|`)
)

// Describe turns one line of `gaia init` output into what it means for the
// person waiting, or reports false for a line that means nothing to them —
// Python log records, blank rules, the setup banner. Those are kept for the
// details view, never shown as progress.
func Describe(line string) (Progress, bool) {
	line = strings.TrimSpace(line)
	p := Progress{Percent: -1}
	if line == "" || logPrefixRe.MatchString(line) {
		return p, false
	}
	if m := percentRe.FindStringSubmatch(line); m != nil {
		n, _ := strconv.Atoi(m[1])
		p.Percent = n
		return p, true
	}
	if strings.HasPrefix(line, "❌") {
		p.Failed = true
		p.Text = strings.TrimSpace(strings.TrimPrefix(line, "❌"))
		return p, true
	}
	if m := stepRe.FindStringSubmatch(line); m != nil {
		switch step := strings.ToLower(m[1]); {
		case strings.Contains(step, "lemonade"):
			p.Phase, p.Text = PhaseServer, "Starting the local model server"
		case strings.Contains(step, "downloading models"):
			p.Phase, p.Text = PhaseModels, "Downloading the models"
		case strings.Contains(step, "python dependencies"):
			p.Phase, p.Text = PhaseFinish, "Installing Python packages"
		case strings.Contains(step, "agent installation"):
			p.Phase, p.Text = PhaseFinish, "Checking the GAIA agent"
		case strings.Contains(step, "verifying"):
			p.Phase, p.Text = PhaseFinish, "Checking that the models load"
		default:
			p.Phase, p.Text = PhaseFinish, m[1]
		}
		return p, true
	}
	if m := fetchRe.FindStringSubmatch(line); m != nil {
		p.Phase, p.Text = PhaseServer, "Downloading the local model server"
		return p, true
	}
	if m := downloadRe.FindStringSubmatch(line); m != nil {
		p.Phase, p.Text = PhaseModels, "Downloading "+m[1]
		return p, true
	}
	return p, false
}

// ProfileSize is what Profile downloads, for a step that has to name it before
// the model server can be asked. Mirrors INIT_PROFILES["gaia"]["approx_size"].
const ProfileSize = "~6 GB"
