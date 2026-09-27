// Command hyperlite is the workstation client of a Hyperlite server: it signs in
// through the web interface (device authorization), then opens tunnels to the
// virtual machines over the server's HTTPS port, so that the workstation's own SSH
// client or remote desktop connects to VMs that are not directly reachable.
package main

import (
	"errors"
	"fmt"
	"os"
	"strings"
)

// version is set at build time (-ldflags "-X main.version=...").
var version = "dev"

const usage = `hyperlite: connect to Hyperlite virtual machines from this workstation.

Usage:
  hyperlite login <server-url>        Sign in (approved in the web interface)
  hyperlite logout [--server URL]     Sign out and revoke this workstation's token
  hyperlite status                    Show the servers this workstation is signed in to
  hyperlite vms [--server URL]        List the virtual machines
  hyperlite ssh <vm> [ssh options]    Open an SSH session (user@vm to choose the user)
  hyperlite rdp <vm>                  Open a remote desktop session (Windows VMs)
  hyperlite tunnel <vm> <port> [--listen ADDR:PORT | --stdio]
                                      Forward a VM port (default: a free local port)
  hyperlite setup                     Register hyperlite:// links ("Open in a terminal"
                                      buttons of the web interface)
  hyperlite version

Options:
  --server URL   Server to use when this workstation is signed in to several
  --yes          Trust the server certificate without asking (login only)

Environment:
  HYPERLITE_SERVER, HYPERLITE_TOKEN   Non-interactive use (scripts, CI)
  HYPERLITE_CONFIG                    Configuration file to use
  HTTPS_PROXY, NO_PROXY               Corporate proxy
`

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "hyperlite:", err)
		var exit exitCode
		if errors.As(err, &exit) {
			os.Exit(int(exit))
		}
		os.Exit(1)
	}
}

type exitCode int

func (e exitCode) Error() string { return fmt.Sprintf("exit status %d", int(e)) }

// flags pulls the global options out of args, whatever their position.
type flags struct {
	server string
	yes    bool
	listen string
	stdio  bool
	rest   []string
}

func parseFlags(args []string, passthrough bool) (flags, error) {
	var f flags
	for i := 0; i < len(args); i++ {
		a := args[i]
		switch {
		case a == "--server" || a == "--listen":
			if i+1 >= len(args) {
				return f, fmt.Errorf("%s needs a value", a)
			}
			if a == "--server" {
				f.server = args[i+1]
			} else {
				f.listen = args[i+1]
			}
			i++
		case strings.HasPrefix(a, "--server="):
			f.server = strings.TrimPrefix(a, "--server=")
		case strings.HasPrefix(a, "--listen="):
			f.listen = strings.TrimPrefix(a, "--listen=")
		case a == "--yes" || a == "-y":
			f.yes = true
		case a == "--stdio":
			f.stdio = true
		case a == "--":
			f.rest = append(f.rest, args[i+1:]...)
			return f, nil
		case strings.HasPrefix(a, "--") && !passthrough:
			return f, fmt.Errorf("unknown option %s", a)
		default:
			f.rest = append(f.rest, a)
		}
	}
	return f, nil
}

func run(args []string) error {
	if len(args) == 0 || args[0] == "help" || args[0] == "--help" || args[0] == "-h" {
		fmt.Print(usage)
		return nil
	}
	cmd, args := args[0], args[1:]
	// ssh passes its unknown options through to OpenSSH.
	f, err := parseFlags(args, cmd == "ssh")
	if err != nil {
		return err
	}
	switch cmd {
	case "version", "--version":
		fmt.Println("hyperlite", version)
		return nil
	case "login":
		if len(f.rest) != 1 {
			return errors.New("usage: hyperlite login <server-url>")
		}
		return cmdLogin(f.rest[0], f.yes)
	case "logout":
		return cmdLogout(f.server)
	case "status":
		return cmdStatus()
	case "vms":
		return cmdVMs(f.server)
	case "ssh":
		if len(f.rest) < 1 {
			return errors.New("usage: hyperlite ssh <vm> [ssh options]")
		}
		return cmdSSH(f.server, f.rest[0], f.rest[1:])
	case "rdp":
		if len(f.rest) != 1 {
			return errors.New("usage: hyperlite rdp <vm>")
		}
		return cmdRDP(f.server, f.rest[0])
	case "tunnel":
		if len(f.rest) != 2 {
			return errors.New("usage: hyperlite tunnel <vm> <port> [--listen ADDR:PORT | --stdio]")
		}
		return cmdTunnel(f.server, f.rest[0], f.rest[1], f.listen, f.stdio)
	case "setup":
		return cmdSetup()
	case "open":
		if len(f.rest) != 1 {
			return errors.New("usage: hyperlite open <hyperlite:// link>")
		}
		return cmdOpen(f.rest[0])
	}
	return fmt.Errorf("unknown command %q (see hyperlite help)", cmd)
}
