package client

import (
	"context"
	"net/http"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/event"
)

// The regression that broke every query on #2496: the TUI sent
// `can_answer_questions` unconditionally, and the PUBLISHED sidecar — whose
// request model is strict — 422s an unknown field. Both halves of a feature do
// not ship on the same clock, so the client has to ask before it sends.
func TestOldStrictPeerGetsNoUnknownField(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.4" // what the published 0.5.0 binary reports
	f.strictBody = true       // pydantic extra="forbid"
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"final","answer":"hi"}`)
		flush()
	}

	c := f.client(t)
	c.opts.Interactive = true // the case that used to send `true` and 422
	ch, err := c.Send(context.Background(), "say hi")
	if err != nil {
		t.Fatalf("Send against an old strict peer failed: %v", err)
	}
	got := collect(t, ch)

	if raw := f.lastRawBody(); strings.Contains(raw, "can_answer_questions") {
		t.Errorf("the field was sent to a peer that rejects it: %s", raw)
	}
	if !hasFinal(got) {
		t.Errorf("the turn did not complete: %#v", got)
	}
}

// The one-shot direction of the same bug — it sent `false`, which 422s just the
// same. "Not interactive" was never the trigger; the field's presence was.
func TestOneShotAgainstOldStrictPeerWorks(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.4"
	f.strictBody = true
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"final","answer":"hi"}`)
		flush()
	}

	c := f.client(t) // Interactive stays false
	ch, err := c.Send(context.Background(), "say hi")
	if err != nil {
		t.Fatalf("one-shot Send against an old strict peer failed: %v", err)
	}
	if !hasFinal(collect(t, ch)) {
		t.Error("the one-shot did not complete")
	}
	if raw := f.lastRawBody(); strings.Contains(raw, "can_answer_questions") {
		t.Errorf("the field was sent to a peer that rejects it: %s", raw)
	}
}

// A peer that DOES speak 2.6 must be told the truth, explicitly — including
// `false`, which the agent branches on. Omitting it there would silently let a
// one-shot be asked a question it cannot answer.
func TestNewPeerIsToldTheCapabilityBothWays(t *testing.T) {
	for _, tc := range []struct {
		name        string
		interactive bool
		want        bool
	}{
		{"interactive", true, true},
		{"one-shot", false, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := newFakeRelay(t)
			f.contractVersion = "2.6"
			f.strictBody = true
			f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
				frame(w, `{"type":"final","answer":"hi"}`)
				flush()
			}

			c := f.client(t)
			c.opts.Interactive = tc.interactive
			ch, err := c.Send(context.Background(), "say hi")
			if err != nil {
				t.Fatalf("Send: %v", err)
			}
			collect(t, ch)

			// strictBody's decoder does NOT know the field, so a 2.6 peer here
			// would 422 — assert the turn survived AND the value is right.
			got := f.lastQuery().CanAnswerQuestions
			if got == nil {
				t.Fatalf("the field was omitted for a 2.6 peer: %s", f.lastRawBody())
			}
			if *got != tc.want {
				t.Errorf("can_answer_questions = %t, want %t", *got, tc.want)
			}
		})
	}
}

// A sidecar so old it has no /version route at all must still work.
func TestMissingVersionRouteDegradesQuietlyButWorks(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "" // route 404s
	f.strictBody = true
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"final","answer":"hi"}`)
		flush()
	}

	c := f.client(t)
	ch, err := c.Send(context.Background(), "say hi")
	if err != nil {
		t.Fatalf("Send with no /version route failed: %v", err)
	}
	if !hasFinal(collect(t, ch)) {
		t.Error("the turn did not complete")
	}
}

// An interactive user whose agent cannot be asked anything is TOLD so — once.
// Otherwise the in-conversation mailbox fix just never appears and reads as
// broken rather than as out of date.
func TestInteractiveUserIsToldWhenThePeerIsTooOld(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.4"
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"final","answer":"hi"}`)
		flush()
	}

	c := f.client(t)
	c.opts.Interactive = true

	ch, err := c.Send(context.Background(), "first")
	if err != nil {
		t.Fatal(err)
	}
	first := collect(t, ch)
	notice := firstNotice(first)
	if notice == "" {
		t.Fatalf("no notice on the first turn: %#v", first)
	}
	for _, want := range []string{"2.4", "cannot ask questions", "gaia hub install email"} {
		if !strings.Contains(notice, want) {
			t.Errorf("notice is missing %q: %s", want, notice)
		}
	}

	// Once per launch, not once per turn.
	ch, err = c.Send(context.Background(), "second")
	if err != nil {
		t.Fatal(err)
	}
	if again := firstNotice(collect(t, ch)); again != "" {
		t.Errorf("the notice repeated on a later turn: %s", again)
	}
}

// A one-shot cannot act on the notice, so it is not given one.
func TestOneShotGetsNoCapabilityNotice(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.4"
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"final","answer":"hi"}`)
		flush()
	}

	c := f.client(t)
	ch, err := c.Send(context.Background(), "say hi")
	if err != nil {
		t.Fatal(err)
	}
	if notice := firstNotice(collect(t, ch)); notice != "" {
		t.Errorf("a one-shot was given an unactionable notice: %s", notice)
	}
}

// The version is probed once, not once per turn.
func TestContractIsProbedOncePerClient(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.6"
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"final","answer":"hi"}`)
		flush()
	}

	c := f.client(t)
	for i := 0; i < 3; i++ {
		ch, err := c.Send(context.Background(), "hi")
		if err != nil {
			t.Fatal(err)
		}
		collect(t, ch)
	}
	if n := f.versionProbes(); n != 1 {
		t.Errorf("/version was probed %d times, want 1", n)
	}
}

func TestContractAtLeast(t *testing.T) {
	for _, tc := range []struct {
		in   string
		want bool
	}{
		{"2.6", true},
		{"2.7", true},
		{"2.10", true}, // MINOR is numeric, not lexical
		{"3.0", true},
		{"2.5", false},
		{"2.4", false},
		{"1.9", false},
		{"2", false},
		{"", false},
		{"not-a-version", false},
		{" 2.6 ", true},
	} {
		if got := contractAtLeast(tc.in, 2, 6); got != tc.want {
			t.Errorf("contractAtLeast(%q, 2, 6) = %t, want %t", tc.in, got, tc.want)
		}
	}
}

func hasFinal(events []interface{}) bool {
	for _, e := range events {
		if _, ok := e.(event.CanonicalFinalEvent); ok {
			return true
		}
	}
	return false
}

func firstNotice(events []interface{}) string {
	for _, e := range events {
		if n, ok := e.(event.CanonicalNoticeEvent); ok {
			return n.Text
		}
	}
	return ""
}

// The remedy must name an AGENT-scoped command. `gaia install` / `gaia uninstall`
// exist and look right — which is the trap — but they are GAIA-WIDE: bare
// `gaia uninstall` is the tiered cleanup of the GAIA install itself, one flag
// from `--purge`. They also reject a trailing agent id, so a user handed
// `gaia uninstall email` gets an argparse error and may retry without the
// argument, landing on the wrong tool entirely.
func TestRemedyNamesTheAgentScopedCommand(t *testing.T) {
	notice := noticeForMissingCapability("email", "2.4")

	for _, want := range []string{"gaia hub uninstall email", "gaia hub install email"} {
		if !strings.Contains(notice, want) {
			t.Errorf("remedy does not name %q: %s", want, notice)
		}
	}
	// The bare forms must not appear even as a substring of the advice.
	for _, forbidden := range []string{"`gaia install ", "`gaia uninstall ", "gaia install email", "gaia uninstall email"} {
		if strings.Contains(notice, forbidden) && !strings.Contains(notice, "hub "+strings.TrimPrefix(forbidden, "`")) {
			t.Errorf("remedy names the GAIA-wide command %q: %s", forbidden, notice)
		}
	}
	if strings.Contains(notice, "`gaia install") || strings.Contains(notice, "`gaia uninstall") {
		t.Errorf("remedy opens a command with the bare verb: %s", notice)
	}
}

// Every agent id the notice is built for keeps the scoped form.
func TestRemedyIsScopedForAnyAgent(t *testing.T) {
	for _, id := range []string{"email", "analyst", "code"} {
		notice := noticeForMissingCapability(id, "2.5")
		if !strings.Contains(notice, "gaia hub install "+id) {
			t.Errorf("remedy for %q is not agent-scoped: %s", id, notice)
		}
	}
}
