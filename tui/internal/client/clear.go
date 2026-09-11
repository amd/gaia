package client

import (
	"context"
	"fmt"

	"github.com/amd/gaia/tui/internal/event"
)

// ConversationResetter clears agent-owned context with an acknowledged round trip.
// Calls must be serialized with Send.
type ConversationResetter interface {
	ClearConversation(context.Context) error
}

const clearConversationQuery = "\x00gaia:clear_conversation\x00"

func (s *SubprocessClient) ClearConversation(ctx context.Context) error {
	if !s.canonical {
		return fmt.Errorf("this subprocess does not support clearing conversation context")
	}
	s.mu.Lock()
	started := s.started
	s.mu.Unlock()
	if !started {
		return nil
	}
	ch, err := s.Send(ctx, clearConversationQuery)
	if err != nil {
		return err
	}
	acknowledged := false
	for evt := range ch {
		switch e := evt.(type) {
		case event.CanonicalFinalEvent:
			acknowledged = e.Answer == "conversation_cleared"
		case event.CanonicalErrorEvent:
			return fmt.Errorf("%s", e.Detail)
		case event.AgentErrorEvent:
			return fmt.Errorf("%s", e.Content)
		}
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if !acknowledged {
		return fmt.Errorf("the agent did not acknowledge clearing its conversation; upgrade the flagship agent")
	}
	return nil
}
