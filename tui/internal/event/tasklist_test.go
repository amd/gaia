package event

import "testing"

func TestParseCanonicalTaskList(t *testing.T) {
	got := ParseCanonicalEvent([]byte(`{"type":"task_list","items":[` +
		`{"id":1,"content":"Read the config","status":"completed","started_at":1.5,"completed_at":3},` +
		`{"id":2,"content":"Fix the bug","status":"in_progress","started_at":3,"completed_at":null}],` +
		`"source":"agent","current_action":null,"action_started_at":null,"updated_at":3}`))

	e, ok := got.(CanonicalTaskListEvent)
	if !ok {
		t.Fatalf("parsed as %T, want CanonicalTaskListEvent", got)
	}
	if len(e.Items) != 2 || e.Items[1].Status != TaskInProgress || e.Items[0].CompletedAt != 3 {
		t.Errorf("items decoded wrong: %+v", e.Items)
	}
	if e.CurrentAction != "" || e.ActionStartedAt != 0 {
		t.Errorf("a null action must decode as none: %q %v", e.CurrentAction, e.ActionStartedAt)
	}
}

func TestMalformedTaskListIsVisible(t *testing.T) {
	got := ParseCanonicalEvent([]byte(`{"type":"task_list","items":"oops"}`))
	if _, ok := got.(CanonicalMalformedEvent); !ok {
		t.Fatalf("parsed as %T, want CanonicalMalformedEvent", got)
	}
}
