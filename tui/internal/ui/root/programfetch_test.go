// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package root

import (
	"context"
	"errors"
	"os"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/gaiaprogram"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

var offered = gaiaprogram.Status{CanDownload: true, SizeBytes: 12 << 20, Version: "0.27.0", Target: "/home/u/.gaia/bin/gaiacpp"}

// offerSession is a Python session that asked for a gaiacpp this PC lacks and
// was offered the download.
func offerSession(t *testing.T) (FlagshipModel, programFixture, *nullClient) {
	t.Helper()
	f := installPrograms(t, catalog.PythonAgentBinary)
	m, c := programSession(t, f, preflight.FlagshipAgentPython)
	m.checkProgram = func(context.Context) (gaiaprogram.Status, error) { return offered, nil }
	updated, cmd := m.agentProgram("gaiacpp")
	m = updated.(FlagshipModel)
	if cmd == nil {
		t.Fatal("a missing gaiacpp did not ask the release for one")
	}
	updated, _ = m.Update(cmd())
	return updated.(FlagshipModel), f, c
}

func pressKey(t *testing.T, m FlagshipModel, key tea.KeyType) (FlagshipModel, tea.Cmd) {
	t.Helper()
	updated, cmd := m.Update(tea.KeyMsg{Type: key})
	return updated.(FlagshipModel), cmd
}

func transcript(m FlagshipModel) string {
	var texts []string
	for _, msg := range m.chat.Messages() {
		texts = append(texts, msg.Content)
	}
	return strings.Join(texts, "\n")
}

func TestMissingGaiacppIsOfferedAsADownload(t *testing.T) {
	m, f, c := offerSession(t)
	if m.programFetch == nil || m.programFetch.stage != fetchOffered {
		t.Fatalf("no offer is pending: %+v", m.programFetch)
	}
	got := transcript(m)
	for _, want := range []string{"gaiacpp (Beta) is not on this PC yet", "gaiacpp 0.27.0 (Beta)", "12.0 MB",
		"checksums", "Press Enter to download it and switch, or Esc to cancel"} {
		if !strings.Contains(got, want) {
			t.Errorf("the offer does not say %q:\n%s", want, got)
		}
	}
	if strings.Contains(got, "Cannot switch") || strings.Contains(got, "install it in one of those places") {
		t.Errorf("the offer still reads as a dead end:\n%s", got)
	}
	if c.closed || m.agent.Native || f.saved(t) != "" {
		t.Fatal("an offer changed the session or the saved choice")
	}
}

func TestEscDeclinesTheOfferAndChangesNothing(t *testing.T) {
	m, f, c := offerSession(t)
	m.downloadProgram = func(context.Context, func(gaiaprogram.Progress)) (string, error) {
		t.Fatal("a declined offer downloaded anyway")
		return "", nil
	}
	m, cmd := pressKey(t, m, tea.KeyEsc)
	if cmd != nil || m.programFetch != nil || c.closed || f.saved(t) != "" {
		t.Fatal("Esc did not simply drop the offer")
	}
	if got := lastLine(t, m); !strings.Contains(got, "was not downloaded") || !strings.Contains(got, "still on gaiapy") {
		t.Errorf("declining is not confirmed: %s", got)
	}
}

// Typing over the offer must not answer it: Enter then sends what was typed.
func TestTheOfferIsNotAnsweredWhileSomethingIsTyped(t *testing.T) {
	m, _, _ := offerSession(t)
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("hello")})
	m = updated.(FlagshipModel)
	if _, _, took := m.programFetchKey(tea.KeyMsg{Type: tea.KeyEnter}); took {
		t.Fatal("Enter with text in the composer answered the download offer")
	}
	if m.programFetch == nil || m.programFetch.stage != fetchOffered {
		t.Fatal("typing dropped the offer")
	}
}

func TestEnterDownloadsWithProgressThenSwitchesToGaiacpp(t *testing.T) {
	m, f, _ := offerSession(t)
	release := make(chan struct{})
	m.downloadProgram = func(_ context.Context, progress func(gaiaprogram.Progress)) (string, error) {
		progress(gaiaprogram.Progress{Done: 6 << 20, Total: 12 << 20})
		<-release
		// The download is what puts gaiacpp on this PC.
		if err := os.WriteFile(f.path(catalog.NativeAgentBinary), []byte("#!/bin/sh\n"), 0o755); err != nil {
			return "", err
		}
		return f.path(catalog.NativeAgentBinary), nil
	}
	m, cmd := pressKey(t, m, tea.KeyEnter)
	if cmd == nil || m.programFetch == nil || m.programFetch.stage != fetchDownloading {
		t.Fatal("Enter did not start the download")
	}
	if got := lastLine(t, m); !strings.Contains(got, "Downloading gaiacpp") {
		t.Fatalf("no progress line: %s", got)
	}

	updated, cmd := m.Update(cmd())
	m = updated.(FlagshipModel)
	if got := lastLine(t, m); !strings.Contains(got, "6.0 MB of 12.0 MB (50%)") {
		t.Fatalf("progress is not shown: %s", got)
	}
	if n := strings.Count(transcript(m), "Downloading gaiacpp"); n != 1 {
		t.Fatalf("progress took %d lines, want one that updates", n)
	}

	close(release)
	updated, _ = m.Update(cmd())
	m = updated.(FlagshipModel)
	if m.programFetch != nil {
		t.Fatal("the finished download is still pending")
	}
	if !strings.Contains(transcript(m)+strings.Join(pendingText(m), "\n"), "checked it against the release's checksum") {
		t.Fatalf("the verified install is not reported:\n%s", transcript(m))
	}
	m = passGate(t, m)
	if !m.agent.Native || f.saved(t) != preflight.FlagshipAgentNative {
		t.Fatalf("the session did not move to gaiacpp: native=%v saved=%q", m.agent.Native, f.saved(t))
	}
	if !strings.Contains(header(t, m), "(gaiacpp · Beta)") {
		t.Fatalf("the header does not mark gaiacpp as Beta:\n%s", header(t, m))
	}
}

func pendingText(m FlagshipModel) []string {
	var out []string
	for _, msg := range m.pendingTranscript {
		out = append(out, msg.Content)
	}
	return out
}

func TestAFailedDownloadSaysWhyAndChangesNothing(t *testing.T) {
	m, f, c := offerSession(t)
	m.downloadProgram = func(context.Context, func(gaiaprogram.Progress)) (string, error) {
		return "", errors.New("SHA-256 mismatch for the download. Refusing to install it")
	}
	m, cmd := pressKey(t, m, tea.KeyEnter)
	updated, _ := m.Update(cmd())
	m = updated.(FlagshipModel)
	got := lastLine(t, m)
	for _, want := range []string{"gaiacpp was not installed", "SHA-256 mismatch", "still on gaiapy"} {
		if !strings.Contains(got, want) {
			t.Errorf("the failure does not say %q: %s", want, got)
		}
	}
	if m.programFetch != nil || c.closed || m.agent.Native || f.saved(t) != "" || m.activeView != viewChat {
		t.Fatal("a failed download changed the session")
	}
}

func TestEscCancelsADownloadInProgress(t *testing.T) {
	m, _, _ := offerSession(t)
	m.downloadProgram = func(ctx context.Context, _ func(gaiaprogram.Progress)) (string, error) {
		<-ctx.Done()
		return "", errors.New("the gaiacpp download was cancelled")
	}
	m, cmd := pressKey(t, m, tea.KeyEnter)
	m, _ = pressKey(t, m, tea.KeyEsc)
	updated, _ := m.Update(cmd())
	m = updated.(FlagshipModel)
	if got := lastLine(t, m); !strings.Contains(got, "cancelled") || m.programFetch != nil {
		t.Fatalf("Esc did not cancel the download: %s", got)
	}
}

func TestAnUnreachableReleaseIsToldApartFromOneWithoutGaiacpp(t *testing.T) {
	f := installPrograms(t, catalog.PythonAgentBinary)
	m, _ := programSession(t, f, preflight.FlagshipAgentPython)
	m.checkProgram = func(context.Context) (gaiaprogram.Status, error) {
		return gaiaprogram.Status{Reason: "could not reach the release: timed out", Retry: true}, nil
	}
	updated, cmd := m.agentProgram("gaiacpp")
	updated, _ = updated.(FlagshipModel).Update(cmd())
	got := lastLine(t, updated.(FlagshipModel))
	if !strings.Contains(got, "could not reach its release") || !strings.Contains(got, "Check your connection") {
		t.Errorf("an unreachable release is not explained: %s", got)
	}
}

// A gaiacpp that failed its check is offered again as a fresh download.
func TestAGaiacppThatFailedItsCheckIsOfferedAgain(t *testing.T) {
	f := installPrograms(t, catalog.PythonAgentBinary)
	m, _ := programSession(t, f, preflight.FlagshipAgentPython)
	m.programs = func(program string) (*catalog.Catalog, string, error) {
		if program == catalog.NativeAgentBinary {
			return nil, "", &catalog.NativeIntegrityError{Binary: "gaiacpp", Record: "r.json", Reason: "it no longer matches what was downloaded"}
		}
		return f.resolve(program)
	}
	m.checkProgram = func(context.Context) (gaiaprogram.Status, error) { return offered, nil }
	updated, cmd := m.agentProgram("gaiacpp")
	if cmd == nil {
		t.Fatal("a gaiacpp that failed its check was not offered again")
	}
	if got := lastLine(t, updated.(FlagshipModel)); !strings.Contains(got, "no longer matches what was downloaded") {
		t.Errorf("the reason is not given: %s", got)
	}
}
