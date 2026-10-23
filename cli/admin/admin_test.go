package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestItemsGoToTheQueryOrTheBody(t *testing.T) {
	o, err := parseOptions([]string{"/api/v1/vms/web1/start", "force:=true", "wait==false", "note=hi", "--node", "n2"})
	if err != nil {
		t.Fatal(err)
	}
	req, err := buildRequest("POST", o, nil)
	if err != nil {
		t.Fatal(err)
	}
	if req.path != "/vms/web1/start?node=n2&wait=false" {
		t.Errorf("path %q", req.path)
	}
	if body, _ := json.Marshal(req.body); string(body) != `{"force":true,"note":"hi"}` {
		t.Errorf("body %s", body)
	}

	o, _ = parseOptions([]string{"vms", "cluster=true"})
	req, _ = buildRequest("GET", o, nil)
	if req.path != "/vms?cluster=true" || req.body != nil {
		t.Errorf("a get sends its items as the query: %q %v", req.path, req.body)
	}

	o, _ = parseOptions([]string{"/pools", "--data", "-"})
	req, err = buildRequest("POST", o, strings.NewReader(` {"name": "p1"} `))
	if err != nil {
		t.Fatal(err)
	}
	if body, _ := json.Marshal(req.body); string(body) != `{"name":"p1"}` {
		t.Errorf("--data - reads standard input: %s", body)
	}

	o, _ = parseOptions([]string{"/vms/web1/stop"})
	req, _ = buildRequest("POST", o, nil)
	if body, _ := json.Marshal(req.body); string(body) != `{}` {
		t.Errorf("a post with no item still sends a body: %s", body)
	}
}

func TestWrongUsageIsSaidWithItsExitCode(t *testing.T) {
	for _, args := range [][]string{
		{"get"},
		{"post", "/pools", "a:=notjson"},
		{"post", "/pools", "--data", "{}", "name=x"},
		{"post", "/pools", "--data", "{oops"},
		{"get", "/vms", "x:=1"},
		{"get", "/vms", "--timeout", "soon"},
		{"get", "/vms", "--frobnicate"},
		{"reboot"},
	} {
		var out, errOut bytes.Buffer
		if code := run(args, nil, &out, &errOut); code != exitUsage {
			t.Errorf("%v: exit %d, %s", args, code, errOut.String())
		}
	}
}

// fakeAPI is a node's API over TLS, with a task that runs for `polls` reads before it ends with `outcome`.
type fakeAPI struct {
	srv       *httptest.Server
	certFile  string
	tokenFile string
	polls     atomic.Int32
	cancelled atomic.Bool
	auth      sync.Map // the Authorization headers seen
}

func newFakeAPI(t *testing.T, outcome string, polls int32) *fakeAPI {
	f := &fakeAPI{}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/v1/vms", func(w http.ResponseWriter, r *http.Request) {
		f.auth.Store(r.Header.Get("Authorization"), true)
		fmt.Fprint(w, `{"items":[{"name":"web1","state":"active","memory_bytes":4294967296,"disks":[{"x":1}]},`+
			`{"name":"db1","state":"stopped","memory_bytes":1073741824,"disks":[]}],"total":2}`)
	})
	mux.HandleFunc("POST /api/v1/vms/{name}/start", func(w http.ResponseWriter, r *http.Request) {
		f.auth.Store(r.Header.Get("Authorization"), true)
		if r.PathValue("name") == "busy" {
			w.WriteHeader(http.StatusConflict)
			fmt.Fprint(w, `{"detail":"The VM is already running","code":"conflict"}`)
			return
		}
		if r.URL.Query().Get("node") != "n2" {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		w.WriteHeader(http.StatusAccepted)
		fmt.Fprint(w, `{"task_id":"t1","status":"running"}`)
	})
	mux.HandleFunc("GET /api/v1/tasks/t1", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("on") != "n2" {
			w.WriteHeader(http.StatusNotFound)
			fmt.Fprint(w, `{"detail":"Task not found","code":"not_found"}`)
			return
		}
		status, errText := "running", ""
		if f.polls.Add(1) > polls {
			status = outcome
			if outcome == "failed" {
				errText = "no space left"
			}
		}
		json.NewEncoder(w).Encode(map[string]any{
			"id": "t1", "type": "start_vm", "target": "web1", "status": status, "error": errText, "cancellable": true,
		})
	})
	mux.HandleFunc("GET /api/v1/tasks/t1/log", func(w http.ResponseWriter, r *http.Request) {
		items := []map[string]string{{"at": "10:00:00", "message": "starting"}}
		if f.polls.Load() > 1 {
			items = append(items, map[string]string{"at": "10:00:01", "message": "started"})
		}
		json.NewEncoder(w).Encode(map[string]any{"items": items, "total": len(items)})
	})
	mux.HandleFunc("POST /api/v1/tasks/t1/cancel", func(w http.ResponseWriter, r *http.Request) {
		f.cancelled.Store(true)
	})
	f.srv = httptest.NewTLSServer(mux)
	t.Cleanup(f.srv.Close)

	dir := t.TempDir()
	f.certFile = filepath.Join(dir, "hyperlite.crt")
	pemCert := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: f.srv.Certificate().Raw})
	if err := os.WriteFile(f.certFile, pemCert, 0o644); err != nil {
		t.Fatal(err)
	}
	f.tokenFile = filepath.Join(dir, "token")
	if err := os.WriteFile(f.tokenFile, []byte("hlt_alice\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return f
}

func (f *fakeAPI) run(args ...string) (int, string, string) {
	var out, errOut bytes.Buffer
	args = append(args, "--server", f.srv.URL, "--cert", f.certFile, "--token-file", f.tokenFile)
	code := run(args, nil, &out, &errOut)
	return code, out.String(), errOut.String()
}

func TestAListIsATableInTheAPIOrder(t *testing.T) {
	f := newFakeAPI(t, "succeeded", 0)
	code, out, errOut := f.run("get", "/vms")
	if code != 0 {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	lines := strings.Split(strings.TrimSpace(out), "\n")
	if len(lines) != 3 || !strings.HasPrefix(lines[0], "NAME") || !strings.Contains(lines[0], "MEMORY_BYTES") {
		t.Fatalf("table:\n%s", out)
	}
	if strings.Contains(lines[0], "DISKS") {
		t.Errorf("a list in a cell is left to --json:\n%s", out)
	}
	if !strings.Contains(lines[1], "4294967296") {
		t.Errorf("a size is printed as the API wrote it:\n%s", out)
	}
	if _, ok := f.auth.Load("Bearer hlt_alice"); !ok {
		t.Error("the token of --token-file was not sent")
	}
}

func TestJSONIsTheAnswerAsItIs(t *testing.T) {
	f := newFakeAPI(t, "succeeded", 0)
	code, out, _ := f.run("get", "/vms", "--json")
	var v struct {
		Total int `json:"total"`
	}
	if code != 0 || json.Unmarshal([]byte(out), &v) != nil || v.Total != 2 {
		t.Fatalf("exit %d, %s", code, out)
	}
}

func TestALongOperationIsWaitedForOnItsMember(t *testing.T) {
	f := newFakeAPI(t, "succeeded", 1)
	code, out, errOut := f.run("post", "/vms/web1/start", "--node", "n2")
	if code != 0 {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	for _, want := range []string{"starting", "started", "task t1 (start_vm of web1) succeeded"} {
		if !strings.Contains(out, want) {
			t.Errorf("%q missing from:\n%s", want, out)
		}
	}
	if strings.Count(out, "starting") != 1 {
		t.Errorf("a log line is printed once:\n%s", out)
	}
}

func TestAFailedTaskExitsWithThree(t *testing.T) {
	f := newFakeAPI(t, "failed", 0)
	code, _, errOut := f.run("post", "/vms/web1/start", "--node", "n2")
	if code != exitTask || !strings.Contains(errOut, "no space left") {
		t.Fatalf("exit %d: %s", code, errOut)
	}
}

func TestNoWaitPrintsTheTask(t *testing.T) {
	f := newFakeAPI(t, "succeeded", 5)
	code, out, _ := f.run("post", "/vms/web1/start", "--node", "n2", "--no-wait")
	if code != 0 || !strings.Contains(out, "task_id") || f.polls.Load() != 0 {
		t.Fatalf("exit %d, polls %d: %s", code, f.polls.Load(), out)
	}
}

func TestATimeoutLeavesTheTaskRunning(t *testing.T) {
	f := newFakeAPI(t, "succeeded", 1000)
	code, _, errOut := f.run("post", "/vms/web1/start", "--node", "n2", "--timeout", "1500ms")
	if code != exitTimeout || !strings.Contains(errOut, "hyperlite-admin wait t1 --node n2") {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	if f.cancelled.Load() {
		t.Error("the task was cancelled: stopping the wait must not stop the work")
	}
}

func TestARefusalNamesItsCode(t *testing.T) {
	f := newFakeAPI(t, "succeeded", 0)
	code, _, errOut := f.run("post", "/vms/busy/start")
	if code != exitAPI || !strings.Contains(errOut, "already running (conflict)") {
		t.Fatalf("exit %d: %s", code, errOut)
	}
}

func TestAnotherCertificateIsRefused(t *testing.T) {
	f := newFakeAPI(t, "succeeded", 0)
	// Every httptest server has the same certificate: another node's is made here.
	key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	tmpl := &x509.Certificate{SerialNumber: big.NewInt(2), NotBefore: time.Now(), NotAfter: time.Now().Add(time.Hour)}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	otherCert := filepath.Join(t.TempDir(), "other.crt")
	os.WriteFile(otherCert, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), 0o644)
	var out, errOut bytes.Buffer
	code := run([]string{"get", "/vms", "--server", f.srv.URL, "--cert", otherCert, "--token-file", f.tokenFile},
		nil, &out, &errOut)
	if code == 0 || !strings.Contains(errOut.String(), "not trusted") {
		t.Fatalf("exit %d: %s", code, errOut.String())
	}
}

// fakeDaemon answers on a Unix socket as hyperlite-daemon does, one JSON object per line.
func fakeDaemon(t *testing.T, answer func(op string) map[string]any) (string, *atomic.Int32) {
	path := filepath.Join(t.TempDir(), "daemon.sock")
	ln, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ln.Close() })
	var asked atomic.Int32
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			line, _ := bufio.NewReader(conn).ReadBytes('\n')
			var req struct {
				Op string `json:"op"`
			}
			_ = json.Unmarshal(line, &req)
			asked.Add(1)
			data, _ := json.Marshal(answer(req.Op))
			conn.Write(append(data, '\n'))
			conn.Close()
		}
	}()
	return path, &asked
}

func TestRootsTicketIsAskedAgainBeforeItLapses(t *testing.T) {
	n := 0
	path, asked := fakeDaemon(t, func(op string) map[string]any {
		if op != "auth.local" {
			return map[string]any{"ok": false, "error": "unknown_operation"}
		}
		n++
		// 30 s left is under renewBefore: the next request asks again.
		return map[string]any{"ok": true, "result": map[string]any{"token": fmt.Sprintf("ticket%d", n), "expires_in_s": 30}}
	})
	d := &daemonTicket{socket: path}
	first, err := d.token(context.Background())
	if err != nil || first != "ticket1" {
		t.Fatalf("%q %v", first, err)
	}
	second, _ := d.token(context.Background())
	if second != "ticket2" || asked.Load() != 2 {
		t.Errorf("a ticket about to lapse is renewed: %q after %d asks", second, asked.Load())
	}

	path, asked = fakeDaemon(t, func(string) map[string]any {
		return map[string]any{"ok": true, "result": map[string]any{"token": "long", "expires_in_s": 300}}
	})
	d = &daemonTicket{socket: path}
	for range 3 {
		if tok, _ := d.token(context.Background()); tok != "long" {
			t.Fatal(tok)
		}
	}
	if asked.Load() != 1 {
		t.Errorf("a fresh ticket is kept: asked %d times", asked.Load())
	}
}

func TestADaemonRefusalIsSaid(t *testing.T) {
	path, _ := fakeDaemon(t, func(string) map[string]any {
		return map[string]any{"ok": false, "error": "forbidden", "detail": "Lockdown is on: use a named account's token"}
	})
	_, err := (&daemonTicket{socket: path}).token(context.Background())
	if err == nil || !strings.Contains(err.Error(), "Lockdown is on") {
		t.Fatalf("%v", err)
	}

	path, _ = fakeDaemon(t, func(string) map[string]any {
		return map[string]any{"ok": false, "error": "unknown_operation", "detail": "Unknown operation"}
	})
	_, err = (&daemonTicket{socket: path}).token(context.Background())
	if err == nil || !strings.Contains(err.Error(), "--token-file") {
		t.Fatalf("an older daemon points to the token: %v", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	_, err = (&daemonTicket{socket: filepath.Join(t.TempDir(), "none.sock")}).token(ctx)
	if err == nil || !strings.Contains(err.Error(), "does not answer") {
		t.Fatalf("%v", err)
	}
}
