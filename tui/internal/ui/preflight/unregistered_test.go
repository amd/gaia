package preflight

import (
	"context"
	"strings"
	"testing"
)

// The daemon reads its agent list once, at start. An agent it does not list is
// only fixed by restarting it, so both remedies for "not registered" must say
// so — and `gaia init` alone, which installs outside the daemon, must not be
// offered as the whole fix.
func TestAnUnregisteredAgentIsFixedByRestartingTheDaemon(t *testing.T) {
	onlyEmail := `{"agents":[{"agent_id":"email","state":"stopped","mode":null,` +
		`"pid":null,"port":null,"base_url":null,"api_version":null,"agent_version":null,` +
		`"started_at":null,"dev_src_dir":null}]}`

	cases := []struct {
		name        string
		agentID     string
		agents      string
		wantInstall string
	}{
		{"flagship, empty list", "gaia", agentsEmpty, "gaia init"},
		{"flagship, other agents listed", "gaia", onlyEmail, "gaia init"},
		{"email, empty list", "email", agentsEmpty, "gaia hub install email"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := ConfigFor(tc.agentID, "")
			row := sidecarRow(t, newFake().with("GET /daemon/v1/agents", 200, tc.agents), cfg)
			if row.State != StateFailed || row.Line != "not installed" {
				t.Fatalf("row = %s %q, want failed/not installed", row.State.Word(), row.Line)
			}
			assertRestartRemedy(t, row.Remedy, tc.wantInstall)
		})
	}
}

func TestADaemon404NamesTheRestart(t *testing.T) {
	for id, wantInstall := range map[string]string{"gaia": "gaia init", "email": "gaia hub install email"} {
		d := Ladder{AgentID: id}.Status("call the agent", 404,
			`{"detail":"unknown agent '`+id+`'. Registered: chat"}`)
		assertRestartRemedy(t, d.AsRemedy(), wantInstall)
		if !strings.Contains(d.Remedy, "Registered: chat") {
			t.Errorf("agent %q: the daemon's own answer was dropped: %q", id, d.Remedy)
		}
	}
}

// With no agent named there is nothing to install or restart for; listing what
// the daemon knows stays the answer.
func TestA404WithNoAgentStillListsTheRegisteredAgents(t *testing.T) {
	d := Ladder{}.Status("call the agent", 404, "")
	if d.Command != "gaia daemon agents" {
		t.Errorf("command = %q, want gaia daemon agents", d.Command)
	}
	if strings.Contains(d.Remedy, "gaia init") || strings.Contains(d.Remedy, "restart") {
		t.Errorf("a 404 with no agent named prescribes an install or restart: %q", d.Remedy)
	}
}

// A registered agent that is merely stopped is the daemon's to start; telling
// the user to restart the whole service there would kill every other agent.
func TestARegisteredButStoppedAgentIsNotSentToRestartTheDaemon(t *testing.T) {
	row := sidecarRow(t, newFake().with("GET /daemon/v1/agents", 200, agentsStopped), EmailConfig())
	if row.Line != "installed, not started" {
		t.Fatalf("line = %q", row.Line)
	}
	if strings.Contains(row.Remedy.Command, "daemon restart") || strings.Contains(row.Remedy.Action, "restart the background") {
		t.Errorf("a stopped agent was sent to restart the daemon: %+v", row.Remedy)
	}
}

func sidecarRow(t *testing.T, f *fakeTransport, cfg Config) Row {
	t.Helper()
	rep := &Report{}
	checkSidecar(context.Background(), f, cfg, rep)
	row, ok := rep.Find(KeySidecar)
	if !ok {
		t.Fatal("no sidecar row")
	}
	return row
}

func assertRestartRemedy(t *testing.T, r Remedy, wantInstall string) {
	t.Helper()
	if r.Command != "gaia daemon restart" {
		t.Errorf("command = %q, want gaia daemon restart", r.Command)
	}
	if !strings.Contains(r.Action, "`"+wantInstall+"`") {
		t.Errorf("action %q does not name %q", r.Action, wantInstall)
	}
	if strings.Contains(r.Action, "gaia hub install gaia") {
		t.Errorf("the flagship was sent to the hub: %q", r.Action)
	}
	if r.Where == "" {
		t.Error("no log to look at")
	}
}
