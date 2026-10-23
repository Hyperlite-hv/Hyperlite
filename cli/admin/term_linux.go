package main

import (
	"os"
	"syscall"
	"unsafe"
)

// isTerminal tells whether f is a terminal a person types into: a character device such as /dev/null is not, and a
// question asked there would be answered by nobody.
func isTerminal(f *os.File) bool {
	var t syscall.Termios
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, f.Fd(), syscall.TCGETS, uintptr(unsafe.Pointer(&t)))
	return errno == 0
}
