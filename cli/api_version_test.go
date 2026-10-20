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
