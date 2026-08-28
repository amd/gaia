package catalog

import "testing"

func TestNewCatalog(t *testing.T) {
	c := NewCatalog()
	if c == nil {
		t.Fatal("NewCatalog() returned nil")
	}
	if len(c.agents) == 0 {
		t.Fatal("NewCatalog() returned an empty catalog")
	}
}

func TestAll(t *testing.T) {
	c := NewCatalog()
	all := c.All()
	if len(all) != len(c.agents) {
		t.Errorf("All() returned %d agents, want %d", len(all), len(c.agents))
	}
	// A copy, so a caller mutating a row cannot corrupt the catalog.
	all[0].Name = "MUTATED"
	if c.agents[0].Name == "MUTATED" {
		t.Error("All() handed out the catalog's own slice")
	}
}

func TestGetValidID(t *testing.T) {
	agent := NewCatalog().Get(FlagshipID)
	if agent == nil {
		t.Fatalf("Get(%q) returned nil", FlagshipID)
	}
	if agent.ID != FlagshipID {
		t.Errorf("Get returned %q", agent.ID)
	}
}

func TestGetMissingID(t *testing.T) {
	if got := NewCatalog().Get("nonexistent"); got != nil {
		t.Errorf("Get(nonexistent) = %+v, want nil", got)
	}
}

func TestSetStatus(t *testing.T) {
	c := NewCatalog()
	c.SetStatus(FlagshipID, StatusAvailable)
	if got := c.Get(FlagshipID).Status; got != StatusAvailable {
		t.Errorf("status = %s, want available", got)
	}
}

func TestSetStatusNonexistent(t *testing.T) {
	c := NewCatalog()
	before := len(c.All())
	c.SetStatus("nope", StatusAvailable) // must not panic or add a row
	if got := len(c.All()); got != before {
		t.Errorf("SetStatus on an unknown id changed the catalog size to %d", got)
	}
}

func TestIsLaunchable(t *testing.T) {
	if !StatusInstalled.IsLaunchable() {
		t.Error("installed is not launchable")
	}
	if StatusAvailable.IsLaunchable() {
		t.Error("available is launchable; it is not on disk yet")
	}
}

func TestStatusString(t *testing.T) {
	for status, want := range map[AgentStatus]string{
		StatusInstalled: "installed",
		StatusAvailable: "available",
		AgentStatus(99): "unknown",
	} {
		if got := status.String(); got != want {
			t.Errorf("String() = %q, want %q", got, want)
		}
	}
}

// The zero value has to be the stdio path every pre-daemon entry used, or an
// entry that declares no transport would silently be routed through a relay it
// has no sidecar behind.
func TestTransportZeroValueIsSubprocess(t *testing.T) {
	var zero Transport
	if zero != TransportSubprocess {
		t.Errorf("zero Transport = %v, want TransportSubprocess", zero)
	}
	if got := zero.String(); got != "subprocess" {
		t.Errorf("zero Transport.String() = %q", got)
	}
}

// email is an HTTP sidecar the daemon supervises; there is no binary for the
// TUI to spawn. Marking it subprocess would send the launch looking for one.
func TestEmailUsesTheDaemonTransport(t *testing.T) {
	email := NewCatalog().Get("email")
	if email == nil {
		t.Fatal("the seed catalog no longer has an email entry")
	}
	if email.Transport != TransportDaemon {
		t.Errorf("email transport = %v, want daemon", email.Transport)
	}
	if email.BinaryPath != "" {
		t.Errorf("email declares a binary %q; the daemon owns its lifecycle", email.BinaryPath)
	}
}

// --mock stands in for the agent binary, so it must not touch a daemon agent —
// there is nothing to spawn there for it to replace.
func TestSetMockBinarySkipsDaemonTransport(t *testing.T) {
	c := NewCatalog()
	c.SetMockBinary("/tmp/mock-agent")

	if got := c.Get("email").BinaryPath; got != "" {
		t.Errorf("--mock gave the daemon-transport email a binary %q", got)
	}
	if got := c.Get(FlagshipID).BinaryPath; got != "/tmp/mock-agent" {
		t.Errorf("--mock left the flagship pointing at %q", got)
	}
}
