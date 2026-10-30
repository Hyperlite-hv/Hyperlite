//go:build !linux

package main

import "os"

// isTerminal: hyperlite-admin runs on the nodes, which are Linux; elsewhere (a build for vet) nothing is a terminal,
// so a destructive action takes --yes.
func isTerminal(*os.File) bool { return false }
