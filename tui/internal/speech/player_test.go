// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package speech

import (
	"errors"
	"reflect"
	"strings"
	"testing"
)

func lookPathOf(have ...string) func(string) (string, error) {
	return func(name string) (string, error) {
		for _, h := range have {
			if h == name {
				return "/bin/" + name, nil
			}
		}
		return "", errors.New("not found")
	}
}

func TestWindowsPlaysThroughPowerShellWithThePathQuoted(t *testing.T) {
	p, err := findPlayer("windows", lookPathOf("powershell"))
	if err != nil {
		t.Fatal(err)
	}
	got := p.Argv(`C:\Users\O'Brien\AppData\Local\Temp\gaia-read-aloud-1.wav`)
	want := []string{"/bin/powershell", "-NoProfile", "-NonInteractive", "-Command",
		`(New-Object Media.SoundPlayer 'C:\Users\O''Brien\AppData\Local\Temp\gaia-read-aloud-1.wav').PlaySync()`}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q\nwant %q", got, want)
	}
}

func TestMacPlaysWithAfplay(t *testing.T) {
	p, err := findPlayer("darwin", lookPathOf("afplay"))
	if err != nil {
		t.Fatal(err)
	}
	if got := p.Argv("/tmp/a.wav"); !reflect.DeepEqual(got, []string{"/bin/afplay", "/tmp/a.wav"}) {
		t.Fatalf("got %q", got)
	}
}

func TestLinuxTakesTheFirstPlayerOnPath(t *testing.T) {
	for _, c := range []struct {
		have []string
		want string
	}{
		{[]string{"aplay", "paplay", "pw-play"}, "pw-play"},
		{[]string{"aplay", "paplay"}, "paplay"},
		{[]string{"aplay"}, "aplay"},
	} {
		p, err := findPlayer("linux", lookPathOf(c.have...))
		if err != nil {
			t.Fatal(err)
		}
		if p.Name() != c.want {
			t.Errorf("with %v got %s, want %s", c.have, p.Name(), c.want)
		}
	}
}

func TestLinuxWithoutAPlayerSaysWhatIsNeeded(t *testing.T) {
	_, err := findPlayer("linux", lookPathOf())
	if !errors.Is(err, ErrNoPlayer) {
		t.Fatalf("got %v", err)
	}
	for _, want := range []string{"PipeWire", "PulseAudio", "ALSA"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error does not name %s: %v", want, err)
		}
	}
	for _, cmd := range []string{"apt", "dnf", "pacman", "install"} {
		if strings.Contains(err.Error(), cmd) {
			t.Errorf("error tells the user to run a command (%s): %v", cmd, err)
		}
	}
}
