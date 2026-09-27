package main

import (
	"errors"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

// Links of the web interface ("Open in a terminal" buttons):
//
//	hyperlite://ssh/<vm>?server=<server-url>[&user=<user>]
//	hyperlite://rdp/<vm>?server=<server-url>
//
// A link comes from a web page, so nothing in it is trusted: the VM and user names
// must match strict patterns, and a server this workstation is not signed in to is
// only used after the person confirms it in the terminal and approves the sign-in in
// that server's web interface (see serverFor).
type link struct {
	action, vm, user string
	server           *server
}

func parseLink(raw string, cfg *config) (*link, error) {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "hyperlite" {
		return nil, errors.New("not a hyperlite:// link")
	}
	l := &link{action: u.Host, vm: strings.Trim(u.Path, "/"), user: u.Query().Get("user")}
	if u.User != nil || u.Port() != "" || u.Fragment != "" {
		return nil, errors.New("malformed hyperlite:// link")
	}
	if l.action != "ssh" && l.action != "rdp" {
		return nil, fmt.Errorf("unsupported link action %q", l.action)
	}
	if err := checkVM(l.vm); err != nil {
		return nil, err
	}
	if l.user != "" && !sshUserRe.MatchString(l.user) {
		return nil, fmt.Errorf("invalid user name %q", l.user)
	}
	srv, err := normalizeURL(u.Query().Get("server"))
	if err != nil {
		return nil, err
	}
	l.server = &server{URL: srv}
	if s, ok := cfg.Servers[srv]; ok {
		l.server = s
	}
	return l, nil
}

func cmdOpen(raw string) error {
	cfg, err := loadConfig()
	if err != nil {
		return err
	}
	l, err := parseLink(raw, cfg)
	if err != nil {
		pause(err)
		return err
	}
	if l.action == "rdp" {
		err := cmdRDP(l.server.URL, l.vm)
		if err != nil {
			pause(err)
		}
		return err
	}
	self, err := os.Executable()
	if err != nil {
		return err
	}
	target := l.vm
	if l.user != "" {
		target = l.user + "@" + l.vm
	}
	return openTerminal(self, []string{"ssh", "--server", l.server.URL, target})
}

// pause keeps the window of a failed link open long enough to read the error.
func pause(err error) {
	fmt.Fprintln(os.Stderr, "hyperlite:", err)
	fmt.Fprintln(os.Stderr, "Press Enter to close.")
	_, _ = fmt.Scanln()
}

func openTerminal(self string, args []string) error {
	switch runtime.GOOS {
	case "windows":
		// PowerShell in a new window, as asked from the web interface. Every value was
		// validated above, and each one is single-quoted for PowerShell.
		quoted := []string{"& " + psQuote(self)}
		for _, a := range args {
			quoted = append(quoted, psQuote(a))
		}
		cmd := exec.Command("powershell.exe", "-NoExit", "-NoProfile", "-Command", strings.Join(quoted, " "))
		newConsole(cmd)
		return cmd.Start()
	case "darwin":
		script := shQuote(self)
		for _, a := range args {
			script += " " + shQuote(a)
		}
		return exec.Command("osascript", "-e", `tell application "Terminal" to do script `+appleQuote(script), "-e", `tell application "Terminal" to activate`).Start()
	default:
		full := append([]string{self}, args...)
		for _, term := range [][]string{{"x-terminal-emulator", "-e"}, {"gnome-terminal", "--"}, {"konsole", "-e"}, {"xfce4-terminal", "-x"}, {"xterm", "-e"}} {
			if bin, err := exec.LookPath(term[0]); err == nil {
				return exec.Command(bin, append(term[1:], full...)...).Start()
			}
		}
		return errors.New("no terminal emulator found")
	}
}

func psQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }
func shQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'" }
func appleQuote(s string) string {
	return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(s) + `"`
}

func openBrowser(u string) error {
	switch runtime.GOOS {
	case "windows":
		return exec.Command("rundll32", "url.dll,FileProtocolHandler", u).Start()
	case "darwin":
		return exec.Command("open", u).Start()
	default:
		return exec.Command("xdg-open", u).Start()
	}
}

func cmdSetup() error {
	self, err := os.Executable()
	if err != nil {
		return err
	}
	if abs, err := filepath.Abs(self); err == nil {
		self = abs
	}
	switch runtime.GOOS {
	case "windows":
		// Per user (HKCU): no administrator rights needed, nothing machine-wide.
		steps := [][]string{
			{"add", `HKCU\Software\Classes\hyperlite`, "/ve", "/d", "URL:Hyperlite", "/f"},
			{"add", `HKCU\Software\Classes\hyperlite`, "/v", "URL Protocol", "/d", "", "/f"},
			{"add", `HKCU\Software\Classes\hyperlite\shell\open\command`, "/ve", "/d", `"` + self + `" open "%1"`, "/f"},
		}
		for _, step := range steps {
			if out, err := exec.Command("reg.exe", step...).CombinedOutput(); err != nil {
				return fmt.Errorf("registry: %v: %s", err, strings.TrimSpace(string(out)))
			}
		}
	case "linux":
		home, err := os.UserHomeDir()
		if err != nil {
			return err
		}
		dir := filepath.Join(home, ".local", "share", "applications")
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return err
		}
		// Desktop entry quoting: double quotes, with \ " ` $ escaped.
		execPath := `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`, "`", "\\`", "$", `\$`).Replace(self) + `"`
		desktop := "[Desktop Entry]\nType=Application\nName=Hyperlite\nNoDisplay=true\nExec=" + execPath + " open %u\nMimeType=x-scheme-handler/hyperlite;\n"
		if err := os.WriteFile(filepath.Join(dir, "hyperlite.desktop"), []byte(desktop), 0o644); err != nil {
			return err
		}
		if out, err := exec.Command("xdg-mime", "default", "hyperlite.desktop", "x-scheme-handler/hyperlite").CombinedOutput(); err != nil {
			return fmt.Errorf("xdg-mime: %v: %s", err, strings.TrimSpace(string(out)))
		}
	default:
		return errors.New("hyperlite:// links are not supported on this system yet: use hyperlite ssh <vm>")
	}
	fmt.Println("hyperlite:// links registered for", self)
	fmt.Println("Keep hyperlite at this place, or run hyperlite setup again after moving it.")
	return nil
}
