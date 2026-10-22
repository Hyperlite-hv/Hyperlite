package main

import (
	"context"
	"errors"
	"net/http"

	"github.com/Hyperlite-hv/Hyperlite/cli/hyperlite"
)

// The API client is the hyperlite package, shared with the Terraform provider; these names keep the commands short.
type (
	untrustedCertError = hyperlite.UntrustedCertError
	apiError           = hyperlite.APIError
)

const apiPrefix = hyperlite.APIPrefix

func hostOnly(hostport string) string { return hyperlite.HostOnly(hostport) }

func clientFor(s *server) *hyperlite.Client {
	return &hyperlite.Client{URL: s.URL, Token: s.Token, Fingerprint: s.Fingerprint, UserAgent: "hyperlite-cli/" + version}
}

func newHTTPClient(s *server) *http.Client { return clientFor(s).HTTPClient() }

// call sends a JSON request and decodes the JSON answer into out (when not nil).
func call(s *server, method, path string, in, out any) error {
	err := clientFor(s).Call(context.Background(), method, path, in, out)
	var e *apiError
	if errors.As(err, &e) && e.Status == http.StatusUnauthorized && e.OAuthError == "" {
		e.Detail = "the session of this workstation is no longer valid: run hyperlite login " + s.URL
	}
	return err
}
