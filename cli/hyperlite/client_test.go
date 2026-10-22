package hyperlite

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestCallSendsTheTokenAndReadsV1Errors(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/vms/web" || r.Header.Get("Authorization") != "Bearer hlt_x" ||
			r.Header.Get("User-Agent") != "test/1" {
			t.Errorf("got %s %s %q %q", r.Method, r.URL.Path, r.Header.Get("Authorization"), r.Header.Get("User-Agent"))
		}
		w.WriteHeader(http.StatusConflict)
		w.Write([]byte(`{"detail": "The VM must be stopped", "code": "conflict"}`))
	}))
	defer srv.Close()
	c := &Client{URL: srv.URL, Token: "hlt_x", UserAgent: "test/1"}
	err := c.Call(context.Background(), "PATCH", "/vms/web", map[string]int{"vcpu": 2}, nil)
	var e *APIError
	if !errors.As(err, &e) || e.Status != 409 || e.Code != "conflict" || e.Error() != "The VM must be stopped" {
		t.Fatalf("err = %#v", err)
	}
	if IsNotFound(err) {
		t.Fatal("a conflict is not a 404")
	}
}

// A server before 1.0 has no /api/v1 and answers a bare "Not Found" there; 1.0 and later name a code.
func TestAnOldServerIsNamedAsSuch(t *testing.T) {
	old := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		w.Write([]byte(`{"detail": "Not Found"}`))
	}))
	defer old.Close()
	err := (&Client{URL: old.URL}).Call(context.Background(), "GET", "/vms", nil, nil)
	if err == nil || !strings.Contains(err.Error(), "before 1.0") {
		t.Fatalf("err = %v, want it to say the server is older than 1.0", err)
	}
	if !IsNotFound(err) {
		t.Fatal("still a 404 for a program")
	}
}

func TestAnUntrustedCertificateIsRefusedThenPinned(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"name": "web"}`))
	}))
	defer srv.Close()
	ctx := context.Background()
	err := (&Client{URL: srv.URL}).Call(ctx, "GET", "/vms/web", nil, nil)
	var certErr *UntrustedCertError
	if !errors.As(err, &certErr) || len(certErr.Fingerprint) != 64 {
		t.Fatalf("err = %v, want an untrusted certificate with its fingerprint", err)
	}
	if want := CertFingerprint(srv.Certificate()); certErr.Fingerprint != want {
		t.Fatalf("fingerprint %s, want %s", certErr.Fingerprint, want)
	}
	var out struct{ Name string }
	if err := (&Client{URL: srv.URL, Fingerprint: certErr.Fingerprint}).Call(ctx, "GET", "/vms/web", nil, &out); err != nil || out.Name != "web" {
		t.Fatalf("pinned: %v, %+v", err, out)
	}
	if err := (&Client{URL: srv.URL, Fingerprint: strings.Repeat("0", 64)}).Call(ctx, "GET", "/vms/web", nil, nil); err == nil {
		t.Fatal("another certificate's fingerprint was accepted")
	}
}

func taskServer(t *testing.T, statuses []string, cancellable bool, cancelled *atomic.Bool) *httptest.Server {
	var polls atomic.Int32
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/v1/tasks/t1" && r.Method == "GET":
			if r.URL.Query().Get("on") != "node2" {
				t.Errorf("task asked without its node: %s", r.URL)
			}
			i := min(int(polls.Add(1))-1, len(statuses)-1)
			c := "false"
			if cancellable {
				c = "true"
			}
			w.Write([]byte(`{"id": "t1", "type": "clone_vm", "target": "web", "status": "` + statuses[i] +
				`", "error": "disk full", "cancellable": ` + c + `}`))
		case r.URL.Path == "/api/v1/tasks/t1/log":
			w.Write([]byte(`{"items": [{"at": "10:00", "message": "copying"}, {"at": "10:01", "message": "no space left"}], "total": 2}`))
		case r.URL.Path == "/api/v1/tasks/t1/cancel" && r.Method == "POST":
			cancelled.Store(true)
			w.Write([]byte(`{"result": "requested"}`))
		default:
			t.Errorf("unexpected %s %s", r.Method, r.URL)
		}
	}))
}

func TestWaitTask(t *testing.T) {
	fast := WaitOptions{Node: "node2", Interval: time.Millisecond, Slower: time.Millisecond}
	var cancelled atomic.Bool

	ok := taskServer(t, []string{"pending", "running", "succeeded"}, false, &cancelled)
	defer ok.Close()
	if task, err := (&Client{URL: ok.URL}).WaitTask(context.Background(), "t1", fast); err != nil || task.Status != "succeeded" {
		t.Fatalf("succeeded: %v, %+v", err, task)
	}

	failed := taskServer(t, []string{"running", "failed"}, false, &cancelled)
	defer failed.Close()
	_, err := (&Client{URL: failed.URL}).WaitTask(context.Background(), "t1", fast)
	var tf *TaskFailedError
	if !errors.As(err, &tf) || !strings.Contains(err.Error(), "disk full") || !strings.Contains(err.Error(), "no space left") {
		t.Fatalf("failed: %v", err)
	}

	stuck := taskServer(t, []string{"running"}, true, &cancelled)
	defer stuck.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	_, err = (&Client{URL: stuck.URL}).WaitTask(ctx, "t1", fast)
	if err == nil || !errors.Is(err, context.DeadlineExceeded) || !cancelled.Load() {
		t.Fatalf("timeout: %v, cancel asked: %v", err, cancelled.Load())
	}
}

func TestHealth(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/health" {
			t.Errorf("asked %s", r.URL.Path)
		}
		w.Write([]byte(`{"status": "ok", "api": 1, "hyperlite_version": "1.1.1"}`))
	}))
	defer srv.Close()
	h, err := (&Client{URL: srv.URL}).GetHealth(context.Background())
	if err != nil || h.CheckAPI() != nil {
		t.Fatalf("%v, %v", err, h)
	}
	for v, want := range map[string]bool{"1.1.1": true, "1.1.0": true, "1.0.9": true, "1.2.0": false, "2.0.0": false, "x": false} {
		if h.AtLeast(v) != want {
			t.Errorf("1.1.1 at least %s: %v", v, !want)
		}
	}
	if (&Health{API: 0}).CheckAPI() == nil || (&Health{API: 2}).CheckAPI() == nil {
		t.Fatal("a server without API v1 accepted")
	}
	if (&Health{API: 1}).AtLeast("1.0.0") {
		t.Fatal("an unknown version is at least nothing")
	}
}
