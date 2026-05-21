package catalog

// Section represents a tab/section in the hub UI.
type Section string

const (
	SectionDashboard  Section = "Dashboard"
	SectionInstalled  Section = "Installed"
	SectionAvailable  Section = "Available"
	SectionComingSoon Section = "Coming Soon"
)

// AllSections returns the tab order for the hub.
func AllSections() []Section {
	return []Section{SectionInstalled, SectionAvailable, SectionComingSoon}
}

// Catalog manages the agent registry.
type Catalog struct {
	agents []Agent
}

// NewCatalog creates a catalog with hardcoded seed agents.
func NewCatalog() *Catalog {
	return &Catalog{agents: seedAgents()}
}

// All returns all agents.
func (c *Catalog) All() []Agent {
	result := make([]Agent, len(c.agents))
	copy(result, c.agents)
	return result
}

// Get returns an agent by ID, or nil if not found.
func (c *Catalog) Get(id string) *Agent {
	for i := range c.agents {
		if c.agents[i].ID == id {
			return &c.agents[i]
		}
	}
	return nil
}

// SetMockBinary overrides all installed agent binary paths with a mock binary for testing.
func (c *Catalog) SetMockBinary(binaryPath string) {
	for i := range c.agents {
		if c.agents[i].Status == StatusInstalled || c.agents[i].Status == StatusActive || c.agents[i].Status == StatusIdle {
			c.agents[i].BinaryPath = binaryPath
			c.agents[i].BinaryArgs = nil
		}
	}
}

// BySection returns agents filtered by their install status section.
func (c *Catalog) BySection(section Section) []Agent {
	var result []Agent
	for _, a := range c.agents {
		switch section {
		case SectionInstalled:
			if a.Status == StatusInstalled || a.Status == StatusActive || a.Status == StatusIdle {
				result = append(result, a)
			}
		case SectionAvailable:
			if a.Status == StatusAvailable {
				result = append(result, a)
			}
		case SectionComingSoon:
			if a.Status == StatusComingSoon {
				result = append(result, a)
			}
		}
	}
	return result
}

// DashboardStats returns counts for the hub dashboard.
func (c *Catalog) DashboardStats() (installed, active, idle int) {
	for _, a := range c.agents {
		switch a.Status {
		case StatusInstalled:
			installed++
		case StatusActive:
			active++
		case StatusIdle:
			idle++
		}
	}
	return
}

// SetStatus updates an agent's status.
func (c *Catalog) SetStatus(id string, status AgentStatus) {
	for i := range c.agents {
		if c.agents[i].ID == id {
			c.agents[i].Status = status
			return
		}
	}
}

// Remove removes an agent by setting it back to Available and clearing binary path.
func (c *Catalog) Remove(id string) {
	for i := range c.agents {
		if c.agents[i].ID == id {
			c.agents[i].Status = StatusAvailable
			c.agents[i].BinaryPath = ""
			c.agents[i].BinaryArgs = nil
			return
		}
	}
}

// IncrementVotes bumps the vote count for a coming-soon agent.
func (c *Catalog) IncrementVotes(id string) {
	for i := range c.agents {
		if c.agents[i].ID == id {
			c.agents[i].Votes++
			return
		}
	}
}

func seedAgents() []Agent {
	return []Agent{
		// --- Installed ---
		{
			ID: "chat", Name: "Chat", Description: "General conversation and Q&A",
			Category: "Conversation", Tags: []string{"chat", "general", "qa"},
			Icon: "💬", Version: "0.1.0", Status: StatusInstalled,
			BinaryPath: "gaia-chat", BinaryArgs: []string{"--json-events"},
		},
		{
			ID: "doc", Name: "Doc", Description: "Document analysis with RAG",
			Category: "Documents", Tags: []string{"documents", "rag", "pdf", "search"},
			Icon: "📄", Version: "0.1.0", Status: StatusInstalled,
			BinaryPath: "gaia-doc", BinaryArgs: []string{"--json-events"},
		},
		{
			ID: "file", Name: "File", Description: "File system navigation and operations",
			Category: "Productivity", Tags: []string{"files", "filesystem", "io"},
			Icon: "📁", Version: "0.1.0", Status: StatusInstalled,
			BinaryPath: "gaia-file", BinaryArgs: []string{"--json-events"},
		},
		{
			ID: "code", Name: "Code", Description: "Code generation and editing",
			Category: "Code", Tags: []string{"code", "programming", "developer"},
			Icon: "🔧", Version: "0.1.0", Status: StatusInstalled,
			BinaryPath: "gaia-code", BinaryArgs: []string{"--json-events"},
		},
		{
			ID: "bash", Name: "Bash", Description: "Shell command execution and automation",
			Category: "DevOps", Tags: []string{"shell", "bash", "terminal", "cli"},
			Icon: "🖥️", Version: "0.1.0", Status: StatusInstalled,
			BinaryPath: "gaia-bash", BinaryArgs: []string{"--json-events", "--model", "Gemma-4-E4B-it-GGUF"},
		},

		// --- Available (not yet downloaded) ---
		{
			ID: "blender", Name: "Blender", Description: "3D scene automation and modeling",
			Category: "Creative", Tags: []string{"3d", "blender", "modeling", "animation"},
			Icon: "🎨", Version: "0.1.0", Status: StatusAvailable,
		},
		{
			ID: "jira", Name: "Jira", Description: "Issue tracking and project management",
			Category: "Productivity", Tags: []string{"jira", "issues", "project", "agile"},
			Icon: "🎫", Version: "0.1.0", Status: StatusAvailable,
		},
		{
			ID: "docker", Name: "Docker", Description: "Container management and orchestration",
			Category: "DevOps", Tags: []string{"docker", "containers", "kubernetes"},
			Icon: "🐳", Version: "0.1.0", Status: StatusAvailable,
		},
		{
			ID: "summarize", Name: "Summarize", Description: "Document and text summarization",
			Category: "Documents", Tags: []string{"summarize", "text", "tldr"},
			Icon: "📝", Version: "0.1.0", Status: StatusAvailable,
		},
		{
			ID: "email", Name: "Email", Description: "Email triage, drafting, and calendar",
			Category: "Productivity", Tags: []string{"email", "gmail", "calendar", "communication"},
			Icon: "📧", Version: "0.1.0", Status: StatusAvailable,
		},

		// --- Coming Soon ---
		{
			ID: "routing", Name: "Routing", Description: "Intelligent agent selection and orchestration",
			Category: "Infrastructure", Tags: []string{"routing", "orchestration", "multi-agent"},
			Icon: "🔀", Version: "0.1.0", Status: StatusComingSoon,
		},
		{
			ID: "browser", Name: "Browser", Description: "Web browsing and automation",
			Category: "Research", Tags: []string{"browser", "web", "scraping", "automation"},
			Icon: "🌐", Version: "0.1.0", Status: StatusComingSoon,
		},
		{
			ID: "data-analyst", Name: "Data Analyst", Description: "Data analysis and visualization",
			Category: "Data", Tags: []string{"data", "analysis", "charts", "csv", "excel"},
			Icon: "📊", Version: "0.1.0", Status: StatusComingSoon,
		},
	}
}
