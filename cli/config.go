package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
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
	return u.Scheme + "://" + strings.ToLower(u.Host), nil
}

// pick returns the server to use: --server, then HYPERLITE_SERVER/HYPERLITE_TOKEN,
// then the only or the default server.
func (c *config) pick(flag string) (*server, error) {
	if env := os.Getenv("HYPERLITE_TOKEN"); env != "" {
		target := flag
		if target == "" {
			target = os.Getenv("HYPERLITE_SERVER")
		}
		if target == "" {
			return nil, errors.New("HYPERLITE_TOKEN is set: set HYPERLITE_SERVER (or --server) too")
		}
		u, err := normalizeURL(target)
		if err != nil {
			return nil, err
		}
		s := &server{URL: u, Token: env}
		if known, ok := c.Servers[u]; ok {
			s.Fingerprint = known.Fingerprint
		}
		return s, nil
	}
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
		return nil, fmt.Errorf("not signed in to %s: run hyperlite login %s", u, u)
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

func (s *server) expired() bool {
	if s.ExpiresAt == "" {
		return false
	}
	t, err := time.Parse(time.RFC3339Nano, s.ExpiresAt)
	return err == nil && time.Now().After(t)
}
