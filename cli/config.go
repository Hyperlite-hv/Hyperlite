package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// server is one Hyperlite server this workstation is signed in to. The token is a
// workstation token of the server (it expires, and can be revoked from the web
// interface or with `hyperlite logout`).
type server struct {
	URL         string `json:"url"`
	Username    string `json:"username"`
	Token       string `json:"token"`
	TokenID     int    `json:"token_id,omitempty"`
	ExpiresAt   string `json:"expires_at,omitempty"`
	Fingerprint string `json:"fingerprint,omitempty"` // pinned certificate (SHA-256), when not trusted by the system
	// FromEnv: the token is HYPERLITE_TOKEN, not a sign-in of this workstation (never saved, never revoked here).
	FromEnv bool `json:"-"`
}

type config struct {
	Default string             `json:"default"`
	Servers map[string]*server `json:"servers"`
}

func configPath() (string, error) {
	if p := os.Getenv("HYPERLITE_CONFIG"); p != "" {
		return p, nil
	}
	dir, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "hyperlite", "config.json"), nil
}

func loadConfig() (*config, error) {
	cfg := &config{Servers: map[string]*server{}}
	path, err := configPath()
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return cfg, nil
	}
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(data, cfg); err != nil {
		return nil, fmt.Errorf("unreadable configuration %s: %w", path, err)
	}
	if cfg.Servers == nil {
		cfg.Servers = map[string]*server{}
	}
	return cfg, nil
}

// save writes the file readable by its owner only: it holds tokens.
func (c *config) save() error {
	path, err := configPath()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// A server's host: a DNS name, an IPv4 address or a bracketed IPv6 address, with an optional port. Nothing else,
// so that the address can never carry a shell metacharacter into the ssh ProxyCommand (see proxyCommand).
var serverHostRe = regexp.MustCompile(`^([a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*|\[[0-9a-f:.]+\])(:[0-9]{1,5})?$`)

// normalizeURL keeps the scheme, host and port only: the identity of a server.
func normalizeURL(raw string) (string, error) {
	raw = strings.TrimSpace(raw)
	if !strings.Contains(raw, "://") {
		raw = "https://" + raw
	}
	u, err := url.Parse(raw)
	if err != nil || u.Hostname() == "" {
		return "", fmt.Errorf("invalid server address %q", raw)
	}
	if u.Scheme != "https" && u.Scheme != "http" {
		return "", fmt.Errorf("the server address must start with https://")
	}
	host := strings.ToLower(u.Host)
	if !serverHostRe.MatchString(host) {
		return "", fmt.Errorf("invalid server address %q", raw)
	}
	return u.Scheme + "://" + host, nil
}

// pick returns the server to use: --server, then HYPERLITE_SERVER/HYPERLITE_TOKEN,
// then the only or the default server.
//
// HYPERLITE_TOKEN is only ever sent to HYPERLITE_SERVER, the server it was issued for. It used to go to any
// --server, and a hyperlite:// link opens a terminal with the link's --server: one click on a link pointing
// elsewhere handed the token to that host. Any other server is reached with its own stored sign-in.
func (c *config) pick(flag string) (*server, error) {
	if env := os.Getenv("HYPERLITE_TOKEN"); env != "" {
		home := os.Getenv("HYPERLITE_SERVER")
		if home == "" {
			return nil, errors.New("HYPERLITE_TOKEN is set: set HYPERLITE_SERVER to the server it belongs to")
		}
		u, err := normalizeURL(home)
		if err != nil {
			return nil, err
		}
		target := u
		if flag != "" {
			if target, err = normalizeURL(flag); err != nil {
				return nil, err
			}
		}
		if target == u {
			s := &server{URL: u, Token: env, FromEnv: true}
			if known, ok := c.Servers[u]; ok {
				s.Fingerprint = known.Fingerprint
			}
			return s, nil
		}
	}
	return c.pickStored(flag)
}

// pickStored is pick without the environment token: the servers this workstation signed in to.
func (c *config) pickStored(flag string) (*server, error) {
	if flag == "" {
		flag = os.Getenv("HYPERLITE_SERVER")
	}
	if flag != "" {
		u, err := normalizeURL(flag)
		if err != nil {
			return nil, err
		}
		if s, ok := c.Servers[u]; ok {
			return s, nil
		}
		return nil, &notSignedInError{url: u}
	}
	if s, ok := c.Servers[c.Default]; ok {
		return s, nil
	}
	if len(c.Servers) == 1 {
		for _, s := range c.Servers {
			return s, nil
		}
	}
	if len(c.Servers) == 0 {
		return nil, errors.New("not signed in: run hyperlite login <server-url>")
	}
	return nil, errors.New("signed in to several servers: choose one with --server")
}

func (c *config) sortedURLs() []string {
	urls := make([]string, 0, len(c.Servers))
	for u := range c.Servers {
		urls = append(urls, u)
	}
	sort.Strings(urls)
	return urls
}

// notSignedInError: the server is known by its address but this workstation has no
// session there yet; interactive commands offer to sign in (see serverFor).
type notSignedInError struct{ url string }

func (e *notSignedInError) Error() string {
	return fmt.Sprintf("not signed in to %s: run hyperlite login %s", e.url, e.url)
}

func (s *server) expired() bool {
	if s.ExpiresAt == "" {
		return false
	}
	t, err := time.Parse(time.RFC3339Nano, s.ExpiresAt)
	return err == nil && time.Now().After(t)
}
