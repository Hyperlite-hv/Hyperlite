package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// A server before 1.0 has no /api/v1 and answers a bare "Not Found" there; 1.0 and later name a code.
func TestAnOldServerIsNamedAsSuch(t *testing.T) {
	var asked string
	old := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		asked = r.URL.Path
		w.WriteHeader(http.StatusNotFound)
		w.Write([]byte(`{"detail": "Not Found"}`))
	}))
	defer old.Close()
	err := call(&server{URL: old.URL}, "GET", "/vms", nil, nil)
	if asked != "/api/v1/vms" {
		t.Fatalf("asked %q, want the API's prefix", asked)
	}
	if err == nil || !strings.Contains(err.Error(), "before 1.0") {
		t.Fatalf("err = %v, want it to say the server is older than 1.0", err)
	}

	current := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		w.Write([]byte(`{"detail": "VM 'web' not found", "code": "not_found"}`))
	}))
	defer current.Close()
	err = call(&server{URL: current.URL}, "GET", "/vms/web", nil, nil)
	if err == nil || err.Error() != "VM 'web' not found" {
		t.Fatalf("err = %v, want the server's own sentence", err)
	}
}

// A token the server no longer accepts is the workstation's session to renew, said with the command that does it.
func TestAnExpiredSessionNamesTheCommand(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("User-Agent") != "hyperlite-cli/"+version {
			t.Errorf("User-Agent %q", r.Header.Get("User-Agent"))
		}
		w.WriteHeader(http.StatusUnauthorized)
		w.Write([]byte(`{"detail": "Not authenticated", "code": "unauthorized"}`))
	}))
	defer srv.Close()
	err := call(&server{URL: srv.URL, Token: "hlt_x"}, "GET", "/vms", nil, nil)
	if err == nil || !strings.Contains(err.Error(), "hyperlite login "+srv.URL) {
		t.Fatalf("err = %v, want the sign-in command", err)
	}
}
