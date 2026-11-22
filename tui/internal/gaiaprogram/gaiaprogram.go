// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package gaiaprogram downloads gaiacpp, the native GAIA agent, by asking the
// `gaia` CLI (`gaia agent-program status|download --json`).
//
// It shells out for the reason gaiamodel does: the release lookup, the
// checksum check and the install live in gaia.daemon.sidecars.gaiacpp_fetch,
// which the Agent UI uses too, so a Go copy would drift from both.
package gaiaprogram

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"time"

	"github.com/amd/gaia/tui/internal/gaiainit"
)

// CheckTimeout bounds the status call: two small reads from the release.
const CheckTimeout = 60 * time.Second

// Binary resolves the `gaia` CLI; tests substitute a stub.
var Binary = func() (string, error) {
	bin, err := gaiainit.ResolveCLI()
	if err != nil {
		return "", errors.New(
			"GAIA's own tools are not installed beside this program, so gaiacpp can't be downloaded from here. " +
				"Reinstall GAIA from https://amd-gaia.ai/#install")
	}
	return bin, nil
}

// Status is what this GAIA release offers for this PC.
type Status struct {
	Installed   bool   `json:"installed"`
	Path        string `json:"path"`
	Target      string `json:"target"`
	CanDownload bool   `json:"can_download"`
	SizeBytes   int64  `json:"size_bytes"`
	Version     string `json:"version"`
	// Reason is why it cannot be downloaded, when CanDownload is false.
	Reason string `json:"reason"`
	// Retry says the release could not be reached, so asking again may work.
	Retry bool `json:"retry"`
}

// Progress is bytes downloaded so far; Total is 0 when the size is unknown.
type Progress struct {
	Done  int64
	Total int64
}

// Check asks whether this release has a gaiacpp for this PC. A release without
// one is a Status with CanDownload false, not an error; an error means the
// question could not be asked.
func Check(ctx context.Context) (Status, error) {
	bin, err := Binary()
	if err != nil {
		return Status{}, err
	}
	ctx, cancel := context.WithTimeout(ctx, CheckTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, bin, "agent-program", "status", "--json")
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	runErr := cmd.Run()

	var exitErr *exec.ExitError
	refused := errors.As(runErr, &exitErr) && exitErr.ExitCode() == 1
	if runErr != nil && !refused {
		return Status{}, fmt.Errorf("could not ask GAIA about gaiacpp (%w): %s", runErr,
			lastLine(stderr.String()+stdout.String()))
	}
	raw := stdout.Bytes()
	i := bytes.IndexByte(raw, '{')
	if i < 0 {
		return Status{}, fmt.Errorf("GAIA gave no answer about gaiacpp: %s",
			lastLine(stderr.String()+stdout.String()))
	}
	var st Status
	if err := json.Unmarshal(raw[i:], &st); err != nil {
		return Status{}, fmt.Errorf("GAIA's answer about gaiacpp could not be read (%w): %s", err, lastLine(stdout.String()))
	}
	return st, nil
}

type event struct {
	Event string `json:"event"`
	Done  int64  `json:"bytes_done"`
	Total int64  `json:"bytes_total"`
	Path  string `json:"path"`
	Error string `json:"error"`
}

// Download downloads, verifies and installs this release's gaiacpp, calling
// onProgress as bytes arrive, and returns where it was installed. Cancelling
// ctx stops it; nothing is installed unless the whole file passed its check.
func Download(ctx context.Context, onProgress func(Progress)) (string, error) {
	bin, err := Binary()
	if err != nil {
		return "", err
	}
	cmd := exec.CommandContext(ctx, bin, "agent-program", "download", "--json")
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return "", fmt.Errorf("could not prepare the gaiacpp download: %w", err)
	}
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		return "", fmt.Errorf("could not start the gaiacpp download: %w", err)
	}

	var last event
	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if !strings.HasPrefix(line, "{") {
			continue
		}
		var ev event
		if json.Unmarshal([]byte(line), &ev) != nil {
			continue
		}
		switch ev.Event {
		case "progress":
			if onProgress != nil {
				onProgress(Progress{Done: ev.Done, Total: ev.Total})
			}
		case "done", "failed":
			last = ev
		}
	}
	waitErr := cmd.Wait()
	if ctx.Err() != nil {
		return "", fmt.Errorf("the gaiacpp download was cancelled: %w", ctx.Err())
	}
	switch {
	case last.Event == "failed":
		return "", errors.New(last.Error)
	case last.Event == "done" && waitErr == nil:
		return last.Path, nil
	}
	return "", fmt.Errorf("the gaiacpp download ended without a result (%v): %s", waitErr, lastLine(stderr.String()))
}

func lastLine(s string) string {
	lines := strings.Split(strings.TrimSpace(s), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		if line := strings.TrimSpace(lines[i]); line != "" {
			return line
		}
	}
	return "(no output)"
}

// HumanSize is a byte count for a sentence: "12.3 MB".
func HumanSize(n int64) string {
	const mb = 1 << 20
	if n >= mb {
		return fmt.Sprintf("%.1f MB", float64(n)/mb)
	}
	return fmt.Sprintf("%d KB", (n+1023)/1024)
}
