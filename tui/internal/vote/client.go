package vote

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

const voteEndpoint = "https://amd-gaia.ai/api/votes"

type VoteRequest struct {
	AgentID string `json:"agent_id"`
}

type VoteResponse struct {
	Success bool `json:"success"`
	Votes   int  `json:"votes"`
}

// VoteResultMsg is sent back to the Bubble Tea model after the HTTP call.
type VoteResultMsg struct {
	AgentID string
	Votes   int
	Err     error
}

// CastVote returns a tea.Cmd that performs the HTTP POST in a goroutine.
func CastVote(agentID string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()

		body, _ := json.Marshal(VoteRequest{AgentID: agentID})
		req, err := http.NewRequestWithContext(ctx, http.MethodPost, voteEndpoint, bytes.NewReader(body))
		if err != nil {
			return VoteResultMsg{AgentID: agentID, Err: err}
		}
		req.Header.Set("Content-Type", "application/json")

		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			// Network error — still increment locally
			return VoteResultMsg{AgentID: agentID, Votes: -1, Err: err}
		}
		defer resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			return VoteResultMsg{AgentID: agentID, Err: fmt.Errorf("vote API returned %d", resp.StatusCode)}
		}

		var vr VoteResponse
		json.NewDecoder(resp.Body).Decode(&vr)
		return VoteResultMsg{AgentID: agentID, Votes: vr.Votes}
	}
}
