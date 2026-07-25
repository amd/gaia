//go:build windows

package daemon

import "golang.org/x/sys/windows"

// stillActive is the exit code Windows reports for a process that has not exited.
const stillActive = 259

// PIDAlive reports whether pid refers to a running process.
//
// A handle can still be opened briefly for an exited process, so the exit code
// is checked too; the status probe remains the real trust check.
func PIDAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if err != nil {
		return false
	}
	defer windows.CloseHandle(h)
	var code uint32
	if err := windows.GetExitCodeProcess(h, &code); err != nil {
		// The pid is taken but we cannot read its state — treat it as alive and
		// let the status probe decide.
		return true
	}
	return code == stillActive
}
