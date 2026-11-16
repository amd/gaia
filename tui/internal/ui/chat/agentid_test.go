package chat

import "testing"

// RunAgent (the direct `chat --agent` launch) used to construct the model
// with the display name in the id slot, so the value reaching m.agentID was
// "Email" instead of the catalog id "email". This pins both constructors
// against that regression.
func TestChatModelAgentIDIsCatalogIDNotDisplayNameForBothLaunchPaths(t *testing.T) {
	cases := []struct {
		name string
		m    ChatModel
	}{
		{"direct-CLI (RunAgent)", NewChatModelForCatalogAgent(&nullClient{}, "email", "Email", false)},
		{"flagship launch", NewChatModelForFlagship(&nullClient{}, "email", "Email", false, false)},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := tc.m.AgentID(); got != "email" {
				t.Errorf("AgentID() = %q, want the catalog id %q, not the display name", got, "email")
			}
			if got := tc.m.ControlSnapshot().Agent; got != "email" {
				t.Errorf("ControlSnapshot().Agent = %q, want %q -- the loopback control API reports this", got, "email")
			}
		})
	}
}

// The readiness gate in front of a flagship launch already ran `gaia init
// --check`. Arming the chat's own first-boot check as well spawns a SECOND
// Python interpreter for up to 30s on every cold launch.
func TestAVerifiedFlagshipLaunchDoesNotReCheckSetup(t *testing.T) {
	verified := NewChatModelForFlagship(&nullClient{}, "gaia", "GAIA", false, true)
	if verified.setupChecking {
		t.Error("the gate already proved setup, and the chat is checking it again")
	}

	// Unverified is the path with no gate in front of it, and it must still ask.
	unverified := NewChatModelForFlagship(&nullClient{}, "gaia", "GAIA", false, false)
	if !unverified.setupChecking {
		t.Error("an unverified flagship launch skipped the first-boot check entirely")
	}
}
