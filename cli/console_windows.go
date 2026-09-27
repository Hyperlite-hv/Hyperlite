package main

import (
	"os/exec"
	"syscall"
)

// newConsole starts the command in a window of its own.
func newConsole(cmd *exec.Cmd) {
	const createNewConsole = 0x00000010
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: createNewConsole}
}
