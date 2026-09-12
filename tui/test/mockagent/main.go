// Package main implements a mock GAIA agent for TUI testing.
// It reads queries from stdin and emits realistic JSONL events to stdout,
// simulating an agent session without requiring a real LLM backend.
//
// It speaks the host's control protocol too — {"gaia_control": ...} lines,
// read on their own goroutine so they land mid-turn, the way the real agent's
// stdin pump does.
//
// With MOCKAGENT_MULTIPROCESS=1 it also reproduces the shipped agent's process
// shape. The release build is a PyInstaller one-file binary: the process the
// host starts is a bootloader, and its CHILD runs the agent while holding both
// pipe ends. Killing only the process the host started leaves the real agent
// running, and no single-process double can show that.
//
// Environment knobs (all optional):
//
//	MOCKAGENT_MULTIPROCESS=1   run as bootloader + child
//	MOCKAGENT_PIDFILE=<path>   the process serving turns writes its pid here
//	                           (and the bootloader its own, to <path>.boot)
//	MOCKAGENT_SIDE_EFFECT=<p>  "slow tool" writes this file only if it completes
//	MOCKAGENT_TOOL_MS=<n>      how long "slow tool" runs (default 5000)
//
// Queries: "slow tool" runs a long tool call that honours a cancel, "stubborn
// slow tool" one that ignores it, and "report bypass" answers
// "bypass=<mode> pid=<pid> turn=<n>".
package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"math/rand"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync/atomic"
	"time"
)

// Must match client.controlKey / client.queryKey on the host side.
const (
	controlKey = "gaia_control"
	queryKey   = "gaia_query"
)

var (
	bypass      atomic.Bool
	turnRunning atomic.Bool
	cancelled   atomic.Bool
	turns       int // touched only by the turn loop
)

func emit(v map[string]interface{}) {
	b, err := json.Marshal(v)
	if err != nil {
		return
	}
	fmt.Println(string(b))
}

func delay(minMs, maxMs int) {
	ms := minMs + rand.Intn(maxMs-minMs+1)
	time.Sleep(time.Duration(ms) * time.Millisecond)
}

// toolScenario returns a tool name, command, and result based on the query.
func toolScenario(query string) (tool, command, stdout, summary string) {
	q := strings.ToLower(query)
	switch {
	case strings.Contains(q, "file") || strings.Contains(q, "list") || strings.Contains(q, "ls"):
		return "bash_execute", "ls -la /tmp",
			"total 48\ndrwxrwxrwt 12 root root 4096 May 20 10:00 .\n-rw-r--r--  1 user user  2300 May 20 09:55 report.txt\n-rw-r--r--  1 user user  1100 May 20 09:50 data.csv\n-rw-r--r--  1 user user 45000 May 20 09:45 backup.tar.gz\ndrwxr-xr-x  2 user user  4096 May 20 09:40 logs\n-rwxr-xr-x  1 user user  8192 May 20 09:35 script.sh",
			"Listed 5 files and 1 directory"
	case strings.Contains(q, "search") || strings.Contains(q, "find") || strings.Contains(q, "grep"):
		return "bash_execute", fmt.Sprintf("grep -r '%s' .", query),
			"./src/main.go:42: // matching result\n./README.md:15: relevant documentation",
			"Found 2 matches"
	case strings.Contains(q, "python") || strings.Contains(q, "code") || strings.Contains(q, "write"):
		return "file_write", "write hello.py",
			"File written: hello.py (12 lines)",
			"Created hello.py"
	case strings.Contains(q, "git") || strings.Contains(q, "status"):
		return "bash_execute", "git status",
			"On branch main\nYour branch is up to date with 'origin/main'.\n\nChanges not staged for commit:\n  modified:   src/main.go\n  modified:   README.md\n\nno changes added to commit",
			"2 files modified"
	case strings.Contains(q, "install") || strings.Contains(q, "setup"):
		return "bash_execute", "pip install requests",
			"Collecting requests\n  Downloading requests-2.31.0.tar.gz (110 kB)\nInstalling collected packages: requests\nSuccessfully installed requests-2.31.0",
			"Installed requests 2.31.0"
	default:
		return "bash_execute", "echo 'hello world'",
			"hello world",
			"Command executed successfully"
	}
}

func handleQuery(query string) {
	tool, command, stdout, summary := toolScenario(query)
	totalSteps := 3

	// Step 1: Thinking
	emit(map[string]interface{}{
		"type": "step", "step": 1, "total": totalSteps, "status": "running",
	})
	delay(100, 200)

	emit(map[string]interface{}{
		"type":    "thinking",
		"content": fmt.Sprintf("Let me analyze the request: \"%s\"", query),
	})
	delay(200, 400)

	emit(map[string]interface{}{
		"type": "status", "status": "working", "message": "Analyzing request",
	})
	delay(150, 300)

	// Step 2: Tool execution
	emit(map[string]interface{}{
		"type": "step", "step": 2, "total": totalSteps, "status": "running",
	})
	delay(50, 100)

	emit(map[string]interface{}{
		"type": "tool_start", "tool": tool, "detail": command,
	})
	delay(100, 200)

	emit(map[string]interface{}{
		"type": "tool_args", "tool": tool,
		"args": map[string]string{"command": command},
	})
	delay(300, 600)

	emit(map[string]interface{}{
		"type": "tool_end", "success": true,
	})
	delay(50, 100)

	if strings.Contains(strings.ToLower(query), "fail the tool") {
		// A tool that fails with its own actionable remedy — the shape that used
		// to reach the user only as the model's paraphrase of it.
		emit(map[string]interface{}{
			"type": "tool_result", "title": tool, "success": false,
			"summary": "the tool could not run",
			"result_data": map[string]interface{}{
				"ok":     false,
				"status": "error",
				"code":   "CONNECTOR_ERROR",
				"error": "no forwarded 'google' credential is available to the email sidecar.\n" +
					"Connect and grant it in one command — no Agent UI required:\n" +
					"`gaia connectors connect google --scopes gmail.readonly --grant-agent installed:email`",
			},
		})
		delay(50, 100)
		emit(map[string]interface{}{
			"type": "answer", "content": "I could not reach your mailbox.", "steps": 3, "tools_used": 1,
		})
		return
	}

	emit(map[string]interface{}{
		"type": "tool_result", "title": tool, "success": true,
		"command_output": map[string]string{"stdout": stdout},
		"summary":        summary,
	})
	delay(100, 200)

	// Step 3: Generate answer
	emit(map[string]interface{}{
		"type": "step", "step": 3, "total": totalSteps, "status": "running",
	})
	delay(200, 400)

	answer := fmt.Sprintf("Based on your request \"%s\", here's what I found:\n\n"+
		"## Results\n\n"+
		"I executed `%s` and got the following output:\n\n"+
		"```\n%s\n```\n\n"+
		"**Summary:** %s\n\n"+
		"Let me know if you need anything else!",
		query, command, stdout, summary)

	emit(map[string]interface{}{
		"type": "answer", "content": answer,
		"steps": totalSteps, "tools_used": 1,
	})
}

// slowTool stands in for a long gated tool call — a recursive delete. It
// leaves its side effect only if it runs to completion, so a test can prove a
// cancelled tool call never finished.
//
// honourCancel=false models a call the agent cannot interrupt — a subprocess
// already in flight, or an agent too old to know the cancel verb — which only
// killing the process stops.
func slowTool(honourCancel bool) {
	detail := "rm -rf ./build"
	if !honourCancel {
		detail = "rm -rf ./dist"
	}
	emit(map[string]interface{}{
		"type": "tool_start", "tool": "bash_execute", "detail": detail,
	})
	emit(map[string]interface{}{
		"type": "tool_args", "tool": "bash_execute",
		"args": map[string]string{"command": detail},
	})
	const slice = 50 * time.Millisecond
	for elapsed := time.Duration(0); elapsed < toolDuration(); elapsed += slice {
		if honourCancel && cancelled.Load() {
			emit(map[string]interface{}{
				"type": "answer", "content": "stopped", "steps": 1, "tools_used": 0,
			})
			return
		}
		time.Sleep(slice)
	}
	if path := os.Getenv("MOCKAGENT_SIDE_EFFECT"); path != "" {
		if err := os.WriteFile(path, []byte("the tool ran to completion\n"), 0o644); err != nil {
			fmt.Fprintf(os.Stderr, "mockagent: could not write the side effect %s: %v\n", path, err)
		}
	}
	emit(map[string]interface{}{"type": "tool_end", "success": true})
	emit(map[string]interface{}{
		"type": "answer", "content": "tool finished", "steps": 1, "tools_used": 1,
	})
}

func toolDuration() time.Duration {
	raw := os.Getenv("MOCKAGENT_TOOL_MS")
	if raw == "" {
		return 5 * time.Second
	}
	ms, err := strconv.Atoi(raw)
	if err != nil || ms < 0 {
		fmt.Fprintf(os.Stderr, "mockagent: MOCKAGENT_TOOL_MS=%q is not a non-negative integer\n", raw)
		os.Exit(2)
	}
	return time.Duration(ms) * time.Millisecond
}

func runTurn(query string) {
	turns++
	cancelled.Store(false)
	turnRunning.Store(true)
	defer turnRunning.Store(false)

	switch q := strings.ToLower(strings.TrimSpace(query)); {
	case strings.Contains(q, "stubborn slow tool"):
		slowTool(false)
	case strings.Contains(q, "slow tool"):
		slowTool(true)
	case q == "report bypass":
		// pid and turn make every answer unique, so a test can tell this turn's
		// answer from an earlier one still on screen.
		emit(map[string]interface{}{
			"type":       "answer",
			"content":    fmt.Sprintf("bypass=%t pid=%d turn=%d", bypass.Load(), os.Getpid(), turns),
			"steps":      1,
			"tools_used": 0,
		})
	default:
		handleQuery(query)
	}
}

// handleControl applies a control line and reports whether it was one. A line
// that merely looks like JSON is still a query.
func handleControl(line string) bool {
	if !strings.HasPrefix(line, "{") {
		return false
	}
	var msg map[string]interface{}
	if json.Unmarshal([]byte(line), &msg) != nil {
		return false
	}
	verb, ok := msg[controlKey]
	if !ok {
		return false
	}
	switch verb {
	case "cancel":
		if turnRunning.Load() {
			cancelled.Store(true)
		}
	case "bypass":
		enabled, _ := msg["enabled"].(bool)
		bypass.Store(enabled)
	default:
		fmt.Fprintf(os.Stderr, "mockagent: ignored control verb %v\n", verb)
	}
	return true
}

func unwrapQuery(line string) string {
	if strings.HasPrefix(line, "{") {
		var msg map[string]interface{}
		if json.Unmarshal([]byte(line), &msg) == nil {
			if q, ok := msg[queryKey].(string); ok {
				return q
			}
		}
	}
	return line
}

// pumpStdin reads stdin on its own goroutine so a control line reaches the
// agent while a turn is running — the only moment a cancel means anything.
func pumpStdin(queries chan<- string) {
	defer close(queries)
	scanner := bufio.NewScanner(os.Stdin)
	// 1MB buffer for large queries
	scanner.Buffer(make([]byte, 1024*1024), 1024*1024)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || handleControl(line) {
			continue
		}
		queries <- unwrapQuery(line)
	}
	if err := scanner.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "mockagent: stdin read error: %v\n", err)
		os.Exit(1)
	}
}

// runBootloader re-executes this binary as the agent, handing it this
// process's own stdin/stdout/stderr handles, and waits for it — the one-file
// bootloader shape. *os.File streams are passed through, not copied, so the
// child really does hold the pipe ends.
func runBootloader() int {
	self, err := os.Executable()
	if err != nil {
		fmt.Fprintf(os.Stderr, "mockagent: cannot locate its own binary to re-execute: %v\n", err)
		return 1
	}
	if path := os.Getenv("MOCKAGENT_PIDFILE"); path != "" {
		if err := writePidFile(path + ".boot"); err != nil {
			fmt.Fprintf(os.Stderr, "mockagent: %v\n", err)
			return 1
		}
	}
	cmd := exec.Command(self, os.Args[1:]...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	cmd.Env = append(os.Environ(), "MOCKAGENT_ROLE=child")
	if err := cmd.Run(); err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			return exitErr.ExitCode()
		}
		fmt.Fprintf(os.Stderr, "mockagent: the agent child failed to run: %v\n", err)
		return 1
	}
	return 0
}

func writePidFile(path string) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, []byte(strconv.Itoa(os.Getpid())), 0o644); err != nil {
		return fmt.Errorf("could not write pid file %s: %w", tmp, err)
	}
	if err := os.Rename(tmp, path); err != nil {
		return fmt.Errorf("could not publish pid file %s: %w", path, err)
	}
	return nil
}

func main() {
	if os.Getenv("MOCKAGENT_MULTIPROCESS") == "1" && os.Getenv("MOCKAGENT_ROLE") != "child" {
		os.Exit(runBootloader())
	}

	for _, a := range os.Args[1:] {
		if a == "--bypass-permissions" {
			bypass.Store(true)
		}
	}
	if path := os.Getenv("MOCKAGENT_PIDFILE"); path != "" {
		if err := writePidFile(path); err != nil {
			fmt.Fprintf(os.Stderr, "mockagent: %v\n", err)
			os.Exit(1)
		}
	}

	queries := make(chan string, 16)
	go pumpStdin(queries)
	for q := range queries {
		runTurn(q)
	}
}
