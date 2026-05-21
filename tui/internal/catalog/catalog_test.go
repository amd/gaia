package catalog

import (
	"strings"
	"testing"
)

func TestNewCatalog(t *testing.T) {
	c := NewCatalog()
	if c == nil {
		t.Fatal("NewCatalog() returned nil")
	}
	if len(c.agents) == 0 {
		t.Fatal("NewCatalog() returned empty catalog")
	}
}

func TestAll(t *testing.T) {
	c := NewCatalog()
	all := c.All()
	if len(all) != len(c.agents) {
		t.Fatalf("All() returned %d agents, want %d", len(all), len(c.agents))
	}
	// Verify it's a copy, not a reference
	all[0].Name = "MUTATED"
	if c.agents[0].Name == "MUTATED" {
		t.Fatal("All() returned a reference, not a copy")
	}
}

func TestGetValidID(t *testing.T) {
	c := NewCatalog()
	agent := c.Get("chat")
	if agent == nil {
		t.Fatal("Get('chat') returned nil")
	}
	if agent.ID != "chat" {
		t.Fatalf("Get('chat').ID = %q, want 'chat'", agent.ID)
	}
	if agent.Name != "Chat" {
		t.Fatalf("Get('chat').Name = %q, want 'Chat'", agent.Name)
	}
}

func TestGetMissingID(t *testing.T) {
	c := NewCatalog()
	agent := c.Get("nonexistent")
	if agent != nil {
		t.Fatalf("Get('nonexistent') returned %+v, want nil", agent)
	}
}

func TestBySectionInstalled(t *testing.T) {
	c := NewCatalog()
	installed := c.BySection(SectionInstalled)
	if len(installed) == 0 {
		t.Fatal("BySection(Installed) returned empty")
	}
	for _, a := range installed {
		if !a.Status.IsLaunchable() {
			t.Fatalf("BySection(Installed) included non-launchable agent %q with status %s", a.ID, a.Status)
		}
	}
}

func TestBySectionAvailable(t *testing.T) {
	c := NewCatalog()
	available := c.BySection(SectionAvailable)
	if len(available) == 0 {
		t.Fatal("BySection(Available) returned empty")
	}
	for _, a := range available {
		if a.Status != StatusAvailable {
			t.Fatalf("BySection(Available) included agent %q with status %s", a.ID, a.Status)
		}
	}
}

func TestBySectionComingSoon(t *testing.T) {
	c := NewCatalog()
	comingSoon := c.BySection(SectionComingSoon)
	if len(comingSoon) == 0 {
		t.Fatal("BySection(ComingSoon) returned empty")
	}
	for _, a := range comingSoon {
		if a.Status != StatusComingSoon {
			t.Fatalf("BySection(ComingSoon) included agent %q with status %s", a.ID, a.Status)
		}
	}
}

func TestBySectionCoverage(t *testing.T) {
	c := NewCatalog()
	all := c.All()
	installed := c.BySection(SectionInstalled)
	available := c.BySection(SectionAvailable)
	comingSoon := c.BySection(SectionComingSoon)
	total := len(installed) + len(available) + len(comingSoon)
	if total != len(all) {
		t.Fatalf("sections sum to %d agents, want %d", total, len(all))
	}
}

func TestDashboardStats(t *testing.T) {
	c := NewCatalog()
	installed, active, idle := c.DashboardStats()
	if installed != 5 {
		t.Fatalf("DashboardStats() installed = %d, want 5", installed)
	}
	if active != 0 {
		t.Fatalf("DashboardStats() active = %d, want 0", active)
	}
	if idle != 0 {
		t.Fatalf("DashboardStats() idle = %d, want 0", idle)
	}
}

func TestSetStatus(t *testing.T) {
	c := NewCatalog()
	c.SetStatus("chat", StatusActive)
	agent := c.Get("chat")
	if agent.Status != StatusActive {
		t.Fatalf("after SetStatus, status = %s, want active", agent.Status)
	}

	// Verify dashboard stats updated
	installed, active, _ := c.DashboardStats()
	if active != 1 {
		t.Fatalf("after SetStatus(active), active = %d, want 1", active)
	}
	if installed != 4 {
		t.Fatalf("after SetStatus(active), installed = %d, want 4", installed)
	}
}

func TestSetStatusNonexistent(t *testing.T) {
	c := NewCatalog()
	// Should not panic
	c.SetStatus("nonexistent", StatusActive)
}

func TestRemove(t *testing.T) {
	c := NewCatalog()
	agent := c.Get("bash")
	if agent.BinaryPath == "" {
		t.Fatal("bash agent should have a BinaryPath before Remove")
	}

	c.Remove("bash")
	agent = c.Get("bash")
	if agent.Status != StatusAvailable {
		t.Fatalf("after Remove, status = %s, want available", agent.Status)
	}
	if agent.BinaryPath != "" {
		t.Fatalf("after Remove, BinaryPath = %q, want empty", agent.BinaryPath)
	}
	if agent.BinaryArgs != nil {
		t.Fatalf("after Remove, BinaryArgs = %v, want nil", agent.BinaryArgs)
	}
}

func TestRemoveNonexistent(t *testing.T) {
	c := NewCatalog()
	// Should not panic
	c.Remove("nonexistent")
}

func TestIncrementVotes(t *testing.T) {
	c := NewCatalog()
	agent := c.Get("routing")
	if agent.Votes != 0 {
		t.Fatalf("initial Votes = %d, want 0", agent.Votes)
	}

	c.IncrementVotes("routing")
	if agent.Votes != 1 {
		t.Fatalf("after IncrementVotes, Votes = %d, want 1", agent.Votes)
	}

	c.IncrementVotes("routing")
	if agent.Votes != 2 {
		t.Fatalf("after second IncrementVotes, Votes = %d, want 2", agent.Votes)
	}
}

func TestIncrementVotesNonexistent(t *testing.T) {
	c := NewCatalog()
	// Should not panic
	c.IncrementVotes("nonexistent")
}

func TestFilterValue(t *testing.T) {
	a := Agent{
		Name:        "Chat",
		Description: "General conversation and Q&A",
		Category:    "Conversation",
		Tags:        []string{"chat", "general", "qa"},
	}
	fv := a.FilterValue()
	if !strings.Contains(fv, "Chat") {
		t.Fatalf("FilterValue() missing Name, got %q", fv)
	}
	if !strings.Contains(fv, "General conversation") {
		t.Fatalf("FilterValue() missing Description, got %q", fv)
	}
	if !strings.Contains(fv, "Conversation") {
		t.Fatalf("FilterValue() missing Category, got %q", fv)
	}
	if !strings.Contains(fv, "qa") {
		t.Fatalf("FilterValue() missing tag 'qa', got %q", fv)
	}
}

func TestTitle(t *testing.T) {
	a := Agent{Icon: "💬", Name: "Chat"}
	title := a.Title()
	want := "💬 Chat"
	if title != want {
		t.Fatalf("Title() = %q, want %q", title, want)
	}
}

func TestStatusDot(t *testing.T) {
	tests := []struct {
		status AgentStatus
		want   string
	}{
		{StatusActive, "●"},
		{StatusIdle, "●"},
		{StatusInstalled, "●"},
		{StatusAvailable, "○"},
		{StatusComingSoon, "◌"},
		{AgentStatus(99), " "},
	}
	for _, tt := range tests {
		got := tt.status.StatusDot()
		if got != tt.want {
			t.Errorf("StatusDot(%s) = %q, want %q", tt.status, got, tt.want)
		}
	}
}

func TestIsLaunchable(t *testing.T) {
	tests := []struct {
		status AgentStatus
		want   bool
	}{
		{StatusInstalled, true},
		{StatusActive, true},
		{StatusIdle, true},
		{StatusAvailable, false},
		{StatusComingSoon, false},
		{AgentStatus(99), false},
	}
	for _, tt := range tests {
		got := tt.status.IsLaunchable()
		if got != tt.want {
			t.Errorf("IsLaunchable(%s) = %v, want %v", tt.status, got, tt.want)
		}
	}
}

func TestStatusString(t *testing.T) {
	tests := []struct {
		status AgentStatus
		want   string
	}{
		{StatusInstalled, "installed"},
		{StatusActive, "active"},
		{StatusIdle, "idle"},
		{StatusAvailable, "available"},
		{StatusComingSoon, "coming soon"},
		{AgentStatus(99), "unknown"},
	}
	for _, tt := range tests {
		got := tt.status.String()
		if got != tt.want {
			t.Errorf("String(%d) = %q, want %q", tt.status, got, tt.want)
		}
	}
}

func TestAllSections(t *testing.T) {
	sections := AllSections()
	if len(sections) != 3 {
		t.Fatalf("AllSections() returned %d sections, want 3", len(sections))
	}
	if sections[0] != SectionInstalled {
		t.Fatalf("AllSections()[0] = %q, want %q", sections[0], SectionInstalled)
	}
	if sections[1] != SectionAvailable {
		t.Fatalf("AllSections()[1] = %q, want %q", sections[1], SectionAvailable)
	}
	if sections[2] != SectionComingSoon {
		t.Fatalf("AllSections()[2] = %q, want %q", sections[2], SectionComingSoon)
	}
}
