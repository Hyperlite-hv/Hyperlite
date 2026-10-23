// Command hyperlite-admin drives a node, and through it the cluster, from the node's shell, as qm, pct and pvesh do on
// Proxmox VE (docs/design/admin-cli.md): every action is a call to the node's own API v1, so it is checked, recorded as a
// task and audited as from the dashboard.
package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"github.com/Hyperlite-hv/Hyperlite/cli/hyperlite"
)

// version is set at build time (-ldflags "-X main.version=...").
var version = "dev"

const usage = `hyperlite-admin: administer this Hyperlite node and its cluster through API v1.

Usage:
  hyperlite-admin get    <path> [items]   Any route of API v1, as pvesh does on Proxmox VE
  hyperlite-admin post   <path> [items]   (the path without /api/v1, e.g. /vms/web1/start)
  hyperlite-admin put    <path> [items]
  hyperlite-admin patch  <path> [items]
  hyperlite-admin delete <path> [items]
  hyperlite-admin wait   <task-id>        Wait for a task and print its log
  hyperlite-admin version

Items:
  key==value     a query parameter
  key=value      a query parameter of get and delete, a text field of the body otherwise
  key:=json      a field of the body given as JSON (a number, true, a list...)

Options:
  --node NAME        The member that does the work (the API's ?node=); also where its task is followed
  --json             The API's answer as it is, for scripts
  --data JSON        The whole body (or @FILE, or - for standard input), instead of key=value items
  --no-wait          Print the task of a long operation instead of waiting for it
  --timeout DUR      Stop waiting after DUR (e.g. 90s, 10m); the task goes on
  --server URL       The API to call (default https://127.0.0.1:8000, this node)
  --cert FILE        The certificate to pin (default /etc/hyperlite/local/hyperlite.crt)
  --token-file FILE  Act as the account of this API token instead of root@<node>

Environment:
  HYPERLITE_TOKEN    An API token, as --token-file
  HYPERLITE_DAEMON_SOCKET
                     hyperlite-daemon's socket, where root's ticket comes from

Exit codes: 0 done, 1 refused or failed by the API, 2 wrong usage, 3 the task failed, 4 still running at --timeout.
`

const (
	exitAPI     = 1
	exitUsage   = 2
	exitTask    = 3
	exitTimeout = 4
)

// exitError carries the exit code of a failure whose message was already shaped.
type exitError struct {
	code int
	err  error
}

func (e *exitError) Error() string { return e.err.Error() }
func (e *exitError) Unwrap() error { return e.err }

func usageError(format string, a ...any) error {
	return &exitError{exitUsage, fmt.Errorf(format, a...)}
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr))
}

// run is the whole command, its exit code returned: what the tests call.
func run(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
	err := dispatch(args, stdin, stdout, stderr)
	if err == nil {
		return 0
	}
	code := exitAPI
	var ee *exitError
	if errors.As(err, &ee) {
		code = ee.code
	}
	var api *hyperlite.APIError
	if errors.As(err, &api) && api.Code != "" {
		fmt.Fprintf(stderr, "hyperlite-admin: %s (%s)\n", err, api.Code)
	} else {
		fmt.Fprintln(stderr, "hyperlite-admin:", err)
	}
	if code == exitUsage {
		fmt.Fprintln(stderr, "Run hyperlite-admin help for the usage.")
	}
	return code
}

type options struct {
	node      string
	json      bool
	data      string
	dataSet   bool
	noWait    bool
	timeout   time.Duration
	server    string
	cert      string
	tokenFile string
	rest      []string
}

// parseOptions pulls the options out of args, whatever their position; "--" ends them.
func parseOptions(args []string) (options, error) {
	o := options{server: defaultServer, cert: defaultCert}
	valued := map[string]*string{
		"--node": &o.node, "--data": &o.data, "--server": &o.server, "--cert": &o.cert, "--token-file": &o.tokenFile,
	}
	var timeout string
	valued["--timeout"] = &timeout
	for i := 0; i < len(args); i++ {
		a := args[i]
		name, value, hasValue := strings.Cut(a, "=")
		if target, ok := valued[name]; ok && strings.HasPrefix(a, "--") {
			if !hasValue {
				if i+1 >= len(args) {
					return o, usageError("%s needs a value", name)
				}
				i++
				value = args[i]
			}
			*target = value
			if name == "--data" {
				o.dataSet = true
			}
			continue
		}
		switch {
		case a == "--json":
			o.json = true
		case a == "--no-wait":
			o.noWait = true
		case a == "--":
			o.rest = append(o.rest, args[i+1:]...)
			i = len(args)
		case strings.HasPrefix(a, "--"):
			return o, usageError("unknown option %s", a)
		default:
			o.rest = append(o.rest, a)
		}
	}
	if timeout != "" {
		d, err := time.ParseDuration(timeout)
		if err != nil || d <= 0 {
			return o, usageError("--timeout takes a duration such as 90s or 10m, not %q", timeout)
		}
		o.timeout = d
	}
	return o, nil
}

func dispatch(args []string, stdin io.Reader, stdout, stderr io.Writer) error {
	if len(args) == 0 || args[0] == "help" || args[0] == "--help" || args[0] == "-h" {
		fmt.Fprint(stdout, usage)
		return nil
	}
	cmd := args[0]
	if cmd == "version" || cmd == "--version" {
		fmt.Fprintln(stdout, "hyperlite-admin", version)
		return nil
	}
	o, err := parseOptions(args[1:])
	if err != nil {
		return err
	}
	ctx := context.Background()
	if o.timeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, o.timeout)
		defer cancel()
	}
	switch cmd {
	case "get", "post", "put", "patch", "delete":
		req, err := buildRequest(strings.ToUpper(cmd), o, stdin)
		if err != nil {
			return err
		}
		s, err := newSession(o)
		if err != nil {
			return err
		}
		return s.do(ctx, req, o, stdout, stderr)
	case "wait":
		if len(o.rest) != 1 {
			return usageError("wait takes one task id")
		}
		s, err := newSession(o)
		if err != nil {
			return err
		}
		return s.wait(ctx, o.rest[0], o.node, o, stdout, stderr)
	}
	return usageError("unknown command %q", cmd)
}
