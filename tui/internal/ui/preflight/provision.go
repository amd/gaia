package preflight

import (
	"bufio"
	"context"
	"fmt"
	"net/http"
	"strings"
)

// maxProvisionLines bounds the retained progress transcript.
const maxProvisionLines = 200

// ProvisionResult is the outcome of a model pull.
type ProvisionResult struct {
	// OK is the authoritative outcome. See Provision for why it is read from
	// the final line and not from the HTTP status.
	OK bool
	// Final is the last non-empty progress line — the one that carries ✓ or ✗.
	Final string
	// Lines is the last maxProvisionLines progress lines, in order, for the
	// details pane.
	Lines []string
	// Diagnosis is set when the pull failed, so the caller has a remedy rather
	// than a transcript.
	Diagnosis Diagnosis
}

// Provision runs POST /v1/<agent>/init and streams the sidecar's progress lines
// to onLine as they arrive.
//
// The outcome is read from the FINAL line, not from the status code: once the
// sidecar has committed a streamed 200 the status can no longer change, so a
// pull that fails half-way still arrives as "200 OK" with a ✗ line. A 503 before
// any streaming (Lemonade unreachable) is a real status and is handled as one.
//
// onLine may be nil. It is called from the calling goroutine.
func Provision(ctx context.Context, t Transport, cfg Config, onLine func(string)) ProvisionResult {
	cfg = cfg.withDefaults()
	l := Ladder{AgentID: cfg.AgentID}

	stream, err := t.Stream(ctx, http.MethodPost, "/v1/"+cfg.AgentID+"/init", nil)
	if err != nil {
		return ProvisionResult{
			Diagnosis: l.Error("download the AI model", err),
			Final:     "✗ the download could not be started",
		}
	}
	defer stream.Body.Close()

	res := ProvisionResult{}
	scanner := bufio.NewScanner(stream.Body)
	// Progress lines are short; the cap only guards against a non-line-oriented
	// body being read into memory without bound.
	scanner.Buffer(make([]byte, 0, 64*1024), 1<<20)
	for scanner.Scan() {
		line := strings.TrimRight(scanner.Text(), "\r")
		if strings.TrimSpace(line) == "" {
			continue
		}
		// Keep a bounded tail: the final line is what decides the outcome, and a
		// pull that narrates for an hour must not grow this without bound.
		res.Lines = append(res.Lines, line)
		if len(res.Lines) > maxProvisionLines {
			res.Lines = res.Lines[len(res.Lines)-maxProvisionLines:]
		}
		res.Final = line
		if onLine != nil {
			onLine(line)
		}
	}
	if err := scanner.Err(); err != nil {
		d := l.Error("download the AI model", err)
		res.Diagnosis = d
		res.Final = "✗ the download stopped early: " + err.Error()
		return res
	}

	// A pre-stream failure (the sidecar refusing before it commits a 200) is the
	// one case where the status is the truth.
	if stream.Status != http.StatusOK {
		res.Diagnosis = l.Status("download the AI model", stream.Status, strings.Join(res.Lines, "\n"))
		if res.Final == "" {
			res.Final = fmt.Sprintf("✗ the agent refused the download (HTTP %d)", stream.Status)
		}
		return res
	}

	switch {
	case strings.HasPrefix(res.Final, "✓"):
		res.OK = true
	case res.Final == "":
		res.Diagnosis = Diagnosis{
			Cause:   "The download ended without saying whether it worked.",
			Remedy:  "Press r to re-check whether the model landed, then retry.",
			Command: "gaia init",
			Where:   fmt.Sprintf("~/.gaia/agents/%s/logs/", cfg.AgentID),
		}
		res.Final = "✗ the download ended with no result"
	default:
		// ✗ or ⚠ — run the failing line through the ladder so the user gets a
		// remedy, not just the sidecar's narration.
		res.Diagnosis = l.Text("download the AI model", res.Final)
	}
	return res
}
