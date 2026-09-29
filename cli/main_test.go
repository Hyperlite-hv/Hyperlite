package main

import (
	"errors"
	"strings"
	"testing"
)

func TestNormalizeURL(t *testing.T) {
	cases := map[string]string{
		"hl.example.com":                    "https://hl.example.com",
		"https://HL.example.com:8443/app/x": "https://hl.example.com:8443",
		" http://10.0.0.5:8011 ":            "http://10.0.0.5:8011",
	}
	for in, want := range cases {
		got, err := normalizeURL(in)
		if err != nil || got != want {
			t.Errorf("normalizeURL(%q) = %q, %v; want %q", in, got, err, want)
		}
	}
	for _, bad := range []string{"ftp://x", "https://", "https:///path"} {
		if _, err := normalizeURL(bad); err == nil {
			t.Errorf("normalizeURL(%q) accepted", bad)
		}
	}
}

func TestParseFlags(t *testing.T) {
	f, err := parseFlags([]string{"vm1", "--server", "https://a", "-L", "8080:localhost:80", "uptime"}, true)
	if err != nil || f.server != "https://a" || strings.Join(f.rest, " ") != "vm1 -L 8080:localhost:80 uptime" {
		t.Fatalf("got %+v, %v", f, err)
	}
	if _, err := parseFlags([]string{"--bogus"}, false); err == nil {
		t.Fatal("unknown option accepted")
	}
	f, _ = parseFlags([]string{"vm", "22", "--listen=127.0.0.1:2222"}, false)
	if f.listen != "127.0.0.1:2222" {
		t.Fatalf("listen = %q", f.listen)
	}
}

func signedIn() *config {
	return &config{Servers: map[string]*server{"https://hl.example.com:8443": {URL: "https://hl.example.com:8443", Token: "hlt_x"}}}
}

func TestParseLinkValidatesEveryPart(t *testing.T) {
	cfg := signedIn()
	l, err := parseLink("hyperlite://ssh/web-01?server=https%3A%2F%2Fhl.example.com%3A8443&user=deploy", cfg)
	if err != nil || l.action != "ssh" || l.vm != "web-01" || l.user != "deploy" {
		t.Fatalf("got %+v, %v", l, err)
	}
	bad := []string{
		"https://ssh/web-01?server=https%3A%2F%2Fhl.example.com%3A8443",               // not our scheme
		"hyperlite://shell/web-01?server=https%3A%2F%2Fhl.example.com%3A8443",         // unknown action
		"hyperlite://ssh/web-01?server=ftp%3A%2F%2Fhl.example.com",                    // not an https server
		"hyperlite://user@ssh/web-01?server=https%3A%2F%2Fhl.example.com%3A8443",      // malformed
		"hyperlite://ssh/web;calc.exe?server=https%3A%2F%2Fhl.example.com%3A8443",     // VM name injection
		"hyperlite://ssh/web-01?server=https%3A%2F%2Fhl.example.com%3A8443&user=a'b",  // user injection
		"hyperlite://ssh/-oProxyCommand=x?server=https%3A%2F%2Fhl.example.com%3A8443", // option injection
	}
	// A server this workstation is not signed in to is kept without a token: the
	// terminal then asks before signing in (serverFor), nothing is sent to it before.
	l, err = parseLink("hyperlite://ssh/web-01?server=https%3A%2F%2Fother.example.com", cfg)
	if err != nil || l.server.URL != "https://other.example.com" || l.server.Token != "" {
		t.Fatalf("unknown server: %+v, %v", l, err)
	}
	for _, raw := range bad {
		if _, err := parseLink(raw, cfg); err == nil {
			t.Errorf("accepted %s", raw)
		}
	}
}

func TestQuoting(t *testing.T) {
	if got := psQuote(`C:\Users\O'Brien\hyperlite.exe`); got != `'C:\Users\O''Brien\hyperlite.exe'` {
		t.Errorf("psQuote = %s", got)
	}
	if got := shQuote("it's"); got != `'it'\''s'` {
		t.Errorf("shQuote = %s", got)
	}
}

func TestProxyCommandEscapesPercent(t *testing.T) {
	pc, err := proxyCommand(&server{URL: "https://hl.example.com"}, "vm1", 22)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(pc, " tunnel --stdio --server https://hl.example.com vm1 22") || !strings.HasPrefix(pc, `"`) {
		t.Fatalf("proxy command %q", pc)
	}
}

func TestWorkstationName(t *testing.T) {
	name := workstationName()
	if name == "" || hostnameChars.MatchString(name) || len(name) > 63 {
		t.Fatalf("workstation name %q", name)
	}
}

func TestEnvironmentTokenOnlyGoesToItsServer(t *testing.T) {
	t.Setenv("HYPERLITE_TOKEN", "hlt_secret")
	t.Setenv("HYPERLITE_SERVER", "https://hv.example.com")
	cfg := &config{Servers: map[string]*server{}}
	s, err := cfg.pick("")
	if err != nil || s.URL != "https://hv.example.com" || s.Token != "hlt_secret" || !s.FromEnv {
		t.Fatalf("home server: %+v, %v", s, err)
	}
	if s, err = cfg.pick("https://HV.example.com"); err != nil || s.Token != "hlt_secret" {
		t.Fatalf("same server named differently: %+v, %v", s, err)
	}
	// A link pointing elsewhere: the token must not be sent there.
	s, err = cfg.pick("https://attacker.example.net")
	var notIn *notSignedInError
	if !errors.As(err, &notIn) || s != nil {
		t.Fatalf("token handed to another server: %+v, %v", s, err)
	}
	t.Setenv("HYPERLITE_SERVER", "")
	if _, err := cfg.pick("https://hv.example.com"); err == nil {
		t.Fatal("HYPERLITE_TOKEN without HYPERLITE_SERVER accepted")
	}
}

func TestServerAddressesCannotCarryShellCharacters(t *testing.T) {
	for _, raw := range []string{
		"https://hv.example.com;touch${IFS}x",
		"https://hv.example.com`id`",
		"https://a$(id).example.com",
		"https://hv example.com",
		"https://hv.example.com'",
	} {
		if u, err := normalizeURL(raw); err == nil {
			t.Errorf("accepted %q as %q", raw, u)
		}
	}
	for _, raw := range []string{"hv.example.com", "https://10.0.0.5:8000", "https://[fd00::1]:8000", "http://hv-01.lan"} {
		if _, err := normalizeURL(raw); err != nil {
			t.Errorf("refused %q: %v", raw, err)
		}
	}
	if _, err := proxyCommand(&server{URL: "https://x;id"}, "vm1", 22); err == nil {
		t.Error("proxyCommand accepted an unchecked address")
	}
}
