// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package gaiadiscord

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"testing"
)

// stubGaia installs a fake `gaia` that prints stdout, exits with code, and
// records its argv and stdin next to itself. A .cmd on Windows and a shell
// script elsewhere, so the tests run on every platform.
func stubGaia(t *testing.T, stdout string, code int) (argsFile, stdinFile string) {
	t.Helper()
	dir := t.TempDir()
	out := filepath.Join(dir, "out.txt")
	argsFile = filepath.Join(dir, "args.txt")
	stdinFile = filepath.Join(dir, "stdin.txt")
	if err := os.WriteFile(out, []byte(stdout), 0o644); err != nil {
		t.Fatal(err)
	}
	var path, script string
	if runtime.GOOS == "windows" {
		path = filepath.Join(dir, "gaia.cmd")
		script = "@echo off\r\necho %*> \"" + argsFile + "\"\r\n" +
			"findstr \"^\" > \"" + stdinFile + "\"\r\n" +
			"type \"" + out + "\"\r\nexit /b " + strconv.Itoa(code) + "\r\n"
	} else {
		path = filepath.Join(dir, "gaia")
		script = "#!/bin/sh\necho \"$@\" > '" + argsFile + "'\ncat > '" + stdinFile +
			"'\ncat '" + out + "'\nexit " + strconv.Itoa(code) + "\n"
	}
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	original := Binary
	Binary = func() (string, error) { return path, nil }
	t.Cleanup(func() { Binary = original })
	return argsFile, stdinFile
}

func read(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func TestQueryReadsTheStatus(t *testing.T) {
	stubGaia(t, "log line\n{\n  \"connected\": true,\n  \"paired\": true,\n"+
		"  \"state\": \"connected\",\n  \"bot_name\": \"gaia\",\n  \"owner_name\": \"me\",\n"+
		"  \"invite_url\": \"https://discord.com/oauth2/authorize?client_id=1\"\n}", 0)
	s, err := Query(context.Background())
	if err != nil {
		t.Fatalf("Query: %v", err)
	}
	if !s.Connected || !s.Paired || s.State != StateConnected || s.OwnerName != "me" {
		t.Errorf("fields did not survive: %+v", s)
	}
	if !strings.Contains(s.Summary(), "answering me") {
		t.Errorf("summary %q", s.Summary())
	}
}

func TestAFailedProbeIsNotReportedAsNotConnected(t *testing.T) {
	stubGaia(t, "usage: gaia [-h] ... invalid choice: 'discord'", 2)
	_, err := Query(context.Background())
	if !errors.Is(err, ErrUnanswered) {
		t.Fatalf("an old gaia must read as unanswered, got %v", err)
	}
}

func TestConnectSendsTheTokenOnStdinNeverArgv(t *testing.T) {
	argsFile, stdinFile := stubGaia(t,
		`{"connected": true, "bot_name": "gaia", "invite_url": "https://x", "bot_public": true}`, 0)
	r, err := Connect(context.Background(), "  secret.token.value  ")
	if err != nil {
		t.Fatalf("Connect: %v", err)
	}
	if r.BotName != "gaia" || !r.BotPublic {
		t.Errorf("result %+v", r)
	}
	if strings.Contains(read(t, argsFile), "secret") {
		t.Error("the token must never be passed as an argument")
	}
	if strings.TrimSpace(read(t, stdinFile)) != "secret.token.value" {
		t.Errorf("stdin was %q", read(t, stdinFile))
	}
}

func TestConnectSurfacesDiscordsRefusal(t *testing.T) {
	stubGaia(t, `{"connected": false, "error": "Discord refused that token."}`, 1)
	_, err := Connect(context.Background(), "bad")
	if err == nil || !strings.Contains(err.Error(), "Discord refused that token") {
		t.Fatalf("want the CLI's own reason, got %v", err)
	}
}

func TestStartDetachesAndPassesTheModel(t *testing.T) {
	argsFile, _ := stubGaia(t, `{"connected": true, "state": "pairing", "pairing_code": "ABCD2345"}`, 0)
	s, err := Start(context.Background(), "fireworks.deepseek-v4p1-flash")
	if err != nil {
		t.Fatalf("Start: %v", err)
	}
	if s.PairingCode != "ABCD2345" {
		t.Errorf("status %+v", s)
	}
	args := read(t, argsFile)
	for _, want := range []string{"discord", "start", "--detach", "--model", "fireworks.deepseek-v4p1-flash"} {
		if !strings.Contains(args, want) {
			t.Errorf("args %q lack %q", args, want)
		}
	}
}

func TestTakeOverIsARestartWithTheFlag(t *testing.T) {
	argsFile, _ := stubGaia(t, `{"connected": true, "state": "connected"}`, 0)
	if _, err := Restart(context.Background(), "", true); err != nil {
		t.Fatal(err)
	}
	if args := read(t, argsFile); !strings.Contains(args, "restart") || !strings.Contains(args, "--take-over") {
		t.Errorf("args %q", args)
	}
}
