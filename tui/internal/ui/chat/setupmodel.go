package chat

import tea "github.com/charmbracelet/bubbletea"

// applySetupChatModel makes a model selected by first-run setup take effect in
// the current chat before the setup gate is released. The model setup screen
// has already downloaded and tested it; the chat still uses its normal /model
// control path so the live agent confirms the switch before the next turn.
func (m ChatModel) applySetupChatModel(model string) (ChatModel, tea.Cmd) {
	if model == "" || !m.supportsModelCommand() {
		return m, nil
	}
	// First-run setup can finish before the agent process starts. In that case
	// change its launch model directly: the following warm-up is then the first
	// turn and cannot race a separate /model turn. A running session must use
	// the normal command so the agent confirms its live switch.
	if c, ok := m.client.(interface{ SetModelBeforeStart(string) bool }); ok && c.SetModelBeforeStart(model) {
		m.modelDisplay = model
		m.modelBackend = "lemonade"
		m.modelRemote = false
		m.claudeMode = false
		// The setup choice supersedes any model-restore request that was
		// waiting behind the first-boot gate; otherwise releaseAfterSetupGate
		// could restore the old model immediately after this selection.
		m.startup = startupModel{}
		m.rememberModel(savedChoice{provider: "local", model: model})
		return m, nil
	}
	next, cmd := m.submit(modelCommandPrefix + " " + model)
	return next.(ChatModel), cmd
}
