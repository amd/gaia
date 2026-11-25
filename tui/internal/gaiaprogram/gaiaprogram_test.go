// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package gaiaprogram

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The test binary stands in for `gaia`: run with GAIAPROGRAM_STUB set, it
// records its arguments, prints GAIAPROGRAM_STDOUT and exits with
// GAIAPROGRAM_EXIT.
func TestMain(m *testing.M) {
	if os.Getenv("GAIAPROGRAM_STUB") != "" {
		_ = os.WriteFile(os.Getenv("GAIAPROGRAM_ARGS"), []byte(strings.Join(os.Args[1:], " ")), 0o600)
		fmt.Fprint(os.Stderr, "[INFO] gaia.daemon | a log line\n")
		fmt.Print(os.Getenv("GAIAPROGRAM_STDOUT"))
		code := 0
		fmt.Sscan(os.Getenv("GAIAPROGRAM_EXIT"), &code)
		os.Exit(code)
	}
	os.Exit(m.Run())
}

func stub(t *testing.T, stdout string, exit int) (args func() string) {
	t.Helper()
	argsFile := filepath.Join(t.TempDir(), "args")
	t.Setenv("GAIAPROGRAM_STUB", "1")
	t.Setenv("GAIAPROGRAM_STDOUT", stdout)
	t.Setenv("GAIAPROGRAM_EXIT", fmt.Sprint(exit))
	t.Setenv("GAIAPROGRAM_ARGS", argsFile)
	original := Binary
	Binary = func() (string, error) { return os.Args[0], nil }
	t.Cleanup(func() { Binary = original })
	return func() string {
		raw, err := os.ReadFile(argsFile)
		if err != nil {
			t.Fatalf("the stub never ran: %v", err)
		}
		return string(raw)
	}
}

func TestCheckReadsWhatTheReleaseOffers(t *testing.T) {
	args := stub(t, `{"installed": false, "path": null, "target": "/h/bin/gaiacpp", "can_download": true,
 "size_bytes": 12345678, "version": "0.27.0", "reason": null, "retry": false}`, 0)
	st, err := Check(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if got := args(); got != "agent-program status --json" {
		t.Errorf("asked with %q", got)
	}
	if !st.CanDownload || st.SizeBytes != 12345678 || st.Version != "0.27.0" || st.Target != "/h/bin/gaiacpp" || st.Reason != "" {
		t.Errorf("status = %+v", st)
	}
}

// Exit 1 with a report is a release that has no gaiacpp, not a failed question.
func TestCheckReportsAReleaseWithoutGaiacppAsAStatus(t *testing.T) {
	stub(t, `{"installed": false, "can_download": false, "size_bytes": null, "version": null,
 "reason": "this GAIA release has no gaiacpp to download.", "retry": false}`, 1)
	st, err := Check(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if st.CanDownload || st.Retry || !strings.Contains(st.Reason, "no gaiacpp to download") {
		t.Errorf("status = %+v", st)
	}
}

func TestCheckFailsLoudlyWhenTheCLICannotAnswer(t *testing.T) {
	stub(t, "", 2)
	if _, err := Check(context.Background()); err == nil || !strings.Contains(err.Error(), "a log line") {
		t.Fatalf("err = %v, want the CLI's last line", err)
	}
	stub(t, "not json", 0)
	if _, err := Check(context.Background()); err == nil {
		t.Fatal("output with no JSON was accepted")
	}
}

func TestDownloadReportsProgressAndWhereItInstalled(t *testing.T) {
	args := stub(t, `{"event": "progress", "bytes_done": 0, "bytes_total": 100}
{"event": "progress", "bytes_done": 60, "bytes_total": 100}
{"event": "progress", "bytes_done": 100, "bytes_total": 100}
{"event": "done", "path": "/h/bin/gaiacpp"}
`, 0)
	var seen []Progress
	path, err := Download(context.Background(), func(p Progress) { seen = append(seen, p) })
	if err != nil {
		t.Fatal(err)
	}
	if got := args(); got != "agent-program download --json" {
		t.Errorf("asked with %q", got)
	}
	if path != "/h/bin/gaiacpp" || len(seen) != 3 || seen[1] != (Progress{Done: 60, Total: 100}) {
		t.Errorf("path = %q, progress = %+v", path, seen)
	}
}

func TestDownloadReturnsTheReasonAFailedDownloadGives(t *testing.T) {
	stub(t, `{"event": "progress", "bytes_done": 10, "bytes_total": null}
{"event": "failed", "error": "SHA-256 mismatch. Refusing to install it."}
`, 1)
	_, err := Download(context.Background(), nil)
	if err == nil || err.Error() != "SHA-256 mismatch. Refusing to install it." {
		t.Fatalf("err = %v", err)
	}
}

// A CLI that dies without a result must not read as an install.
func TestDownloadWithoutAResultIsAnError(t *testing.T) {
	stub(t, `{"event": "progress", "bytes_done": 10, "bytes_total": 100}`+"\n", 3)
	if _, err := Download(context.Background(), nil); err == nil || !strings.Contains(err.Error(), "without a result") {
		t.Fatalf("err = %v", err)
	}
	stub(t, `{"event": "done", "path": "/h/bin/gaiacpp"}`+"\n", 3)
	if _, err := Download(context.Background(), nil); err == nil {
		t.Fatal("a done line from a CLI that exited non-zero was trusted")
	}
}

func TestHumanSize(t *testing.T) {
	for n, want := range map[int64]string{12 << 20: "12.0 MB", 1536: "2 KB", 1 << 20: "1.0 MB"} {
		if got := HumanSize(n); got != want {
			t.Errorf("HumanSize(%d) = %q, want %q", n, got, want)
		}
	}
}
