package root

import (
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/update"
)

func TestTerminalUpdateNoticeNamesBothVersions(t *testing.T) {
	m := FlagshipModel{terminalVersion: "0.25.1"}.noteTerminalUpdate("0.25.2")
	for _, want := range []string{"Terminal UI v0.25.1", "GAIA v0.25.2", update.ReleasesPage} {
		if !strings.Contains(m.launchNotice, want) {
			t.Errorf("notice = %q, want %q", m.launchNotice, want)
		}
	}
}

func TestTerminalUpdateNoticeTreatsFinalAsNewerThanRC(t *testing.T) {
	m := FlagshipModel{terminalVersion: "0.25.1-rc.5"}.noteTerminalUpdate("0.25.1")
	if !strings.Contains(m.launchNotice, "GAIA v0.25.1") {
		t.Fatalf("notice = %q, final release should supersede RC", m.launchNotice)
	}
}

func TestTerminalUpdateNoticeSkipsDevAndOlderCore(t *testing.T) {
	cases := []struct{ name, tui, core string }{
		{"dev", "dev", "0.25.2"},
		{"same", "0.25.2", "0.25.2"},
		{"newer tui", "0.25.3", "0.25.2"},
		{"invalid core", "0.25.2", "nightly"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			m := FlagshipModel{terminalVersion: tc.tui}.noteTerminalUpdate(tc.core)
			if m.launchNotice != "" {
				t.Errorf("notice = %q, want none", m.launchNotice)
			}
		})
	}
}

func TestTerminalUpdateNoticeRunsOnce(t *testing.T) {
	m := FlagshipModel{terminalVersion: "0.25.1"}.noteTerminalUpdate("0.25.2")
	first := m.launchNotice
	m = m.noteTerminalUpdate("0.25.3")
	if m.launchNotice != first {
		t.Errorf("notice changed on second check: %q", m.launchNotice)
	}
}
