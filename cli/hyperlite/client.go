// Package hyperlite is a Go client for Hyperlite's HTTP API (version 1, under /api/v1).
//
// hyperlite-cli is built on it, and so is the Terraform / OpenTofu provider (docs/design/terraform-provider.md): both
// talk to a server the same way, with the same TLS rule, the same errors and the same wait for long operations.
package hyperlite

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"time"
)

// APIPrefix is where the server's API lives (Hyperlite 1.0 and later). An older server does not know it, and a server
// newer than this client answers 410 at an address it removed: both are said plainly (see Call).
const APIPrefix = "/api/v1"

// DefaultTimeout bounds one request when Client.Timeout is zero. A long operation is not one request: it answers a
// task at once, followed with WaitTask.
const DefaultTimeout = 30 * time.Second

// Client is one Hyperlite server reached with one token.
type Client struct {
	URL         string // scheme://host[:port], nothing else
	Token       string // a personal API token (hlt_...) or a workstation token; empty for the routes that need none
	Fingerprint string // SHA-256 of a pinned certificate, when the system does not trust the server's
	UserAgent   string // names the program, e.g. hyperlite-cli/1.1.1
	Timeout     time.Duration
	// Insecure accepts any certificate. Only for a lab: a pinned Fingerprint is the way to trust an internal one.
	Insecure bool
}

// UntrustedCertError is returned when the server certificate is neither trusted by the system (an internal or
// self-signed certificate) nor pinned for this server. Fingerprint is what to pin once a person checked it.
type UntrustedCertError struct {
	Fingerprint string
	Cause       error
}

func (e *UntrustedCertError) Error() string {
	return fmt.Sprintf("the server certificate is not trusted (SHA-256 %s): %v", e.Fingerprint, e.Cause)
}

func (e *UntrustedCertError) Unwrap() error { return e.Cause }

// CertFingerprint is the SHA-256 of a certificate, in the form the dashboard shows and Client.Fingerprint takes.
func CertFingerprint(cert *x509.Certificate) string {
	sum := sha256.Sum256(cert.Raw)
	return strings.ToUpper(hex.EncodeToString(sum[:]))
}

// TLSConfig verifies the server with the system trust store (so a certificate from an enterprise CA deployed on the
// workstation just works) and, failing that, accepts only the pinned certificate.
func TLSConfig(host, pinned string) *tls.Config {
	return &tls.Config{
		MinVersion:         tls.VersionTLS12,
		InsecureSkipVerify: true, //nolint:gosec // verification is done below, with pinning as the fallback
		VerifyConnection: func(cs tls.ConnectionState) error {
			if len(cs.PeerCertificates) == 0 {
				return errors.New("no server certificate")
			}
			leaf := cs.PeerCertificates[0]
			opts := x509.VerifyOptions{DNSName: host, Intermediates: x509.NewCertPool()}
			for _, c := range cs.PeerCertificates[1:] {
				opts.Intermediates.AddCert(c)
			}
			_, err := leaf.Verify(opts)
			if err == nil {
				return nil
			}
			fp := CertFingerprint(leaf)
			if pinned != "" && strings.EqualFold(pinned, fp) {
				return nil
			}
			return &UntrustedCertError{Fingerprint: fp, Cause: err}
		},
	}
}

// HostOnly is the host of host:port (or the input when it has no port).
func HostOnly(hostport string) string {
	if h, _, err := net.SplitHostPort(hostport); err == nil {
		return h
	}
	return hostport
}

// HTTPClient is the client every request of c goes through: also what a WebSocket dial needs (the CLI's tunnel).
func (c *Client) HTTPClient() *http.Client {
	host := HostOnly(strings.TrimPrefix(strings.TrimPrefix(c.URL, "https://"), "http://"))
	tlsConf := TLSConfig(host, c.Fingerprint)
	if c.Insecure {
		tlsConf = &tls.Config{MinVersion: tls.VersionTLS12, InsecureSkipVerify: true} //nolint:gosec // asked for, labs only
	}
	timeout := c.Timeout
	if timeout == 0 {
		timeout = DefaultTimeout
	}
	return &http.Client{
		Timeout: timeout,
		Transport: &http.Transport{
			Proxy:               http.ProxyFromEnvironment,
			TLSClientConfig:     tlsConf,
			TLSHandshakeTimeout: 15 * time.Second,
		},
	}
}

// APIError is an answer of the server with a status of 300 or more.
type APIError struct {
	Status int
	Detail string // for a person; may change between versions
	Code   string // for a program: one of the codes of docs/api.md, which only ever grows
	// OAuthError is the RFC 8628 error of the device sign-in endpoints (authorization_pending, access_denied...).
	OAuthError string
}

func (e *APIError) Error() string {
	if e.Detail != "" {
		return e.Detail
	}
	return fmt.Sprintf("HTTP %d", e.Status)
}

// IsNotFound tells whether err is the server saying the object does not exist.
func IsNotFound(err error) bool {
	var e *APIError
	return errors.As(err, &e) && e.Status == http.StatusNotFound
}

// Call sends a JSON request to the API (path without the prefix, e.g. "/vms/web") and decodes the JSON answer into
// out (when not nil).
func (c *Client) Call(ctx context.Context, method, path string, in, out any) error {
	var body io.Reader
	if in != nil {
		data, err := json.Marshal(in)
		if err != nil {
			return err
		}
		body = bytes.NewReader(data)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.URL+APIPrefix+path, body)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/json")
	if c.UserAgent != "" {
		req.Header.Set("User-Agent", c.UserAgent)
	}
	if in != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if c.Token != "" {
		req.Header.Set("Authorization", "Bearer "+c.Token)
	}
	resp, err := c.HTTPClient().Do(req)
	if err != nil {
		var certErr *UntrustedCertError
		if errors.As(err, &certErr) {
			return certErr
		}
		return fmt.Errorf("cannot reach %s: %w", c.URL, err)
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if resp.StatusCode >= 300 {
		e := &APIError{Status: resp.StatusCode}
		var payload struct {
			Detail any    `json:"detail"`
			Error  string `json:"error"`
			Code   string `json:"code"`
		}
		if json.Unmarshal(data, &payload) == nil {
			e.Code, e.OAuthError = payload.Code, payload.Error
			if d, ok := payload.Detail.(string); ok {
				e.Detail = d
			}
		}
		// A server before 1.0 has no /api/v1: it answers every address there with a bare "Not Found", which a server
		// of 1.0 or later never does (its errors name a code).
		if resp.StatusCode == http.StatusNotFound && e.Detail == "Not Found" && e.Code == "" {
			e.Detail = "this server runs a Hyperlite version before 1.0: update it, or use a client of its version"
		}
		return e
	}
	if out == nil || len(data) == 0 {
		return nil
	}
	return json.Unmarshal(data, out)
}
