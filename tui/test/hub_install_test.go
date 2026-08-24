package test

import (
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
)

// newHubOnFakeDaemon returns a driver whose catalog has already been merged
// with the fake daemon's, i.e. the state a user sees a moment after opening the
// hub on a machine with the daemon running.
func newHubOnFakeDaemon(t *testing.T) (*driver, *fakeDaemon) {
	t.Helper()
	fake := newFakeDaemon(t)
	d := newDriver(t, fake.client(), 120, 40)
	d.pump(d.m.Init())
	return d, fake
}

func TestCatalogLoadMakesEmailInstallable(t *testing.T) {
	d, _ := newHubOnFakeDaemon(t)

	d.gotoTab("Available")
	visible := d.m.VisibleAgentIDs()
	if len(visible) != 1 || visible[0] != "email" {
		t.Fatalf("Available tab = %v, want exactly [email] once the hub catalog is merged", visible)
	}

	view := plainView(d.m)
	if !strings.Contains(view, "38.0 MB") {
		t.Errorf("hub does not show the download size; view:\n%s", view)
	}
	if !strings.Contains(view, "i install") {
		t.Errorf("footer does not advertise the install key; view:\n%s", view)
	}
}

// Seed agents the hub does not offer must not sit in Available promising an
// install that would fail — they read "not out" instead (design bar: no dead
// ends).
func TestUnofferedSeedAgentsLeaveAvailable(t *testing.T) {
	d, _ := newHubOnFakeDaemon(t)

	for _, id := range []string{"chat", "doc", "file"} {
		agent := d.cat.Get(id)
		if agent == nil {
			t.Fatalf("seed agent %q disappeared from the catalog", id)
		}
		if agent.Status == catalog.StatusAvailable {
			t.Errorf("%q is still listed as Available but the hub cannot install it", id)
		}
		if agent.NotOfferedReason == "" {
			t.Errorf("%q was demoted with no reason for the user to read", id)
		}
	}
}

// The trust gate is the whole point of the 403: a refusal must become a
// question, never a retry.
func TestInstallRefusedWithoutTrustThenSucceedsAfterConfirm(t *testing.T) {
	d, fake := newHubOnFakeDaemon(t)
	d.gotoTab("Available")
	d.selectAgent("email")

	d.send(key("i"))

	if got := fake.installCallCount(); got != 1 {
		t.Fatalf("after pressing i the daemon saw %d install calls, want exactly 1", got)
	}
	if trusted := fake.installBody(0)["trusted"]; trusted != false {
		t.Fatalf("first install call sent trusted=%v, want false — trust is never assumed", trusted)
	}
	if overlay := d.m.Overlay(); overlay != "trust" {
		t.Fatalf("overlay after the 403 = %q, want %q", overlay, "trust")
	}

	// The gate must name what is being trusted, or it is just a yes/no box.
	view := plainView(d.m)
	for _, want := range []string{"email", "0.5.0", "AMD", "experimental", "38.0 MB", "gmail:send"} {
		if !strings.Contains(view, want) {
			t.Errorf("trust gate does not show %q; view:\n%s", want, view)
		}
	}
	if !strings.Contains(view, "has not verified") {
		t.Errorf("trust gate does not say the code is unverified; view:\n%s", view)
	}

	// Declining must not install anything.
	d.send(keyEsc())
	if got := fake.installCallCount(); got != 1 {
		t.Fatalf("declining the trust gate produced %d install calls, want 1 (no retry)", got)
	}
	if d.m.Overlay() != "" {
		t.Fatalf("trust gate still up after esc: %q", d.m.Overlay())
	}

	// Only an explicit yes retries, and only then with the opt-in.
	d.send(key("i"))
	if got := fake.installCallCount(); got != 2 {
		t.Fatalf("second i produced %d total install calls, want 2", got)
	}
	d.send(key("y"))

	if got := fake.installCallCount(); got != 3 {
		t.Fatalf("after confirming, the daemon saw %d install calls, want 3", got)
	}
	if trusted := fake.installBody(2)["trusted"]; trusted != true {
		t.Fatalf("confirmed install sent trusted=%v, want true", trusted)
	}
}

// A queued install must poll to a terminal state and flip the row.
func TestInstallPollsToCompletion(t *testing.T) {
	d, fake := newHubOnFakeDaemon(t)
	d.gotoTab("Available")
	d.selectAgent("email")

	d.send(key("i")) // 403 → trust gate
	d.send(key("y")) // approve → 202 → poll

	if got := fake.installCallCount(); got != 2 {
		t.Fatalf("install calls = %d, want 2", got)
	}
	id, status := d.m.InstallState()
	if id != "email" || status != catalog.InstallCompleted {
		t.Fatalf("install state = (%q, %q), want (email, completed)", id, status)
	}
	agent := d.cat.Get("email")
	if agent == nil || !agent.Status.IsLaunchable() {
		t.Fatalf("email is not launchable after a completed install: %+v", agent)
	}
	if agent.InstalledVersion != "0.5.0" {
		t.Errorf("installed version = %q, want 0.5.0", agent.InstalledVersion)
	}

	view := plainView(d.m)
	if !strings.Contains(view, "Installed Email") {
		t.Errorf("install box does not report success; view:\n%s", view)
	}
}

func TestInstallFailureIsReportedNotSwallowed(t *testing.T) {
	fake := newFakeDaemon(t)
	fake.progress = []map[string]any{{
		"agent_id": "email", "status": "failed", "phase": "verify", "percent": 60.0,
		"version": "0.5.0",
		"error":   "the downloaded file does not match its published checksum",
	}}
	d := newDriver(t, fake.client(), 120, 40)
	d.pump(d.m.Init())
	d.gotoTab("Available")
	d.selectAgent("email")

	d.send(key("i"))
	d.send(key("y"))

	_, status := d.m.InstallState()
	if status != catalog.InstallFailed {
		t.Fatalf("install status = %q, want failed", status)
	}
	view := plainView(d.m)
	if !strings.Contains(view, "checksum") {
		t.Errorf("failure box does not carry the daemon's reason; view:\n%s", view)
	}
	if agent := d.cat.Get("email"); agent.Status.IsLaunchable() {
		t.Error("a failed install left the agent marked installed")
	}
}

func TestUninstallGoesThroughTheDaemonAfterConfirmation(t *testing.T) {
	fake := newFakeDaemon(t)
	fake.catalogBody = emailCatalog(true)
	d := newDriver(t, fake.client(), 120, 40)
	d.pump(d.m.Init())

	d.gotoTab("Installed")
	d.selectAgent("email")

	d.send(key("d"))
	if d.m.Overlay() != "confirm" {
		t.Fatalf("d did not open the uninstall confirmation (overlay %q)", d.m.Overlay())
	}
	if len(fake.uninstallCalls()) != 0 {
		t.Fatal("opening the confirmation already called DELETE")
	}

	d.send(key("n"))
	if len(fake.uninstallCalls()) != 0 {
		t.Fatalf("declining still uninstalled: %v", fake.uninstallCalls())
	}

	d.send(key("d"))
	d.send(key("y"))
	if got := fake.uninstallCalls(); len(got) != 1 || got[0] != "email" {
		t.Fatalf("uninstall calls = %v, want [email]", got)
	}
	if agent := d.cat.Get("email"); agent.Status.IsLaunchable() {
		t.Error("email is still launchable after uninstall")
	}
}

// `backspace` means "go back" to every terminal user; it must not be a
// destructive-action trigger.
func TestBackspaceDoesNotOpenUninstall(t *testing.T) {
	fake := newFakeDaemon(t)
	fake.catalogBody = emailCatalog(true)
	d := newDriver(t, fake.client(), 120, 40)
	d.pump(d.m.Init())
	d.gotoTab("Installed")
	d.selectAgent("email")

	d.send(keyBackspace())

	if overlay := d.m.Overlay(); overlay != "" {
		t.Fatalf("backspace opened the %q overlay — it must never trigger uninstall", overlay)
	}
	if len(fake.uninstallCalls()) != 0 {
		t.Fatalf("backspace uninstalled %v", fake.uninstallCalls())
	}
}

// Nothing may reach the install route without a daemon; the failure has to be
// visible rather than a key that quietly does nothing.
func TestInstallWithoutDaemonFailsLoudly(t *testing.T) {
	d := newDriver(t, nil, 120, 40)
	d.gotoTab("Available")
	d.selectAgent("email")

	d.send(key("i"))

	view := plainView(d.m)
	if !strings.Contains(view, "daemon connection") {
		t.Errorf("no daemon connection was not reported; view:\n%s", view)
	}
}
