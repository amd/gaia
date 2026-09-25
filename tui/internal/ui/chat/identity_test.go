// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"
)

// The header must name which agent is answering, not just the product and the
// model — see #3979. Both the flagship and a non-brand agent get the same
// "agent <id> v<version>" shape, so a user reading the header always has the
// exact string --agent expects back.
func TestHeaderNamesTheRunningAgent(t *testing.T) {
	flagship := NewChatModelForFlagship(&nullClient{}, "gaia", "GAIA", "0.2.0", false, true)
	flagship.width, flagship.height = 100, 30

	header := ansi.Strip(flagship.renderHeader())
	if !strings.Contains(header, "agent gaia v0.2.0") {
		t.Errorf("flagship header does not name the agent: %q", header)
	}

	email := NewChatModelForCatalogAgent(&nullClient{}, "email", "Email", "0.1.0", false)
	email.width, email.height = 100, 30

	header = ansi.Strip(email.renderHeader())
	if !strings.Contains(header, "agent email v0.1.0") {
		t.Errorf("email header does not name the agent: %q", header)
	}
}

// A newly installed agent with no known version yet must not invent one —
// no bare "v" and no fabricated number.
func TestHeaderOmitsTheVersionWhenUnknown(t *testing.T) {
	m := NewChatModelForFlagship(&nullClient{}, "gaia", "GAIA", "", false, true)
	m.width, m.height = 100, 30

	header := ansi.Strip(m.renderHeader())
	if !strings.Contains(header, "agent gaia") {
		t.Errorf("header lost the agent id: %q", header)
	}
	if strings.Contains(header, " v") {
		t.Errorf("header invented a version with nothing to show: %q", header)
	}
}

// The stable agent id belongs on screen somewhere a user would actually read
// it, not just in debug logging or the control API.
func TestTheAgentIDAppearsInTheRenderedFrame(t *testing.T) {
	m := NewChatModelForCatalogAgent(&nullClient{}, "email", "Email", "0.1.0", false)
	m.width, m.height = 100, 30
	m.resize()

	if !strings.Contains(ansi.Strip(m.View()), "email") {
		t.Errorf("the agent id never appears in the frame:\n%s", ansi.Strip(m.View()))
	}
}

// On a narrow terminal something has to give, and it must not be the model
// chip — a user who cannot tell which model is answering has lost more than
// one who cannot tell which agent is. The identity chip is what degrades.
func TestNarrowHeaderKeepsTheModelChipOverTheIdentityChip(t *testing.T) {
	m := NewChatModelForFlagship(&nullClient{}, "gaia", "GAIA", "0.2.0", false, true)
	m.width, m.height = 40, 30
	m = feed(t, m, canonicalModelPing("Gemma-4-E4B-it-GGUF", "Gemma-4-E4B-it-GGUF", false))

	header := ansi.Strip(m.renderHeader())
	if !strings.Contains(header, "Gemma-4-E4B-it-GGUF") {
		t.Errorf("the model chip was lost on a 40-column terminal: %q", header)
	}
	if strings.Contains(header, "agent gaia") {
		t.Errorf("the identity chip should have given way at this width, but is still present: %q", header)
	}

	// A wide terminal has room for both — nothing should be dropped there.
	m.width = 100
	wide := ansi.Strip(m.renderHeader())
	if !strings.Contains(wide, "agent gaia") || !strings.Contains(wide, "Gemma-4-E4B-it-GGUF") {
		t.Errorf("a 100-column terminal should show both chips: %q", wide)
	}
}
