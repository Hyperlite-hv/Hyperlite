package main

import (
	"bufio"
	"context"
	"crypto/rand"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"net"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/Hyperlite-hv/Hyperlite/cli/hyperlite"
)

const (
	defaultServer = "https://127.0.0.1:8000"
	defaultCert   = "/etc/hyperlite/local/hyperlite.crt"
	defaultSocket = "/run/hyperlite/daemon.sock" // app/daemon/__init__.py
	socketEnv     = "HYPERLITE_DAEMON_SOCKET"
	tokenEnv      = "HYPERLITE_TOKEN"
	// A ticket is asked again this long before it expires, so a request never leaves with one about to lapse.
	renewBefore = time.Minute
)

// credential gives the token of the next request.
type credential interface {
	token(ctx context.Context) (string, error)
}

// staticToken is a named account's API token: its rights and its name in the audit.
type staticToken string

func (t staticToken) token(context.Context) (string, error) { return string(t), nil }

// pickCredential: a named account's token when one is given, root's own ticket from hyperlite-daemon otherwise.
func pickCredential(o options) (credential, error) {
	if o.tokenFile != "" {
		data, err := os.ReadFile(o.tokenFile)
		if err != nil {
			return nil, usageError("--token-file: %v", err)
		}
		t := strings.TrimSpace(string(data))
		if t == "" {
			return nil, usageError("--token-file: %s is empty", o.tokenFile)
		}
		return staticToken(t), nil
	}
	if t := strings.TrimSpace(os.Getenv(tokenEnv)); t != "" {
		return staticToken(t), nil
	}
	if os.Geteuid() != 0 {
		return nil, usageError("run it as root, or act as an account with --token-file or %s", tokenEnv)
	}
	path := os.Getenv(socketEnv)
	if path == "" {
		path = defaultSocket
	}
	return &daemonTicket{socket: path}, nil
}

// daemonTicket is root's session as root@<node>: hyperlite-daemon signs it for a peer whose uid the kernel says is 0
// (docs/design/admin-cli.md, 4.3). It is kept in memory only, and asked again before it lapses during a long wait.
type daemonTicket struct {
	socket  string
	mu      sync.Mutex
	value   string
	expires time.Time
}

type daemonAnswer struct {
	OK     bool   `json:"ok"`
	Error  string `json:"error"`
	Detail string `json:"detail"`
	Result struct {
		Token      string `json:"token"`
		ExpiresInS int    `json:"expires_in_s"`
	} `json:"result"`
}

func (d *daemonTicket) token(ctx context.Context) (string, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.value != "" && time.Until(d.expires) > renewBefore {
		return d.value, nil
	}
	answer, err := askDaemon(ctx, d.socket, "auth.local")
	if err != nil {
		return "", err
	}
	if !answer.OK {
		switch answer.Error {
		case "unknown_operation":
			return "", fmt.Errorf("this node's hyperlite-daemon cannot sign root's ticket yet: update the node, or use --token-file")
		case "":
			return "", errors.New("hyperlite-daemon refused root's ticket")
		}
		return "", fmt.Errorf("hyperlite-daemon refused root's ticket: %s (%s)", answer.Detail, answer.Error)
	}
	if answer.Result.Token == "" {
		return "", errors.New("hyperlite-daemon gave no ticket")
	}
	d.value = answer.Result.Token
	d.expires = time.Now().Add(time.Duration(answer.Result.ExpiresInS) * time.Second)
	return d.value, nil
}

// askDaemon sends one operation on hyperlite-daemon's socket: one JSON object per line each way (app/daemon/__init__.py).
func askDaemon(ctx context.Context, path, op string) (*daemonAnswer, error) {
	var dialer net.Dialer
	conn, err := dialer.DialContext(ctx, "unix", path)
	if err != nil {
		return nil, fmt.Errorf("hyperlite-daemon does not answer on %s: %w", path, err)
	}
	defer conn.Close()
	deadline := time.Now().Add(30 * time.Second)
	if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
		deadline = d
	}
	_ = conn.SetDeadline(deadline)
	id := make([]byte, 16)
	_, _ = rand.Read(id)
	request, _ := json.Marshal(map[string]any{
		"op": op, "args": map[string]any{}, "user": "root", "request_id": hex.EncodeToString(id),
	})
	// The daemon may refuse this process before reading the request: its answer is still there to read.
	_, _ = conn.Write(append(request, '\n'))
	line, err := bufio.NewReader(conn).ReadBytes('\n')
	if err != nil && len(line) == 0 {
		return nil, fmt.Errorf("hyperlite-daemon gave no answer: %w", err)
	}
	var answer daemonAnswer
	if err := json.Unmarshal(line, &answer); err != nil {
		return nil, errors.New("hyperlite-daemon gave an unreadable answer")
	}
	return &answer, nil
}

// pinnedFingerprint is the SHA-256 of the node's own certificate, which the API serves: a self-signed certificate is
// then trusted for this node alone. Empty when the file cannot be read (another server, with a certificate the system
// trusts).
func pinnedFingerprint(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	block, _ := pem.Decode(data)
	if block == nil {
		return ""
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return ""
	}
	return hyperlite.CertFingerprint(cert)
}
