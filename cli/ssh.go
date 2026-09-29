package main

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"regexp"
	"runtime"
	"strings"
)

var sshUserRe = regexp.MustCompile(`^[A-Za-z0-9._][A-Za-z0-9._-]{0,31}$`)

// proxyCommand is the ssh ProxyCommand that runs this very program in --stdio mode.
// '%' is doubled because ssh expands %-tokens in ProxyCommand. OpenSSH runs it through a shell, so every part
// must be inert there: the program path is quoted, the VM name and port are validated, and the server address is
// checked again against the strict form normalizeURL produces (no quote, space, $, ; or backtick can get in).
func proxyCommand(s *server, vm string, port int) (string, error) {
	self, err := os.Executable()
	if err != nil {
		return "", err
	}
	if u, err := normalizeURL(s.URL); err != nil || u != s.URL {
		return "", fmt.Errorf("refusing server address %q in an ssh command", s.URL)
	}
	if err := checkVM(vm); err != nil {
		return "", err
	}
	quoted := `"` + strings.ReplaceAll(self, "%", "%%") + `"`
	return fmt.Sprintf("%s tunnel --stdio --server %s %s %d", quoted, strings.ReplaceAll(s.URL, "%", "%%"), vm, port), nil
}

func cmdSSH(flag, target string, extra []string) error {
	user, vm := "", target
	if i := strings.LastIndex(target, "@"); i >= 0 {
		user, vm = target[:i], target[i+1:]
	}
	if err := checkVM(vm); err != nil {
		return err
	}
	if user != "" && !sshUserRe.MatchString(user) {
		return fmt.Errorf("invalid user name %q", user)
	}
	s, err := serverFor(flag)
	if err != nil {
		return err
	}
	if user == "" {
		// The user created with the VM, as the web terminal does.
		var info vmInfo
		if err := call(s, "GET", "/vms/"+url.PathEscape(vm), nil, &info); err != nil {
			return err
		}
		if info.SSHUser != nil && sshUserRe.MatchString(*info.SSHUser) {
			user = *info.SSHUser
		}
	}
	sshBin, err := exec.LookPath("ssh")
	if err != nil {
		return errors.New("no ssh client found: install OpenSSH (on Windows: Settings > Optional features > OpenSSH Client)")
	}
	pc, err := proxyCommand(s, vm, 22)
	if err != nil {
		return err
	}
	dest := vm
	if user != "" {
		dest = user + "@" + vm
	}
	args := []string{
		"-o", "ProxyCommand=" + pc,
		// Host keys are remembered per server and VM name, not per (internal) address.
		"-o", "HostKeyAlias=" + vm + "." + hostOnly(strings.SplitN(s.URL, "://", 2)[1]) + ".hyperlite",
		"-o", "ServerAliveInterval=30",
	}
	// OpenSSH also reads options after the destination, so what the user typed after
	// the VM name (options, then a remote command) follows it unchanged.
	args = append(args, dest)
	args = append(args, extra...)
	cmd := exec.Command(sshBin, args...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	signal.Ignore(os.Interrupt) // Ctrl+C belongs to the remote shell
	if err := cmd.Run(); err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			return exitCode(exit.ExitCode())
		}
		return err
	}
	return nil
}

func cmdRDP(flag, vm string) error {
	if err := checkVM(vm); err != nil {
		return err
	}
	s, err := serverFor(flag)
	if err != nil {
		return err
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return err
	}
	addr := ln.Addr().String()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	if runtime.GOOS != "windows" {
		fmt.Printf("Remote desktop of %s available on %s: open it with your RDP client (Ctrl+C to stop).\n", vm, addr)
		return serveForward(ctx, ln, s, vm, 3389, nil)
	}
	fmt.Printf("Opening the remote desktop of %s...\n", vm)
	client := exec.Command("mstsc.exe", "/v:"+addr)
	if err := client.Start(); err != nil {
		ln.Close()
		return fmt.Errorf("cannot start the remote desktop client: %w", err)
	}
	ended := make(chan struct{})
	go func() { _ = client.Wait(); close(ended) }()
	return serveForward(ctx, ln, s, vm, 3389, ended)
}
