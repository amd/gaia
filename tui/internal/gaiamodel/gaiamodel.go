// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package gaiamodel adds and removes local models from a Hugging Face link by
// asking the `gaia` CLI (`gaia model check|add|remove --json`).
//
// It shells out rather than checking links in Go for the reason gaiaslack
// does: gaia.llm.custom_models owns what "valid and supported" means — the
// Hugging Face reads, the llama.cpp architecture table, the fit rule — and the
// Agent UI uses the same code, so a Go copy would drift from both.
package gaiamodel

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os/exec"
	"regexp"
	"strings"
	"time"
)

// Timeout bounds one call. A check reads Hugging Face and, once per llama.cpp
// release, GitHub; removing a model asks Lemonade to delete its files.
const Timeout = 90 * time.Second

// Binary resolves the `gaia` CLI on PATH; tests substitute a stub.
var Binary = func() (string, error) {
	bin, err := exec.LookPath("gaia")
	if err != nil {
		return "", fmt.Errorf(
			"the `gaia` CLI is not on PATH, so models can't be added from a link. " +
				"Install GAIA with `curl -fsSL https://amd-gaia.ai/install.sh | sh` " +
				"(on Windows: `irm https://amd-gaia.ai/install.ps1 | iex`)")
	}
	return bin, nil
}

// Check is one check run on a link.
type Check struct {
	ID     string `json:"id"`
	Label  string `json:"label"`
	OK     bool   `json:"ok"`
	Detail string `json:"detail"`
}

// Variant is one quantization the repo offers.
type Variant struct {
	Quant string `json:"quant"`
	// File is the variant's first file, unique where quant names repeat.
	File   string  `json:"file"`
	SizeGB float64 `json:"size_gb"`
	Fits   bool    `json:"fits"`
}

// Report is what `gaia model check` found. OK says whether it can be added;
// Error is the first failed check.
type Report struct {
	OK          bool      `json:"ok"`
	URL         string    `json:"url"`
	Checks      []Check   `json:"checks"`
	Error       string    `json:"error"`
	Warnings    []string  `json:"warnings"`
	Variants    []Variant `json:"variants"`
	ModelID     string    `json:"model_id"`
	DisplayName string    `json:"display_name"`
	Quant       string    `json:"quant"`
	File        string    `json:"file"`
	SizeGB      float64   `json:"size_gb"`
}

// Removed is what `gaia model remove` did.
type Removed struct {
	ID          string   `json:"removed"`
	DisplayName string   `json:"display_name"`
	Cleared     []string `json:"cleared"`
}

// CheckLink checks url without saving or downloading anything. quant "" lets
// GAIA pick one that fits. A refused link is a Report with OK false, not an
// error; an error means the question could not be asked.
func CheckLink(ctx context.Context, link, quant string) (Report, error) {
	args, err := linkArgs("check", link, quant)
	if err != nil {
		return Report{}, err
	}
	var r Report
	err = run(ctx, &r, args...)
	return r, err
}

// Add checks url and, when it passes, adds the model to the shared list.
func Add(ctx context.Context, link, quant string) (Report, error) {
	args, err := linkArgs("add", link, quant)
	if err != nil {
		return Report{}, err
	}
	var reply struct {
		Report
		Nested *Report `json:"report"`
	}
	if err := run(ctx, &reply, args...); err != nil {
		return Report{}, err
	}
	if reply.Nested != nil {
		return *reply.Nested, nil
	}
	return reply.Report, nil
}

// Remove removes a model the user added, and its download.
func Remove(ctx context.Context, id string) (Removed, error) {
	if !safeArg.MatchString(id) {
		return Removed{}, fmt.Errorf("%q is not a model id", id)
	}
	var r Removed
	err := run(ctx, &r, "model", "remove", "--json", "--", id)
	return r, err
}

// safeArg is what may reach the CLI from a paste: URL path characters only,
// so no shell or `gaia` wrapper script can read anything else into it.
var safeArg = regexp.MustCompile(`^[A-Za-z0-9._~:/%+@-]+$`)

// linkArgs passes the link as scheme, host and path. The query and fragment
// say nothing about the model, and a `&` in them is a command separator to
// cmd.exe if `gaia` resolves to a .cmd wrapper.
func linkArgs(action, link, quant string) ([]string, error) {
	clean, err := cleanLink(link)
	if err != nil {
		return nil, err
	}
	args := []string{"model", action, "--json"}
	if quant != "" {
		if !safeArg.MatchString(quant) {
			return nil, fmt.Errorf("%q is not a quantization name", quant)
		}
		args = append(args, "--quant", quant)
	}
	return append(args, "--", clean), nil
}

func cleanLink(link string) (string, error) {
	link = strings.TrimSpace(link)
	if strings.Contains(link, "://") {
		u, err := url.Parse(link)
		if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" {
			return "", fmt.Errorf("%q is not a web link — paste a Hugging Face model link", link)
		}
		link = u.Scheme + "://" + u.Host + u.EscapedPath()
	} else if i := strings.IndexAny(link, "?#"); i >= 0 {
		link = link[:i]
	}
	if link == "" || strings.HasPrefix(link, "-") || !safeArg.MatchString(link) {
		return "", fmt.Errorf("%q is not a Hugging Face link GAIA can check — paste the model page's address", link)
	}
	return link, nil
}

// refusal is exit code 1 with an {"ok": false, "error"} body that is not a report.
type refusal struct {
	OK     *bool  `json:"ok"`
	Error  string `json:"error"`
	Checks []any  `json:"checks"`
}

func run(ctx context.Context, out any, args ...string) error {
	bin, err := Binary()
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, Timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, bin, args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	runErr := cmd.Run()

	// Logging goes to stderr, but decode from the first '{' in case a banner
	// lands on stdout.
	raw := stdout.Bytes()
	if i := bytes.IndexByte(raw, '{'); i >= 0 {
		raw = raw[i:]
	} else {
		raw = nil
	}
	var exitErr *exec.ExitError
	refused := errors.As(runErr, &exitErr) && exitErr.ExitCode() == 1
	if runErr != nil && !refused {
		return fmt.Errorf("`gaia %s` failed (%w): %s", strings.Join(args[:2], " "), runErr,
			lastLine(stderr.String()+stdout.String()))
	}
	if len(raw) == 0 {
		return fmt.Errorf("`gaia %s` printed no JSON: %s", strings.Join(args[:2], " "),
			lastLine(stderr.String()+stdout.String()))
	}
	if refused {
		var r refusal
		if err := json.Unmarshal(raw, &r); err == nil && r.Checks == nil && r.Error != "" {
			// Refused before any check ran: nothing to show but the reason.
			return errors.New(r.Error)
		}
	}
	if err := json.Unmarshal(raw, out); err != nil {
		return fmt.Errorf("`gaia %s` returned something that is not its JSON (%w): %s",
			strings.Join(args[:2], " "), err, lastLine(stdout.String()))
	}
	return nil
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
