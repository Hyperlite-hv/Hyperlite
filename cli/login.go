package main

import (
	"bufio"
	"errors"
	"fmt"
	"os"
	"regexp"
	"strings"
	"text/tabwriter"
	"time"
)

var hostnameChars = regexp.MustCompile(`[^A-Za-z0-9._-]`)

func workstationName() string {
	name, err := os.Hostname()
	if err != nil || name == "" {
		name = "workstation"
	}
	name = hostnameChars.ReplaceAllString(name, "-")
	name = strings.TrimLeft(name, "._-")
	if name == "" {
		name = "workstation"
	}
	if len(name) > 63 {
		name = name[:63]
	}
	return name
}

func confirm(question string) bool {
	fmt.Fprint(os.Stderr, question+" [y/N] ")
	line, _ := bufio.NewReader(os.Stdin).ReadString('\n')
	line = strings.ToLower(strings.TrimSpace(line))
	return line == "y" || line == "yes" || line == "o" || line == "oui"
}

// interactive tells whether a person can answer on the terminal (not the case in
// ProxyCommand mode, where stdin is the SSH stream).
func interactive() bool {
	fi, err := os.Stdin.Stat()
	return err == nil && fi.Mode()&os.ModeCharDevice != 0
}

// serverFor returns the server to use and, from a terminal, signs this workstation
// in first when needed (first use, or an expired session): one approval in the web
// interface instead of a separate login step.
func serverFor(flag string) (*server, error) {
	cfg, err := loadConfig()
	if err != nil {
		return nil, err
	}
	s, err := cfg.pick(flag)
	var notIn *notSignedInError
	switch {
	case err == nil && !s.expired():
		return s, nil
	case err == nil && s.Token != "" && os.Getenv("HYPERLITE_TOKEN") == "":
		notIn = &notSignedInError{url: s.URL}
		fmt.Fprintln(os.Stderr, "The session of this workstation has expired.")
	case errors.As(err, &notIn):
		fmt.Fprintf(os.Stderr, "This workstation is not signed in to %s yet.\n", notIn.url)
	default:
		if err == nil {
			return s, nil
		}
		return nil, err
	}
	if !interactive() {
		return nil, notIn
	}
	fmt.Fprintf(os.Stderr, "Sign in to %s now? Press Enter to continue, Ctrl+C to cancel. ", notIn.url)
	if _, err := bufio.NewReader(os.Stdin).ReadString('\n'); err != nil {
		return nil, notIn
	}
	if err := cmdLogin(notIn.url, false); err != nil {
		return nil, err
	}
	cfg, err = loadConfig()
	if err != nil {
		return nil, err
	}
	return cfg.pick(notIn.url)
}

func cmdLogin(rawURL string, yes bool) error {
	u, err := normalizeURL(rawURL)
	if err != nil {
		return err
	}
	cfg, err := loadConfig()
	if err != nil {
		return err
	}
	s := &server{URL: u}
	if known, ok := cfg.Servers[u]; ok {
		s.Fingerprint = known.Fingerprint
	}

	type startAnswer struct {
		DeviceCode              string `json:"device_code"`
		UserCode                string `json:"user_code"`
		VerificationURIComplete string `json:"verification_uri_complete"`
		ExpiresIn               int    `json:"expires_in"`
		Interval                int    `json:"interval"`
	}
	var start startAnswer
	body := map[string]string{"hostname": workstationName(), "client_version": version}
	err = call(s, "POST", "/auth/cli/start", body, &start)
	var certErr *untrustedCertError
	if errors.As(err, &certErr) {
		// First contact with an internal certificate: the same trust-on-first-use
		// decision as an SSH host key, made once and remembered for this server.
		fmt.Fprintf(os.Stderr, "The certificate of %s is not trusted by this computer.\nSHA-256 fingerprint: %s\n", u, certErr.fingerprint)
		fmt.Fprintln(os.Stderr, "Compare it with the one shown by the server administrator before trusting it.")
		if !yes && !confirm("Trust this certificate for this server?") {
			return errors.New("sign-in cancelled")
		}
		s.Fingerprint = certErr.fingerprint
		err = call(s, "POST", "/auth/cli/start", body, &start)
	}
	if err != nil {
		return err
	}

	fmt.Printf("To sign in, approve this workstation in Hyperlite:\n\n    %s\n\nand check that it shows the code  %s\n\n", start.VerificationURIComplete, start.UserCode)
	if err := openBrowser(start.VerificationURIComplete); err != nil {
		fmt.Println("(Open the link above in your browser.)")
	}
	fmt.Println("Waiting for the approval...")

	interval := time.Duration(max(start.Interval, 1)) * time.Second
	deadline := time.Now().Add(time.Duration(start.ExpiresIn) * time.Second)
	for time.Now().Before(deadline) {
		time.Sleep(interval)
		var tok struct {
			AccessToken string `json:"access_token"`
			TokenID     int    `json:"token_id"`
			Username    string `json:"username"`
			ExpiresAt   string `json:"expires_at"`
		}
		err := call(s, "POST", "/auth/cli/token", map[string]string{"device_code": start.DeviceCode}, &tok)
		var apiErr *apiError
		if errors.As(err, &apiErr) {
			switch apiErr.code {
			case "authorization_pending":
				continue
			case "access_denied":
				return errors.New("the sign-in was refused in the web interface")
			case "expired_token":
				return errors.New("the code expired: run hyperlite login again")
			}
		}
		if err != nil {
			return err
		}
		s.Token, s.TokenID, s.Username, s.ExpiresAt = tok.AccessToken, tok.TokenID, tok.Username, tok.ExpiresAt
		cfg.Servers[u] = s
		cfg.Default = u
		if err := cfg.save(); err != nil {
			return fmt.Errorf("signed in, but the configuration could not be saved: %w", err)
		}
		fmt.Printf("Signed in to %s as %s (until %s).\n", u, s.Username, shortDate(s.ExpiresAt))
		return nil
	}
	return errors.New("the code expired: run hyperlite login again")
}

func shortDate(iso string) string {
	t, err := time.Parse(time.RFC3339Nano, iso)
	if err != nil {
		return iso
	}
	return t.Local().Format("2006-01-02 15:04")
}

func cmdLogout(flag string) error {
	cfg, err := loadConfig()
	if err != nil {
		return err
	}
	s, err := cfg.pick(flag)
	if err != nil {
		return err
	}
	if s.TokenID != 0 {
		if err := call(s, "DELETE", fmt.Sprintf("/auth/tokens/%d", s.TokenID), nil, nil); err != nil {
			fmt.Fprintln(os.Stderr, "warning: the token could not be revoked on the server:", err)
		}
	}
	delete(cfg.Servers, s.URL)
	if cfg.Default == s.URL {
		cfg.Default = ""
	}
	if err := cfg.save(); err != nil {
		return err
	}
	fmt.Println("Signed out of", s.URL)
	return nil
}

func cmdStatus() error {
	cfg, err := loadConfig()
	if err != nil {
		return err
	}
	if len(cfg.Servers) == 0 {
		fmt.Println("Not signed in. Run: hyperlite login <server-url>")
		return nil
	}
	w := tabwriter.NewWriter(os.Stdout, 0, 2, 2, ' ', 0)
	fmt.Fprintln(w, "SERVER\tUSER\tVALID UNTIL\t")
	for _, u := range cfg.sortedURLs() {
		s := cfg.Servers[u]
		mark, until := "", shortDate(s.ExpiresAt)
		if u == cfg.Default {
			mark = " (default)"
		}
		if s.expired() {
			until += " (expired)"
		}
		fmt.Fprintf(w, "%s%s\t%s\t%s\t\n", u, mark, s.Username, until)
	}
	return w.Flush()
}

type vmInfo struct {
	Name    string  `json:"nom"`
	State   string  `json:"etat"`
	IP      *string `json:"ip"`
	SSHUser *string `json:"utilisateur_ssh"`
	Node    string  `json:"node"`
}

var stateLabel = map[string]string{"actif": "running", "arrete": "stopped", "suspendu": "paused", "plante": "crashed"}

func cmdVMs(flag string) error {
	s, err := serverFor(flag)
	if err != nil {
		return err
	}
	var vms []vmInfo
	if err := call(s, "GET", "/vms", nil, &vms); err != nil {
		return err
	}
	w := tabwriter.NewWriter(os.Stdout, 0, 2, 2, ' ', 0)
	fmt.Fprintln(w, "NAME\tSTATE\tIP\tSSH USER\t")
	for _, vm := range vms {
		state := stateLabel[vm.State]
		if state == "" {
			state = vm.State
		}
		fmt.Fprintf(w, "%s\t%s\t%s\t%s\t\n", vm.Name, state, deref(vm.IP), deref(vm.SSHUser))
	}
	return w.Flush()
}

func deref(p *string) string {
	if p == nil || *p == "" {
		return "-"
	}
	return *p
}
