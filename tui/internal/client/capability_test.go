package client

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"testing"
	"time"
)

// SubprocessClient always knows the answer immediately: nothing to probe.
func TestSubprocessSupportsMemoryUnconditionally(t *testing.T) {
	c := NewSubprocessClient("", nil, false)
	defer c.Close()

	supported, known := c.Supports(CapabilityMemory)
	if !known || !supported {
		t.Errorf("Supports(CapabilityMemory) = (%t, %t), want (true, true)", supported, known)
	}
}

func TestSubprocessProbeCapabilitiesIsANoOp(t *testing.T) {
	c := NewSubprocessClient("", nil, false)
	defer c.Close()

	if err := c.ProbeCapabilities(context.Background()); err != nil {
		t.Errorf("ProbeCapabilities: %v", err)
	}
}

// SSEClient must never block or probe from Supports -- paletteFiltered and
// syncPalette call it synchronously, per keystroke.
func TestSSESupportsIsUnknownBeforeAnyProbe(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	c := f.clientFor(t, "gaia")
	defer c.Close()

	supported, known := c.Supports(CapabilityMemory)
	if known {
		t.Fatalf("Supports must be unknown before ProbeCapabilities/negotiate ever ran, got (%t, %t)", supported, known)
	}
	if n := f.versionProbes(); n != 0 {
		t.Errorf("Supports triggered %d /version probes, want 0 (must never probe)", n)
	}
}

func TestSSESupportsIsTrueOncePeerIsProbedAndNewEnough(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	c := f.clientFor(t, "gaia")
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := c.ProbeCapabilities(ctx); err != nil {
		t.Fatalf("ProbeCapabilities: %v", err)
	}

	supported, known := c.Supports(CapabilityMemory)
	if !known || !supported {
		t.Errorf("Supports(CapabilityMemory) = (%t, %t), want (true, true) after a 2.13 probe", supported, known)
	}
}

func TestSSESupportsIsFalseOncePeerIsProbedAndTooOld(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.12" // predates memory (2.13)
	c := f.clientFor(t, "gaia")
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := c.ProbeCapabilities(ctx); err != nil {
		t.Fatalf("ProbeCapabilities: %v", err)
	}

	supported, known := c.Supports(CapabilityMemory)
	if !known || supported {
		t.Errorf("Supports(CapabilityMemory) = (%t, %t), want (false, true) after a 2.12 probe", supported, known)
	}
}

// ProbeCapabilities and a real Send both negotiate the same cached contract
// -- whichever runs first must not force a second round-trip for the other.
func TestSSEProbeCapabilitiesSharesTheCacheWithNegotiate(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	c := f.clientFor(t, "gaia")
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := c.ProbeCapabilities(ctx); err != nil {
		t.Fatalf("ProbeCapabilities: %v", err)
	}
	if _, err := c.FetchMemory(ctx); err != nil {
		t.Fatalf("FetchMemory: %v", err)
	}
	if n := f.versionProbes(); n != 1 {
		t.Errorf("/version was probed %d times, want 1 (ProbeCapabilities + FetchMemory must share the cache)", n)
	}
}

// A probe that never got an answer from the peer (relay error, timeout, 401,
// 503) must stay unknown, not collapse into "known and unsupported" -- that
// is exactly what hid /memory behind one flaky probe (#3978 A1).
func TestSSESupportsIsUnknownAfterAFailedProbe(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	f.versionStatus = http.StatusServiceUnavailable
	c := f.clientFor(t, "gaia")
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := c.ProbeCapabilities(ctx); err != nil {
		t.Fatalf("ProbeCapabilities: %v", err)
	}

	supported, known := c.Supports(CapabilityMemory)
	if known {
		t.Errorf("Supports(CapabilityMemory) = (%t, %t), want known == false after a failed probe", supported, known)
	}
}

// A 404 on /version is a real answer -- the route does not exist, which
// itself means "old enough to predate every contract this file tracks" --
// and must keep meaning "known and unsupported", never "unknown".
func TestSSESupportsIsKnownFalseWhenVersionRouteIs404(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "" // 404s /version, like a sidecar old enough to lack it
	c := f.clientFor(t, "gaia")
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := c.ProbeCapabilities(ctx); err != nil {
		t.Fatalf("ProbeCapabilities: %v", err)
	}

	supported, known := c.Supports(CapabilityMemory)
	if !known || supported {
		t.Errorf("Supports(CapabilityMemory) = (%t, %t), want (false, true) for a 404 /version", supported, known)
	}
}

// FetchMemory after a failed probe must surface a transport-flavoured error,
// never ErrMemoryContractTooOld -- that error tells the user to reinstall a
// possibly fully-current agent (#3978 A1).
func TestSSEFetchMemoryAfterFailedProbeIsNotContractTooOld(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	f.versionStatus = http.StatusServiceUnavailable
	c := f.clientFor(t, "gaia")
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	_, err := c.FetchMemory(ctx)
	if err == nil {
		t.Fatal("expected an error when the contract probe never got an answer, got nil")
	}
	var tooOld *ErrMemoryContractTooOld
	if errors.As(err, &tooOld) {
		t.Fatalf("a failed probe must not surface as ErrMemoryContractTooOld: %v", err)
	}
}

// A single failed probe must not turn into a re-probe storm: negotiate caches
// the failure exactly like it caches success, at one round-trip per client.
func TestSSEFailedProbeIsCachedNotRetried(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	f.versionStatus = http.StatusServiceUnavailable
	c := f.clientFor(t, "gaia")
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := c.ProbeCapabilities(ctx); err != nil {
		t.Fatalf("ProbeCapabilities: %v", err)
	}
	if _, err := c.FetchMemory(ctx); err == nil {
		t.Fatal("expected FetchMemory to still refuse after the failed probe")
	}
	if n := f.versionProbes(); n != 1 {
		t.Errorf("/version was probed %d times, want 1 (a failed probe must still be cached, not retried)", n)
	}
}

// AgentVersion (#3979 plumbing) reads the same cache: "" before any probe,
// the peer's release version afterward.
func TestSSEAgentVersionBeforeAndAfterProbe(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	f.agentReleaseVersion = "0.2.0"
	c := f.clientFor(t, "gaia")
	defer c.Close()

	if got := c.AgentVersion(); got != "" {
		t.Errorf("AgentVersion() before any probe = %q, want \"\"", got)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := c.ProbeCapabilities(ctx); err != nil {
		t.Fatalf("ProbeCapabilities: %v", err)
	}

	if got := c.AgentVersion(); got != "0.2.0" {
		t.Errorf("AgentVersion() after probe = %q, want 0.2.0", got)
	}
}

func TestSSEAgentVersionEmptyWhenPeerOmitsIt(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	// agentReleaseVersion left "" -- an old peer that predates the field.
	c := f.clientFor(t, "gaia")
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := c.ProbeCapabilities(ctx); err != nil {
		t.Fatalf("ProbeCapabilities: %v", err)
	}

	if got := c.AgentVersion(); got != "" {
		t.Errorf("AgentVersion() = %q, want \"\" when the peer never reported one", got)
	}
}

// A sidecar numbering its own contract past the memory floor does NOT thereby
// gain a memory route. `email` reports 2.14 today and has none, so a
// version-only gate would offer /memory there and then 404 — the
// advertised-then-refused shape #3978 exists to remove.
func TestSupportsMemoryIsScopedToTheAgentThatHasTheRoute(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.14" // numerically past the memory floor
	c := f.clientFor(t, "email")
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := c.ProbeCapabilities(ctx); err != nil {
		t.Fatalf("ProbeCapabilities: %v", err)
	}

	supported, known := c.Supports(CapabilityMemory)
	if supported {
		t.Error("an agent with no memory route must not report the memory capability, whatever its contract number")
	}
	if !known {
		t.Error("this is a definite answer, not an unresolved probe")
	}
}

// ...and the refusal must say the agent has no store, never "your agent is out
// of date" — it is current, it simply is not the one that keeps memory.
func TestFetchMemoryOnAnAgentWithoutAStoreIsNotAContractComplaint(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.14"
	c := f.clientFor(t, "email")
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	_, err := c.FetchMemory(ctx)
	if err == nil {
		t.Fatal("expected a refusal for an agent that keeps no memory store")
	}
	var tooOld *ErrMemoryContractTooOld
	if errors.As(err, &tooOld) {
		t.Fatalf("refusal must not blame the agent's version: %v", err)
	}
	if !strings.Contains(err.Error(), "does not keep a memory store") {
		t.Errorf("refusal does not name the real reason: %v", err)
	}
}

// An unrecognized capability is unknown, never a confident "unsupported" —
// otherwise the next capability added is hidden by whichever transport was not
// taught about it, silently.
func TestSupportsAnUnknownCapabilityIsUnknownNotUnsupported(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	sse := f.clientFor(t, "gaia")
	defer sse.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := sse.ProbeCapabilities(ctx); err != nil {
		t.Fatalf("ProbeCapabilities: %v", err)
	}

	sub := NewSubprocessClient("", nil, false)
	defer sub.Close()

	for name, c := range map[string]CapabilityReporter{"sse": sse, "subprocess": sub} {
		if _, known := c.Supports(Capability("not-a-real-capability")); known {
			t.Errorf("%s claims to know about a capability it has never heard of", name)
		}
	}
}

// The two sidecars spell the release version differently: gaia's
// /v1/gaia/version says "version", email's says "agentVersion". Decoding one
// leaves the header permanently blank for the other.
func TestAgentVersionAcceptsBothWireSpellings(t *testing.T) {
	for _, tc := range []struct{ name, body string }{
		{"version", `{"apiVersion":"2.13","version":"0.2.0","agent":"gaia"}`},
		{"agentVersion", `{"apiVersion":"2.14","agentVersion":"0.2.0"}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := newFakeRelay(t)
			f.versionBody = tc.body
			c := f.clientFor(t, "gaia")
			defer c.Close()

			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			if err := c.ProbeCapabilities(ctx); err != nil {
				t.Fatalf("ProbeCapabilities: %v", err)
			}
			if got := c.AgentVersion(); got != "0.2.0" {
				t.Errorf("AgentVersion() = %q, want 0.2.0", got)
			}
		})
	}
}

// The "this agent keeps no memory" answer needs nothing from the peer, so it
// must not spawn a sidecar or probe just to refuse — and a probe that then
// failed would report a version problem for a situation with no version in it.
func TestFetchMemoryOnAnAgentWithoutAStoreCostsNoRoundTrip(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.14"
	c := f.clientFor(t, "email")
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if _, err := c.FetchMemory(ctx); err == nil {
		t.Fatal("expected a refusal")
	}
	if got := f.versionProbes(); got != 0 {
		t.Errorf("refusing an agent with no memory store cost %d /version probe(s), want 0", got)
	}
}
