package main

import (
	"bytes"
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

// untrustedCertError is returned when the server certificate is neither trusted by
// the system (an internal or self-signed certificate) nor pinned for this server.
type untrustedCertError struct {
	fingerprint string
	cause       error
}

func (e *untrustedCertError) Error() string {
	return fmt.Sprintf("the server certificate is not trusted (SHA-256 %s): %v", e.fingerprint, e.cause)
}

func fingerprint(cert *x509.Certificate) string {
	sum := sha256.Sum256(cert.Raw)
	return strings.ToUpper(hex.EncodeToString(sum[:]))
}

// tlsConfig verifies the server with the system trust store (so a certificate
// from an enterprise CA deployed on the workstation just works) and, failing that,
// accepts only the certificate pinned when signing in.
func tlsConfig(host, pinned string) *tls.Config {
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
			fp := fingerprint(leaf)
			if pinned != "" && strings.EqualFold(pinned, fp) {
				return nil
			}
			return &untrustedCertError{fingerprint: fp, cause: err}
		},
	}
}

func hostOnly(hostport string) string {
	if h, _, err := net.SplitHostPort(hostport); err == nil {
		return h
	}
	return hostport
}

func newHTTPClient(s *server) *http.Client {
	host := hostOnly(strings.TrimPrefix(strings.TrimPrefix(s.URL, "https://"), "http://"))
	return &http.Client{
		Timeout: 30 * time.Second,
		Transport: &http.Transport{
			Proxy:               http.ProxyFromEnvironment,
			TLSClientConfig:     tlsConfig(host, s.Fingerprint),
			TLSHandshakeTimeout: 15 * time.Second,
		},
	}
}

type apiError struct {
	status int
	detail string
	code   string // RFC 8628 error code of the sign-in endpoints
}

func (e *apiError) Error() string {
	if e.detail != "" {
		return e.detail
	}
	return fmt.Sprintf("HTTP %d", e.status)
}

// call sends a JSON request and decodes the JSON answer into out (when not nil).
func call(s *server, method, path string, in, out any) error {
	var body io.Reader
	if in != nil {
		data, err := json.Marshal(in)
		if err != nil {
			return err
		}
		body = bytes.NewReader(data)
	}
	req, err := http.NewRequest(method, s.URL+path, body)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "hyperlite-cli/"+version)
	if in != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if s.Token != "" {
		req.Header.Set("Authorization", "Bearer "+s.Token)
	}
	resp, err := newHTTPClient(s).Do(req)
	if err != nil {
		var certErr *untrustedCertError
		if errors.As(err, &certErr) {
			return certErr
		}
		return fmt.Errorf("cannot reach %s: %w", s.URL, err)
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if resp.StatusCode >= 300 {
		e := &apiError{status: resp.StatusCode}
		var payload struct {
			Detail any    `json:"detail"`
			Error  string `json:"error"`
		}
		if json.Unmarshal(data, &payload) == nil {
			e.code = payload.Error
			if d, ok := payload.Detail.(string); ok {
				e.detail = d
			}
		}
		if resp.StatusCode == http.StatusUnauthorized && e.code == "" {
			e.detail = "the session of this workstation is no longer valid: run hyperlite login " + s.URL
		}
		return e
	}
	if out == nil || len(data) == 0 {
		return nil
	}
	return json.Unmarshal(data, out)
}
