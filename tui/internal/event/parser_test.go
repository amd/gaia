package event

import (
	"encoding/json"
	"testing"
)

func TestParseStepEvent(t *testing.T) {
	line := []byte(`{"type":"step","step":1,"total":10,"status":"started"}`)
	e, err := ParseEvent(line)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	step, ok := e.(StepEvent)
	if !ok {
		t.Fatalf("expected StepEvent, got %T", e)
	}
	if step.Step != 1 || step.Total != 10 || step.Status != "started" {
		t.Errorf("unexpected values: %+v", step)
	}
}

func TestParseThinkingEvent(t *testing.T) {
	line := []byte(`{"type":"thinking","content":"I need to check the system logs."}`)
	e, err := ParseEvent(line)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	thinking, ok := e.(ThinkingEvent)
	if !ok {
		t.Fatalf("expected ThinkingEvent, got %T", e)
	}
	if thinking.Content != "I need to check the system logs." {
		t.Errorf("unexpected content: %q", thinking.Content)
	}
}

func TestParseStatusEvent(t *testing.T) {
	line := []byte(`{"type":"status","status":"working","message":"Analyzing files"}`)
	e, err := ParseEvent(line)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	status, ok := e.(StatusEvent)
	if !ok {
		t.Fatalf("expected StatusEvent, got %T", e)
	}
	if status.Status != "working" || status.Message != "Analyzing files" {
		t.Errorf("unexpected values: %+v", status)
	}
}

func TestParseStatusCompleteEvent(t *testing.T) {
	line := []byte(`{"type":"status","status":"complete","steps":3,"total":10}`)
	e, err := ParseEvent(line)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	status, ok := e.(StatusEvent)
	if !ok {
		t.Fatalf("expected StatusEvent, got %T", e)
	}
	if status.Status != "complete" || status.Steps != 3 || status.Total != 10 {
		t.Errorf("unexpected values: %+v", status)
	}
}

func TestParseToolStartEvent(t *testing.T) {
	line := []byte(`{"type":"tool_start","tool":"bash_execute"}`)
	e, err := ParseEvent(line)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	ts, ok := e.(ToolStartEvent)
	if !ok {
		t.Fatalf("expected ToolStartEvent, got %T", e)
	}
	if ts.Tool != "bash_execute" {
		t.Errorf("unexpected tool: %q", ts.Tool)
	}
}

func TestParseToolArgsEvent(t *testing.T) {
	line := []byte(`{"type":"tool_args","tool":"bash_execute","args":{"command":"ls -la /tmp"}}`)
	e, err := ParseEvent(line)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	ta, ok := e.(ToolArgsEvent)
	if !ok {
		t.Fatalf("expected ToolArgsEvent, got %T", e)
	}
	if ta.Tool != "bash_execute" {
		t.Errorf("unexpected tool: %q", ta.Tool)
	}
	var args map[string]string
	if err := json.Unmarshal(ta.Args, &args); err != nil {
		t.Fatalf("failed to parse args: %v", err)
	}
	if args["command"] != "ls -la /tmp" {
		t.Errorf("unexpected command: %q", args["command"])
	}
}

func TestParseToolResultEvent(t *testing.T) {
	line := []byte(`{"type":"tool_result","title":"bash_execute","success":true,"command_output":{"stdout":"file1.txt\nfile2.txt"},"summary":"Listed 2 files","result_data":{"status":"success","exit_code":0}}`)
	e, err := ParseEvent(line)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	tr, ok := e.(ToolResultEvent)
	if !ok {
		t.Fatalf("expected ToolResultEvent, got %T", e)
	}
	if tr.Title != "bash_execute" || !tr.Success || tr.Summary != "Listed 2 files" {
		t.Errorf("unexpected values: %+v", tr)
	}
}

func TestParseToolEndEvent(t *testing.T) {
	line := []byte(`{"type":"tool_end","success":true}`)
	e, err := ParseEvent(line)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	te, ok := e.(ToolEndEvent)
	if !ok {
		t.Fatalf("expected ToolEndEvent, got %T", e)
	}
	if !te.Success {
		t.Errorf("expected success=true")
	}
}

func TestParseAnswerEvent(t *testing.T) {
	line := []byte(`{"type":"answer","content":"Here are the files in /tmp.","steps":2,"tools_used":1}`)
	e, err := ParseEvent(line)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	a, ok := e.(AnswerEvent)
	if !ok {
		t.Fatalf("expected AnswerEvent, got %T", e)
	}
	if a.Content != "Here are the files in /tmp." || a.Steps != 2 || a.ToolsUsed != 1 {
		t.Errorf("unexpected values: %+v", a)
	}
}

func TestParseAgentErrorEvent(t *testing.T) {
	line := []byte(`{"type":"agent_error","content":"Model load failed"}`)
	e, err := ParseEvent(line)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	ae, ok := e.(AgentErrorEvent)
	if !ok {
		t.Fatalf("expected AgentErrorEvent, got %T", e)
	}
	if ae.Content != "Model load failed" {
		t.Errorf("unexpected content: %q", ae.Content)
	}
}

func TestParsePlanEvent(t *testing.T) {
	line := []byte(`{"type":"plan","steps":[{"tool":"bash_execute","tool_args":{"command":"ls"}}],"current_step":0}`)
	e, err := ParseEvent(line)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	p, ok := e.(PlanEvent)
	if !ok {
		t.Fatalf("expected PlanEvent, got %T", e)
	}
	if p.CurrentStep != 0 {
		t.Errorf("unexpected current_step: %d", p.CurrentStep)
	}
}

func TestParseDoneEvent(t *testing.T) {
	line := []byte(`{"type":"done","message_id":"abc123","content":"completed"}`)
	e, err := ParseEvent(line)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	d, ok := e.(DoneEvent)
	if !ok {
		t.Fatalf("expected DoneEvent, got %T", e)
	}
	if d.MessageID != "abc123" {
		t.Errorf("unexpected message_id: %q", d.MessageID)
	}
}

func TestParseChunkEvent(t *testing.T) {
	line := []byte(`{"type":"chunk","content":"Hello "}`)
	e, err := ParseEvent(line)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	c, ok := e.(ChunkEvent)
	if !ok {
		t.Fatalf("expected ChunkEvent, got %T", e)
	}
	if c.Content != "Hello " {
		t.Errorf("unexpected content: %q", c.Content)
	}
}

func TestParseErrorEvent(t *testing.T) {
	line := []byte(`{"type":"error","content":"connection timeout"}`)
	e, err := ParseEvent(line)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	ev, ok := e.(ErrorEvent)
	if !ok {
		t.Fatalf("expected ErrorEvent, got %T", e)
	}
	if ev.Content != "connection timeout" {
		t.Errorf("unexpected content: %q", ev.Content)
	}
}

func TestParseInvalidJSON(t *testing.T) {
	_, err := ParseEvent([]byte(`not json`))
	if err == nil {
		t.Fatal("expected error for invalid JSON")
	}
}

func TestParseUnknownType(t *testing.T) {
	_, err := ParseEvent([]byte(`{"type":"unknown_event"}`))
	if err == nil {
		t.Fatal("expected error for unknown type")
	}
}
