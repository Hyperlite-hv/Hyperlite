package main

import (
	"fmt"
	"os"
	"runtime"
)

// cmdFirstRun is what a double-click on the program does: register the hyperlite://
// links for this user, then explain what comes next. Signing in happens on the first
// click on a button of the web interface.
func cmdFirstRun() error {
	err := cmdSetup()
	if err == nil {
		fmt.Println()
		fmt.Println("Hyperlite is ready on this computer.")
		fmt.Println("In the web interface, open a VM's Console tab, then From your workstation:")
		fmt.Println("the first click asks you to approve this computer, the next ones connect directly.")
	} else {
		fmt.Fprintln(os.Stderr, "hyperlite:", err)
		fmt.Println()
		fmt.Print(usage)
	}
	if runtime.GOOS == "windows" {
		// Started from the file explorer: keep the window open long enough to read it.
		fmt.Println()
		fmt.Println("Press Enter to close this window.")
		_, _ = fmt.Scanln()
	}
	return nil
}
