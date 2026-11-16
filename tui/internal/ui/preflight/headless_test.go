package preflight

import (
	"context"
	"strings"
	"testing"
)

func TestHeadlessActionRewritesEveryRemedyShape(t *testing.T) {
	cases := map[string]string{
		"Then press r to re-check.":           "",
		"Start it, then press r to re-check.": "Start it.",
		"Fix the file, then press r.":         "Fix the file.",
		"Upgrade the installed GAIA core, then press r. A restart brings the same version back.":                         "Upgrade the installed GAIA core. A restart brings the same version back.",
		"Connect Gmail, Outlook, or Microsoft 365 — press f to choose. For Outlook swap `google` for `microsoft` below.": "Connect Gmail, Outlook, or Microsoft 365. For Outlook swap `google` for `microsoft` below.",
		"Press r — this clears on its own in a moment.":                                                                  "This clears on its own in a moment.",
		"Reconnect it — press f, or run the command. Takes about a minute.":                                              "Reconnect it. Takes about a minute.",
		"Press r to try again; the checks continue either way.":                                                          "",
		"Press f and setup starts it. By hand instead: run this, then press r to re-check.":                              "By hand instead: run this.",
		"Stop GAIA's model server, then press r — it starts again with current settings.":                                "Stop GAIA's model server — it starts again with current settings.",
		"Press f to install it, or run this in a terminal, then press r.":                                                "Run this in a terminal.",
		"Press f to use it, or g to install GAIA's own. To switch back later, run the command.":                          "To switch back later, run the command.",
		"To use it, save its key with the command below, then press r. Or press g to install GAIA's own instead.":        "To use it, save its key with the command below.",
		"Press p, choose Fireworks AI, and connect it.":                                                                  "",
		"Press p and choose it again to download it, or pick a downloaded model.":                                        "Pick a downloaded model.",
		"Press enter to start without it, or fix it first.":                                                              "Fix it first.",
		"Press d for the setup log, fix what it names, then retry.":                                                      "",
		"Restart it, then press r to re-check. Context is 4096 tokens.":                                                  "Restart it. Context is 4096 tokens.",
		"`gaia init` sets it up — it keeps that terminal, so press r back here once it is up.":                           "`gaia init` sets it up — it keeps that terminal.",
		"below — then start it again and press r.":                                                                       "below — then start it again.",
		// Untouched: no keystroke, including words that merely contain "press".
		"Choose Local, Fireworks AI or AMD LLM Gateway.": "Choose Local, Fireworks AI or AMD LLM Gateway.",
		"Decompress the archive, then retry.":            "Decompress the archive, then retry.",
		"Press releases are at amd.com":                  "Press releases are at amd.com",
		"":                                               "",
	}
	for in, want := range cases {
		got := headlessAction(in)
		if got != want {
			t.Errorf("headlessAction(%q)\n got  %q\n want %q", in, got, want)
		}
		if keystroke.MatchString(got) {
			t.Errorf("headlessAction(%q) still asks for a key: %q", in, got)
		}
	}
}

// The one-shot prints Report.String after it has exited; the interactive gate
// renders Remedy.Action itself and must keep its keys.
func TestReportStringIsKeystrokeFreeButRowsKeepTheirKeys(t *testing.T) {
	scenarios := map[string]*fakeTransport{
		"unreachable": newFake().with("GET /v1/email/init", 503, initUnreachable),
		"too old":     newFake().with("GET /v1/email/init", 503, initTooOld),
		"model":       newFake().with("GET /v1/email/init", 503, initModelMissing),
		"mailbox":     newFake().with("GET /v1/email/connectors", 200, connectorsNone),
		"no send":     newFake().with("GET /v1/email/connectors", 200, connectorsNoSend),
		"no grant":    newFake().with("GET /v1/email/connectors", 200, connectorsNoGrant),
		"creds dead":  newFake().with("POST /v1/email/search", 502, searchNoForwardedCredential),
		"revoked":     newFake().with("POST /v1/email/search", 403, searchRevoked),
		"sidecar":     newFake().with("GET /daemon/v1/agents", 200, agentsStopped),
		"wedged":      newFake().with("GET /v1/email/health", 503, healthWedged),
	}
	interactiveKeys := 0
	for name, f := range scenarios {
		rep := Check(context.Background(), f, EmailConfig())
		out := rep.String()
		if m := keystroke.FindString(out); m != "" {
			t.Errorf("%s: one-shot report tells the reader to %q:\n%s", name, m, out)
		}
		for _, row := range rep.Rows {
			if keystroke.MatchString(row.Remedy.Action) {
				interactiveKeys++
			}
			for _, c := range row.Remedy.Commands() {
				if row.State != StateOK && !strings.Contains(out, c.Line) {
					t.Errorf("%s: report dropped the %q command", name, c.Line)
				}
			}
		}
	}
	if interactiveKeys == 0 {
		t.Fatal("no row offered a key, so this test no longer proves the gate keeps them")
	}
}
