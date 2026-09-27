//go:build !windows

package main

import "os/exec"

func newConsole(*exec.Cmd) {}
