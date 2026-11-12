package client

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
)

func TestAListedSessionPassesItsHistoryFileAndKindToTheChild(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	agent := catalog.Agent{ID: catalog.FlagshipID, BinaryPath: self, BinaryArgs: []string{"--json-events"}, CanonicalEvents: true}
	c, err := ForAgent(agent, ForAgentOptions{HistoryFile: "/h/abc.sqlite3", SessionKind: "chat"})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	if got := strings.Join(c.(*SubprocessClient).args, " "); got != "--json-events --history-file /h/abc.sqlite3 --session-kind chat" {
		t.Fatalf("args = %q", got)
	}
	if strings.Join(agent.BinaryArgs, " ") != "--json-events" {
		t.Fatalf("the catalog entry was mutated: %q", agent.BinaryArgs)
	}
}

func TestOnlyTheSpawnedFlagshipCanKeepAListedSession(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	opts := ForAgentOptions{HistoryFile: "/h/abc.sqlite3", SessionKind: "chat"}
	for _, agent := range []catalog.Agent{
		{ID: "email", Transport: catalog.TransportDaemon},
		{ID: catalog.FlagshipID, Transport: catalog.TransportDaemon},
		{ID: "custom", BinaryPath: self},
	} {
		if _, err := ForAgent(agent, opts); err == nil || !strings.Contains(err.Error(), "cannot keep a resumable session") {
			t.Errorf("%s over %v: err = %v", agent.ID, agent.Transport, err)
		}
	}
}

func TestAKindWithoutAHistoryFileIsRefused(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	agent := catalog.Agent{ID: catalog.FlagshipID, BinaryPath: self, CanonicalEvents: true}
	if _, err := ForAgent(agent, ForAgentOptions{SessionKind: "chat"}); err == nil {
		t.Error("a kind with no history file was accepted")
	}
}

// A pinned transcript (GAIA_HISTORY_FILE) is kept but not listed.
func TestAHistoryFileWithoutAKindIsKeptUnlisted(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	agent := catalog.Agent{ID: catalog.FlagshipID, BinaryPath: self, CanonicalEvents: true}
	c, err := ForAgent(agent, ForAgentOptions{HistoryFile: "/h/mine.sqlite3"})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	if got := strings.Join(c.(*SubprocessClient).args, " "); got != "--history-file /h/mine.sqlite3" {
		t.Fatalf("args = %q", got)
	}
}

func TestSessionFlagsMatchTheWireContract(t *testing.T) {
	path := filepath.Join("..", "..", "..", "tests", "fixtures", "stdio", "gaia_stdio_wire.json")
	raw, err := os.ReadFile(path) // #nosec G304 -- fixed relative path to a checked-in fixture
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		SessionIndex struct {
			Argv []string `json:"argv"`
		} `json:"session_index"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(fixture.SessionIndex.Argv, " "); got != HistoryFileFlag+" "+SessionKindFlag {
		t.Fatalf("fixture argv %q; this build sends %s %s", got, HistoryFileFlag, SessionKindFlag)
	}
}
