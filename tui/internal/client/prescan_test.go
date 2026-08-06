package client

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"testing"
	"time"
)

func TestFetchPreScanReturnsResultObject(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.11"
	f.prescanBody = `{"schema_version":"2.11","result":{"kind":"email_pre_scan","urgent":[],"actionable":[],"informational_count":0,"suggested_archives":[],"suggested_drafts":[],"needs_review":[],"scanned":5,"needs_you":[{"ref":1,"kind":"urgent","sender":"a@example.com","subject":"Sync","why":"asked for a reply"}],"needs_you_total":1,"bulk":{"count":0,"filter_tests":[]}}}`
	c := f.client(t)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	raw, err := c.FetchPreScan(ctx)
	if err != nil {
		t.Fatalf("FetchPreScan: %v", err)
	}

	var decoded struct {
		Kind     string `json:"kind"`
		NeedsYou []struct {
			Sender string `json:"sender"`
		} `json:"needs_you"`
	}
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatalf("FetchPreScan returned invalid JSON: %v (%s)", err, raw)
	}
	if decoded.Kind != "email_pre_scan" {
		t.Errorf("kind = %q, want email_pre_scan", decoded.Kind)
	}
	if len(decoded.NeedsYou) != 1 || decoded.NeedsYou[0].Sender != "a@example.com" {
		t.Errorf("needs_you = %+v, want one item from a@example.com", decoded.NeedsYou)
	}
}

func TestFetchPreScanSurfacesNoMailboxConnected(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.11"
	f.prescanStatus = http.StatusServiceUnavailable
	c := f.client(t)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	_, err := c.FetchPreScan(ctx)
	if err == nil {
		t.Fatal("expected an error when no mailbox is connected, got nil")
	}
	var tooOld *ErrPreScanContractTooOld
	if errors.As(err, &tooOld) {
		t.Fatalf("a 503 must surface as a plain error, not the contract-too-old gate: %v", err)
	}
}

func TestFetchPreScanNeverPresentsTheSidecarBearer(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.11"
	c := f.client(t)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if _, err := c.FetchPreScan(ctx); err != nil {
		t.Fatalf("FetchPreScan: %v", err)
	}

	for _, auth := range f.auths {
		if auth == "Bearer "+sidecarBearer {
			t.Fatalf("the TUI presented the sidecar bearer token on a relayed request")
		}
	}
}

// ---------------------------------------------------------------------------
// #2743 -- version gate. A pre-2.11 peer's /prescan response simply omits
// needs_you/bulk, which decodes as an empty slice indistinguishable from a
// genuinely clear inbox. FetchPreScan must refuse to trust that field from
// a peer it hasn't confirmed is new enough, and say so distinctly rather
// than returning the empty envelope as if it were valid.
// ---------------------------------------------------------------------------

func TestFetchPreScanRefusesAnOldPeerContract(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.6" // predates needs_you (2.11)
	c := f.client(t)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	_, err := c.FetchPreScan(ctx)
	if err == nil {
		t.Fatal("expected ErrPreScanContractTooOld for a peer below 2.11, got nil")
	}
	var tooOld *ErrPreScanContractTooOld
	if !errors.As(err, &tooOld) {
		t.Fatalf("error = %v (%T), want *ErrPreScanContractTooOld", err, err)
	}
	if tooOld.Version != "2.6" {
		t.Errorf("tooOld.Version = %q, want 2.6", tooOld.Version)
	}
}

func TestFetchPreScanOldPeerNeverCallsPrescanEndpoint(t *testing.T) {
	// The version check must happen BEFORE the real POST — there is no
	// point calling an endpoint whose response we've already decided not
	// to trust, and skipping it means an old sidecar with a genuinely
	// broken /prescan route still degrades cleanly.
	f := newFakeRelay(t)
	f.contractVersion = "2.10"
	c := f.client(t)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if _, err := c.FetchPreScan(ctx); err == nil {
		t.Fatal("expected ErrPreScanContractTooOld for a peer at 2.10")
	}
}

func TestFetchPreScanAcceptsExactFloorVersion(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.11"
	c := f.client(t)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if _, err := c.FetchPreScan(ctx); err != nil {
		t.Fatalf("a peer at exactly the floor version must be accepted: %v", err)
	}
}
