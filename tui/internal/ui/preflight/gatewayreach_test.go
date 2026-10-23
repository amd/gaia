// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/lemonade"
)

func stubChatGateway(t *testing.T, err error) {
	t.Helper()
	old := probeChatGateway
	probeChatGateway = func(context.Context, string, string) error { return err }
	t.Cleanup(func() { probeChatGateway = old })
}

func TestAnUnreachableGatewayIsMarkedUnavailableOnTheFirstRunChoice(t *testing.T) {
	stubChatGateway(t, &lemonade.UnreachableError{Host: "llm-api.amd.com", Err: errors.New("no such host")})
	for _, o := range withGatewayReach(context.Background(), chatOptions("", "")) {
		switch o.Provider {
		case "amd":
			if !strings.Contains(o.Unavailable, "No network available") {
				t.Fatalf("AMD option unavailable = %q", o.Unavailable)
			}
		default:
			if o.Unavailable != "" {
				t.Fatalf("%s marked unavailable: %q", o.Provider, o.Unavailable)
			}
		}
	}
}

func TestAReachableGatewayStaysChoosable(t *testing.T) {
	stubChatGateway(t, nil)
	for _, o := range withGatewayReach(context.Background(), chatOptions("", "")) {
		if o.Unavailable != "" {
			t.Fatalf("%s marked unavailable: %q", o.Provider, o.Unavailable)
		}
	}
}

func TestTheFirstRunChooserGreysOutAndRefusesAnUnreachableGateway(t *testing.T) {
	m, chosen, _ := choosingGate(t, 110, 40)
	for i := range m.rep.ChatOptions {
		if m.rep.ChatOptions[i].Provider == "amd" {
			m.rep.ChatOptions[i].Unavailable = "No network available — can't reach llm-api.amd.com (needs AMD's network or VPN)"
		}
	}
	if !m.Choosing() {
		t.Fatal("a first run did not open on the choice")
	}
	m, _ = press(m, "3")
	if view := ansi.Strip(m.View()); !strings.Contains(view, "No network available") {
		t.Fatalf("the chooser does not say the gateway is unreachable:\n%s", view)
	}
	m, _ = press(m, "enter")
	if *chosen != "" || !m.Choosing() {
		t.Fatalf("an unreachable gateway was chosen (%q)", *chosen)
	}
	if !strings.Contains(m.note, "press r") {
		t.Fatalf("note = %q", m.note)
	}
}
