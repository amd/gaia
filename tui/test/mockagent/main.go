// Package main implements a mock GAIA agent for TUI testing.
// It reads queries from stdin and emits realistic JSONL events to stdout,
// simulating an agent session without requiring a real LLM backend.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"math/rand"
	"os"
	"strings"
	"time"
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

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	// 1MB buffer for large queries
	scanner.Buffer(make([]byte, 1024*1024), 1024*1024)

	for scanner.Scan() {
		query := strings.TrimSpace(scanner.Text())
		if query == "" {
			continue
		}
		handleQuery(query)
	}

	if err := scanner.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "mockagent: stdin read error: %v\n", err)
		os.Exit(1)
	}
}
