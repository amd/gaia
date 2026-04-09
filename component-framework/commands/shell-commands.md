---
template_id: shell-commands
template_type: commands
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Shell command patterns for file operations, process management, and system information
schema_version: "1.0"
---

# Shell Command Patterns

## Purpose

This template provides reusable shell command patterns for common operations including file management, process control, and system introspection.

## File Operations

### Copy Files/Directories

```bash
# Pattern: Copy file
command_template: |
  cp {{FLAGS}} {{SOURCE}} {{DESTINATION}}
example: |
  cp -r src/ backup/
variables:
  - SOURCE: Source file or directory path
  - DESTINATION: Destination path
  - FLAGS: -r (recursive), -v (verbose), -u (update)
```

### Move/Rename Files

```bash
# Pattern: Move or rename file
command_template: |
  mv {{FLAGS}} {{SOURCE}} {{DESTINATION}}
example: |
  mv old_name.txt new_name.txt
variables:
  - SOURCE: Current file path
  - DESTINATION: New path or name
  - FLAGS: -v (verbose), -n (no-clobber)
```

### Delete Files/Directories

```bash
# Pattern: Delete file or directory
command_template: |
  rm {{FLAGS}} {{PATH}}
example: |
  rm -rf build/
variables:
  - PATH: File or directory to delete
  - FLAGS: -r (recursive), -f (force), -i (interactive)
warning: "Use with caution - irreversible operation"
```

### Find Files

```bash
# Pattern: Find files by name pattern
command_template: |
  find {{SEARCH_PATH}} -name "{{PATTERN}}" {{OPTIONS}}
example: |
  find . -name "*.py" -type f
variables:
  - SEARCH_PATH: Directory to search
  - PATTERN: File name pattern (glob)
  - OPTIONS: -type f (files), -type d (dirs), -mtime (modified time)
```

### List Files

```bash
# Pattern: List directory contents
command_template: |
  ls {{FLAGS}} {{PATH}}
example: |
  ls -la src/
variables:
  - PATH: Directory to list
  - FLAGS: -l (long), -a (all), -h (human-readable)
```

## Process Management

### List Processes

```bash
# Pattern: List running processes
command_template: |
  ps {{FLAGS}} {{FILTERS}}
example: |
  ps aux | grep python
variables:
  - FLAGS: aux (all processes), -ef (full format)
  - FILTERS: grep pattern to filter results
```

### Kill Process

```bash
# Pattern: Terminate a process
command_template: |
  kill {{FLAGS}} {{PID}}
example: |
  kill -15 12345
variables:
  - PID: Process ID to terminate
  - FLAGS: -15 (SIGTERM), -9 (SIGKILL)
```

### Start Background Process

```bash
# Pattern: Run process in background
command_template: |
  {{COMMAND}} &
example: |
  python server.py &
variables:
  - COMMAND: Command to run in background
```

### Check Process Status

```bash
# Pattern: Check if process is running
command_template: |
  pgrep -f "{{PATTERN}}"
example: |
  pgrep -f "lemonade-server"
variables:
  - PATTERN: Process name or command pattern
```

## System Information

### Disk Usage

```bash
# Pattern: Check disk space
command_template: |
  df {{FLAGS}} {{PATH}}
example: |
  df -h /
variables:
  - PATH: Path to check
  - FLAGS: -h (human-readable), -i (inodes)
```

### Directory Size

```bash
# Pattern: Check directory size
command_template: |
  du {{FLAGS}} {{PATH}}
example: |
  du -sh node_modules/
variables:
  - PATH: Directory to measure
  - FLAGS: -s (summary), -h (human-readable), -c (total)
```

### Memory Usage

```bash
# Pattern: Check memory usage
command_template: |
  free {{FLAGS}}
example: |
  free -h
variables:
  - FLAGS: -h (human-readable), -m (megabytes)
```

### System Info

```bash
# Pattern: Get system information
command_template: |
  uname {{FLAGS}}
example: |
  uname -a
variables:
  - FLAGS: -a (all), -r (release), -s (system name)
```

## Text Processing

### Search Text in Files

```bash
# Pattern: Grep for text pattern
command_template: |
  grep {{FLAGS}} "{{PATTERN}}" {{FILES}}
example: |
  grep -r "TODO" src/
variables:
  - PATTERN: Text pattern to search
  - FILES: Files or directories to search
  - FLAGS: -r (recursive), -i (case-insensitive), -n (line numbers)
```

### Count Lines/Words

```bash
# Pattern: Count lines, words, characters
command_template: |
  wc {{FLAGS}} {{FILES}}
example: |
  wc -l *.py
variables:
  - FILES: Files to count
  - FLAGS: -l (lines), -w (words), -c (characters)
```

### View File Content

```bash
# Pattern: View file with pagination
command_template: |
  less {{FLAGS}} {{FILE}}
example: |
  less large_log.txt
variables:
  - FILE: File to view
  - FLAGS: -N (line numbers), -S (chop long lines)
```

## Network Commands

### Check Network Connectivity

```bash
# Pattern: Test network connection
command_template: |
  ping {{FLAGS}} {{HOST}}
example: |
  ping -c 4 google.com
variables:
  - HOST: Host to ping
  - FLAGS: -c (count), -i (interval)
```

### Check Port Connectivity

```bash
# Pattern: Test port connectivity
command_template: |
  nc {{FLAGS}} {{HOST}} {{PORT}}
example: |
  nc -zv localhost 8080
variables:
  - HOST: Host to check
  - PORT: Port number
  - FLAGS: -z (scan), -v (verbose)
```

## Related Components

- [[component-framework/commands/git-commands.md]] - For Git operations
- [[component-framework/commands/build-commands.md]] - For build operations
- [[component-framework/commands/test-commands.md]] - For test execution
