package client

import (
	"context"
	"errors"
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

// A switch the agent cannot make is a 409 that is not a busy session; the chat
// needs to tell the two apart to put the old model back.
func TestARefusedSwitchIsNotReportedAsABusySession(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.15"
	f.queryStatus = http.StatusConflict
	// Verbatim shape of server.py's refusal.
	f.queryBody = `{"detail":"could not switch session s1 to 'Qwen3-4B-GGUF': model failed ` +
		`to load. The session is still running Gemma-4-E4B-it-GGUF."}`
	c := f.clientFor(t, "gaia")
	c.SetTurnModel("Qwen3-4B-GGUF")

	_, err := c.Send(context.Background(), "hi")
	var refused *ModelSwitchRefusedError
	if !errors.As(err, &refused) || refused.Model != "Qwen3-4B-GGUF" {
		t.Fatalf("Send = %v, want a ModelSwitchRefusedError for the turn's model", err)
	}
	if strings.Contains(err.Error(), "still finishing the previous turn") {
		t.Errorf("a refused switch was described as a busy session: %v", err)
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
