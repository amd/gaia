package client

import (
	"context"
	"net/http"
	"strings"
	"testing"
)

func finalStream(w http.ResponseWriter, flush func(), _ queryRequest) {
	frame(w, `{"type":"final","answer":"ok"}`)
	flush()
}

// On the daemon relay a model switch is the `model` the next turn carries; the
// sidecar moves the retained session onto it.
func TestSetTurnModelIsWhatTheNextTurnCarries(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.15"
	f.stream = finalStream
	c := f.clientFor(t, "gaia")

	if err := c.CheckModelSwitch(context.Background()); err != nil {
		t.Fatalf("a 2.15 flagship refused a model switch: %v", err)
	}
	c.SetTurnModel("Qwen3-4B-GGUF")
	ch, err := c.Send(context.Background(), "hi")
	if err != nil {
		t.Fatal(err)
	}
	collect(t, ch)
	if got := f.lastQuery().Model; got != "Qwen3-4B-GGUF" {
		t.Fatalf("the turn was sent with model %q, want the switched one", got)
	}
}

func TestAFlagshipBelowTheSwitchContractIsRefusedWithTheFix(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	c := f.clientFor(t, "gaia")

	err := c.CheckModelSwitch(context.Background())
	if err == nil {
		t.Fatal("a 2.13 flagship, which keeps a session on its first model, was offered a switch")
	}
	for _, want := range []string{"2.13", "2.14", "gaia hub install gaia"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal does not name %q: %v", want, err)
		}
	}
}

// Every sidecar numbers its own contract: email at 2.14+ still has no model switch.
func TestOnlyTheFlagshipSwitchesModels(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.15"
	if err := f.clientFor(t, "email").CheckModelSwitch(context.Background()); err == nil {
		t.Fatal("the email agent was offered a model switch")
	}
}

func TestAPeerWithNoVersionRouteIsNotOfferedTheSwitch(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "" // no /version route: old enough to predate every contract
	if err := f.clientFor(t, "gaia").CheckModelSwitch(context.Background()); err == nil {
		t.Fatal("a peer with no /version route was offered a switch")
	}
}
