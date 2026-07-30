package chat

import "testing"

// RunAgent (the direct `chat --agent` launch) used to construct the model
// with the display name in the id slot, so the value reaching m.agentID was
// "Email" instead of the catalog id "email". This pins both launch paths
// against that regression: a test that only covers the hub path would not
// have caught it.
func TestChatModelAgentIDIsCatalogIDNotDisplayNameForBothLaunchPaths(t *testing.T) {
	cases := []struct {
		name string
		m    ChatModel
	}{
		{"direct-CLI (RunAgent)", NewChatModelForCatalogAgent(&nullClient{}, "email", "Email", false)},
		{"hub launch", NewChatModelFromHub(&nullClient{}, "email", "Email", false)},
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

// A direct launch has no RootModel underneath it to handle ReturnToHubMsg, so
// it must never claim it can return to a hub -- see CanReturnToHub.
func TestNewChatModelForCatalogAgentDoesNotEnableHubReturn(t *testing.T) {
	m := NewChatModelForCatalogAgent(&nullClient{}, "email", "Email", false)
	if m.CanReturnToHub() {
		t.Fatal("a direct-CLI launch must not enable hub-return")
	}
}
